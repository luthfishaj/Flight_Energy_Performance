-- The data quality checks.
--
-- Each block below is one check: find the flights that fail it, and write a
-- row into fact_flight_quality saying which rule they broke, what was
-- measured, and what the limit was.
--
-- Nothing is ever deleted. Flights stay in the table and get labelled, so
-- it is always possible to count what was excluded and why.
--
-- This file is NOT run by 'flightperf init'. It lives in sql/pipeline/
-- rather than sql/01_schema/ because it is a processing step, not part of
-- building the tables. The quality runner splits it on the "-- >>>" markers
-- and runs each block separately, passing the thresholds from params.yml.
--
-- Storing observed_value alongside threshold_value matters: later it is
-- possible to ask whether a flight was 19 hours long or 400, which a plain
-- pass/fail flag would throw away.


-- >>> COM_NO_ORIGIN
-- No departure airport. OpenSky infers the airport from where the aircraft
-- was first seen, and that fails where receiver coverage is thin.
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, detail)
SELECT flight_id, 'COM_NO_ORIGIN', 'HARD', 'origin is blank in the source file'
FROM core.fact_flight
WHERE origin_icao IS NULL;


-- >>> COM_NO_DESTINATION
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, detail)
SELECT flight_id, 'COM_NO_DESTINATION', 'HARD', 'destination is blank in the source file'
FROM core.fact_flight
WHERE destination_icao IS NULL;


-- >>> COM_NO_TYPE
-- The biggest gap in the data. Without an aircraft type there is no fuel
-- figure, because the performance model is keyed on the type.
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, detail)
SELECT flight_id, 'COM_NO_TYPE', 'HARD', 'typecode is blank in the source file'
FROM core.fact_flight
WHERE type_icao IS NULL;


-- >>> COM_NO_CALLSIGN
-- Soft, because the flight is still usable - it just cannot be attributed
-- to an airline.
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, detail)
SELECT flight_id, 'COM_NO_CALLSIGN', 'SOFT', 'callsign is blank in the source file'
FROM core.fact_flight
WHERE callsign IS NULL;


-- >>> ID_AIRPORT_UNKNOWN
-- The source gave an airport code, but it is not in the reference data.
-- Usually a disused code. Without coordinates there is no distance.
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, detail)
SELECT
    f.flight_id,
    'ID_AIRPORT_UNKNOWN',
    'HARD',
    'unknown code: ' || concat_ws(', ',
        CASE WHEN f.origin_icao IS NOT NULL AND ao.airport_icao IS NULL
             THEN f.origin_icao END,
        CASE WHEN f.destination_icao IS NOT NULL AND ad.airport_icao IS NULL
             THEN f.destination_icao END)
FROM core.fact_flight f
LEFT JOIN core.dim_airport ao ON ao.airport_icao = f.origin_icao
LEFT JOIN core.dim_airport ad ON ad.airport_icao = f.destination_icao
WHERE (f.origin_icao      IS NOT NULL AND ao.airport_icao IS NULL)
   OR (f.destination_icao IS NOT NULL AND ad.airport_icao IS NULL);


-- >>> ID_TYPE_UNSUPPORTED
-- The aircraft type is known but the performance model does not cover it.
-- Not an error in the data - it is a coverage limit of the model, and the
-- share of traffic affected has to be reported.
--
-- This only produces results once the fuel step has populated
-- openap_supported. Before then every type is FALSE by default, and
-- flagging all of them would be meaningless - hence the EXISTS guard.
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, detail)
SELECT
    f.flight_id,
    'ID_TYPE_UNSUPPORTED',
    'SOFT',
    'no performance data for ' || f.type_icao
FROM core.fact_flight f
JOIN core.dim_aircraft_type t ON t.type_icao = f.type_icao
WHERE t.openap_supported = FALSE
  AND t.openap_proxy_type IS NULL
  AND EXISTS (SELECT 1 FROM core.dim_aircraft_type WHERE openap_supported);


-- >>> ID_DUPLICATE_ICAO24
-- The same transponder code on two flights whose times overlap. One
-- aircraft cannot fly two flights at once, so at least one row is wrong.
--
-- The join condition is the standard overlap test: two periods overlap if
-- each one starts before the other ends. The a.flight_id < b.flight_id
-- keeps each pair from matching twice and stops a row matching itself.
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, detail)
SELECT DISTINCT
    a.flight_id,
    'ID_DUPLICATE_ICAO24',
    'HARD',
    'overlaps another flight on the same transponder code'
FROM core.fact_flight a
JOIN core.fact_flight b
  ON b.icao24 = a.icao24
 AND a.flight_id < b.flight_id
 AND a.first_seen_utc < b.last_seen_utc
 AND b.first_seen_utc < a.last_seen_utc;


-- >>> ID_CALLSIGN_CONFLICT
-- Two different aircraft using the same callsign at the same time.
-- Airlines reuse callsigns through the day, which is fine, but not
-- simultaneously.
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, detail)
SELECT DISTINCT
    a.flight_id,
    'ID_CALLSIGN_CONFLICT',
    'SOFT',
    'callsign ' || a.callsign || ' in use by another aircraft at the same time'
