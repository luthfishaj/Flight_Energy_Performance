-- Dimension tables.
--
-- This is a star schema: dimension tables hold the descriptive stuff
-- (which airport, which aircraft, which day) and the fact table holds the
-- numbers. Keeping them separate avoids repeating the airport name on
-- every single flight row.
--
-- One row per thing in each table:
--   dim_date          -> one row per day
--   dim_airport       -> one row per airport
--   dim_aircraft_type -> one row per aircraft model (A320, B738, ...)
--   dim_aircraft      -> one row per physical aircraft
--   dim_operator      -> one row per airline
--   dim_quality_rule  -> one row per data quality check
--   dim_source        -> one row per file that was downloaded

CREATE SCHEMA IF NOT EXISTS core;


-- One row per calendar day.
-- Power BI needs a proper date table for its time functions to work, so
-- one is built here rather than leaving it to guess.
CREATE TABLE IF NOT EXISTS core.dim_date (
    date_key            INTEGER   PRIMARY KEY,   -- 20220614 style
    full_date           DATE      NOT NULL,
    year                SMALLINT  NOT NULL,
    quarter             SMALLINT  NOT NULL,
    month               SMALLINT  NOT NULL,
    month_name          VARCHAR   NOT NULL,
    day_of_month        SMALLINT  NOT NULL,
    day_of_week         SMALLINT  NOT NULL,      -- 1 = Monday
    day_name            VARCHAR   NOT NULL,
    is_weekend          BOOLEAN   NOT NULL,
    iso_week            SMALLINT  NOT NULL,
    season_iata         VARCHAR                  -- S22 = summer 2022, W22 = winter
);


-- One row per airport, keyed on the 4-letter ICAO code (LFPG, LFML...).
-- Loaded from the OurAirports CSV.
--
-- The coordinates are not typed in by hand. They are used to calculate the
-- distance between two airports, which drives every fuel number in the
-- project, so a wrong figure would quietly break the results.
CREATE TABLE IF NOT EXISTS core.dim_airport (
    airport_icao        VARCHAR   PRIMARY KEY,   -- LFPG
    airport_iata        VARCHAR,                 -- CDG
    airport_name        VARCHAR   NOT NULL,
    municipality        VARCHAR,
    iso_country         VARCHAR,                 -- FR, DE, ES...
    iso_region          VARCHAR,
    latitude_deg        DOUBLE PRECISION    NOT NULL,
    longitude_deg       DOUBLE PRECISION    NOT NULL,
    elevation_ft        INTEGER,
    airport_type        VARCHAR,                 -- large_airport, medium_airport...
    is_in_scope         BOOLEAN   DEFAULT FALSE, -- listed in config/airports_fr.yml
    scope_tier          VARCHAR,                 -- core / extended / corsica
    is_eea              BOOLEAN,                 -- needed for the EU ETS rules

    -- Sanity checks so bad coordinates cannot get in.
    CONSTRAINT chk_lat CHECK (latitude_deg BETWEEN -90 AND 90),
    CONSTRAINT chk_lon CHECK (longitude_deg BETWEEN -180 AND 180)
);


-- One row per aircraft model.
--
-- Built from the aircraft types that actually turn up in the flight list,
-- then filled in with what OpenAP knows about each one.
--
-- openap_supported matters: OpenAP only covers certain types. If a type is
-- not covered its fuel cannot be calculated, so the share of traffic
-- affected has to be known and reported.
CREATE TABLE IF NOT EXISTS core.dim_aircraft_type (
    type_icao           VARCHAR   PRIMARY KEY,   -- A320, B738, AT76...
    manufacturer        VARCHAR,
    model               VARCHAR,
    engine_type         VARCHAR,                 -- jet, turboprop
    mtow_kg             DOUBLE PRECISION,                  -- max takeoff weight
    oew_kg              DOUBLE PRECISION,                  -- empty weight
    typical_seats       INTEGER,                 -- for the per-seat metrics
    openap_supported    BOOLEAN   DEFAULT FALSE,
    openap_proxy_type   VARCHAR,                 -- similar type used instead, if any
    n_flights_seen      INTEGER                  -- how often it appears in the data
);


