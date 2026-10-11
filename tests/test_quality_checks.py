"""Checks on the data quality rules themselves."""

from flightperf.quality.checks import CHECKS_SQL, split_checks, thresholds


def test_every_check_is_found_and_named():
    checks = split_checks(CHECKS_SQL.read_text(encoding="utf-8"))
    codes = [code for code, _ in checks]

    assert len(codes) == len(set(codes)), "a rule code is used twice"
    assert len(codes) >= 15
    assert "COM_NO_TYPE" in codes
    assert "CON_DURATION_LONG" in codes


def test_each_check_writes_to_the_findings_table():
    """Every check must insert into fact_flight_quality and nowhere else."""
    for code, sql in split_checks(CHECKS_SQL.read_text(encoding="utf-8")):
        assert "INSERT INTO core.fact_flight_quality" in sql, code
        assert "DELETE" not in sql.upper(), f"{code} must not delete anything"
        assert "DROP" not in sql.upper(), f"{code} must not drop anything"


def test_thresholds_are_all_present():
    values = thresholds()
    assert values["min_flight_duration_s"] < values["max_flight_duration_s"]
    assert values["min_avg_speed_kt"] < values["max_avg_speed_kt"]
    assert values["max_airport_distance_nm"] > 0
