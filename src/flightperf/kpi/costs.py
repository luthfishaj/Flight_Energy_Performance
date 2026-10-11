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
    ADD COLUMN IF NOT EXISTS ets_cost_eur          DOUBLE PRECISION;
"""


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

    scope = settings.params["eu_ets"]["scope"]
    free_allocation = settings.params["eu_ets"].get("free_allocation_share") or 0.0

    with con.cursor() as cur:
        cur.execute(EXTRA_COLUMNS)

        # How much of a flight's CO2 the EU carbon market covers. Flights
        # between two European airports are in scope; flights leaving or
        # arriving from outside fall under different arrangements.
        cur.execute(
            """
            UPDATE core.fact_flight f
            SET ets_scope_factor = CASE
                WHEN f.is_intra_eea THEN %s
                WHEN ao.is_eea AND NOT coalesce(ad.is_eea, FALSE) THEN %s
                WHEN coalesce(ao.is_eea, FALSE) = FALSE AND ad.is_eea THEN %s
                ELSE 0.0
            END
            FROM core.dim_airport ao, core.dim_airport ad
            WHERE ao.airport_icao = f.origin_icao
              AND ad.airport_icao = f.destination_icao
            """,
            [scope["intra_eea"], scope["eea_to_third_country"],
             scope["third_country_to_eea"]],
        )

        # The modelled fuel assumes the straight-line distance. Real flights
        # are longer, so it gets scaled up. The original figure is kept so
        # the size of the correction stays visible.
        cur.execute(
            """
            UPDATE core.fact_flight
            SET fuel_burn_adjusted_kg = fuel_burn_kg * %(ext)s,
                co2_adjusted_kg       = co2_kg * %(ext)s,
                fuel_cost_eur         = fuel_burn_kg * %(ext)s / 1000.0 * %(fuel)s,
                -- Airlines pay for the covered share of their emissions,
                -- less whatever free allowances still apply.
                ets_cost_eur          = co2_kg * %(ext)s / 1000.0
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

    con.commit()
    log.info("costed %s flights", format(n_priced, ","))
    return {
        "flights": n_priced,
        "in_ets_scope": n_in_scope,
        "fuel_tonnes": float(fuel_t or 0),
        "co2_tonnes": float(co2_t or 0),
        "fuel_cost_eur": float(fuel_eur or 0),
        "ets_cost_eur": float(ets_eur or 0),
        "route_extension_factor": extension,
        "eua_price": eua_price,
        "fuel_price": fuel_price,
    }
