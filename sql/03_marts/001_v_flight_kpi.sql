-- The view Power BI connects to. One row per flight.
--
-- This is a view rather than a table so that all the KPI formulas stay here
-- in a .sql file that can be read and changed. Putting them in DAX inside
-- the .pbix file instead would hide them in a binary, with no way to see
-- what changed between versions.

-- Dropped and recreated rather than CREATE OR REPLACE: PostgreSQL only
-- lets REPLACE add columns at the end, so inserting one in the middle
-- fails. Dropping first means the column order can be whatever reads best.
DROP VIEW IF EXISTS core.v_flight_kpi;

CREATE VIEW core.v_flight_kpi AS
SELECT
    f.flight_id,
    f.date_key,
    d.full_date,
    d.year,
    d.month,
    d.month_name,
    d.day_name,
    d.is_weekend,

    f.callsign,
    f.flight_number,
    f.icao24,
    f.registration,

    f.operator_icao,
    o.operator_name,
    o.is_cargo,

    f.type_icao,
    t.manufacturer,
    t.model,
    t.typical_seats,
    t.openap_supported,

    ao.airport_icao           AS origin_icao,
    ao.airport_name           AS origin_name,
    ao.municipality           AS origin_city,
    ao.iso_country            AS origin_country,
    ad.airport_icao           AS destination_icao,
    ad.airport_name           AS destination_name,
    ad.municipality           AS destination_city,
    ad.iso_country            AS destination_country,

    -- Route label that ignores direction, so Paris->Nice and Nice->Paris
    -- group together. Sorting the two codes alphabetically gives the same
    -- label either way round.
    --
    -- The NULL check matters: least() and greatest() skip NULLs rather than
    -- returning one, so a flight with a missing origin would otherwise come
    -- out as the nonsense route LFPG-LFPG.
    CASE WHEN f.origin_icao IS NOT NULL AND f.destination_icao IS NOT NULL
         THEN least(f.origin_icao, f.destination_icao) || '-' ||
              greatest(f.origin_icao, f.destination_icao)
    END                                                     AS route_key,

    f.great_circle_nm,
    f.great_circle_nm * 1.852                               AS great_circle_km,
    f.duration_s / 60.0                                     AS duration_minutes,

    -- Average speed over the whole flight. Used by one of the quality
    -- checks, and useful on its own for spotting odd rows.
    CASE WHEN f.duration_s > 0
         THEN f.great_circle_nm / (f.duration_s / 3600.0)
    END                                                     AS avg_speed_kt,

    -- KPI 1: total fuel and emissions
    f.fuel_burn_kg,
    f.co2_kg,
    f.co2_kg / 1000.0                                       AS co2_tonnes,
    f.nox_kg,

    -- KPI 2: how efficient was the flight, per distance and per seat
    CASE WHEN f.great_circle_nm > 0
         THEN f.fuel_burn_kg / f.great_circle_nm
    END                                                     AS fuel_kg_per_nm,

    -- Per seat-kilometre. Note this uses the number of seats the aircraft
    -- has, NOT the number of passengers on board - the source data does not
    -- say how full the plane was. So it measures the efficiency of the
    -- capacity offered, not of the passengers carried, and it is labelled
    -- that way on the dashboard. (1 nautical mile = 1.852 km.)
    CASE WHEN t.typical_seats > 0 AND f.great_circle_nm > 0
         THEN f.fuel_burn_kg / (t.typical_seats * f.great_circle_nm * 1.852)
    END                                                     AS fuel_kg_per_ask,

    CASE WHEN t.typical_seats > 0 AND f.great_circle_nm > 0
         THEN f.co2_kg / (t.typical_seats * f.great_circle_nm * 1.852)
    END                                                     AS co2_kg_per_ask,

    -- KPI 3: corrected for the extra distance real flights fly.
    -- fuel_burn_kg above is the raw model output over the straight line;
    -- these are the figures to report. Both are kept so the size of the
    -- correction is visible.
    f.fuel_burn_adjusted_kg,
    f.co2_adjusted_kg,
    f.co2_adjusted_kg / 1000.0                              AS co2_adjusted_tonnes,

    -- KPI 4: what it cost, at the prices in params.yml.
    -- ets_cost_eur is the market VALUE of the covered emissions, not the
    -- cash airlines paid: free allocation is deliberately ignored, and in
    -- 2022 airlines still received a large share of allowances free.
    f.fuel_cost_eur,
    f.ets_cost_eur,
    f.fuel_cost_eur + f.ets_cost_eur                        AS total_cost_eur,

    f.is_domestic_fr,
    f.is_intra_eea,
    f.ets_scope_factor,
    f.co2_adjusted_kg / 1000.0 * f.ets_scope_factor          AS ets_chargeable_tonnes,

    -- Data quality columns, so any chart can be filtered to trusted flights
    f.quality_score,
    f.quality_status,
    f.is_analysis_eligible,
    f.source_id

FROM core.fact_flight              f
JOIN core.dim_date                 d  ON d.date_key      = f.date_key
-- LEFT JOIN on the rest: better to keep a flight with an unknown airline
-- than to silently drop it from every count.
LEFT JOIN core.dim_operator        o  ON o.operator_icao = f.operator_icao
LEFT JOIN core.dim_aircraft_type   t  ON t.type_icao     = f.type_icao
LEFT JOIN core.dim_airport         ao ON ao.airport_icao = f.origin_icao
LEFT JOIN core.dim_airport         ad ON ad.airport_icao = f.destination_icao;
