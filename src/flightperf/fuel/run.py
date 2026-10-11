"""Applies the fuel model to every flight in the database.

Two steps:

  1. Work out which aircraft types OpenAP can model, and record what it
     knows about each one (weights, seats, whether a stand-in type had to
     be used).

  2. Calculate the fuel and CO2 for every flight that passed the quality
     checks.

Doing step 2 one flight at a time would mean millions of calls into the
performance model, which is far too slow. Fuel burn changes smoothly with
distance, though, so instead a curve is worked out per aircraft type at a
spread of distances, and each flight is read off that curve. The curves are
stored in the database so the model's output can be inspected directly
rather than taken on trust.
"""

import logging

from flightperf.config import _load_yaml
from flightperf.fuel.model import CO2_PER_KG_FUEL, FuelEstimator, UnsupportedAircraft

log = logging.getLogger(__name__)

# Distances the curve is built at, in nautical miles. Closely spaced at the
# short end, where fuel per mile changes fastest because climb and descent
# dominate; wider apart further out, where the curve is nearly a straight
# line.
CURVE_DISTANCES_NM = [
    50, 75, 100, 150, 200, 250, 300, 400, 500, 600, 750, 900,
    1100, 1300, 1600, 2000, 2500, 3000, 3500, 4000, 5000, 6000, 7500, 9000,
]

CURVE_DDL = """
CREATE TABLE IF NOT EXISTS core.fuel_curve (
    type_icao     VARCHAR NOT NULL,
    distance_nm   DOUBLE PRECISION NOT NULL,
    fuel_kg       DOUBLE PRECISION NOT NULL,
    airborne_s    DOUBLE PRECISION,
    PRIMARY KEY (type_icao, distance_nm),
    FOREIGN KEY (type_icao) REFERENCES core.dim_aircraft_type (type_icao)
);
"""


def resolve_aircraft_types(con):
    """Fill in what OpenAP knows about each aircraft type in the data.

    Sets openap_supported, the stand-in type where one was used, the
    weights, and the seat count the per-seat metrics rely on.
    """
    with con.cursor() as cur:
        cur.execute(
            "SELECT type_icao, n_flights_seen FROM core.dim_aircraft_type "
            "ORDER BY n_flights_seen DESC"
        )
        types = cur.fetchall()

    supported, unsupported = [], []
    for type_icao, n_flights in types:
        try:
            info = FuelEstimator(type_icao).info
        except (UnsupportedAircraft, KeyError, ValueError, FileNotFoundError):
            # OpenAP raises a range of errors for a type it does not know.
            unsupported.append((type_icao, n_flights))
            continue
        supported.append((info, n_flights))

    with con.cursor() as cur:
        cur.execute(
            "UPDATE core.dim_aircraft_type "
            "SET openap_supported = FALSE, openap_proxy_type = NULL"
        )
        for info, _n in supported:
            cur.execute(
                """
                UPDATE core.dim_aircraft_type
                SET openap_supported = TRUE,
                    openap_proxy_type = %s,
                    mtow_kg = %s,
                    oew_kg = %s,
                    typical_seats = %s,
                    engine_type = %s
                WHERE type_icao = %s
                """,
                [
                    info["proxy"],
                    info["mtow_kg"],
                    info["oew_kg"],
                    info["seats"],
                    info["engine_type"],
                    info["type_icao"],
                ],
            )
    con.commit()

    n_sup_flights = sum(n for _, n in supported)
    n_unsup_flights = sum(n for _, n in unsupported)
    log.info(
        "%s of %s aircraft types can be modelled (%s of %s flights)",
        format(len(supported), ","), format(len(types), ","),
        format(n_sup_flights, ","), format(n_sup_flights + n_unsup_flights, ","),
    )
    return {
        "types_total": len(types),
        "types_supported": len(supported),
        "flights_supported": n_sup_flights,
        "flights_unsupported": n_unsup_flights,
    }


def build_curves(con):
    """Work out the fuel-against-distance curve for each supported type."""
    with con.cursor() as cur:
        cur.execute(CURVE_DDL)
        cur.execute(
            "SELECT type_icao FROM core.dim_aircraft_type "
            "WHERE openap_supported ORDER BY n_flights_seen DESC"
        )
        types = [row[0] for row in cur.fetchall()]

        cur.execute("DELETE FROM core.fuel_curve")

        rows = []
        for type_icao in types:
            estimator = FuelEstimator(type_icao)
            for distance in CURVE_DISTANCES_NM:
                result = estimator.estimate(distance)
                rows.append((type_icao, float(distance), result["fuel_kg"],
                             result["airborne_seconds"]))

        cur.executemany(
            "INSERT INTO core.fuel_curve (type_icao, distance_nm, fuel_kg, airborne_s) "
            "VALUES (%s, %s, %s, %s)",
            rows,
        )
    con.commit()

    log.info("built curves for %s types (%s points)", len(types), format(len(rows), ","))
    return len(types), len(rows)


