"""Works out what each flight's fuel and emissions cost.

This is the last calculation step, and the only one that needs numbers from
outside the data: the carbon price, the fuel price, and how much further a
real flight travels than the straight line between airports.

Those numbers are deliberately left empty in config/params.yml until they
have been looked up and their source written down. Running this step before
then fails with a list of what is missing, rather than quietly treating a
missing carbon price as zero and producing a confident wrong answer.
"""

import logging

from flightperf.config import get_settings
from flightperf.fuel.run import validate

log = logging.getLogger(__name__)

# The settings this step cannot run without.
REQUIRED = [
    (
        "prices.eua_eur_per_tonne",
        (
            "Carbon price in euros per tonne of CO2. Use an average over the "
            "period rather than one day, and record which period."
        ),
    ),
    (
        "prices.jet_a1_eur_per_tonne",
        (
            "Jet fuel price in euros per tonne. The IATA jet fuel price "
            "monitor publishes a Europe index."
        ),
    ),
    (
        "fuel_model.route_extension_factor",
        (
            "How much further a real flight travels than the straight line. "
            "EUROCONTROL publishes flight efficiency figures this can come "
            "from. 1.00 means no correction, which would understate every "
            "fuel figure."
        ),
    ),
]

# These columns are declared in sql/01_schema/002_facts.sql. This stays as a
# safety net for a database built before they were added there.
EXTRA_COLUMNS = """
ALTER TABLE core.fact_flight
    ADD COLUMN IF NOT EXISTS fuel_burn_adjusted_kg DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS co2_adjusted_kg       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS fuel_cost_eur         DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS ets_cost_eur          DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS carbon_market         VARCHAR,
    ADD COLUMN IF NOT EXISTS fuel_calibration_factor DOUBLE PRECISION;
"""


def calibration_factors(con):
    """Per-type factors that bring the model into line with ICAO.

    The validation step compares OpenAP's cruise burn with ICAO's published
    fuel table, type by type. Where they differ, ICAO is taken as the
    reference: it is built from airline fuel data, while OpenAP is a
    generic physics model. The factor is ICAO divided by the model, so a
    type the model overstates by 23% gets 1 / 1.23.

    Worked out from config/validation_reference.yml on every run rather
    than written down, so the two can never drift apart. The factor scales
    the whole flight, climb and descent included, although ICAO's figure is
    for cruise - cruise is most of the fuel on all but the shortest flights.
    """
    results, _ = validate(con)
    return {
        type_icao: 1.0 / ratio
        for type_icao, _, _, ratio, _ in results
        if ratio
    }


def missing_settings():
    """Which required settings are still empty. Empty list means ready."""
    settings = get_settings()
    missing = []
    for key, explanation in REQUIRED:
        try:
            settings.require(key)
        except (KeyError, ValueError):
            missing.append((key, explanation))
    return missing


