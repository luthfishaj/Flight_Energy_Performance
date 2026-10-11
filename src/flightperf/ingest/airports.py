"""Loads airport names and coordinates from OurAirports.

Source : https://davidmegginson.github.io/ourairports-data/airports.csv
Licence: public domain
Size   : about 13 MB, roughly 80,000 rows

The file lists every airport in the world, including heliports and tiny
airstrips. Only the ones with a proper 4-letter ICAO code are kept, since
those are the only ones the flight list can refer to.

The coordinates from this file are what every distance in the project is
based on, which is why they are downloaded rather than typed in.
"""

import logging

from flightperf.config import DATA_DIR, get_settings
from flightperf.ingest.download import download_file, record_source

log = logging.getLogger(__name__)

SOURCE_ID = "ourairports"
SOURCE_NAME = "OurAirports airport database"
PUBLISHER = "OurAirports (David Megginson)"
URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
LICENCE = "Public domain"

# Countries in the European Economic Area, needed to decide which flights
# the EU carbon rules cover. EEA = the EU plus Iceland, Liechtenstein and
# Norway. Switzerland is not in the EEA but runs a linked carbon market, so
# it is included here and the choice is noted in the writeup.
EEA_COUNTRIES = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",              # EU member states
    "IS", "LI", "NO",              # the rest of the EEA
    "CH",                          # linked carbon market, not EEA
]

# The staging table mirrors the CSV exactly - every column is text, so a
# stray value cannot stop the file loading. The typing happens afterwards,
# on the way into dim_airport, where a bad row can be spotted and dropped.
STAGING_DDL = """
DROP TABLE IF EXISTS core.stg_airports;
CREATE TABLE core.stg_airports (
    id                  TEXT,
    ident               TEXT,
    type                TEXT,
    name                TEXT,
    latitude_deg        TEXT,
    longitude_deg       TEXT,
    elevation_ft        TEXT,
    continent           TEXT,
    iso_country         TEXT,
    iso_region          TEXT,
    municipality        TEXT,
    scheduled_service   TEXT,
    icao_code           TEXT,
    iata_code           TEXT,
    gps_code            TEXT,
    local_code          TEXT,
    home_link           TEXT,
    wikipedia_link      TEXT,
    keywords            TEXT
);
"""


def load_airports(con, force=False):
    """Download the airport CSV and load it into core.dim_airport."""
    target = DATA_DIR / "raw" / "ourairports" / "airports.csv"
    download_file(URL, target, force=force)

    with con.cursor() as cur:
        cur.execute(STAGING_DDL)

        # COPY is how PostgreSQL loads bulk data, and it is far faster than
        # inserting row by row. The file is streamed to the server rather
        # than being read into memory first.
        log.info("loading %s", target.name)
        with open(target, "r", encoding="utf-8") as f, cur.copy(
            "COPY core.stg_airports FROM STDIN WITH (FORMAT csv, HEADER true)"
        ) as copy:
            while True:
                block = f.read(1024 * 1024)
                if not block:
                    break
                copy.write(block)

        # Keep only real airports with a 4-letter ICAO code. That drops
        # heliports, seaplane bases and closed fields, none of which appear
        # in the flight list.
        cur.execute("DELETE FROM core.dim_airport")
        cur.execute(
            """
            INSERT INTO core.dim_airport (
                airport_icao, airport_iata, airport_name, municipality,
                iso_country, iso_region, latitude_deg, longitude_deg,
                elevation_ft, airport_type, is_in_scope, scope_tier, is_eea
            )
            SELECT
                ident,
                nullif(iata_code, ''),
                name,
                nullif(municipality, ''),
                iso_country,
                iso_region,
                latitude_deg::DOUBLE PRECISION,
                longitude_deg::DOUBLE PRECISION,
                nullif(elevation_ft, '')::INTEGER,
                type,
                FALSE,                  -- set just below, from the config file
                NULL,
                iso_country = ANY(%s)
            FROM core.stg_airports
            WHERE ident ~ '^[A-Z]{4}$'
              AND type IN ('large_airport', 'medium_airport', 'small_airport')
              AND nullif(latitude_deg, '')  IS NOT NULL
              AND nullif(longitude_deg, '') IS NOT NULL
            """,
            [EEA_COUNTRIES],
        )

        # Mark the French airports the project is actually about. The list
        # comes from config/airports_fr.yml so it can be changed without
        # editing any code.
        scoped = get_settings().scoped_airports()
        for code, tier in scoped.items():
            cur.execute(
                """
                UPDATE core.dim_airport
                SET is_in_scope = TRUE, scope_tier = %s
                WHERE airport_icao = %s
                """,
                [tier, code],
            )

        # The staging table has done its job. Dropping it keeps the database
        # from carrying around a second copy of everything.
        cur.execute("DROP TABLE core.stg_airports")

        cur.execute("SELECT count(*) FROM core.dim_airport")
        n_total = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM core.dim_airport WHERE is_in_scope")
        n_scoped = cur.fetchone()[0]

        # If an airport in the config file is missing from the reference
        # data, every flight to it would silently vanish later on. Better to
        # know about it now.
        cur.execute(
            """
            SELECT unnest(%s::text[])
            EXCEPT
            SELECT airport_icao FROM core.dim_airport
            """,
            [list(scoped.keys())],
        )
        missing = [row[0] for row in cur.fetchall()]
        if missing:
            log.warning("airports in the config file but not in the data: %s", missing)

    record_source(
        con, SOURCE_ID, SOURCE_NAME, PUBLISHER, URL, LICENCE, target, n_total,
        notes="Filtered to 4-letter ICAO codes and real airports only.",
    )
    con.commit()

    log.info("loaded %s airports, %s of them in scope", format(n_total, ","), n_scoped)
    return n_total
