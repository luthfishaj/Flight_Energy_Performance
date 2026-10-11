-- Fact tables. These hold the numbers.
--
-- One row per flight. The source data (the OpenSky flight list) is already
-- one row per flight, so there is no trajectory table and no need to
-- reconstruct flights from raw position reports.
--
-- What that means in practice: the distance used is the great-circle
-- distance between the two airports, not the path the aircraft actually
-- flew. Real aircraft fly further than that, so every fuel figure here is
-- an underestimate. That limitation is stated wherever the numbers appear.


-- One row per flight.
CREATE TABLE IF NOT EXISTS core.fact_flight (
    flight_id               VARCHAR   PRIMARY KEY,  -- built from icao24 + first_seen
    icao24                  VARCHAR   NOT NULL,     -- transponder code
    callsign                VARCHAR,                -- AFR1234
    flight_number           VARCHAR,                -- AF1234, where the source has it
    registration            VARCHAR,                -- F-GKXA
    operator_icao           VARCHAR,                -- first 3 letters of the callsign
    type_icao               VARCHAR,                -- A320, B738...

    -- Where the aircraft type came from. The source file leaves it blank on
    -- a lot of rows, so it gets filled in afterwards by looking the
    -- transponder code up in the aircraft registry. Recording which is
    -- which means a fuel figure can always be traced back to whether the
    -- aircraft type was observed or inferred.
    type_source             VARCHAR,                -- 'reported' | 'registry'

    origin_icao             VARCHAR,
    destination_icao        VARCHAR,
    date_key                INTEGER   NOT NULL,     -- day the flight started (UTC)

    first_seen_utc          TIMESTAMP NOT NULL,
    last_seen_utc           TIMESTAMP NOT NULL,
    duration_s              INTEGER,

    -- Where the aircraft was first and last picked up by a receiver. Not a
    -- full flight path, just the two endpoints. Used to check that the
    -- stated origin and destination airports are plausible.
    first_lat               DOUBLE PRECISION,
    first_lon               DOUBLE PRECISION,
    last_lat                DOUBLE PRECISION,
    last_lon                DOUBLE PRECISION,

    -- Straight-line distance between origin and destination.
    great_circle_nm         DOUBLE PRECISION,

    -- Fuel and emissions from the OpenAP model.
    -- These stay NULL when the flight could not be modelled (unknown
    -- aircraft type, or it failed the quality checks). NULL is not the same
    -- as zero and the two must not get mixed up later.
    fuel_burn_kg            DOUBLE PRECISION,
    co2_kg                  DOUBLE PRECISION,
    nox_kg                  DOUBLE PRECISION,

    -- Corrected for the extra distance real flights fly compared with the
    -- straight line, and priced. Written by the cost step, not the fuel
    -- step: fuel_burn_kg stays as the raw model output so the size of the
    -- correction stays visible.
    fuel_burn_adjusted_kg   DOUBLE PRECISION,
    co2_adjusted_kg         DOUBLE PRECISION,
    fuel_cost_eur           DOUBLE PRECISION,
    ets_cost_eur            DOUBLE PRECISION,

    -- Flags used by the cost calculations.
    is_domestic_fr          BOOLEAN,
    is_intra_eea            BOOLEAN,
    ets_scope_factor        DOUBLE PRECISION,                 -- how much of the CO2 the EU ETS covers

    -- Filled in by the quality step.
    quality_score           DOUBLE PRECISION,                 -- between 0 and 1
    quality_status          VARCHAR,                -- PASS / SOFT_FAIL / HARD_FAIL
    is_analysis_eligible    BOOLEAN   DEFAULT FALSE,

    source_id               VARCHAR,                -- which file this row came from
    ingested_at_utc         TIMESTAMP DEFAULT now(),

    FOREIGN KEY (date_key)  REFERENCES core.dim_date (date_key),
    FOREIGN KEY (source_id) REFERENCES core.dim_source (source_id),

    CONSTRAINT chk_times CHECK (last_seen_utc >= first_seen_utc),
    CONSTRAINT chk_fuel  CHECK (fuel_burn_kg IS NULL OR fuel_burn_kg >= 0)
);


-- One row per problem found, per flight.
--
-- Stored long like this instead of adding a dozen true/false columns to
-- fact_flight. That way the Power BI page can filter by rule, and adding a
-- new check does not mean rebuilding the table.
CREATE TABLE IF NOT EXISTS core.fact_flight_quality (
    flight_id               VARCHAR   NOT NULL,
    rule_code               VARCHAR   NOT NULL,
    severity                VARCHAR   NOT NULL,     -- HARD or SOFT
    observed_value          DOUBLE PRECISION,                 -- what was measured
    threshold_value         DOUBLE PRECISION,                 -- what the limit was
    detail                  VARCHAR,

    PRIMARY KEY (flight_id, rule_code),
    FOREIGN KEY (flight_id) REFERENCES core.fact_flight (flight_id),
    FOREIGN KEY (rule_code) REFERENCES core.dim_quality_rule (rule_code)
);
