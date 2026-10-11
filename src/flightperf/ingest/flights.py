"""Loads the monthly flight list from the OpenSky dataset on Zenodo.

Source : https://zenodo.org/records/7923702
Licence: Creative Commons, non-commercial
Size   : one file per month, roughly 150-220 MB compressed
Period : January 2019 to December 2022

The file is already one row per flight, with the departure airport, the
arrival airport and the aircraft type, so there is no need to reconstruct
flights from raw position reports.

It stays compressed on disk. Python unzips it a block at a time while
streaming it into PostgreSQL, so the uncompressed gigabyte never has to
exist as a file.

Worth knowing about the source: the origin and destination are not taken
from airline schedules. OpenSky works them out by matching the start and end
of each track to the nearest airport, which fails wherever receiver coverage
is thin. A fair number of rows therefore have a blank airport or a missing
aircraft type. That is not a mistake in the file - it is the nature of the
data, and measuring it is part of the project.
"""

import calendar
import gzip
import logging

from flightperf.config import DATA_DIR, get_settings
from flightperf.ingest.download import download_file, record_source
from flightperf.ingest.enrich import (
    apply_aircraft_registry,
    rebuild_aircraft_tables,
    rebuild_operator_table,
)

log = logging.getLogger(__name__)

SOURCE_NAME = "OpenSky Network monthly flight list"
PUBLISHER = "The OpenSky Network (via Zenodo)"
ZENODO_RECORD = "https://zenodo.org/records/7923702"
LICENCE = "Creative Commons, non-commercial"

# Earth's radius in nautical miles, for the distance calculation.
EARTH_RADIUS_NM = 3440.065

# The dataset only covers these years.
FIRST_YEAR = 2019
LAST_YEAR = 2022

# Staging mirrors the CSV exactly, every column as text. Loading everything
# as text means one malformed value cannot stop a 3-million-row file from
# loading; the typing happens afterwards, where problems can be handled.
#
# UNLOGGED because this table is thrown away at the end of the load. It tells
# PostgreSQL not to write the rows to the write-ahead log, which makes the
# load noticeably faster.
STAGING_DDL = """
DROP TABLE IF EXISTS core.stg_flightlist;
CREATE UNLOGGED TABLE core.stg_flightlist (
    callsign        TEXT,
    number          TEXT,
    icao24          TEXT,
    registration    TEXT,
    typecode        TEXT,
    origin          TEXT,
    destination     TEXT,
    firstseen       TEXT,
    lastseen        TEXT,
    day             TEXT,
    latitude_1      TEXT,
    longitude_1     TEXT,
    altitude_1      TEXT,
    latitude_2      TEXT,
    longitude_2     TEXT,
    altitude_2      TEXT
);
"""


def build_url(year, month):
    """Work out the download URL and file name for one month.

    The files are named after the first and last day of the month, for
    example flightlist_20220601_20220630.csv.gz.
    """
    if not FIRST_YEAR <= year <= LAST_YEAR:
        raise ValueError(
            "The dataset only covers "
            + str(FIRST_YEAR) + " to " + str(LAST_YEAR) + ", not " + str(year)
        )

    last_day = calendar.monthrange(year, month)[1]
    start = f"{year:04d}{month:02d}01"
    end = f"{year:04d}{month:02d}{last_day:02d}"
    file_name = "flightlist_" + start + "_" + end + ".csv.gz"
    url = ZENODO_RECORD + "/files/" + file_name + "?download=1"
    return url, file_name


