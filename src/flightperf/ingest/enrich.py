"""Applies the reference lookups to the flights.

The aircraft registry and the airline list are stored permanently in
core.ref_aircraft and core.ref_airline. This module is what actually uses
them, and it gets called from two places:

  * after loading the reference data, to fix up flights already there
  * after loading flights, to pick up reference data already there

That way the ingest commands can be run in any order and the result is the
same, instead of the aircraft registry silently doing nothing because the
flights had not been loaded yet.
"""

import logging

log = logging.getLogger(__name__)

# Cargo airlines are worth flagging separately: they carry no passengers,
# so every per-seat metric is meaningless for them. The airline list has no
# cargo flag, so they are spotted two ways.
#
# By name, for the ones that say so ("Bluebird Cargo", "Arcus-Air Logistic").
CARGO_HINTS = ["cargo", "freight", "logistic", "fedex", "ups airlines"]

# By code, for the big ones whose names give nothing away. FedEx is listed
# as "Federal Express" and DHL's European hub airline as "European Air
# Transport", so the name check alone missed every major cargo operator.
# Europe Airpost (FPO) is left out on purpose: it flies passenger charters
# as well as mail.
CARGO_OPERATORS = [
    "FDX",  # FedEx (Federal Express)
    "UPS",  # UPS (United Parcel Service)
    "BCS",  # DHL / European Air Transport Leipzig
    "DHK",  # DHL Air UK
    "TAY",  # ASL Airlines Belgium (ex TNT Airways)
    "ABR",  # ASL Airlines Ireland (ex Air Contractors)
    "SWN",  # West Atlantic Sweden
    "NPT",  # West Atlantic UK (Atlantic Airlines)
    "CLX",  # Cargolux
    "BOX",  # AeroLogic
    "GTI",  # Atlas Air
    "CKS",  # Kalitta Air
]


def apply_aircraft_registry(cur):
    """Fill in missing aircraft types by looking up the transponder code.

    Only touches flights whose type is blank. A type that came with the
    flight data is left alone, and type_source records which is which.
    """
    cur.execute(
        """
        UPDATE core.fact_flight f
        SET type_icao = r.typecode,
            type_source = 'registry'
        FROM core.ref_aircraft r
        WHERE f.type_icao IS NULL
          AND r.icao24 = lower(f.icao24)
          AND r.typecode IS NOT NULL
        """
    )
    recovered = cur.rowcount
    if recovered:
        log.info("recovered %s aircraft types from the registry", format(recovered, ","))
    return recovered


def apply_airline_names(cur):
    """Put names and countries against the airline codes seen in the data.

    Also sets the cargo flag. It lives here rather than in the airline
    loader because loading flights rebuilds dim_operator from scratch, which
    used to wipe the flag whenever flights were loaded after airlines.
    """
    cur.execute(
        """
        UPDATE core.dim_operator o
        SET operator_name = r.operator_name,
            country = r.country
        FROM core.ref_airline r
        WHERE r.operator_icao = o.operator_icao
        """
    )
    named = cur.rowcount
    if named:
        log.info("named %s operators", format(named, ","))

    cur.execute(
        """
        UPDATE core.dim_operator
        SET is_cargo = operator_icao = ANY(%s)
                       OR coalesce(lower(operator_name) LIKE ANY(%s), FALSE)
        """,
        [CARGO_OPERATORS, [f"%{hint}%" for hint in CARGO_HINTS]],
    )
    cur.execute("SELECT count(*) FROM core.dim_operator WHERE is_cargo")
    log.info("flagged %s cargo operators", cur.fetchone()[0])
    return named


