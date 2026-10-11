"""Loads airline names from OpenFlights.

Source : https://github.com/jpatokal/openflights
Licence: Open Database License
Size   : about 400 KB, roughly 6,000 airlines

The flight list has no airline column. The airline is worked out from the
first three letters of the callsign - AFR1234 means Air France - but that
leaves the dashboard showing codes like AFR and RYR rather than names.

This file turns those codes into names, countries, and whether the airline
is still operating.

There is no header row and missing values are written as \\N, both of which
have to be handled on load.
"""

import logging

from flightperf.config import DATA_DIR
from flightperf.ingest.download import download_file, record_source
from flightperf.ingest.enrich import apply_airline_names

log = logging.getLogger(__name__)

SOURCE_ID = "openflights_airlines"
SOURCE_NAME = "OpenFlights airline database"
PUBLISHER = "OpenFlights"
URL = (
    "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
)
LICENCE = "Open Database License"

STAGING_DDL = """
DROP TABLE IF EXISTS core.stg_airlines;
CREATE UNLOGGED TABLE core.stg_airlines (
    airline_id      TEXT,
    name            TEXT,
    alias           TEXT,
    iata            TEXT,
    icao            TEXT,
    callsign        TEXT,
    country         TEXT,
    active          TEXT
);
"""


def load_airlines(con, force=False):
    """Download the airline list and put names against the operator codes."""
    target = DATA_DIR / "raw" / "openflights" / "airlines.dat"
    download_file(URL, target, force=force)

    with con.cursor() as cur:
        cur.execute(STAGING_DDL)

        # HEADER false because the file starts straight in with data.
        log.info("loading %s", target.name)
        with open(target, "r", encoding="utf-8", errors="replace") as f, cur.copy(
            "COPY core.stg_airlines FROM STDIN WITH (FORMAT csv, HEADER false)"
        ) as copy:
            while True:
                block = f.read(256 * 1024)
                if not block:
                    break
                copy.write(block)

        cur.execute("SELECT count(*) FROM core.stg_airlines")
        n_rows = cur.fetchone()[0]

        # Keep the rows with a real three-letter ICAO code, permanently.
        # Missing values arrive as the literal characters \N.
        cur.execute("DELETE FROM core.ref_airline")
        # A raw string, because the SQL contains a backslash-N that Python
        # would otherwise try to read as an escape sequence.
        cur.execute(
            r"""
            INSERT INTO core.ref_airline
                (operator_icao, operator_name, country, is_active)
            SELECT DISTINCT ON (upper(trim(icao)))
                upper(trim(icao)),
                trim(name),
                nullif(trim(country), '\N'),
                (trim(active) = 'Y')
            FROM core.stg_airlines
            WHERE upper(trim(icao)) ~ '^[A-Z]{3}$'
              AND trim(name) NOT IN ('', '\N')
            -- Where a code appears twice, prefer the airline still flying.
            ORDER BY upper(trim(icao)), (trim(active) = 'Y') DESC, trim(name)
            """
        )
        cur.execute("SELECT count(*) FROM core.ref_airline")
        n_usable = cur.fetchone()[0]

        # Also sets the cargo flag - see CARGO_OPERATORS in enrich.py.
        matched = apply_airline_names(cur)

        cur.execute("SELECT count(*) FROM core.dim_operator")
        n_operators = cur.fetchone()[0]

        cur.execute("DROP TABLE core.stg_airlines")

    record_source(
        con, SOURCE_ID, SOURCE_NAME, PUBLISHER, URL, LICENCE, target, n_usable,
        notes=(
            format(n_rows, ",") + " airlines in the file, "
            + format(n_usable, ",") + " with a valid 3-letter code. Named "
            + format(matched, ",") + " of the " + format(n_operators, ",")
            + " operators seen in the flights."
        ),
    )
    con.commit()

    log.info("named %s of %s operators", format(matched, ","), format(n_operators, ","))
    return {"file_rows": n_rows, "usable": n_usable,
            "matched": matched, "operators": n_operators}