def load_flights(con, year, month, force=False):
    """Download one month of flights and load the French ones into fact_flight."""
    url, file_name = build_url(year, month)
    target = DATA_DIR / "raw" / "opensky" / file_name
    download_file(url, target, force=force)

    source_id = f"flightlist_{year:04d}{month:02d}"
    scoped = list(get_settings().scoped_airports().keys())

    with con.cursor() as cur:
        cur.execute(STAGING_DDL)

        # Stream the gzip straight into PostgreSQL. gzip.open decompresses a
        # block at a time and COPY sends it on, so the full uncompressed file
        # never has to be held in memory or written to disk.
        log.info("loading %s (this takes a few minutes)", file_name)
        with gzip.open(target, "rt", encoding="utf-8") as f, cur.copy(
            "COPY core.stg_flightlist FROM STDIN WITH (FORMAT csv, HEADER true)"
        ) as copy:
            while True:
                block = f.read(1024 * 1024)
                if not block:
                    break
                copy.write(block)

        cur.execute("SELECT count(*) FROM core.stg_flightlist")
        n_raw = cur.fetchone()[0]
        log.info("%s flights in the file (worldwide)", format(n_raw, ","))

        # Clear out anything left from a previous run of this same month, so
        # the load can be repeated safely. Quality flags go first because
        # they point at the flights, and the flights point at the source row.
        cur.execute(
            """
            DELETE FROM core.fact_flight_quality
            WHERE flight_id IN (
                SELECT flight_id FROM core.fact_flight WHERE source_id = %s
            )
            """,
            [source_id],
        )
        cur.execute("DELETE FROM core.fact_flight WHERE source_id = %s", [source_id])

    # The source row has to exist before any flights are inserted, because
    # fact_flight has a foreign key pointing at it. The row count is filled
    # in afterwards, once it is known.
    record_source(
        con, source_id, SOURCE_NAME, PUBLISHER, ZENODO_RECORD, LICENCE,
        target, 0, notes="Loading...",
    )

    with con.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.fact_flight (
                flight_id, icao24, callsign, flight_number, registration,
                operator_icao, type_icao, origin_icao, destination_icao,
                date_key, first_seen_utc, last_seen_utc, duration_s,
                first_lat, first_lon, last_lat, last_lon,
                great_circle_nm, is_domestic_fr, is_intra_eea, source_id
            )
            WITH in_scope AS (
                SELECT
                    icao24,
                    nullif(trim(callsign), '')     AS callsign,
                    nullif(trim(number), '')       AS number,
                    nullif(trim(registration), '') AS registration,
                    nullif(trim(typecode), '')     AS typecode,
                    nullif(trim(origin), '')       AS origin,
                    nullif(trim(destination), '')  AS destination,
                    firstseen::TIMESTAMPTZ         AS firstseen,
                    lastseen::TIMESTAMPTZ          AS lastseen,
                    day::DATE                      AS day,
                    nullif(latitude_1,  '')::DOUBLE PRECISION AS lat1,
                    nullif(longitude_1, '')::DOUBLE PRECISION AS lon1,
                    nullif(latitude_2,  '')::DOUBLE PRECISION AS lat2,
                    nullif(longitude_2, '')::DOUBLE PRECISION AS lon2
                FROM core.stg_flightlist
                WHERE trim(origin) = ANY(%s) OR trim(destination) = ANY(%s)
            ),
            deduped AS (
                -- The file contains a small number of exactly duplicated
                -- rows: same aircraft, same start time, every column the
                -- same. They have to go, otherwise the flight count is
                -- inflated and the primary key fails.
                --
                -- Where duplicates are not identical, the most complete row
                -- wins: one with a destination and an aircraft type beats
                -- one without.
                SELECT *
                FROM (
                    SELECT
                        s.*,
                        row_number() OVER (
                            PARTITION BY icao24, firstseen
                            ORDER BY
                                (destination IS NULL),
                                (typecode IS NULL),
                                lastseen DESC
                        ) AS rn
                    FROM in_scope s
                ) ranked
                WHERE rn = 1
            )
            SELECT
                -- One aircraft can only start one flight at a given second,
                -- so the transponder code plus the start time identifies a
                -- flight uniquely. Hashing it keeps the key short.
                md5(f.icao24 || '_' || extract(epoch FROM f.firstseen)::TEXT),
                f.icao24,
                f.callsign,
                f.number,
                f.registration,

                -- The airline is the first three letters of the callsign
                -- (AFR1234 = Air France). Only valid if what follows is a
                -- flight number, so callsigns that are all letters - usually
                -- private aircraft using their registration - are skipped.
                CASE WHEN f.callsign ~ '^[A-Z]{3}[0-9]'
                     THEN substr(f.callsign, 1, 3)
                END,

                f.typecode,
                f.origin,
                f.destination,
                CAST(to_char(f.day, 'YYYYMMDD') AS INTEGER),

                f.firstseen,
                f.lastseen,
                CAST(extract(epoch FROM f.lastseen - f.firstseen) AS INTEGER),

                f.lat1,
                f.lon1,
                f.lat2,
                f.lon2,

                -- Great-circle distance between the two airports, using the
                -- haversine formula. This is the shortest path over the
                -- surface of the earth; a real flight is always somewhat
                -- longer, so every fuel figure built on it is an
                -- underestimate.
                CASE WHEN ao.airport_icao IS NOT NULL
                       AND ad.airport_icao IS NOT NULL
                     THEN 2 * %s * asin(sqrt(
                            power(sin(radians(ad.latitude_deg - ao.latitude_deg) / 2), 2)
                            + cos(radians(ao.latitude_deg))
                            * cos(radians(ad.latitude_deg))
                            * power(sin(radians(ad.longitude_deg - ao.longitude_deg) / 2), 2)
                          ))
                END,

                -- Both ends in France.
                ao.iso_country = 'FR' AND ad.iso_country = 'FR',
                -- Both ends inside the European Economic Area.
                ao.is_eea AND ad.is_eea,

                %s
            FROM deduped f
            LEFT JOIN core.dim_airport ao ON ao.airport_icao = f.origin
            LEFT JOIN core.dim_airport ad ON ad.airport_icao = f.destination
            """,
            [scoped, scoped, EARTH_RADIUS_NM, source_id],
        )

        cur.execute(
            "SELECT count(*) FROM core.fact_flight WHERE source_id = %s", [source_id]
        )
        n_loaded = cur.fetchone()[0]

        # Staging has done its job. Dropping it stops the database carrying
        # around 3 million rows that were filtered out anyway.
        cur.execute("DROP TABLE core.stg_flightlist")

        # Flights arrive with the type blank on many rows. If the aircraft
        # registry has already been loaded, use it now; if not, loading it
        # later will come back and fill these in.
        cur.execute(
            "UPDATE core.fact_flight SET type_source = 'reported' "
            "WHERE type_icao IS NOT NULL AND type_source IS NULL"
        )
        apply_aircraft_registry(cur)
        rebuild_aircraft_tables(cur)
        rebuild_operator_table(cur)

        cur.execute(
            "UPDATE core.dim_source SET rows_loaded = %s, notes = %s "
            "WHERE source_id = %s",
            [
                n_loaded,
                f"Month {year}-{month:02d}. "
                + format(n_raw, ",") + " flights worldwide in the file, "
                + format(n_loaded, ",") + " touching a French airport in scope.",
                source_id,
            ],
        )

    con.commit()

    log.info(
        "loaded %s flights touching French airports (from %s worldwide)",
        format(n_loaded, ","), format(n_raw, ","),
    )
    return n_loaded
