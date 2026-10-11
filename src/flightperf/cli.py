"""Command line interface.

The pipeline is split into steps that can each be run on their own:

    flightperf init                 create the database and its tables
    flightperf ingest airports      download airport names and coordinates
    flightperf ingest flights       download one month of flights
    flightperf build quality        run the data quality checks
    flightperf build fuel           calculate fuel and CO2 with OpenAP
    flightperf build kpi            work out the carbon costs
    flightperf status               show how many rows are in each table
    flightperf sources              show where the data came from

They are separate because the monthly flight list is a 200 MB download, and
it should not have to be repeated every time something changes further down
the pipeline.

Power BI connects straight to the database, so there is no export step.
"""

import logging

import psycopg
import typer
from rich.console import Console
from rich.table import Table

from flightperf import db
from flightperf.config import get_settings
from flightperf.fuel.run import (
    apply_to_flights,
    build_curves,
    resolve_aircraft_types,
    validate,
)
from flightperf.ingest.aircraft import load_aircraft
from flightperf.ingest.airlines import load_airlines
from flightperf.ingest.airports import load_airports
from flightperf.ingest.flights import load_flights
from flightperf.kpi.costs import compute_costs, missing_settings
from flightperf.quality.checks import run_checks, summarise
from flightperf.seed import seed_dim_date

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
console = Console()

app = typer.Typer(add_completion=False, help=__doc__)
ingest_app = typer.Typer(help="Download the source data.")
build_app = typer.Typer(help="Turn the raw flights into fuel figures and KPIs.")
app.add_typer(ingest_app, name="ingest")
app.add_typer(build_app, name="build")


def not_built_yet(step, waiting_on):
    """Placeholder for the steps that have not been written yet.

    It raises rather than just printing a message, because a step that
    quietly does nothing ends up producing an empty dashboard with no clue as
    to which part failed.
    """
    raise NotImplementedError(
        "The '" + step + "' step isn't written yet. Waiting on: " + waiting_on
    )


def open_database():
    """Connect, with a readable message if PostgreSQL is not reachable.

    The default psycopg error is a wall of text about sockets, which is not
    much help when the real problem is that the password is missing from
    .env or the service is not running.
    """
    settings = get_settings()

    if not settings.server_is_reachable():
        console.print("[red]PostgreSQL is not answering on "
                      + settings.pg_host + ":" + settings.pg_port + "[/red]")
        console.print()
        console.print("If it is not installed yet:")
        console.print("  winget install -e --id PostgreSQL.PostgreSQL.17")
        console.print("If it is installed, check the service is running.")
        raise typer.Exit(1)

    try:
        return db.connect()
    except psycopg.OperationalError as error:
        console.print("[red]Could not connect to PostgreSQL.[/red]")
        console.print("  trying: " + settings.describe_connection())
        console.print("  " + str(error).strip().splitlines()[0])
        console.print()
        console.print("Things worth checking:")
        console.print("  - Is PostgreSQL installed and the service running?")
        console.print("  - Is there a .env file with PGPASSWORD set?")
        console.print("  - Has 'flightperf init' been run yet?")
        raise typer.Exit(1) from error


@app.command()
def init(
    start: str = typer.Option("2019-01-01", help="First day in the date table."),
    end: str = typer.Option("2022-12-31", help="Last day in the date table."),
):
    """Create the database, build all the tables, and fill in the date table.

    The default date range matches the years the flight list covers. Safe to
    run more than once.
    """
    settings = get_settings()
    console.print("Connecting as " + settings.describe_connection())

    if not settings.server_is_reachable():
        console.print("[red]PostgreSQL is not answering on "
                      + settings.pg_host + ":" + settings.pg_port + "[/red]")
        console.print()
        console.print("If it is not installed yet:")
        console.print("  winget install -e --id PostgreSQL.PostgreSQL.17")
        console.print("Then copy .env.example to .env and set PGPASSWORD.")
        raise typer.Exit(1)

    try:
        created = db.create_database_if_missing()
    except psycopg.OperationalError as error:
        console.print("[red]Could not connect to PostgreSQL.[/red]")
        console.print("  " + str(error).strip().splitlines()[0])
        console.print()
        console.print("If PostgreSQL is not installed yet:")
        console.print("  winget install -e --id PostgreSQL.PostgreSQL.17")
        console.print("Then copy .env.example to .env and set PGPASSWORD.")
        raise typer.Exit(1) from error

    if created:
        console.print("[green]Created database " + settings.pg_database + "[/green]")

    con = db.connect()
    try:
        files_run = db.migrate(con)
        n_dates = seed_dim_date(con, start, end)
    finally:
        con.close()

    console.print("[green]Database ready[/green]")
    console.print("  SQL files run  : " + str(files_run))
    console.print("  dates in table : " + str(n_dates))


