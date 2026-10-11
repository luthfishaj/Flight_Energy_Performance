"""Works out how much fuel each flight burned, using OpenAP.

The source data gives an aircraft type and two airports, nothing more. So
the fuel has to be modelled rather than measured.

How it works. OpenAP knows, for a given aircraft type, how much fuel per
second it burns at a given weight, speed and altitude. That is only useful
with a flight profile to apply it to, so this module builds a simple one:
climb to cruise altitude, cruise, descend. The fuel flow is worked out in
small steps along that profile, and the aircraft gets lighter as it goes.

What it deliberately does NOT include:

  * Taxi fuel. OpenAP has no idle model, and making up a number would be
    worse than leaving it out. So these are airborne figures, and real
    block fuel is a few percent higher.
  * The extra distance real flights fly compared to the straight line
    between airports. That correction needs a published figure and is
    applied later, in the KPI step.

Both are listed as limitations wherever the numbers appear.
"""

import logging
import warnings

from openap import FuelFlow, aero, prop

log = logging.getLogger(__name__)

# Standard planning mass for one passenger plus their baggage, in kg. This
# is the usual figure used in flight planning.
PASSENGER_MASS_KG = 100.0

# Aircraft carry more fuel than the trip itself needs, and that extra
# weight burns extra fuel, so it has to be part of the takeoff mass.
#
# A flat percentage is wrong: on a short hop the fixed reserves dwarf the
# trip fuel, while on a twelve-hour sector they are a rounding error. So
# reserves are modelled the way flight planning actually does it - a
# percentage of the trip for contingency, plus a fixed flying time for
# diversion and holding.
CONTINGENCY_SHARE = 0.05          # 5% of trip fuel
RESERVE_FLYING_MINUTES = 45.0     # alternate airport plus final reserve

# The flight profile. These are ordinary values for an airliner rather than
# anything type-specific, and they are the biggest simplification here.
CLIMB_RATE_FPM = 1800.0
DESCENT_RATE_FPM = -1500.0

# Below this the aircraft is manoeuvring near the airport; the profile
# starts and ends here rather than at ground level.
PROFILE_FLOOR_FT = 1500.0

# How many steps to break each phase into when integrating. Twenty is
# plenty - going finer changes the answer by well under a percent.
STEPS_PER_PHASE = 20

# Burning one kg of jet fuel produces this many kg of CO2. Standard figure,
# used by ICAO and in EU reporting.
CO2_PER_KG_FUEL = 3.16

METRES_PER_FOOT = 0.3048
KNOTS_PER_MS = 1.0 / 0.514444


class UnsupportedAircraft(Exception):
    """Raised when OpenAP has no performance data for an aircraft type."""


def resolve_type(type_icao):
    """Look up one aircraft type in OpenAP.

    Returns a dictionary describing it, or raises UnsupportedAircraft.

    OpenAP covers 37 types directly and maps a further set onto similar
    aircraft through a synonym table - a Boeing 737-500 is modelled as a
    737-400, for instance. Which of the two happened is recorded, because
    a synonym is an extra approximation the reader should know about.
    """
    code = type_icao.lower()
    available = {a.lower() for a in prop.available_aircraft()}

    if code in available:
        proxy = None
    else:
        # Ask for it with synonyms allowed. OpenAP warns when it substitutes
        # a type; that warning is the signal, so it gets captured rather
        # than printed 88,000 times.
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                prop.aircraft(code, use_synonym=True)
            proxy = None
            for w in caught:
                text = str(w.message)
                if "synonym" in text:
                    # Message reads: "Aircraft: using synonym b734 for b735"
                    proxy = text.split("synonym")[1].split("for")[0].strip()
            if proxy is None:
                proxy = code
        except Exception as error:
            raise UnsupportedAircraft(type_icao) from error

    ac = prop.aircraft(code, use_synonym=True)

    pax = ac.get("pax") or {}
    low, high = pax.get("low"), pax.get("high")
    if low and high:
        seats = round((low + high) / 2)
    else:
        seats = pax.get("max")

    return {
        "type_icao": type_icao,
        "supported": True,
        "proxy": proxy.upper() if proxy else None,
        "mtow_kg": ac.get("mtow"),
        "oew_kg": ac.get("oew"),
        "seats": seats,
        "cruise_alt_ft": ac["cruise"]["height"] / METRES_PER_FOOT,
        "cruise_mach": ac["cruise"]["mach"],
        "engine_type": (ac.get("engine") or {}).get("type"),
    }


