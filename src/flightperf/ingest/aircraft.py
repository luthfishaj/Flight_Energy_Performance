"""Loads the aircraft registry from OpenSky.

Source : https://s3.opensky-network.org/data-samples/metadata/
Licence: free to use, from the OpenSky Network
Size   : about 61 MB, roughly 500,000 aircraft

This file maps a transponder code (icao24) to the aircraft it belongs to -
its registration, manufacturer, model and type code.

Why it matters: the flight list leaves the aircraft type blank on 37% of
rows, and without a type there is no fuel figure. But every row does carry
an icao24. Looking that code up here fills in a large share of the gap, and
turns flights that would have been dropped into usable ones.

A quirk to know about: the file's header row is entirely blank, so the
columns cannot be read by name. They are declared by position instead.
"""

import logging

from flightperf.config import DATA_DIR
from flightperf.ingest.download import download_file, record_source
from flightperf.ingest.enrich import apply_aircraft_registry, rebuild_aircraft_tables

log = logging.getLogger(__name__)

SOURCE_ID = "opensky_aircraft"
SOURCE_NAME = "OpenSky aircraft registry"
PUBLISHER = "The OpenSky Network"
URL = (
    "https://s3.opensky-network.org/data-samples/metadata/"
    "aircraft-database-complete-2024-01.csv"
)
LICENCE = "Free to use (OpenSky Network)"

# The columns in order, since the header row is blank. Only a few are
# actually used; the rest are named so the positions line up.
STAGING_DDL = """
DROP TABLE IF EXISTS core.stg_aircraft;
CREATE UNLOGGED TABLE core.stg_aircraft (
    icao24              TEXT,   -- 1: transponder code
    registration        TEXT,   -- 2: tail number
    manufacturer_icao   TEXT,   -- 3
    manufacturer_name   TEXT,   -- 4: e.g. Boeing
    model               TEXT,   -- 5: e.g. 717-200
    typecode            TEXT,   -- 6: e.g. B712  <- the one that matters
    serial_number       TEXT,   -- 7
    line_number         TEXT,   -- 8
    icao_aircraft_class TEXT,   -- 9: e.g. L2J (landplane, 2 jets)
    operator_name       TEXT,   -- 10
    operator_callsign   TEXT,   -- 11
    operator_icao       TEXT,   -- 12
    operator_iata       TEXT,   -- 13
    owner               TEXT,   -- 14
    spare               TEXT    -- 15: unused
);
"""


def load_aircraft(con, force=False):
    """Download the aircraft registry and use it to fill in missing types."""
    target = DATA_DIR / "raw" / "opensky" / "aircraft-database.csv"
    download_file(URL, target, force=force)

    with con.cursor() as cur:
        cur.execute(STAGING_DDL)

        log.info("loading %s", target.name)
        with open(target, "r", encoding="utf-8", errors="replace") as f, cur.copy(
            "COPY core.stg_aircraft FROM STDIN WITH (FORMAT csv, HEADER true)"
        ) as copy:
            while True:
                block = f.read(1024 * 1024)
                if not block:
                    break
                copy.write(block)

        cur.execute("SELECT count(*) FROM core.stg_aircraft")
        n_registry = cur.fetchone()[0]
        log.info("%s aircraft in the registry", format(n_registry, ","))

        # Keep the registry permanently, tidied down to the rows that are
        # usable: a transponder code and a type code, one row per code.
        # Storing it rather than just applying it means the ingest commands
        # can be run in any order.
        cur.execute("DELETE FROM core.ref_aircraft")
        cur.execute(
            """
            INSERT INTO core.ref_aircraft
                (icao24, registration, typecode, manufacturer, model, operator_icao)
            SELECT DISTINCT ON (lower(trim(icao24)))
                lower(trim(icao24)),
                nullif(trim(registration), ''),
                upper(nullif(trim(typecode), '')),
                nullif(trim(manufacturer_name), ''),
                nullif(trim(model), ''),
                upper(nullif(trim(operator_icao), ''))
            FROM core.stg_aircraft
            WHERE nullif(trim(icao24), '') IS NOT NULL
              AND nullif(trim(typecode), '') IS NOT NULL
            ORDER BY lower(trim(icao24)), registration NULLS LAST
            """
        )
        cur.execute("SELECT count(*) FROM core.ref_aircraft")
        n_usable = cur.fetchone()[0]

        # How many flights were missing a type before the fix. Zero if the
        # flights have not been loaded yet, which is fine - they will pick
        # the registry up when they arrive.
        cur.execute("SELECT count(*) FROM core.fact_flight WHERE type_icao IS NULL")
        missing_before = cur.fetchone()[0]

        recovered = apply_aircraft_registry(cur)
        rebuild_aircraft_tables(cur)

        cur.execute("SELECT count(*) FROM core.fact_flight WHERE type_icao IS NULL")
        missing_after = cur.fetchone()[0]

        cur.execute("DROP TABLE core.stg_aircraft")

    record_source(
        con, SOURCE_ID, SOURCE_NAME, PUBLISHER, URL, LICENCE, target, n_usable,
        notes=(
            format(n_registry, ",") + " aircraft in the file, "
            + format(n_usable, ",") + " with both a transponder code and a type. "
            "Recovered the aircraft type for " + format(recovered, ",") + " flights."
        ),
    )
    con.commit()

    log.info(
        "recovered %s aircraft types (%s flights still missing one, was %s)",
        format(recovered, ","), format(missing_after, ","), format(missing_before, ","),
    )
    return {
        "registry_rows": n_registry,
        "usable_rows": n_usable,
        "missing_before": missing_before,
        "recovered": recovered,
        "missing_after": missing_after,
    }