@ingest_app.command("airports")
def ingest_airports(
    force: bool = typer.Option(False, help="Download again even if the file is there."),
):
    """Download airport names and coordinates from OurAirports (about 13 MB)."""
    con = open_database()
    try:
        n = load_airports(con, force=force)
    finally:
        con.close()
    console.print("[green]Loaded " + format(n, ",") + " airports[/green]")


@ingest_app.command("aircraft")
def ingest_aircraft(
    force: bool = typer.Option(False, help="Download again even if the file is there."),
):
    """Download the aircraft registry and fill in missing aircraft types.

    The flight list leaves the type blank on a lot of rows, but always
    carries a transponder code. Looking that code up here recovers the type
    for many of them. Run 'build quality' again afterwards.
    """
    con = open_database()
    try:
        result = load_aircraft(con, force=force)
    finally:
        con.close()

    console.print("[green]Aircraft registry loaded[/green]")
    console.print("  aircraft in the file  : " + format(result["registry_rows"], ","))
    console.print("  usable entries        : " + format(result["usable_rows"], ","))
    console.print("  flights missing a type: " + format(result["missing_before"], ","))
    console.print("  types recovered       : " + format(result["recovered"], ","))
    console.print("  still missing         : " + format(result["missing_after"], ","))


@ingest_app.command("airlines")
def ingest_airlines(
    force: bool = typer.Option(False, help="Download again even if the file is there."),
):
    """Download airline names so the dashboard shows names, not codes."""
    con = open_database()
    try:
        result = load_airlines(con, force=force)
    finally:
        con.close()
    console.print("[green]Airline names loaded[/green]")
    console.print("  named " + format(result["matched"], ",") + " of "
                  + format(result["operators"], ",") + " operators seen in the data")


@ingest_app.command("flights")
def ingest_flights(
    year: int = typer.Option(..., help="Year, between 2019 and 2022."),
    month: int = typer.Option(..., min=1, max=12, help="Month, 1 to 12."),
    force: bool = typer.Option(False, help="Download again even if the file is there."),
):
    """Download one month of flights from Zenodo (about 200 MB).

    Run 'ingest airports' first - the distance calculation needs the airport
    coordinates.
    """
    con = open_database()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT count(*) FROM core.dim_airport")
            if cur.fetchone()[0] == 0:
                raise typer.BadParameter(
                    "No airports loaded yet. Run 'flightperf ingest airports' first."
                )
        n = load_flights(con, year, month, force=force)
    finally:
        con.close()
    console.print("[green]Loaded " + format(n, ",") + " flights[/green]")


@build_app.command("quality")
def build_quality():
    """Run the data quality checks and give each flight a score.

    Nothing is deleted - flights are labelled, so it is always possible to
    count what was excluded and why. Safe to run again after loading more
    data or fixing a check.
    """
    con = open_database()
    try:
        run_checks(con)
        summary = summarise(con)
    finally:
        con.close()

    total = summary["total"]

    def share(n):
        return f"{100.0 * n / total:.1f}%" if total else "-"

    verdicts = Table(title="Verdict per flight")
    verdicts.add_column("verdict", style="cyan")
    verdicts.add_column("flights", justify="right")
    verdicts.add_column("share", justify="right")
    verdicts.add_row("Pass", format(summary["pass"], ","), share(summary["pass"]))
    verdicts.add_row("Kept, but flagged", format(summary["soft_fail"], ","),
                     share(summary["soft_fail"]))
    verdicts.add_row("Dropped", format(summary["hard_fail"], ","),
                     share(summary["hard_fail"]))
    console.print(verdicts)

    findings = Table(title="What was found")
    findings.add_column("check", style="cyan")
    findings.add_column("level")
    findings.add_column("flights", justify="right")
    findings.add_column("share", justify="right")
    for rule_code, rule_name, severity, n in summary["by_rule"]:
        findings.add_row(rule_name, severity, format(n, ","), share(n))
    console.print(findings)

    console.print(
        "[green]" + format(summary["eligible"], ",") + " of "
        + format(total, ",") + " flights (" + share(summary["eligible"])
        + ") are usable for the fuel analysis[/green]"
    )


@build_app.command("fuel")
def build_fuel():
    """Work out fuel burn and CO2 for every flight that passed the checks.

    Run 'build quality' first. Flights that failed the checks keep NULL
    fuel rather than zero, because not knowing is not the same as nothing.
    """
    con = open_database()
    try:
        types = resolve_aircraft_types(con)
        _n_types, n_points = build_curves(con)
        result = apply_to_flights(con)
    finally:
        con.close()

    console.print("[green]Fuel calculated[/green]")
    console.print("  aircraft types modelled : " + format(types["types_supported"], ",")
                  + " of " + format(types["types_total"], ","))
    console.print("  curve points built      : " + format(n_points, ","))
    console.print("  flights with a figure   : " + format(result["with_fuel"], ",")
                  + " of " + format(result["eligible"], ",") + " eligible")
    console.print("  total fuel              : " + format(round(result["fuel_tonnes"]), ",")
                  + " tonnes")
    console.print("  total CO2               : " + format(round(result["co2_tonnes"]), ",")
                  + " tonnes")
    console.print()
    console.print("[dim]Airborne fuel only - taxi is not included. Distances are "
                  "straight lines, so these are underestimates.[/dim]")