def rebuild_aircraft_tables(cur):
    """Rebuild dim_aircraft and dim_aircraft_type from the flights.

    Has to happen after the types are filled in, otherwise the lookup
    tables miss everything the registry recovered.
    """
    cur.execute("DELETE FROM core.dim_aircraft")

    # The types are updated in place rather than deleted and reinserted:
    # once the fuel model has run, fuel_curve points at these rows, so a
    # DELETE fails on the foreign key. Updating in place also keeps the seat
    # counts and OpenAP flags the fuel step wrote.
    cur.execute(
        "UPDATE core.dim_aircraft_type "
        "SET n_flights_seen = 0, manufacturer = NULL, model = NULL"
    )
    cur.execute(
        """
        INSERT INTO core.dim_aircraft_type (type_icao, n_flights_seen)
        SELECT type_icao, count(*)
        FROM core.fact_flight
        WHERE type_icao IS NOT NULL
        GROUP BY type_icao
        ON CONFLICT (type_icao) DO UPDATE
        SET n_flights_seen = EXCLUDED.n_flights_seen
        """
    )
    # Types no flight uses any more, unless a fuel curve still needs them.
    # fuel_curve only exists once the fuel step has run.
    cur.execute("SELECT to_regclass('core.fuel_curve') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute(
            """
            DELETE FROM core.dim_aircraft_type
            WHERE n_flights_seen = 0
              AND type_icao NOT IN (SELECT type_icao FROM core.fuel_curve)
            """
        )
    else:
        cur.execute("DELETE FROM core.dim_aircraft_type WHERE n_flights_seen = 0")

    # Manufacturer and model come from the registry, which fills them in
    # far more consistently than the flight list does.
    #
    # Each type takes the most common value among its aircraft, not the
    # first one alphabetically. The registry has a few aircraft filed under
    # the wrong type code, and alphabetical order let one of them win:
    # every B737 was labelled as an Alexander Schleicher glider. The two
    # columns are voted on separately so that "Boeing" beats the rarer
    # "The Boeing Company" even where the model strings differ.
    cur.execute(
        """
        UPDATE core.dim_aircraft_type t
        SET manufacturer = m.manufacturer,
            model = d.model
        FROM (
            SELECT DISTINCT ON (typecode) typecode, manufacturer
            FROM core.ref_aircraft
            WHERE manufacturer IS NOT NULL AND typecode IS NOT NULL
            GROUP BY typecode, manufacturer
            ORDER BY typecode, count(*) DESC, manufacturer
        ) m
        LEFT JOIN (
            SELECT DISTINCT ON (typecode) typecode, model
            FROM core.ref_aircraft
            WHERE model IS NOT NULL AND typecode IS NOT NULL
            GROUP BY typecode, model
            ORDER BY typecode, count(*) DESC, model
        ) d ON d.typecode = m.typecode
        WHERE m.typecode = t.type_icao
        """
    )

    cur.execute(
        """
        INSERT INTO core.dim_aircraft (icao24, registration, type_icao,
                                       operator_icao, n_flights_seen)
        SELECT
            f.icao24,
            coalesce(mode() WITHIN GROUP (ORDER BY f.registration),
                     max(r.registration)),
            mode() WITHIN GROUP (ORDER BY f.type_icao),
            mode() WITHIN GROUP (ORDER BY f.operator_icao),
            count(*)
        FROM core.fact_flight f
        LEFT JOIN core.ref_aircraft r ON r.icao24 = lower(f.icao24)
        GROUP BY f.icao24
        """
    )

    cur.execute(
        """
        UPDATE core.dim_aircraft
        SET type_icao = NULL
        WHERE type_icao IS NOT NULL
          AND type_icao NOT IN (SELECT type_icao FROM core.dim_aircraft_type)
        """
    )


def rebuild_operator_table(cur):
    """Rebuild dim_operator from the flights, then name the airlines."""
    cur.execute("DELETE FROM core.dim_operator")
    cur.execute(
        """
        INSERT INTO core.dim_operator (operator_icao, n_flights_seen)
        SELECT operator_icao, count(*)
        FROM core.fact_flight
        WHERE operator_icao IS NOT NULL
        GROUP BY operator_icao
        """
    )
    return apply_airline_names(cur)
