"""Basic checks that the config files load properly."""

from flightperf.config import get_settings


def test_config_files_load():
    settings = get_settings()
    assert settings.params, "params.yml came back empty"
    assert settings.airports, "airports_fr.yml came back empty"


def test_airports_are_in_scope():
    airports = get_settings().scoped_airports()
    # Marseille matters here - it's where CMA CGM is based.
    assert "LFML" in airports
    assert airports["LFPG"] == "core"
    assert airports["LFKJ"] == "corsica"


def test_connection_string_has_no_password_when_described():
    """describe_connection is printed to the terminal, so it must not leak."""
    settings = get_settings()
    described = settings.describe_connection()
    assert settings.pg_database in described
    assert "password" not in described.lower()