@build_app.command("validate")
def build_validate():
    """Check the fuel model against published cruise burn figures.

    An unvalidated physics model is just a plausible-looking guess, so this
    is part of the pipeline rather than a one-off sanity check.
    """
    con = open_database()
    try:
        results, tolerance = validate(con)
    finally:
        con.close()

    table = Table(title=f"Modelled cruise burn vs published (tolerance {tolerance:.0%})")
    table.add_column("type", style="cyan")
    table.add_column("model kg/nm", justify="right")
    table.add_column("ICAO kg/nm", justify="right")
    table.add_column("ratio", justify="right")
    table.add_column("verdict")

    n_ok = 0
    for type_icao, model_kg_nm, real_kg_nm, ratio, verdict in results:
        if verdict == "ok":
            n_ok += 1
        table.add_row(
            type_icao,
            f"{model_kg_nm:.2f}" if model_kg_nm else "-",
            f"{real_kg_nm:.2f}",
            f"{ratio:.2f}" if ratio else "-",
            verdict if verdict == "ok" else "[red]" + verdict + "[/red]",
        )
    console.print(table)
    console.print(f"{n_ok} of {len(results)} types agree within tolerance.")
    console.print("[dim]Published figures: ICAO Carbon Emissions Calculator "
                  "Methodology v13.1, Appendix C - see "
                  "config/validation_reference.yml.[/dim]")


@build_app.command("kpi")
def build_kpi():
    """Work out the fuel cost and EU carbon cost per flight.

    Needs the prices in config/params.yml to be filled in first. Until they
    are, this refuses to run rather than treating a missing price as zero.
    """
    missing = missing_settings()
    if missing:
        console.print("[yellow]Not ready yet.[/yellow] These still need looking "
                      "up and writing into config/params.yml:")
        console.print()
        for key, explanation in missing:
            console.print("  [cyan]" + key + "[/cyan]")
            console.print("    " + explanation)
            console.print()
        console.print("Record where each figure came from and the date, next to "
                      "the value.")
        raise typer.Exit(1)

    con = open_database()
    try:
        result = compute_costs(con)
    finally:
        con.close()

    console.print("[green]Costs calculated[/green]")
    console.print("  flights costed        : " + format(result["flights"], ","))
    console.print("  in EU carbon scope    : " + format(result["in_ets_scope"], ","))
    console.print("  route correction used : x" + f"{result['route_extension_factor']:.3f}")
    console.print("  ICAO calibration      : " + (f"{result['calibrated_types']} types"
                                                  if result["calibrated_types"] else "off"))
    console.print("  fuel (corrected)      : " + format(round(result["fuel_tonnes"]), ",")
                  + " tonnes")
    console.print("  CO2 (corrected)       : " + format(round(result["co2_tonnes"]), ",")
                  + " tonnes")
    console.print("  fuel cost             : EUR " + format(round(result["fuel_cost_eur"]), ","))
    console.print("  carbon cost           : EUR " + format(round(result["ets_cost_eur"]), ","))
    console.print()
    console.print("CO2 by carbon market (eligible flights):")
    total = sum(result["co2_by_market"].values()) or 1
    for market, tonnes in result["co2_by_market"].items():
        console.print(f"  {market:<10}: {round(tonnes):>10,} tonnes  {tonnes / total:6.1%}")


@app.command()
def status():
    """Show how many rows are currently in each table."""
    con = open_database()
    try:
        rows = db.table_counts(con)
    finally:
        con.close()

    table = Table(title="What's in the database")
    table.add_column("table", style="cyan")
    table.add_column("kind")
    table.add_column("rows", justify="right")
    for name, kind, count in rows:
        table.add_row(name, kind, format(count, ","))

    console.print(table)


@app.command()
def sources():
    """Show where every piece of data came from."""
    con = open_database()
    try:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT source_id, publisher, licence, file_name,
                       file_bytes, rows_loaded, downloaded_at_utc
                FROM core.dim_source
                ORDER BY source_id
                """
            )
            rows = cur.fetchall()
    finally:
        con.close()

    if not rows:
        console.print("Nothing downloaded yet.")
        return

    table = Table(title="Data sources")
    for column in ["source", "publisher", "licence", "file", "size", "rows", "downloaded"]:
        table.add_column(column)

    for source_id, publisher, licence, file_name, size, n_rows, when in rows:
        table.add_row(
            source_id,
            publisher,
            licence or "",
            file_name or "",
            "%.1f MB" % (size / 1_000_000) if size else "",
            format(n_rows, ",") if n_rows else "",
            when.strftime("%Y-%m-%d") if when else "",
        )

    console.print(table)


if __name__ == "__main__":
    app()
