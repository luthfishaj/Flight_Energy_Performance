"""Connecting to PostgreSQL and building the tables.

PostgreSQL rather than a file-based database because it is what the job ads
ask for, it is a real server like the ones used at work, and Power BI
connects to it directly. That last point removed a whole step: the finished
tables used to be exported to Parquet files for Power BI to import, and now
the report just reads the database.

Passwords come from .env, which is gitignored.
"""

import logging

import psycopg
from psycopg import sql

from flightperf.config import SQL_DIR, get_settings

log = logging.getLogger(__name__)

# The SQL folders, applied in this order. Within a folder the files run in
# alphabetical order, which is why they are numbered 001_, 002_ and so on.
SQL_FOLDERS = ["01_schema", "03_marts", "04_quality"]


def connect(autocommit=False):
    """Open a connection to the project database."""
    settings = get_settings()
    return psycopg.connect(settings.connection_string(), autocommit=autocommit)


def create_database_if_missing():
    """Create the project database if it does not exist yet.

    Connects to the built-in 'postgres' database to do it, because you
    cannot create a database from inside the one being created. Returns True
    if it had to create it.
    """
    settings = get_settings()

    # CREATE DATABASE cannot run inside a transaction, hence autocommit.
    with psycopg.connect(
        settings.connection_string(database="postgres"), autocommit=True
    ) as con, con.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", [settings.pg_database]
        )
        if cur.fetchone():
            return False

        # A database name cannot be passed as a parameter - identifiers never
        # can - so it goes through psycopg's identifier quoting instead.
        cur.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(settings.pg_database))
        )
        log.info("created database %s", settings.pg_database)
        return True


def find_sql_files():
    """List every .sql file in the order it should be run."""
    files = []
    for folder in SQL_FOLDERS:
        path = SQL_DIR / folder
        if path.exists():
            files.extend(sorted(path.glob("*.sql")))
    return files


def migrate(con=None):
    """Run all the SQL files to build the tables.

    Safe to run more than once: every statement is either CREATE IF NOT
    EXISTS, CREATE OR REPLACE, or an INSERT that ignores duplicates. So
    re-running it does nothing rather than throwing an error.
    """
    close_when_done = con is None
    if con is None:
        con = connect()

    count = 0
    try:
        with con.cursor() as cur:
            for path in find_sql_files():
                log.info("running %s", path.relative_to(SQL_DIR.parent))
                cur.execute(path.read_text(encoding="utf-8"))
                count += 1
        con.commit()
    finally:
        if close_when_done:
            con.close()
    return count


def table_counts(con):
    """Row count for every table and view in the core schema."""
    results = []
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = 'core'
            ORDER BY table_name
            """
        )
        for name, kind in cur.fetchall():
            with con.cursor() as count_cur:
                count_cur.execute(
                    sql.SQL("SELECT count(*) FROM core.{}").format(
                        sql.Identifier(name)
                    )
                )
                n = count_cur.fetchone()[0]
            results.append((name, "view" if kind == "VIEW" else "table", n))
    return results
