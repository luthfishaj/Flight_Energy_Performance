"""Runs the data quality checks and scores every flight.

The checks themselves are SQL, in sql/pipeline/010_quality_checks.sql. This
module splits that file into its individual checks, runs each one with the
thresholds from params.yml, then applies the scoring in
sql/pipeline/011_quality_scoring.sql.

Why the SQL sits in a file rather than in here: the checks are the part of
the project worth reading. Keeping them as SQL means they can be reviewed
and changed without touching Python, and the file reads as a list of
questions about the data.

Why this module has to split the file: PostgreSQL will not accept several
statements in one call when parameters are involved, so each check is sent
separately. The checks are separated by "-- >>> RULE_CODE" marker lines.
"""

import logging
import re

from flightperf.config import SQL_DIR, get_settings

log = logging.getLogger(__name__)

CHECKS_SQL = SQL_DIR / "pipeline" / "010_quality_checks.sql"
SCORING_SQL = SQL_DIR / "pipeline" / "011_quality_scoring.sql"

# Marker that separates one check from the next.
CHECK_MARKER = re.compile(r"^-- >>> ([A-Z0-9_]+)\s*$", re.MULTILINE)


def split_checks(sql_text):
    """Split the checks file into (rule_code, sql) pairs, in file order."""
    parts = CHECK_MARKER.split(sql_text)

    # split() returns [preamble, code1, body1, code2, body2, ...], so the
    # first element is the file header and can be dropped.
    checks = []
    for i in range(1, len(parts) - 1, 2):
        rule_code = parts[i]
        body = parts[i + 1].strip()
        if body:
            checks.append((rule_code, body))
    return checks


def thresholds():
    """The numbers the checks compare against, from params.yml.

    Passed to every check as a dictionary. A check that does not use a
    given threshold simply ignores it.
    """
    quality = get_settings().params.get("quality", {})
    return {
        "min_flight_duration_s": quality["min_flight_duration_s"],
        "max_flight_duration_s": quality["max_flight_duration_s"],
        "min_avg_speed_kt": quality["min_avg_speed_kt"],
        "max_avg_speed_kt": quality["max_avg_speed_kt"],
        "max_airport_distance_nm": quality["max_airport_distance_nm"],
    }


def run_checks(con):
    """Run every check, then score each flight. Returns a list of results.

    Safe to run again: the old findings are cleared first, so re-running
    after loading more flights or fixing a bug gives a clean answer rather
    than doubling everything up.
    """
    params = thresholds()
    checks = split_checks(CHECKS_SQL.read_text(encoding="utf-8"))

    # Sanity check: every rule run here should exist in the catalogue, and
    # every rule in the catalogue should be run. A mismatch means a check
    # was added to one place and not the other, which is easy to do and
    # would otherwise go unnoticed.
    with con.cursor() as cur:
        cur.execute("SELECT rule_code FROM core.dim_quality_rule")
        catalogued = {row[0] for row in cur.fetchall()}

    coded = {code for code, _ in checks}
    if coded - catalogued:
        raise ValueError(
            "checks with no entry in dim_quality_rule: " + ", ".join(sorted(coded - catalogued))
        )
    if catalogued - coded:
        raise ValueError(
            "rules in the catalogue with no check written: "
            + ", ".join(sorted(catalogued - coded))
        )

    results = []
    with con.cursor() as cur:
        cur.execute("DELETE FROM core.fact_flight_quality")

        for rule_code, sql in checks:
            cur.execute(sql, params)
            n = cur.rowcount
            results.append((rule_code, n))
            log.info("%-22s %s flights", rule_code, format(n, ","))

        cur.execute(SCORING_SQL.read_text(encoding="utf-8"))

    con.commit()
    return results


def summarise(con):
    """The headline numbers: how many flights are usable, and why not."""
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE quality_status = 'PASS'),
                count(*) FILTER (WHERE quality_status = 'SOFT_FAIL'),
                count(*) FILTER (WHERE quality_status = 'HARD_FAIL'),
                count(*) FILTER (WHERE is_analysis_eligible)
            FROM core.fact_flight
            """
        )
        total, n_pass, n_soft, n_hard, n_eligible = cur.fetchone()

        cur.execute(
            """
            SELECT q.rule_code, r.rule_name, q.severity, count(*)
            FROM core.fact_flight_quality q
            JOIN core.dim_quality_rule r ON r.rule_code = q.rule_code
            GROUP BY q.rule_code, r.rule_name, q.severity
            ORDER BY count(*) DESC
            """
        )
        by_rule = cur.fetchall()

    return {
        "total": total,
        "pass": n_pass,
        "soft_fail": n_soft,
        "hard_fail": n_hard,
        "eligible": n_eligible,
        "by_rule": by_rule,
    }