def apply_to_flights(con):
    """Read each flight's fuel off its aircraft type's curve.

    Flights are matched to the two nearest points on the curve and the
    answer interpolated between them. Only flights that passed the quality
    checks are touched; the rest keep NULL fuel, which is different from
    zero and has to stay that way.
    """
    with con.cursor() as cur:
        # Clear any previous run so re-running gives a clean answer.
        cur.execute(
            "UPDATE core.fact_flight SET fuel_burn_kg = NULL, co2_kg = NULL "
            "WHERE fuel_burn_kg IS NOT NULL"
        )

        cur.execute(
            """
            WITH eligible AS (
                SELECT f.flight_id, f.type_icao, f.great_circle_nm
                FROM core.fact_flight f
                JOIN core.dim_aircraft_type t ON t.type_icao = f.type_icao
                WHERE f.is_analysis_eligible
                  AND t.openap_supported
                  AND f.great_circle_nm > 0
            ),
            bracketed AS (
                SELECT
                    e.flight_id,
                    e.great_circle_nm,
                    -- The curve point at or below this distance, and the one
                    -- at or above it. DISTINCT ON with ORDER BY picks the
                    -- nearest of each.
                    (SELECT c.distance_nm FROM core.fuel_curve c
                      WHERE c.type_icao = e.type_icao
                        AND c.distance_nm <= e.great_circle_nm
                      ORDER BY c.distance_nm DESC LIMIT 1)  AS d_lo,
                    (SELECT c.fuel_kg FROM core.fuel_curve c
                      WHERE c.type_icao = e.type_icao
                        AND c.distance_nm <= e.great_circle_nm
                      ORDER BY c.distance_nm DESC LIMIT 1)  AS f_lo,
                    (SELECT c.distance_nm FROM core.fuel_curve c
                      WHERE c.type_icao = e.type_icao
                        AND c.distance_nm >= e.great_circle_nm
                      ORDER BY c.distance_nm ASC LIMIT 1)   AS d_hi,
                    (SELECT c.fuel_kg FROM core.fuel_curve c
                      WHERE c.type_icao = e.type_icao
                        AND c.distance_nm >= e.great_circle_nm
                      ORDER BY c.distance_nm ASC LIMIT 1)   AS f_hi
                FROM eligible e
            )
            UPDATE core.fact_flight f
            SET fuel_burn_kg = calc.fuel_kg,
                co2_kg = calc.fuel_kg * %s
            FROM (
                SELECT
                    flight_id,
                    CASE
                        -- Shorter than the first curve point, or longer than
                        -- the last: fall back to the nearest end rather than
                        -- extrapolating into nonsense.
                        WHEN d_lo IS NULL THEN f_hi
                        WHEN d_hi IS NULL THEN f_lo
                        WHEN d_hi = d_lo THEN f_lo
                        ELSE f_lo + (f_hi - f_lo)
                             * (great_circle_nm - d_lo) / (d_hi - d_lo)
                    END AS fuel_kg
                FROM bracketed
            ) calc
            WHERE calc.flight_id = f.flight_id
              AND calc.fuel_kg IS NOT NULL
            """,
            [CO2_PER_KG_FUEL],
        )
        n_updated = cur.rowcount

        cur.execute(
            """
            SELECT
                count(*) FILTER (WHERE fuel_burn_kg IS NOT NULL),
                count(*) FILTER (WHERE is_analysis_eligible),
                sum(fuel_burn_kg) / 1000.0,
                sum(co2_kg) / 1000.0
            FROM core.fact_flight
            """
        )
        with_fuel, eligible, fuel_tonnes, co2_tonnes = cur.fetchone()

    con.commit()
    log.info("fuel calculated for %s flights", format(n_updated, ","))
    return {
        "updated": n_updated,
        "with_fuel": with_fuel,
        "eligible": eligible,
        "fuel_tonnes": float(fuel_tonnes or 0),
        "co2_tonnes": float(co2_tonnes or 0),
    }


def validate(con):
    """Compare the modelled cruise burn against published figures.

    Returns one row per aircraft type: what the model says, what the
    reference says, and the ratio between them.
    """
    reference = _load_yaml("validation_reference.yml")
    published = reference.get("cruise_burn_tonnes_per_hour", {})
    tolerance = reference.get("tolerance", 0.15)

    results = []
    for type_icao, real_tph in sorted(published.items()):
        try:
            estimator = FuelEstimator(type_icao)
        except (UnsupportedAircraft, KeyError, ValueError, FileNotFoundError):
            results.append((type_icao, None, real_tph, None, "not modelled"))
            continue

        # A representative mid-cruise weight: empty plus payload, plus a
        # bit for the fuel still on board.
        mass = min(estimator.zero_fuel_mass_kg * 1.15, estimator.info["mtow_kg"])
        flow_kg_s = float(estimator.flow.enroute(
            mass=mass,
            tas=estimator.cruise_tas_kt,
            alt=estimator.info["cruise_alt_ft"],
            vs=0,
        ))
        model_tph = flow_kg_s * 3600 / 1000.0
        ratio = model_tph / real_tph
        verdict = "ok" if abs(ratio - 1.0) <= tolerance else "OUT OF RANGE"
        results.append((type_icao, model_tph, real_tph, ratio, verdict))

    return results, tolerance