FROM core.fact_flight a
JOIN core.fact_flight b
  ON b.callsign = a.callsign
 AND b.icao24 <> a.icao24
 AND a.first_seen_utc < b.last_seen_utc
 AND b.first_seen_utc < a.last_seen_utc
WHERE a.callsign IS NOT NULL;


-- >>> CON_SAME_AIRPORT
-- Took off and landed at the same place. Real enough - training circuits,
-- test flights, diversions back to base - but not a normal service, so it
-- would distort route averages.
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, detail)
SELECT flight_id, 'CON_SAME_AIRPORT', 'SOFT',
       'departed and arrived at ' || origin_icao
FROM core.fact_flight
WHERE origin_icao IS NOT NULL
  AND origin_icao = destination_icao;


-- >>> CON_ZERO_DISTANCE
-- Both airports known, but the distance came out as zero or negative.
-- Would divide by zero in the KPI view.
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, observed_value, detail)
SELECT flight_id, 'CON_ZERO_DISTANCE', 'HARD', great_circle_nm,
       'distance is not positive'
FROM core.fact_flight
WHERE great_circle_nm IS NOT NULL
  AND great_circle_nm <= 0;


-- >>> CON_DURATION_SHORT
-- Usually a fragment of a track that the source failed to join up properly.
INSERT INTO core.fact_flight_quality
    (flight_id, rule_code, severity, observed_value, threshold_value, detail)
SELECT flight_id, 'CON_DURATION_SHORT', 'HARD',
       duration_s, %(min_flight_duration_s)s,
       'lasted ' || round(duration_s / 60.0) || ' minutes'
FROM core.fact_flight
WHERE duration_s IS NOT NULL
  AND duration_s < %(min_flight_duration_s)s;


-- >>> CON_DURATION_LONG
-- Normally means two separate flights got merged into one row.
INSERT INTO core.fact_flight_quality
    (flight_id, rule_code, severity, observed_value, threshold_value, detail)
SELECT flight_id, 'CON_DURATION_LONG', 'HARD',
       duration_s, %(max_flight_duration_s)s,
       'lasted ' || round(duration_s / 3600.0, 1) || ' hours'
FROM core.fact_flight
WHERE duration_s IS NOT NULL
  AND duration_s > %(max_flight_duration_s)s;


-- >>> CON_SPEED_IMPLAUSIBLE
-- Straight-line distance divided by time. Either the distance or the
-- duration is wrong, and there is no way to tell which.
--
-- The lower limit has to be generous: short flights spend much of their
-- time climbing and descending, so their average works out low.
INSERT INTO core.fact_flight_quality
    (flight_id, rule_code, severity, observed_value, threshold_value, detail)
SELECT
    flight_id,
    'CON_SPEED_IMPLAUSIBLE',
    'HARD',
    great_circle_nm / (duration_s / 3600.0),
    %(max_avg_speed_kt)s,
    'average speed ' || round((great_circle_nm / (duration_s / 3600.0))::numeric)
        || ' knots'
FROM core.fact_flight
WHERE great_circle_nm IS NOT NULL
  AND great_circle_nm > 0
  AND duration_s > 0
  AND (
        great_circle_nm / (duration_s / 3600.0) > %(max_avg_speed_kt)s
     OR great_circle_nm / (duration_s / 3600.0) < %(min_avg_speed_kt)s
  );


-- >>> CON_ORIGIN_FAR
-- The first place the aircraft was seen is a long way from the airport the
-- source says it departed from. Means the departure airport was a guess
-- rather than an observation.
INSERT INTO core.fact_flight_quality
    (flight_id, rule_code, severity, observed_value, threshold_value, detail)
SELECT
    f.flight_id,
    'CON_ORIGIN_FAR',
    'SOFT',
    core.haversine_nm(f.first_lat, f.first_lon, a.latitude_deg, a.longitude_deg),
    %(max_airport_distance_nm)s,
    'first seen ' || round(core.haversine_nm(
        f.first_lat, f.first_lon, a.latitude_deg, a.longitude_deg)::numeric)
        || ' nm from ' || f.origin_icao
FROM core.fact_flight f
JOIN core.dim_airport a ON a.airport_icao = f.origin_icao
WHERE f.first_lat IS NOT NULL
  AND core.haversine_nm(f.first_lat, f.first_lon, a.latitude_deg, a.longitude_deg)
      > %(max_airport_distance_nm)s;


-- >>> CON_DESTINATION_FAR
-- The same thing at the other end of the flight.
INSERT INTO core.fact_flight_quality
    (flight_id, rule_code, severity, observed_value, threshold_value, detail)
SELECT
    f.flight_id,
    'CON_DESTINATION_FAR',
    'SOFT',
    core.haversine_nm(f.last_lat, f.last_lon, a.latitude_deg, a.longitude_deg),
    %(max_airport_distance_nm)s,
    'last seen ' || round(core.haversine_nm(
        f.last_lat, f.last_lon, a.latitude_deg, a.longitude_deg)::numeric)
        || ' nm from ' || f.destination_icao
FROM core.fact_flight f
JOIN core.dim_airport a ON a.airport_icao = f.destination_icao
WHERE f.last_lat IS NOT NULL
  AND core.haversine_nm(f.last_lat, f.last_lon, a.latitude_deg, a.longitude_deg)
      > %(max_airport_distance_nm)s;
