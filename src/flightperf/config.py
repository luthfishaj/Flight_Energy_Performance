"""Loads the settings from the config folder and the .env file.

All the adjustable numbers live in config/*.yml instead of being hardcoded
here. That way a fuel price or a threshold can be changed without touching
any code, and anyone reading the project can see every assumption in one
place.

Database credentials live in .env, which is gitignored, so no password ever
ends up in version control.
"""

import os
import socket
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Work out where the project root is, based on where this file sits.
# config.py is in src/flightperf/, so go up two levels.
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
SQL_DIR = REPO_ROOT / "sql"

# Read the .env file if there is one.
load_dotenv(REPO_ROOT / ".env")

# How long to wait for the database before giving up.
CONNECT_TIMEOUT_SECONDS = 5

# Cache so the YAML files are only read once per run.
_settings = None


def _load_yaml(filename):
    """Read one YAML file from the config folder."""
    path = CONFIG_DIR / filename
    if not path.exists():
        raise FileNotFoundError("Missing config file: " + str(path))
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Settings:
    """Holds everything loaded from config, plus the database connection."""

    def __init__(self, params, airports):
        self.params = params
        self.airports = airports

        # PostgreSQL connection details. The defaults match what the Windows
        # installer sets up, so usually only the password needs setting.
        self.pg_host = os.getenv("PGHOST", "localhost")
        self.pg_port = os.getenv("PGPORT", "5432")
        self.pg_database = os.getenv("PGDATABASE", "flightperf")
        self.pg_user = os.getenv("PGUSER", "postgres")
        self.pg_password = os.getenv("PGPASSWORD", "")

    def connection_string(self, database=None):
        """Build the connection string psycopg needs.

        Pass a database name to connect somewhere other than the project
        database - needed when creating it for the first time, since you
        cannot connect to a database that does not exist yet.
        """
        # connect_timeout matters: without it, a connection to a server that
        # is not running can sit there for a long time instead of failing.
        return (
            "host=" + self.pg_host
            + " port=" + self.pg_port
            + " dbname=" + (database or self.pg_database)
            + " user=" + self.pg_user
            + " password=" + self.pg_password
            + " connect_timeout=" + str(CONNECT_TIMEOUT_SECONDS)
        )

    def server_is_reachable(self, timeout=2.0):
        """Quick check that something is listening on the database port.

        psycopg's own connect_timeout is not always enough - if a firewall
        drops the packets instead of refusing them, the connection attempt
        can sit there for a long time. Opening a plain socket first gives a
        definite answer in a couple of seconds, which is what the tools that
        need to carry on without a database rely on.
        """
        try:
            with socket.create_connection(
                (self.pg_host, int(self.pg_port)), timeout=timeout
            ):
                return True
        except OSError:
            return False

    def describe_connection(self):
        """The connection details without the password, safe to print."""
        return (
            self.pg_user + "@" + self.pg_host + ":" + self.pg_port
            + "/" + self.pg_database
        )

    def scoped_airports(self):
        """Return {airport code: tier} for every airport in scope.

        The YAML groups them as core / extended / corsica, so this flattens
        those groups into one lookup.
        """
        result = {}
        for tier, codes in self.airports.items():
            for code in codes or []:
                result[code] = tier
        return result

    def require(self, key):
        """Get a value out of params.yml, but complain if it is still empty.

        The prices in params.yml start as null because they have not been
        looked up yet. Carrying on with a missing carbon price would quietly
        treat it as zero and produce a confident wrong answer, which is worse
        than crashing. So this raises instead.

        Use dots for nested keys, e.g. require("prices.eua_eur_per_tonne").
        """
        value = self.params
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                raise KeyError("params.yml has no setting called '" + key + "'")
            value = value[part]

        if value is None:
            raise ValueError(
                "'" + key + "' in params.yml is still empty. "
                "Look up the real value, record where it came from, "
                "and set it before running this step."
            )
        return value


def get_settings():
    """Load the config (only reads the files the first time it is called)."""
    global _settings
    if _settings is None:
        _settings = Settings(
            params=_load_yaml("params.yml"),
            airports=_load_yaml("airports_fr.yml"),
        )
    return _settings
