"""Builds the project reference document in files/Project_Summary.docx.

Run it with:

    uv run python scripts/update_summary.py

This document is meant to be read and studied, not just skimmed for status.
It gathers everything about the project in one place: what the data is,
where it comes from, what every table and column means, what the quality
checks do, and what still needs sorting out.

The Word file is never edited by hand. Static explanations live in this
script, and the factual parts - row counts, the quality checks, the list of
downloaded files, the columns in each table - are read straight out of the
database when the script runs. That means the document cannot quietly go out
of date, and the changes show up as readable diffs in version control.

If the database has not been built yet the script still works; it just
leaves the live sections with a note saying so.
"""

import sys
from datetime import date
from pathlib import Path

import psycopg
import psycopg.sql
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

# The project package lives under src/, so it has to be findable before
# the settings can be imported.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

OUTPUT = REPO_ROOT / "files" / "Project_Summary.docx"

TITLE = "Flight Energy Performance"
SUBTITLE = "Working notes and reference"


# ---------------------------------------------------------------------------
# Progress log. Newest entry goes at the bottom.
# One row gets added here every time a step is finished.
# ---------------------------------------------------------------------------
PROGRESS = [
    (
        "2026-09-17",
        "Project setup",
        "Decided what the project is, picked the tools, created the repository.",
    ),
    (
        "2026-09-17",
        "Database and schema",
        (
            "Built the DuckDB database with a star schema, the date table, and "
            "the list of data quality checks."
        ),
    ),
    (
        "2026-09-18",
        "Cut the scope down",
        (
            "The first plan needed seven data sources, three of them behind a "
            "registration or an access application. Replaced the lot with two "
            "plain CSV downloads that need no account. Dropped the trajectory "
            "tables, because the new source is already one row per flight."
        ),
    ),
    (
        "2026-09-18",
        "Airport data loaded",
        (
            "Loaded 17,109 airports from OurAirports, including all 26 French "
            "airports in scope. Added a dim_source table recording the URL, "
            "download date, size and checksum of every file used."
        ),
    ),
    (
        "2026-09-18",
        "Flight data loaded",
        (
            "Loaded June 2022. 3,174,252 flights worldwide in the file, of "
            "which 87,903 touch a French airport."
        ),
    ),
    (
        "2026-09-18",
        "Moved to PostgreSQL",
        (
            "Switched from DuckDB to PostgreSQL: it is what job adverts ask "
            "for, and Power BI connects to it directly, which removed the "
            "Parquet export step entirely. The 200 MB flight list is streamed "
            "in with COPY, unzipped a block at a time. Deleted the leftover "
            "data/curated and data/interim folders."
        ),
    ),
    (
        "2026-09-18",
        "PostgreSQL verified end to end",
        (
            "Installed PostgreSQL 17, rebuilt the database from scratch and "
            "reloaded June 2022. Every figure matches what DuckDB produced, so "
            "nothing broke in the move. The load takes 52 seconds and the "
            "database is 35 MB, down from 640 MB, because staging tables are "
            "now dropped once the load finishes."
        ),
    ),
    (
        "2026-09-23",
        "Quality checks running",
        (
            "Turned the 15 checks into code. Every flight now gets a verdict "
            "and a score, and every problem found is recorded as its own row, "
            "so the dashboard can break it down by reason. Nothing is "
            "deleted. On the first run only 49.6 percent of flights were "
            "usable, the biggest cause being 37 percent with no aircraft type."
        ),
    ),
    (
        "2026-09-23",
        "Aircraft registry added",
        (
            "Every flight carries a transponder code even when the aircraft "
            "type is blank, so a 61 MB registry from OpenSky was loaded to "
            "look the missing types up. It recovered 32,389 of the 32,649, "
            "and usable flights went from 49.6 to 77.5 percent."
        ),
    ),
    (
        "2026-09-23",
        "Airline names added",
        (
            "A 400 KB file from OpenFlights turns operator codes into names, "
            "so the dashboard shows Air France rather than AFR. 323 of the "
            "451 operators in the data are named."
        ),
    ),
    (
        "2026-09-23",
        "Ingest made order-independent",
        (
            "Loading the aircraft registry before the flights used to do "
            "nothing, because there was nothing yet to fix. The reference "
            "data is now kept in its own tables, so whichever order the "
            "commands run in, the result is the same."
        ),
    ),
    (
        "2026-09-23",
        "Fuel model built",
        (
            "OpenAP now gives fuel and CO2 for every flight that passed the "
            "checks. Rather than calling the model 60,000 times, a "
            "fuel-against-distance curve is built per aircraft type and each "
            "flight is read off it. June 2022 comes to 543,054 tonnes of fuel "
            "across 59,772 flights, before the route correction."
        ),
    ),
    (
        "2026-09-23",
        "Model checked against published figures",
        (
            "Modelled cruise burn was compared with published figures for "
            "eight aircraft types. Seven agree within 15 percent. The Boeing "
            "777-300ER comes out 31 percent high, which is reported rather "
            "than hidden - it matters because long-haul flights dominate the "
            "CO2 total."
        ),
    ),
    (
        "2026-09-26",
        "Prices researched and applied",
        (
            "The carbon price, jet fuel price and route-extension factor were "
            "looked up and written into params.yml, each with its source and "
            "the date it was read. All three are June 2022 figures, matching "
            "the flight data. June 2022 works out at 559,128 tonnes of fuel "
            "and 1.77 million tonnes of CO2, worth EUR 730 million in fuel "
            "and EUR 38 million in carbon. The striking result: 74 percent of "
            "the CO2 falls outside the EU carbon market, because the "
            "long-haul flights that dominate emissions are not covered."
        ),
    ),
    (
        "2026-09-23",
        "Cost step written, waiting on prices",
        (
            "The code that turns CO2 into money is written and wired up. It "
            "refuses to run until the carbon price, the fuel price and the "
            "route-extension factor are filled into params.yml, and prints "
            "exactly what is missing."
        ),
    ),
    (
        "2026-09-18",
        "Three bugs found and fixed",
        (
            "The ~ operator is not a regular expression match in DuckDB, so "
            "the airline column was coming out almost entirely empty; "
            "regexp_matches fixed it. least() and greatest() skip NULLs, so a "
            "flight missing an airport produced a nonsense route like "
            "LFPG-LFPG. And the source file contains 491 exactly duplicated "
            "rows, now removed on load."
        ),
    ),
    (
        "2026-10-11",
        "Power BI dashboard built",
        (
            "Five pages on top of core.v_flight_kpi: overview, routes and "
            "airports, airlines and aircraft, carbon cost, and data quality. "
            "Every KPI counts only flights that passed the quality checks. "
            "Power BI imports straight from PostgreSQL, so a pipeline rerun "
            "plus Refresh brings it up to date."
        ),
    ),
    (
        "2026-10-11",
        "Three bugs the dashboard exposed",
        (
            "Aircraft types were named after the first registry entry "
            "alphabetically, so a misfiled glider renamed every B737; each "
            "type now takes the most common name. No airline was flagged as "
            "cargo, because loading flights wiped the flag and the name check "
            "missed 'Federal Express'; cargo is now also spotted by ICAO code "
            "(11 operators, 3,229 flights). And reloading aircraft data after "
            "the fuel step crashed on a foreign key; the rebuild now updates "
            "in place. Fuel, CO2 and cost totals were unchanged."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Where each part of the project has got to.
# ---------------------------------------------------------------------------
STATUS = [
    ("Database and tables", "Done", "PostgreSQL, star schema"),
    ("Airport data", "Done", "17,109 airports from OurAirports"),
    ("Flight data", "Done", "87,903 French flights for June 2022"),
    ("Aircraft registry", "Done", "Recovers 32,389 missing aircraft types"),
    ("Airline names", "Done", "323 of 451 operators named"),
    ("Source tracking", "Done", "URL, date, size and checksum recorded per file"),
    ("Data quality checks", "Done", "15 checks; 77.5 percent of flights usable"),
    ("Fuel model", "Done", "OpenAP; fuel and CO2 for 59,772 flights"),
    ("Model validation", "Done", "7 of 8 types within 15 percent of published"),
    ("Carbon costs", "Done", "Priced at June 2022 market prices, sources cited"),
    ("Power BI report", "Done", "Five pages, built on core.v_flight_kpi"),
]


NEXT_STEPS = [
    (
        "Write up the finding: 74 percent of the CO2 from French air traffic "
        "falls outside the EU carbon market, because the long-haul flights "
        "that dominate emissions are not covered."
    ),
    (
        "Settle whether flights to the UK and Switzerland were covered by the "
        "EU carbon market in 2022. It moves the 74 percent figure directly."
    ),
    "Cite the eight cruise-burn figures in config/validation_reference.yml.",
    "Load June 2019 as well, to compare a pre-COVID month against June 2022.",
]


OPEN_QUESTIONS = [
    (
        "Whether flights from the EEA to the UK and Switzerland were covered "
        "by the EU carbon market in 2022. Switzerland is currently counted as "
        "inside, the UK as outside, and neither has a source. It moves the "
        "headline finding that 74 percent of the CO2 is uncovered."
    ),
    (
        "The route correction uses EUROCONTROL's en-route figure, which "
        "excludes the terminal area around each airport. The full "
        "gate-to-gate extension is larger, so fuel is still understated."
    ),
    (
        "The Boeing 777-300ER is modelled 31 percent above its published "
        "cruise burn. Long-haul flights dominate the CO2 total, so this "
        "pushes the headline figure up. Worth either finding a better "
        "reference or stating the effect on the total."
    ),
    (
        "Taxi fuel is not counted at all, because OpenAP has no model for "
        "engines at idle. Real block fuel is a few percent higher than these "
        "figures."
    ),
    (
        "18,410 flights are still dropped for having no departure or arrival "
        "airport. Unlike the missing aircraft types, there is no reference "
        "file that can fill these in."
    ),
    (
        "The published cruise figures in config/validation_reference.yml are "
        "working numbers and still need proper citations."
    ),
    (
        "Switzerland is counted as part of the European Economic Area in the "
        "code, because it runs a linked carbon market. Strictly it is not in "
        "the EEA. That choice needs stating in the writeup."
    ),
]


# ---------------------------------------------------------------------------
# Terms worth knowing. The project is full of aviation jargon and it is
# easier to have it written down once than to keep looking it up.
# ---------------------------------------------------------------------------
GLOSSARY = [
    (
        "ADS-B",
        (
            "Automatic Dependent Surveillance-Broadcast. Aircraft broadcast "
            "their position, altitude and speed every few seconds. Anyone with "
            "a cheap receiver can pick it up, which is why this data is public."
        ),
    ),
    (
        "icao24",
        (
            "A 24-bit code burned into the aircraft's transponder, written as "
            "six hex characters such as 3946ab. It identifies the airframe. It "
            "can be wrong or duplicated, which is why there are checks for it."
        ),
    ),
    (
        "Callsign",
        (
            "What the crew uses on the radio, such as AFR1234. The first three "
            "letters are the airline - AFR is Air France - so the airline is "
            "worked out from this. Private aircraft use their registration "
            "instead, which is why those flights have no airline."
        ),
    ),
    (
        "Registration",
        "The tail number painted on the aircraft, such as F-GKXA. F- means France.",
    ),
    (
        "ICAO code",
        (
            "A four-letter airport code such as LFPG, used by the flight data. "
            "The first letters indicate the region: LF is France, EG is the "
            "United Kingdom."
        ),
    ),
    (
        "IATA code",
        (
            "The three-letter airport code passengers see on tickets, such as "
            "CDG. Stored for labelling charts, but the data itself uses ICAO."
        ),
    ),
    (
        "Type code",
        (
            "A short code for the aircraft model, such as A320 or B738. The "
            "fuel model is keyed on this, so a flight without one cannot have "
            "its fuel calculated at all."
        ),
    ),
    (
        "Great circle distance",
        (
            "The shortest distance between two points on a sphere - the "
            "straight line an aircraft would fly if airways, weather and "
            "airspace restrictions did not exist. Real flights are always "
            "longer."
        ),
    ),
    (
        "Haversine formula",
        (
            "The trigonometry used to turn two pairs of coordinates into a "
            "great circle distance. It is written out in SQL in the flight "
            "loader."
        ),
    ),
    (
        "Nautical mile",
        (
            "The unit aviation uses for distance. One nautical mile is 1.852 "
            "kilometres. Distances are stored in nautical miles and converted "
            "where needed."
        ),
    ),
    (
        "OpenAP",
        (
            "An open source aircraft performance model from TU Delft. Given an "
            "aircraft type and how it is flying, it returns a fuel flow. It is "
            "what turns a distance into a fuel figure here."
        ),
    ),
    (
        "EU ETS",
        (
            "The European Union Emissions Trading System. Airlines must buy an "
            "allowance for every tonne of CO2 they emit on covered flights, so "
            "emissions have a direct cost. Aviation has been included since "
            "2012."
        ),
    ),
    (
        "SAF",
        (
            "Sustainable Aviation Fuel. EU rules require a minimum share of it "
            "in the fuel uplifted at European airports. It costs considerably "
            "more than ordinary jet fuel, so the mandate has a price attached."
        ),
    ),
    (
        "Star schema",
        (
            "A way of organising tables: one fact table in the middle holding "
            "the numbers, surrounded by dimension tables holding the "
            "descriptions. It avoids repeating the airport name on every "
            "flight row, and it is what Power BI expects."
        ),
    ),
    (
        "Grain",
        (
            "What one row of a table represents. The grain of fact_flight is "
            "one flight. Being clear about grain is what stops numbers being "
            "accidentally double counted."
        ),
    ),
    (
        "PostgreSQL",
        (
            "The database the project runs on. A real server rather than a "
            "file, which is how databases work at work, and Power BI connects "
            "to it directly."
        ),
    ),
    (
        "COPY",
        (
            "The PostgreSQL command for loading bulk data. Far faster than "
            "inserting rows one at a time, and it can read straight from a "
            "stream, which is how the 200 MB flight list gets in."
        ),
    ),
]


# ---------------------------------------------------------------------------
# The two data sources, in full.
# ---------------------------------------------------------------------------
SOURCE_AIRPORTS = {
    "publisher": "OurAirports, maintained by David Megginson",
    "page": "https://ourairports.com/data/",
    "file": "https://davidmegginson.github.io/ourairports-data/airports.csv",
    "licence": "Public domain",
    "size": "About 13 MB, roughly 80,000 rows worldwide",
    "used_for": "Airport names, countries and coordinates",
    "account": "None needed. Direct download.",
}

SOURCE_FLIGHTS = {
    "publisher": "The OpenSky Network, published through Zenodo",
    "page": "https://zenodo.org/records/7923702",
    "file": "flightlist_YYYYMMDD_YYYYMMDD.csv.gz, one file per month",
    "licence": "Creative Commons, non-commercial",
    "size": "150 to 220 MB compressed per month; 8.1 GB for all 49 months",
    "used_for": "The flights themselves",
    "account": "None needed. Direct download.",
}

# What each column in the flight list actually means.
SOURCE_AIRCRAFT = {
    "publisher": "The OpenSky Network",
    "page": "https://s3.opensky-network.org/data-samples/metadata/",
    "file": (
        "https://s3.opensky-network.org/data-samples/metadata/"
        "aircraft-database-complete-2024-01.csv"
    ),
    "licence": "Free to use",
    "size": "About 61 MB, 579,435 aircraft",
    "used_for": "Turning a transponder code into an aircraft type",
    "account": "None needed. Direct download.",
}

SOURCE_AIRLINES = {
    "publisher": "OpenFlights",
    "page": "https://github.com/jpatokal/openflights",
    "file": (
        "https://raw.githubusercontent.com/jpatokal/openflights/master/"
        "data/airlines.dat"
    ),
    "licence": "Open Database License",
    "size": "About 400 KB, 5,774 airlines",
    "used_for": "Turning airline codes into names and countries",
    "account": "None needed. Direct download.",
}

FLIGHTLIST_COLUMNS = [
    ("callsign", "Radio callsign, e.g. AFR1234. The airline is the first three letters."),
    ("number", "Commercial flight number, e.g. AF1234. Often blank."),
    ("icao24", "Transponder code identifying the airframe."),
    ("registration", "Tail number, e.g. F-GKXA."),
    ("typecode", "Aircraft model, e.g. A320. Blank for 37% of flights."),
    ("origin", "Departure airport, ICAO code. Inferred, not scheduled."),
    ("destination", "Arrival airport, ICAO code. Inferred, not scheduled."),
    ("firstseen", "First moment a receiver picked the aircraft up, UTC."),
    ("lastseen", "Last moment a receiver picked the aircraft up, UTC."),
    ("day", "The date the flight belongs to."),
    ("latitude_1, longitude_1", "Where the aircraft was first seen."),
    ("latitude_2, longitude_2", "Where the aircraft was last seen."),
    ("altitude_1, altitude_2", "Altitude at those two points. Not used here."),
]

DATA_LIMITATIONS = [
    (
        "The airports are inferred, not scheduled. OpenSky matches the start "
        "and end of each track to the nearest airport. Where receiver coverage "
        "is thin this fails, so 11% of the June 2022 flights have no departure "
        "airport at all."
    ),
    (
        "The aircraft type is often missing. 37% of the June 2022 flights have "
        "no type code, and without it there is no fuel figure. This is the "
        "single largest gap in the data."
    ),
    (
        "There is no flight path, only the first and last position. So the "
        "distance used is the straight line between the two airports. Real "
        "flights are longer, which means every fuel figure is an underestimate "
        "until a correction factor is applied."
    ),
    (
        "There is no passenger count. Anything expressed per seat is based on "
        "how many seats the aircraft type has, not how many were sold. It "
        "measures the efficiency of the capacity offered, not of the people "
        "carried."
    ),
    (
        "A small number of rows are exact duplicates - 491 out of 3.17 million "
        "in June 2022. They are removed when the file is loaded."
    ),
    (
        "Everything is priced at June 2022 market prices, matching the "
        "month of the flight data, so the money figures mean what this "
        "traffic cost at the time rather than what it would cost today. The "
        "carbon figure is the market value of the emissions, not cash paid: "
        "free allocation is deliberately ignored, and in 2022 airlines still "
        "received a large share of their allowances free of charge."
    ),
]


# ---------------------------------------------------------------------------
# What each table is for. The column lists themselves are read from the
# database, so only the purpose needs writing down here.
# ---------------------------------------------------------------------------
TABLE_NOTES = {
    "fact_flight": (
        "One row per flight. The centre of the schema and the table everything "
        "else describes."
    ),
    "fact_flight_quality": (
        "One row per problem found, per flight. Stored long so that new checks "
        "do not need new columns."
    ),
    "dim_date": (
        "One row per calendar day. Power BI needs a real date table for its "
        "date filters to work."
    ),
    "dim_airport": (
        "One row per airport, from OurAirports. The coordinates drive every "
        "distance in the project."
    ),
    "dim_aircraft": "One row per airframe, built from the flight list.",
    "dim_aircraft_type": (
        "One row per aircraft model. Gets its performance figures from OpenAP."
    ),
    "dim_operator": (
        "One row per airline, worked out from the first three letters of the "
        "callsign."
    ),
    "dim_quality_rule": (
        "The catalogue of data quality checks, held as data so the dashboard "
        "can explain each flag it shows."
    ),
    "dim_source": "One row per downloaded file: URL, date, size, checksum, row count.",
    "ref_aircraft": (
        "The aircraft registry, kept permanently so it can fill in missing "
        "aircraft types whenever flights are loaded."
    ),
    "ref_airline": "Airline codes to names, from OpenFlights.",
    "fuel_curve": (
        "Fuel against distance for each aircraft type. Each flight is read "
        "off this rather than calling the performance model 60,000 times."
    ),
    "stg_airports": "Raw airport CSV as loaded, before filtering. Staging only.",
    "stg_flightlist": (
        "Raw flight list as loaded, before filtering to France. Staging only."
    ),
    "v_flight_kpi": (
        "The view Power BI reads. One row per flight with all the KPIs worked "
        "out."
    ),
}


# ---------------------------------------------------------------------------
# The KPI definitions, in words rather than SQL.
# ---------------------------------------------------------------------------
KPI_NOTES = [
    (
        "great_circle_nm",
        "Straight-line distance between the two airports",
        "Haversine formula on the airport coordinates",
    ),
    (
        "fuel_burn_kg",
        "Fuel used, airborne only",
        "OpenAP, from the aircraft type and the distance. No taxi.",
    ),
    ("co2_kg", "Carbon dioxide produced", "Fuel in kg multiplied by 3.16"),
    (
        "fuel_kg_per_nm",
        "Fuel used per nautical mile",
        "fuel_burn_kg divided by great_circle_nm",
    ),
    (
        "fuel_kg_per_ask",
        "Fuel per available seat-kilometre",
        "Fuel divided by (seats x distance in km). Capacity, not passengers.",
    ),
    (
        "co2_kg_per_ask",
        "CO2 per available seat-kilometre",
        "Same idea using CO2. The standard way airlines compare efficiency.",
    ),
    (
        "avg_speed_kt",
        "Average speed over the whole flight",
        "Distance divided by time. Used by one of the quality checks.",
    ),
    (
        "ets_chargeable_tonnes",
        "CO2 the airline has to buy allowances for",
        "CO2 in tonnes multiplied by the scope factor for that flight",
    ),
    (
        "quality_score",
        "How far the flight can be trusted, from 0 to 1",
        "1.0 if it passed everything; 0 if any hard check failed",
    ),
    (
        "type_source",
        "Where the aircraft type came from",
        "'reported' if the flight data had it, 'registry' if looked up",
    ),
]


COMMANDS = [
    (
        "winget install -e --id PostgreSQL.PostgreSQL.17",
        "Install PostgreSQL. Only needed once.",
    ),
    ("uv sync --group dev", "Install everything. Downloads Python 3.12 itself."),
    ("uv run flightperf init", "Create the database and all its tables. Safe to rerun."),
    ("uv run flightperf ingest airports", "Download the airport list, 13 MB."),
    (
        "uv run flightperf ingest aircraft",
        "Download the aircraft registry, 61 MB. Fills in missing types.",
    ),
    ("uv run flightperf ingest airlines", "Download airline names, 400 KB."),
    (
        "uv run flightperf ingest flights --year 2022 --month 6",
        "Download one month of flights, about 200 MB.",
    ),
    ("uv run flightperf build quality", "Run the 15 data quality checks."),
    ("uv run flightperf build fuel", "Work out fuel and CO2 for each flight."),
    (
        "uv run flightperf build validate",
        "Check the fuel model against published figures.",
    ),
    ("uv run flightperf build kpi", "Work out the costs. Needs prices first."),
    ("uv run flightperf status", "Show how many rows are in each table."),
    ("uv run flightperf sources", "Show where every file came from."),
    ("uv run pytest -q", "Run the tests."),
    ("uv run python scripts/update_summary.py", "Rebuild this document."),
]


# ---------------------------------------------------------------------------
# Reading the live figures out of the database
# ---------------------------------------------------------------------------

def read_database():
    """Pull the current facts out of the database.

    Returns None if PostgreSQL is not running or the database has not been
    built yet, so that this document can still be generated on a fresh
    clone.
    """
    from flightperf.config import get_settings

    settings = get_settings()

    # Check the port is open before trying to connect properly. Without this
    # the script can sit waiting for a database that is not running.
    if not settings.server_is_reachable():
        return None

    try:
        con = psycopg.connect(settings.connection_string())
    except psycopg.Error:
        return None

    data = {}
    try:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = 'core'
                ORDER BY table_name
                """
            )
            tables = cur.fetchall()

            data["row_counts"] = []
            data["columns"] = {}
            for name, kind in tables:
                # Table names come from the catalogue, not from user input,
                # but an identifier still cannot be passed as a parameter.
                cur.execute(
                    psycopg.sql.SQL("SELECT count(*) FROM core.{}").format(
                        psycopg.sql.Identifier(name)
                    )
                )
                n = cur.fetchone()[0]
                data["row_counts"].append(
                    (name, "view" if kind == "VIEW" else "table", format(n, ","))
                )

                cur.execute(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'core' AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    [name],
                )
                data["columns"][name] = cur.fetchall()

            cur.execute(
                """
                SELECT rule_family, rule_code, severity, description, rationale
                FROM core.dim_quality_rule
                ORDER BY rule_family, rule_code
                """
            )
            data["quality_rules"] = cur.fetchall()

            cur.execute(
                """
                SELECT source_id, publisher, file_name, file_bytes,
                       rows_loaded, downloaded_at_utc, file_sha256
                FROM core.dim_source
                ORDER BY source_id
                """
            )
            data["sources"] = cur.fetchall()

            # A quick profile of how complete the flight data actually is.
            cur.execute("SELECT count(*) FROM core.fact_flight")
            if not cur.fetchone()[0]:
                data["completeness"] = None
                return data

            cur.execute(
                """
                SELECT
                    count(*),
                    count(*) FILTER (WHERE origin_icao      IS NULL),
                    count(*) FILTER (WHERE destination_icao IS NULL),
                    count(*) FILTER (WHERE type_icao        IS NULL),
                    count(*) FILTER (WHERE operator_icao    IS NULL),
                    count(*) FILTER (WHERE great_circle_nm  IS NULL)
                FROM core.fact_flight
                """
            )
            data["completeness"] = cur.fetchone()

            cur.execute(
                """
                SELECT route_key, origin_city, destination_city,
                       count(*), round(avg(great_circle_nm))
                FROM core.v_flight_kpi
                WHERE route_key IS NOT NULL
                GROUP BY 1, 2, 3
                ORDER BY count(*) DESC
                LIMIT 10
                """
            )
            data["top_routes"] = cur.fetchall()

            cur.execute(
                """
                SELECT operator_icao, n_flights_seen
                FROM core.dim_operator
                ORDER BY n_flights_seen DESC
                LIMIT 10
                """
            )
            data["top_operators"] = cur.fetchall()

            cur.execute(
                """
                SELECT type_icao, n_flights_seen
                FROM core.dim_aircraft_type
                ORDER BY n_flights_seen DESC
                LIMIT 10
                """
            )
            data["top_types"] = cur.fetchall()

        return data
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Document building
# ---------------------------------------------------------------------------

# A4 is 11906 twips wide, minus the default 1440 margin on each side.
CONTENT_WIDTH = 9026

BODY_SIZE = Pt(10.5)
TABLE_SIZE = Pt(9)


def add_title(doc):
    """Title, subtitle and the date the document was generated."""
    heading = doc.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(TITLE)
    run.bold = True
    run.font.size = Pt(24)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = sub.add_run(SUBTITLE)
    sub_run.font.size = Pt(13)
    sub_run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    stamp = doc.add_paragraph()
    stamp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # Local date is fine here - it is just a "last updated" line on a report.
    today = date.today()  # noqa: DTZ011
    stamp_run = stamp.add_run("Last updated: " + today.strftime("%d %B %Y"))
    stamp_run.italic = True
    stamp_run.font.size = Pt(10)
    stamp_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)


def add_body(doc, text):
    paragraph = doc.add_paragraph(text)
    for run in paragraph.runs:
        run.font.size = BODY_SIZE
    return paragraph


def add_bullets(doc, items):
    for item in items:
        paragraph = doc.add_paragraph(item, style="List Bullet")
        for run in paragraph.runs:
            run.font.size = BODY_SIZE


def add_mono(doc, text):
    """A line of code or a URL, in a monospaced font."""
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.font.name = "Consolas"
    run.font.size = Pt(8.5)
    return paragraph


def add_subheading(doc, text):
    """A small bold label, used above the per-table column lists."""
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = True
    run.font.size = Pt(10)
    return paragraph


def add_table(doc, headers, rows, widths):
    """Add a table with a bold header row.

    widths is a list of column widths in twips and has to add up to
    CONTENT_WIDTH, otherwise Word stretches the columns oddly.
    """
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    header_cells = table.rows[0].cells
    for i, text in enumerate(headers):
        header_cells[i].text = ""
        run = header_cells[i].paragraphs[0].add_run(text)
        run.bold = True
        run.font.size = TABLE_SIZE

    for row_values in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row_values):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run("" if value is None else str(value))
            run.font.size = TABLE_SIZE

    # Column widths have to be set on every cell, not just the table.
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            cell.width = Pt(widths[i] / 20.0)

    doc.add_paragraph()
    return table


def add_source_block(doc, source):
    """The detail block for one data source."""
    add_table(
        doc,
        ["", ""],
        [
            ("Publisher", source["publisher"]),
            ("Licence", source["licence"]),
            ("Size", source["size"]),
            ("Used for", source["used_for"]),
            ("Account needed", source["account"]),
        ],
        [2200, 6826],
    )
    add_body(doc, "Page:")
    add_mono(doc, source["page"])
    add_body(doc, "File:")
    add_mono(doc, source["file"])


def build():
    doc = Document()
    live = read_database()

    add_title(doc)
    doc.add_paragraph()

    # --- 1 -----------------------------------------------------------------
    doc.add_heading("1. What this project is", level=1)
    add_body(
        doc,
        "Aircraft broadcast their position every few seconds and volunteers "
        "around the world record those signals. OpenSky publishes the result as "
        "a monthly file with one row per flight, giving the departure airport, "
        "the arrival airport and the aircraft type.",
    )
    add_body(
        doc,
        "That file says nothing about fuel. This project works the fuel out for "
        "flights going in and out of French airports, turns it into CO2, and "
        "then puts a price on those emissions under the EU carbon rules.",
    )
    add_body(
        doc,
        "The question it sets out to answer: which routes, airlines and "
        "aircraft types account for the most fuel and CO2 on French traffic, "
        "and what would that cost under today's carbon rules?",
    )
    add_body(
        doc,
        "The scope is the 26 French airports listed in config/airports_fr.yml, "
        "and one month of traffic: June 2022. The dataset offers any month "
        "from January 2019 to December 2022, so adding another is a single "
        "command, but the analysis as it stands covers June 2022 only - "
        "87,903 flights over 30 days. Corsican routes are kept as a separate "
        "group, because France has restricted some short domestic flights "
        "where the train is a realistic alternative, and Corsica is the "
        "obvious case where it is not.",
    )

    # --- 2 -----------------------------------------------------------------
    doc.add_heading("2. Why it is being built", level=1)
    add_body(
        doc,
        "This is a portfolio project, built around the three things a data "
        "analyst job actually asks for: writing real SQL, defining sensible "
        "performance indicators, and dealing with data that cannot be trusted "
        "at face value. It also covers a live topic, since airlines are now "
        "paying for their emissions under the EU Emissions Trading System.",
    )
    add_body(
        doc,
        "The same approach works for any moving asset that burns fuel and gets "
        "tracked. Ships report the same kind of data through AIS, and the "
        "shipping industry has its own version of the same carbon rules.",
    )

    # --- 3 -----------------------------------------------------------------
    doc.add_heading("3. Terms worth knowing", level=1)
    add_body(
        doc,
        "The project is full of aviation jargon. These are the terms that come "
        "up repeatedly.",
    )
    add_table(doc, ["Term", "What it means"], GLOSSARY, [2100, 6926])

    # --- 4 -----------------------------------------------------------------
    doc.add_heading("4. Where the data comes from", level=1)
    add_body(
        doc,
        "Four files. All of them are direct downloads that need no account, no "
        "registration and no approval.",
    )

    doc.add_heading("4.1 Airport reference data", level=2)
    add_source_block(doc, SOURCE_AIRPORTS)
    add_body(
        doc,
        "Every distance in the project is calculated from the coordinates in "
        "this file, which is why they are downloaded rather than typed in by "
        "hand. The file lists every airfield in the world including heliports "
        "and closed strips, so only entries with a proper four-letter ICAO code "
        "and a real airport type are kept.",
    )

    doc.add_heading("4.2 Flight list", level=2)
    add_source_block(doc, SOURCE_FLIGHTS)
    add_body(
        doc,
        "One row per flight. The file is left compressed on disk. Python "
        "unzips it a block at a time and streams it into PostgreSQL with "
        "COPY, so the uncompressed gigabyte never has to exist as a file. "
        "The columns are:",
    )
    add_table(doc, ["Column", "What it is"], FLIGHTLIST_COLUMNS, [2400, 6626])

    doc.add_heading("4.3 Aircraft registry", level=2)
    add_source_block(doc, SOURCE_AIRCRAFT)
    add_body(
        doc,
        "This is what fixes the biggest hole in the data. The flight list "
        "leaves the aircraft type blank on 37 percent of rows, but every row "
        "carries a transponder code, and this file maps those codes to "
        "aircraft. Loading it recovered 32,389 of the 32,649 missing types, "
        "which took the share of usable flights from 49.6 percent to 77.5.",
    )
    add_body(
        doc,
        "One quirk: the file's header row is completely blank, so the columns "
        "have to be declared by position rather than read by name.",
    )

    doc.add_heading("4.4 Airline names", level=2)
    add_source_block(doc, SOURCE_AIRLINES)
    add_body(
        doc,
        "The flight list has no airline column. The airline is worked out "
        "from the first three letters of the callsign - AFR1234 means Air "
        "France - and this file turns those codes into names and countries. "
        "323 of the 451 operators in the data are named; the rest are mostly "
        "small or recently formed carriers.",
    )

    doc.add_heading("4.5 Downloading by hand", level=2)
    add_body(
        doc,
        "The pipeline downloads all four files itself, so normally there is nothing "
        "to do. To fetch them manually instead, save them to these exact paths "
        "and the pipeline will use them rather than downloading again. Do not "
        "unzip the second one.",
    )
    add_mono(
        doc,
        "curl -L -o data/raw/ourairports/airports.csv "
        '"https://davidmegginson.github.io/ourairports-data/airports.csv"',
    )
    add_mono(
        doc,
        "curl -L -o data/raw/opensky/flightlist_20220601_20220630.csv.gz "
        '"https://zenodo.org/records/7923702/files/'
        'flightlist_20220601_20220630.csv.gz?download=1"',
    )

    doc.add_heading("4.6 How to cite them", level=2)
    add_bullets(
        doc,
        [
            "OurAirports, airport database, public domain. Give the download date.",
            (
                "Strohmeier, M. et al., Crowdsourced air traffic data from the "
                "OpenSky Network 2019-2022, Earth System Science Data. Give the "
                "Zenodo record and the specific monthly file."
            ),
        ],
    )

    # --- 5 -----------------------------------------------------------------
    doc.add_heading("5. What the data can and cannot tell you", level=1)
    add_body(
        doc,
        "The flights are reconstructed from transponder signals, not taken from "
        "airline schedules. That has consequences, and they are worth stating "
        "plainly rather than hiding.",
    )
    add_bullets(doc, DATA_LIMITATIONS)

    # --- 6 -----------------------------------------------------------------
    doc.add_heading("6. How it fits together", level=1)
    add_body(
        doc,
        "The two downloads go into a PostgreSQL database. The transformations "
        "are written in SQL, the KPI formulas live in a SQL view, and Power BI "
        "connects to the database directly. Python handles the downloading and "
        "the fuel physics, which SQL cannot do.",
    )
    add_mono(doc, "airports.csv  ---+")
    add_mono(doc, "                 +--> PostgreSQL --> SQL view --> Power BI")
    add_mono(doc, "flightlist.gz ---+")
    doc.add_paragraph()

    add_body(doc, "Three choices worth remembering the reasons for:")
    add_bullets(
        doc,
        [
            (
                "The transformations are SQL, not pandas. The distance "
                "calculation, the KPI formulas and the quality checks all sit "
                "in .sql files that can be read, reviewed and tracked in "
                "version control."
            ),
            (
                "Power BI only displays things. The calculations already "
                "happened in the SQL view, so the DAX stays simple. A formula "
                "hidden inside the .pbix file could not be compared between "
                "versions."
            ),
            (
                "PostgreSQL. A real database server, the same kind used at "
                "work and the one named in most job adverts. Power BI connects "
                "to it directly, so the report reads live data rather than "
                "importing exported files."
            ),
        ],
    )

    # --- 7 -----------------------------------------------------------------
    doc.add_heading("7. The commands", level=1)
    add_table(doc, ["Command", "What it does"], COMMANDS, [4400, 4626])

    # --- 8 -----------------------------------------------------------------
    doc.add_heading("8. The database", level=1)

    if live is None:
        add_body(
            doc,
            "The database has not been built yet. Run 'uv run flightperf init' "
            "and the ingest commands, then regenerate this document to fill in "
            "this section.",
        )
    else:
        add_body(
            doc,
            "Everything in this section is read straight out of the database "
            "when this document is generated, so it always matches what is "
            "actually there.",
        )

        doc.add_heading("8.1 What each table is for", level=2)
        add_table(
            doc,
            ["Table", "Kind", "Rows", "What it holds"],
            [
                (name, kind, count, TABLE_NOTES.get(name, ""))
                for name, kind, count in live["row_counts"]
            ],
            [2000, 800, 1100, 5126],
        )

        doc.add_heading("8.2 The columns in each table", level=2)
        for name, _kind, _count in live["row_counts"]:
            add_subheading(doc, name)
            add_table(doc, ["Column", "Type"], live["columns"].get(name, []),
                      [5000, 4026])

    # --- 9 -----------------------------------------------------------------
    doc.add_heading("9. The data quality checks", level=1)
    add_body(
        doc,
        "Before any fuel number is calculated it has to be clear which flights "
        "can be trusted. A HARD failure means the flight is dropped from the "
        "fuel analysis but still counted. A SOFT failure means it is kept and "
        "flagged.",
    )
    add_body(
        doc,
        "Both counts go in the writeup. Reporting that a given share of flights "
        "was dropped, with a breakdown of why, is more convincing than "
        "presenting a suspiciously clean dataset.",
    )
    if live and live["quality_rules"]:
        add_table(
            doc,
            ["Family", "Check", "Level", "What it does", "Why it matters"],
            live["quality_rules"],
            [1500, 1900, 700, 2400, 2526],
        )
    else:
        add_body(doc, "Build the database to fill in the list of checks.")

    # --- 10 ----------------------------------------------------------------
    doc.add_heading("10. The KPIs", level=1)
    add_body(
        doc,
        "These are the columns the Power BI report is built on. They are all "
        "defined in sql/03_marts/001_v_flight_kpi.sql.",
    )
    add_table(
        doc,
        ["Column", "What it is", "How it is worked out"],
        KPI_NOTES,
        [2000, 3100, 3926],
    )

    # --- 11 ----------------------------------------------------------------
    doc.add_heading("11. What the data looks like so far", level=1)

    if live and live.get("completeness"):
        total, no_orig, no_dest, no_type, no_op, no_dist = live["completeness"]

        def share(n):
            return str(round(100.0 * n / total, 1)) + "%"

        add_body(doc, "How complete the loaded flights are:")
        add_table(
            doc,
            ["What is missing", "Flights", "Share"],
            [
                ("Aircraft type", format(no_type, ","), share(no_type)),
                (
                    "Distance (an airport is missing or unknown)",
                    format(no_dist, ","),
                    share(no_dist),
                ),
                ("Departure airport", format(no_orig, ","), share(no_orig)),
                ("Arrival airport", format(no_dest, ","), share(no_dest)),
                ("Airline", format(no_op, ","), share(no_op)),
                ("Total flights loaded", format(total, ","), "100%"),
            ],
            [4200, 2400, 2426],
        )
        add_body(
            doc,
            "The missing aircraft types are the biggest problem, because "
            "without a type there is no fuel figure at all. Whether those "
            "flights are mostly small private aircraft or full airliners "
            "decides how much the totals can be trusted, and that still needs "
            "checking.",
        )

        doc.add_heading("11.1 Busiest routes", level=2)
        add_table(
            doc,
            ["Route", "From", "To", "Flights", "Miles"],
            live["top_routes"],
            [1400, 2600, 2600, 1200, 1226],
        )

        doc.add_heading("11.2 Busiest airlines and aircraft types", level=2)
        ops = live["top_operators"]
        types = live["top_types"]
        combined = []
        for i in range(max(len(ops), len(types))):
            op = ops[i] if i < len(ops) else ("", None)
            ty = types[i] if i < len(types) else ("", None)
            combined.append(
                (
                    op[0],
                    format(op[1], ",") if op[1] else "",
                    ty[0],
                    format(ty[1], ",") if ty[1] else "",
                )
            )
        add_table(
            doc,
            ["Airline", "Flights", "Aircraft type", "Flights"],
            combined,
            [2300, 2200, 2300, 2226],
        )
    else:
        add_body(doc, "No flights loaded yet.")

    # --- 12 ----------------------------------------------------------------
    doc.add_heading("12. Which files have been used", level=1)
    add_body(
        doc,
        "Recorded automatically every time something is downloaded, so it is "
        "possible to prove later exactly which version of a dataset produced a "
        "given number. The same information is shown by 'uv run flightperf "
        "sources'.",
    )
    if live and live["sources"]:
        add_table(
            doc,
            ["Name", "Publisher", "File", "Size", "Rows", "Downloaded", "Checksum"],
            [
                (
                    sid,
                    publisher,
                    fname,
                    str(round(nbytes / 1_000_000, 1)) + " MB" if nbytes else "",
                    format(nrows, ",") if nrows else "",
                    when.strftime("%Y-%m-%d") if when else "",
                    (sha[:12] + "...") if sha else "",
                )
                for sid, publisher, fname, nbytes, nrows, when, sha in live["sources"]
            ],
            [1300, 1700, 1900, 800, 1000, 1100, 1226],
        )
    else:
        add_body(doc, "Nothing downloaded yet.")

    # --- 13 ----------------------------------------------------------------
    doc.add_heading("13. The assumptions", level=1)
    add_body(
        doc,
        "Every number that can be tweaked lives in config/params.yml rather "
        "than in the code, so the assumptions can be checked in one place and "
        "tested for how much they change the answer.",
    )
    add_body(
        doc,
        "Assumptions that have not been verified are deliberately left empty, "
        "and the code refuses to run the steps that need them. A missing carbon "
        "price treated as zero would produce a confident, wrong answer with no "
        "warning at all, which is the worst thing this project could do.",
    )

    # --- 14 onwards --------------------------------------------------------
    doc.add_heading("14. Where each part has got to", level=1)
    add_table(doc, ["Part", "Status", "Notes"], STATUS, [2900, 1600, 4526])

    doc.add_heading("15. Progress log", level=1)
    add_table(doc, ["Date", "Step", "What was done"], PROGRESS, [1300, 2200, 5526])

    doc.add_heading("16. What comes next", level=1)
    add_bullets(doc, NEXT_STEPS)

    doc.add_heading("17. Things still to sort out", level=1)
    add_bullets(doc, OPEN_QUESTIONS)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    path = build()
    print("Wrote " + str(path))