-- One row per actual aircraft, keyed on its transponder address (icao24).
-- Built from the flight list, which carries the registration and type.
--
-- Careful: icao24 is just what the transponder broadcasts. It can be wrong,
-- duplicated, or spoofed. The quality checks look for that.
CREATE TABLE IF NOT EXISTS core.dim_aircraft (
    icao24              VARCHAR   PRIMARY KEY,   -- lowercase hex, e.g. 3946ab
    registration        VARCHAR,                 -- F-GKXA
    type_icao           VARCHAR,
    operator_icao       VARCHAR,
    n_flights_seen      INTEGER,

    FOREIGN KEY (type_icao) REFERENCES core.dim_aircraft_type (type_icao)
);


-- One row per airline.
--
-- The flight list has no airline column, but the first three letters of the
-- callsign are the airline's ICAO code (AFR1234 = Air France). That is how
-- this table gets filled.
CREATE TABLE IF NOT EXISTS core.dim_operator (
    operator_icao       VARCHAR   PRIMARY KEY,   -- AFR
    operator_name       VARCHAR,
    country             VARCHAR,
    is_cargo            BOOLEAN   DEFAULT FALSE,
    is_low_cost         BOOLEAN   DEFAULT FALSE,
    n_flights_seen      INTEGER
);


-- The list of data quality checks. Stored as a table so the dashboard can
-- show the description next to each flag instead of just a code.
CREATE TABLE IF NOT EXISTS core.dim_quality_rule (
    rule_code           VARCHAR   PRIMARY KEY,
    rule_name           VARCHAR   NOT NULL,
    rule_family         VARCHAR   NOT NULL,     -- COMPLETENESS / IDENTITY / CONSISTENCY
    severity            VARCHAR   NOT NULL,     -- HARD or SOFT
    description         VARCHAR   NOT NULL,     -- what the check does
    rationale           VARCHAR                 -- why it means the data is bad
);


-- One row per downloaded file.
--
-- This records where every number in the database came from: the URL, when
-- it was downloaded, how big the file was and its checksum. Without it,
-- there is no way to prove months later which version of a dataset produced
-- a given result.
CREATE TABLE IF NOT EXISTS core.dim_source (
    source_id           VARCHAR   PRIMARY KEY,  -- short name, e.g. 'flightlist_202206'
    source_name         VARCHAR   NOT NULL,     -- OurAirports, OpenSky flight list...
    publisher           VARCHAR   NOT NULL,
    source_url          VARCHAR   NOT NULL,
    licence             VARCHAR,
    file_name           VARCHAR,
    file_bytes          BIGINT,
    file_sha256         VARCHAR,
    downloaded_at_utc   TIMESTAMP,
    rows_loaded         BIGINT,
    notes               VARCHAR
);


-- Reference lookups, kept permanently.
--
-- These hold the downloaded reference data itself, rather than just its
-- effect on the flights. That matters because the commands can then be run
-- in any order: loading the aircraft registry before the flights exist is
-- no longer a wasted step, because the flights pick it up when they arrive.

-- Transponder code -> aircraft, from the OpenSky registry.
CREATE TABLE IF NOT EXISTS core.ref_aircraft (
    icao24              VARCHAR   PRIMARY KEY,
    registration        VARCHAR,
    typecode            VARCHAR,
    manufacturer        VARCHAR,
    model               VARCHAR,
    operator_icao       VARCHAR
);

-- Airline code -> name, from OpenFlights.
CREATE TABLE IF NOT EXISTS core.ref_airline (
    operator_icao       VARCHAR   PRIMARY KEY,
    operator_name       VARCHAR   NOT NULL,
    country             VARCHAR,
    is_active           BOOLEAN
);