class FuelEstimator:
    """Estimates trip fuel for a given aircraft type and distance.

    One instance per type. Building a FuelFlow is slow, so they are reused
    across every flight of that type.
    """

    def __init__(self, type_icao):
        self.info = resolve_type(type_icao)
        self.flow = FuelFlow(ac=self.info["type_icao"].lower(), use_synonym=True)

        alt_m = self.info["cruise_alt_ft"] * METRES_PER_FOOT
        self.cruise_tas_kt = aero.mach2tas(self.info["cruise_mach"], alt_m) * KNOTS_PER_MS

        # Empty weight plus a normal passenger load. OpenAP's "low" seat
        # count is used as the passenger figure, which is a realistic load
        # rather than a full aircraft.
        seats = self.info["seats"] or 0
        self.zero_fuel_mass_kg = self.info["oew_kg"] + seats * PASSENGER_MASS_KG

    def _phase_fuel(self, mass_kg, distance_nm, alt_start_ft, alt_end_ft,
                    tas_kt, vertical_rate_fpm):
        """Fuel burned over one phase, integrated in steps.

        Returns (fuel_kg, seconds). The aircraft gets lighter as it burns,
        so mass is carried forward from step to step rather than held fixed.
        """
        if distance_nm <= 0:
            return 0.0, 0.0

        seconds = distance_nm / tas_kt * 3600.0
        step_seconds = seconds / STEPS_PER_PHASE

        fuel = 0.0
        for i in range(STEPS_PER_PHASE):
            # Conditions at the middle of this step.
            fraction = (i + 0.5) / STEPS_PER_PHASE
            alt = alt_start_ft + (alt_end_ft - alt_start_ft) * fraction

            flow_kg_s = self.flow.enroute(
                mass=mass_kg - fuel,
                tas=tas_kt,
                alt=alt,
                vs=vertical_rate_fpm,
            )
            fuel += float(flow_kg_s) * step_seconds

        return fuel, seconds

    def _trip(self, distance_nm, takeoff_mass_kg):
        """One pass at the whole flight, given a starting weight."""
        cruise_alt = self.info["cruise_alt_ft"]
        cruise_tas = self.cruise_tas_kt

        # Climb and descent are slower than cruise, so they cover less
        # ground per minute. These fractions are rough but reasonable.
        climb_tas = cruise_tas * 0.75
        descent_tas = cruise_tas * 0.80

        climb_seconds = (cruise_alt - PROFILE_FLOOR_FT) / CLIMB_RATE_FPM * 60.0
        descent_seconds = (cruise_alt - PROFILE_FLOOR_FT) / abs(DESCENT_RATE_FPM) * 60.0

        climb_nm = climb_tas * climb_seconds / 3600.0
        descent_nm = descent_tas * descent_seconds / 3600.0

        # On a short hop there is no room for a full climb and descent, so
        # both are squeezed proportionally and the aircraft never reaches
        # its normal cruise altitude.
        if climb_nm + descent_nm > distance_nm:
            squeeze = distance_nm / (climb_nm + descent_nm)
            climb_nm *= squeeze
            descent_nm *= squeeze
            cruise_alt = PROFILE_FLOOR_FT + (cruise_alt - PROFILE_FLOOR_FT) * squeeze
            cruise_nm = 0.0
        else:
            cruise_nm = distance_nm - climb_nm - descent_nm

        mass = takeoff_mass_kg
        total_fuel = 0.0
        total_seconds = 0.0

        for nm, alt_a, alt_b, tas, vs in [
            (climb_nm, PROFILE_FLOOR_FT, cruise_alt, climb_tas, CLIMB_RATE_FPM),
            (cruise_nm, cruise_alt, cruise_alt, cruise_tas, 0.0),
            (descent_nm, cruise_alt, PROFILE_FLOOR_FT, descent_tas, DESCENT_RATE_FPM),
        ]:
            fuel, seconds = self._phase_fuel(mass, nm, alt_a, alt_b, tas, vs)
            total_fuel += fuel
            total_seconds += seconds
            mass -= fuel

        return total_fuel, total_seconds

    def estimate(self, distance_nm):
        """Fuel and CO2 for a flight of this distance.

        The starting weight depends on how much fuel is loaded, and how much
        fuel is needed depends on the weight. That is circular, so it is
        solved by repeating the calculation until the answer stops moving.

        Two passes are enough for a short flight, where fuel is a small part
        of the weight. On a long sector fuel can be a third of the takeoff
        mass, and the first pass is badly wrong - hence looping until the
        change drops below a tenth of a percent.
        """
        if distance_nm is None or distance_nm <= 0:
            return None

        # First guess: fuel is a tenth of the empty-plus-payload mass.
        trip_fuel = self.zero_fuel_mass_kg * 0.10
        seconds = 0.0
        takeoff_mass = self.zero_fuel_mass_kg

        for _ in range(12):
            hours = max(seconds / 3600.0, 0.5)
            reserve = (
                trip_fuel * CONTINGENCY_SHARE
                + (trip_fuel / hours) * (RESERVE_FLYING_MINUTES / 60.0)
            )
            takeoff_mass = min(
                self.zero_fuel_mass_kg + trip_fuel + reserve,
                self.info["mtow_kg"],
            )

            new_fuel, seconds = self._trip(distance_nm, takeoff_mass)

            converged = abs(new_fuel - trip_fuel) < 0.001 * max(new_fuel, 1.0)
            trip_fuel = new_fuel
            if converged:
                break

        return {
            "fuel_kg": trip_fuel,
            "co2_kg": trip_fuel * CO2_PER_KG_FUEL,
            "airborne_seconds": seconds,
            "takeoff_mass_kg": takeoff_mass,
        }
