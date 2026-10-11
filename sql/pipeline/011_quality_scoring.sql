-- Turn the individual check failures into one verdict per flight.
--
-- Run after 010_quality_checks.sql. Every flight gets:
--
--   quality_status        PASS, SOFT_FAIL or HARD_FAIL
--   quality_score         1.0 down to 0.0
--   is_analysis_eligible  whether the fuel model should touch it
--
-- The rule is simple on purpose, because it has to be explainable:
--   any HARD failure  -> the flight cannot be trusted, score 0, excluded
--   only SOFT failures -> kept, but each one costs a bit of score
--   nothing at all    -> perfect score

UPDATE core.fact_flight f
SET
    quality_status = CASE
        WHEN counts.n_hard > 0 THEN 'HARD_FAIL'
        WHEN counts.n_soft > 0 THEN 'SOFT_FAIL'
        ELSE 'PASS'
    END,

    quality_score = CASE
        WHEN counts.n_hard > 0 THEN 0.0
        -- Each soft failure costs 0.15, with a floor of 0.4 so that a
        -- flight which is merely imperfect never looks as bad as one that
        -- failed outright.
        ELSE greatest(0.4, 1.0 - 0.15 * counts.n_soft)
    END,

    -- The single flag everything downstream filters on.
    is_analysis_eligible = (counts.n_hard = 0)

FROM (
    SELECT
        f2.flight_id,
        count(q.rule_code) FILTER (WHERE q.severity = 'HARD') AS n_hard,
        count(q.rule_code) FILTER (WHERE q.severity = 'SOFT') AS n_soft
    FROM core.fact_flight f2
    LEFT JOIN core.fact_flight_quality q ON q.flight_id = f2.flight_id
    GROUP BY f2.flight_id
) AS counts
WHERE counts.flight_id = f.flight_id;