def compute_costs(con):
    """Apply the route correction and work out the costs, per flight.

    Raises if any required price is still missing.
    """
    missing = missing_settings()
    if missing:
        raise ValueError(
            "Cannot work out costs yet. Still missing from config/params.yml:\n"
            + "\n".join("  - " + key for key, _ in missing)
        )

    settings = get_settings()
    eua_price = settings.require("prices.eua_eur_per_tonne")
    fuel_price = settings.require("prices.jet_a1_eur_per_tonne")
    extension = settings.require("fuel_model.route_extension_factor")

    calibrate = settings.params["fuel_model"].get("calibrate_to_icao", False)

    ets = settings.params["eu_ets"]
    free_allocation = ets.get("free_allocation_share") or 0.0
    other_schemes = ets["other_schemes_to_eea"]

    with con.cursor() as cur:
        cur.execute(EXTRA_COLUMNS)

        # Which carbon market covered each flight, by the 2022 rules set out
        # in params.yml. Direction matters: Paris-London is EU ETS but
        # London-Paris is UK ETS. Each airport is first sorted into a zone -
        # the EEA proper, an outermost region (exempt in 2022), or its
        # country code - and the market follows from the two zones.
        #
        # Flights with an unknown airport get no market and a factor of 0,
        # but they fail the quality checks anyway.
        cur.execute("UPDATE core.fact_flight SET carbon_market = NULL")
        cur.execute(
            """
            WITH zone AS (
                SELECT
                    airport_icao,
                    CASE
                        WHEN iso_country = ANY(%(om_countries)s)
                          OR iso_region  = ANY(%(om_regions)s) THEN 'OUTERMOST'
                        WHEN is_eea THEN 'EEA'
                        ELSE iso_country
                    END AS zone
                FROM core.dim_airport
            )
            UPDATE core.fact_flight f
            SET carbon_market = CASE
                    WHEN zo.zone = 'EEA'
                     AND (zd.zone = 'EEA' OR zd.zone = ANY(%(eu_dest)s))
                        THEN 'EU ETS'
                    WHEN zd.zone = 'EEA' AND zo.zone = ANY(%(other_origins)s)
                        THEN (%(other_names)s::text[])[
                                 array_position(%(other_origins)s::text[], zo.zone)]
                    ELSE 'None'
                END
            FROM zone zo, zone zd
            WHERE zo.airport_icao = f.origin_icao
              AND zd.airport_icao = f.destination_icao
            """,
            {
                "om_countries": ets["outermost_regions"]["countries"],
                "om_regions": ets["outermost_regions"]["regions"],
                "eu_dest": ets["eu_ets_departures_to"],
                "other_origins": list(other_schemes.keys()),
                "other_names": list(other_schemes.values()),
            },
        )

        # Only the EU ETS share is priced, so that is what the factor counts.
        cur.execute(
            """
            UPDATE core.fact_flight
            SET ets_scope_factor = CASE WHEN carbon_market = 'EU ETS'
                                        THEN 1.0 ELSE 0.0 END
            """
        )

        # Per-type correction to ICAO's published cruise burn. See
        # calibration_factors() for how the factors are worked out.
        factors = calibration_factors(con) if calibrate else {}
        cur.execute("UPDATE core.fact_flight SET fuel_calibration_factor = NULL")
        cur.execute(
            "CREATE TEMP TABLE calibration (type_icao TEXT PRIMARY KEY, "
            "factor DOUBLE PRECISION) ON COMMIT DROP"
        )
        cur.executemany(
            "INSERT INTO calibration VALUES (%s, %s)", list(factors.items())
        )
        # A type's own factor if it was checked, otherwise the factor of the
        # stand-in type it is modelled as, otherwise no correction.
        cur.execute(
            """
            UPDATE core.fact_flight f
            SET fuel_calibration_factor = coalesce(own.factor, proxy.factor, 1.0)
            FROM core.dim_aircraft_type t
            LEFT JOIN calibration own   ON own.type_icao   = t.type_icao
            LEFT JOIN calibration proxy ON proxy.type_icao = t.openap_proxy_type
            WHERE t.type_icao = f.type_icao
            """
        )

        # The modelled fuel assumes the straight-line distance. Real flights
        # are longer, so it gets scaled up, and then corrected to ICAO. The
        # original figure is kept so the size of both corrections stays
        # visible.
        cur.execute(
            """
            UPDATE core.fact_flight
            SET fuel_burn_adjusted_kg = fuel_burn_kg * %(ext)s
                                        * coalesce(fuel_calibration_factor, 1.0),
                co2_adjusted_kg       = co2_kg * %(ext)s
                                        * coalesce(fuel_calibration_factor, 1.0),
                fuel_cost_eur         = fuel_burn_kg * %(ext)s
                                        * coalesce(fuel_calibration_factor, 1.0)
                                        / 1000.0 * %(fuel)s,
                -- Airlines pay for the covered share of their emissions,
                -- less whatever free allowances still apply.
                ets_cost_eur          = co2_kg * %(ext)s
                                        * coalesce(fuel_calibration_factor, 1.0)
                                        / 1000.0
                                        * coalesce(ets_scope_factor, 0)
                                        * (1 - %(free)s)
                                        * %(eua)s
            WHERE fuel_burn_kg IS NOT NULL
            """,
            {"ext": extension, "fuel": fuel_price, "eua": eua_price,
             "free": free_allocation},
        )
        n_priced = cur.rowcount

        cur.execute(
            """
            SELECT
                sum(fuel_burn_adjusted_kg) / 1000.0,
                sum(co2_adjusted_kg) / 1000.0,
                sum(fuel_cost_eur),
                sum(ets_cost_eur),
                count(*) FILTER (WHERE ets_scope_factor > 0)
            FROM core.fact_flight
            WHERE fuel_burn_kg IS NOT NULL
            """
        )
        fuel_t, co2_t, fuel_eur, ets_eur, n_in_scope = cur.fetchone()

        # CO2 per carbon market, over the flights the analysis trusts.
        cur.execute(
            """
            SELECT coalesce(carbon_market, 'None'), sum(co2_adjusted_kg) / 1000.0
            FROM core.fact_flight
            WHERE fuel_burn_kg IS NOT NULL AND is_analysis_eligible
            GROUP BY 1
            ORDER BY 2 DESC
            """
        )
        co2_by_market = {market: float(t) for market, t in cur.fetchall()}

    con.commit()
    log.info("costed %s flights", format(n_priced, ","))
    return {
        "flights": n_priced,
        "in_ets_scope": n_in_scope,
        "fuel_tonnes": float(fuel_t or 0),
        "co2_tonnes": float(co2_t or 0),
        "fuel_cost_eur": float(fuel_eur or 0),
        "ets_cost_eur": float(ets_eur or 0),
        "co2_by_market": co2_by_market,
        "calibrated_types": len(factors),
        "route_extension_factor": extension,
        "eua_price": eua_price,
        "fuel_price": fuel_price,
    }
