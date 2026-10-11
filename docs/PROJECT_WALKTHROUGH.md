# Project walkthrough

Everything in the project, explained: every column, every SQL file, every
Python module, and the reasoning behind the decisions.

This is the document to study before an interview.

---

## How to use this

Interviewers rarely ask *what* you built. They ask **why** you built it that
way, and what you would change. A candidate who says "I used a star schema"
and a candidate who says "I used a star schema because Power BI expects one
and it stops the airport name being repeated 87,903 times" are treated very
differently.

So this document is organised around reasons, not just descriptions. The
sections that matter most for an interview are **Part 5 (decisions)**,
**Part 6 (problems)** and **Part 7 (likely questions)**.

Anything you cannot explain, go and read the code for. Every file has
comments saying why it does what it does.

---

## The 60-second version

> Aircraft broadcast their position, and volunteers record those signals.
> OpenSky publishes the result as a monthly file with one row per flight -
> where it went, and what aircraft it was. What that file doesn't say is
> how much fuel was burned.
>
> I built a pipeline that fills that in. It loads a month of flights into
> PostgreSQL, runs fifteen data quality checks, models the fuel burn using
> an open aircraft performance model, converts that to CO2, and prices it
> under the EU carbon rules.
>
> The interesting part was the data quality. Only half the flights were
> usable at first, mostly because 37% had no aircraft type recorded. Every
> flight does carry a transponder code, so I joined an external aircraft
> registry to look the missing types up - that recovered 99% of them and
> took usable data from 50% to 78%.
>
> I also validated the fuel model against published cruise-burn figures.
> Seven of the eight aircraft types I checked agree within 15%. The Boeing
> 777-300ER comes out 31% high, which I report rather than hide, because
> long-haul flights dominate the emissions total.

That last paragraph is the one that separates you from other candidates.
Most people present a model. Few present its error bars.

---

# Part 1: The raw data

Four files. Two carry the data, two are reference lookups.

## 1.1 The flight list — the main data

`flightlist_20220601_20220630.csv.gz`, from OpenSky via Zenodo. One row per
flight, 3.17 million rows worldwide for June 2022.

| Column | What it is | Notes |
|---|---|---|
| `callsign` | Radio callsign, e.g. `AFR1234` | First three letters are the airline |
| `number` | Commercial flight number, e.g. `AF1234` | Often blank |
| `icao24` | Transponder code, six hex characters | Identifies the airframe |
| `registration` | Tail number, e.g. `F-GKXA` | `F-` means France |
| `typecode` | Aircraft model, e.g. `A320` | **Blank on 37% of rows** |
| `origin` | Departure airport, ICAO code | **Inferred, not scheduled** |
| `destination` | Arrival airport, ICAO code | Inferred, blank on 9.5% |
| `firstseen` | First moment a receiver saw it, UTC | |
| `lastseen` | Last moment a receiver saw it, UTC | |
| `day` | The date the flight belongs to | |
| `latitude_1`, `longitude_1` | Where it was first seen | Not the airport - just first contact |
| `latitude_2`, `longitude_2` | Where it was last seen | |
| `altitude_1`, `altitude_2` | Altitude at those points | Not used |

**The critical thing to understand about this file:** the airports are not
taken from airline schedules. OpenSky works them out by matching the start
and end of each track to the nearest airport. Where receiver coverage is
thin, that fails - which is why 11% have no departure airport at all.

There is also **no flight path**, only those two endpoints. That is why
distances in this project are straight lines.

## 1.2 Airports

`airports.csv` from OurAirports, public domain, about 80,000 rows.

| Column used | What it is |
|---|---|
| `ident` | The four-letter ICAO code, e.g. `LFPG` |
| `iata_code` | The three-letter code passengers see, e.g. `CDG` |
| `name` | Airport name |
| `latitude_deg`, `longitude_deg` | **Every distance in the project comes from these** |
| `elevation_ft` | Height above sea level |
| `iso_country` | Two-letter country, drives the EU carbon scope |
| `municipality` | Nearest town |
| `type` | `large_airport`, `heliport`, etc - used to filter |

Only 17,109 of the 80,000 are kept: those with a real four-letter ICAO code
and a genuine airport type. The rest are heliports, seaplane bases and
closed airfields that never appear in the flight data.

## 1.3 Aircraft registry

`aircraft-database-complete-2024-01.csv` from OpenSky, 579,435 aircraft.
**This is the file that fixed the project's biggest problem.**

Columns are read **by position**, because the header row in the file is
completely blank.

| Position | Column | Used? |
|---|---|---|
| 1 | `icao24` | Yes - the join key |
| 2 | `registration` | Yes |
| 4 | `manufacturer_name` | Yes |
| 5 | `model` | Yes |
| 6 | `typecode` | **Yes - this is the whole point** |
| 12 | `operator_icao` | Yes |
| others | serial number, line number, owner... | No |

483,017 rows have both a transponder code and a type, which is what makes
them usable.

## 1.4 Airlines

`airlines.dat` from OpenFlights, 5,774 airlines. No header row; missing
values are written as the two characters `\N`.

| Position | Column |
|---|---|
| 2 | Airline name |
| 4 | IATA code |
| 5 | **ICAO code - the join key** |
| 7 | Country |
| 8 | Active `Y`/`N` |

---

# Part 2: The database

Thirteen objects: twelve tables and one view. This is a **star schema** -
one central fact table surrounded by dimension tables that describe it.

## 2.1 `fact_flight` — the centre of everything

**One row per flight.** 87,903 rows. This is the table everything else
describes.

### Identity

| Column | Meaning |
|---|---|
| `flight_id` | Primary key. An MD5 hash of `icao24` + start time. One aircraft can only begin one flight at a given second, so that pair is unique. |
| `icao24` | Transponder code |
| `callsign` | e.g. `AFR1234` |
| `flight_number` | e.g. `AF1234`, often blank |
| `registration` | Tail number |
| `operator_icao` | Airline code, taken from the first three letters of the callsign |
| `type_icao` | Aircraft type, e.g. `A320` |
| `type_source` | **`reported` or `registry`** - whether the type came with the flight data or was looked up afterwards |

`type_source` is worth calling out in an interview. It means any fuel figure
can be traced back to whether the aircraft type was *observed* or *inferred*.
That is called data lineage, and it is the difference between a number you
can defend and one you cannot.

### Where and when

| Column | Meaning |
|---|---|
| `origin_icao`, `destination_icao` | The two airports |
| `date_key` | Day the flight started, as `20220614` - joins to `dim_date` |
| `first_seen_utc`, `last_seen_utc` | Start and end |
| `duration_s` | Seconds between them |
| `first_lat`, `first_lon` | Where it was first picked up |
| `last_lat`, `last_lon` | Where it was last picked up |
| `great_circle_nm` | Straight-line distance between the airports, in nautical miles |

The first/last positions exist for one reason: the quality checks use them
to test whether the stated airport is plausible. If a flight is "from Paris"
but was first seen 400 miles away, that airport was a guess.

### The results

| Column | Meaning |
|---|---|
| `fuel_burn_kg` | Modelled fuel, airborne only |
| `co2_kg` | Fuel × 3.16 |
| `nox_kg` | Reserved, not yet populated |

**These stay NULL when a flight could not be modelled.** Not zero. NULL
means "unknown"; zero means "burned no fuel". Mixing them up would drag
every average down and nobody would notice.

### Regulatory flags

| Column | Meaning |
|---|---|
| `is_domestic_fr` | Both airports in France |
| `is_intra_eea` | Both in the European Economic Area |
| `ets_scope_factor` | How much of this flight's CO2 the EU carbon market covers, 0 to 1 |

### Quality

| Column | Meaning |
|---|---|
| `quality_score` | 0 to 1. Starts at 1; each soft failure costs 0.15; any hard failure sets it to 0. |
| `quality_status` | `PASS`, `SOFT_FAIL` or `HARD_FAIL` |
| `is_analysis_eligible` | The single flag everything downstream filters on |

### Provenance

| Column | Meaning |
|---|---|
| `source_id` | Which downloaded file this row came from |
| `ingested_at_utc` | When it was loaded |

## 2.2 `fact_flight_quality` — one row per problem found

21,224 rows. **Long format, not wide.**

| Column | Meaning |
|---|---|
| `flight_id` | Which flight |
| `rule_code` | Which check it failed |
| `severity` | `HARD` or `SOFT` |
| `observed_value` | What was measured, e.g. `64,800` seconds |
| `threshold_value` | What the limit was |
| `detail` | Human-readable, e.g. "lasted 19.4 hours" |

**Why long and not fifteen boolean columns on `fact_flight`:** adding a
sixteenth check would mean altering the table and rebuilding every chart.
This way it is one new query. Storing the observed value alongside the
threshold means you can later ask *how badly* something failed, which a
true/false flag throws away.

## 2.3 The dimension tables

### `dim_airport` — 17,109 rows

`airport_icao` (key), `airport_iata`, `airport_name`, `municipality`,
`iso_country`, `iso_region`, `latitude_deg`, `longitude_deg`,
`elevation_ft`, `airport_type`, `is_in_scope`, `scope_tier`, `is_eea`.

`is_in_scope` and `scope_tier` mark the 26 French airports the project
covers, set from `config/airports_fr.yml`. `is_eea` drives the carbon scope.

### `dim_aircraft_type` — 260 rows

`type_icao` (key), `manufacturer`, `model`, `engine_type`, `mtow_kg`,
`oew_kg`, `typical_seats`, `openap_supported`, `openap_proxy_type`,
`n_flights_seen`.

- `mtow_kg` - maximum takeoff weight, the ceiling on how much fuel can be carried
- `oew_kg` - operating empty weight, the aircraft with nothing in it
- `typical_seats` - drives every per-seat metric
- `openap_supported` - whether fuel can be modelled at all. **53 of 260 types, but those 53 cover 88% of flights.**
- `openap_proxy_type` - where a similar aircraft was substituted, e.g. a 737-500 modelled as a 737-400

### `dim_aircraft` — 6,134 rows

`icao24` (key), `registration`, `type_icao`, `operator_icao`,
`n_flights_seen`. One row per physical airframe seen in the data.

### `dim_operator` — 451 rows

`operator_icao` (key), `operator_name`, `country`, `is_cargo`,
`is_low_cost`, `n_flights_seen`.

`is_cargo` matters because a freight airline carries no passengers, so every
per-seat metric is meaningless for it.

### `dim_date` — 1,461 rows

`date_key` (key, as `20220614`), `full_date`, `year`, `quarter`, `month`,
`month_name`, `day_of_month`, `day_of_week`, `day_name`, `is_weekend`,
`iso_week`, `season_iata`.

**Why a date table exists at all:** Power BI needs a real one for its date
filtering to work. It also lets you group by things that are not in the
date itself, like the airline scheduling season (`S22` for summer 2022).

### `dim_quality_rule` — 15 rows

`rule_code` (key), `rule_name`, `rule_family`, `severity`, `description`,
`rationale`.

The checks are stored **as data, not code**, so the dashboard can display
"Flight is too long - normally means two flights got merged into one" next
to a flag, instead of `CON_DURATION_LONG`.

### `dim_source` — 4 rows

`source_id` (key), `source_name`, `publisher`, `source_url`, `licence`,
`file_name`, `file_bytes`, `file_sha256`, `downloaded_at_utc`,
`rows_loaded`, `notes`.

**This table is a good thing to mention unprompted.** It records the URL,
date, size and SHA-256 checksum of every file used, so months later you can
prove exactly which version of a dataset produced a given number.

## 2.4 The reference and working tables

### `ref_aircraft` — 483,017 rows
### `ref_airline` — 5,774 rows

The downloaded reference data, kept permanently rather than applied once
and thrown away. **This is what makes the ingest commands order-independent**
- see Part 6.

### `fuel_curve` — 1,272 rows

`type_icao`, `distance_nm`, `fuel_kg`, `airborne_s`.

Fuel against distance for each of the 53 modellable aircraft types, at 24
distances from 50 to 9,000 nautical miles. Each flight is read off this
curve by interpolation rather than calling the physics model 60,000 times.

Storing it in the database rather than holding it in memory means the
model's output can be inspected directly. Somebody can query it and see
exactly what the model claims an A320 burns over 300 miles.

## 2.5 `v_flight_kpi` — the view

48 columns, one row per flight, everything joined together. **This is what
Power BI connects to.**

The columns beyond `fact_flight` are:

| Column | How it is worked out |
|---|---|
| `route_key` | `LFBO-LFPO`, alphabetically sorted so both directions group together |
| `great_circle_km` | Nautical miles × 1.852 |
| `duration_minutes` | Seconds ÷ 60 |
| `avg_speed_kt` | Distance ÷ time |
| `co2_tonnes` | kg ÷ 1000 |
| `fuel_kg_per_nm` | Fuel ÷ distance |
| `fuel_kg_per_ask` | Fuel ÷ (seats × distance in km) |
| `co2_kg_per_ask` | Same with CO2 |
| `ets_chargeable_tonnes` | CO2 tonnes × scope factor |

Plus the airport names, city names, countries, airline name and
manufacturer pulled in from the dimension tables.

**ASK means Available Seat-Kilometre.** It is the standard way airlines
compare efficiency, because a big aircraft naturally burns more in total -
what matters is fuel per seat carried one kilometre.

**The honest caveat, which you should volunteer:** this uses the number of
seats the aircraft *has*, not how many were sold. The source data has no
passenger counts. So it measures the efficiency of capacity offered, not of
passengers carried.

---

# Part 3: The SQL files

795 lines across seven files, in two groups.

## 3.1 Structure — run once, by `flightperf init`

### `sql/01_schema/001_dimensions.sql` (175 lines)

`CREATE SCHEMA core`, then all seven dimension tables plus the two
reference tables. Check constraints live here too - for example, latitude
must be between -90 and 90, so a corrupt coordinate cannot get in.

### `sql/01_schema/002_facts.sql` (94 lines)

`fact_flight` and `fact_flight_quality`. Foreign keys tie flights to dates
and to their source file; check constraints enforce that a flight cannot
end before it starts and fuel cannot be negative.

### `sql/01_schema/003_functions.sql` (35 lines)

One function: `core.haversine_nm(lat1, lon1, lat2, lon2)`, the distance
between two points on the earth.

It was written out by hand in the flight loader first. Pulling it into a
function means the same formula is used everywhere - the loader and three
quality checks all call it. Marked `IMMUTABLE` so PostgreSQL knows the
answer depends only on the inputs and can optimise accordingly.

### `sql/03_marts/001_v_flight_kpi.sql` (108 lines)

The view. **Every KPI formula lives here.**

Why a view rather than DAX inside the Power BI file: a formula buried in a
`.pbix` binary cannot be read, reviewed, or compared between versions. In a
`.sql` file it is all three.

Note the `JOIN` on `dim_date` but `LEFT JOIN` on everything else: better to
keep a flight with an unknown airline than silently drop it from every count.

### `sql/04_quality/001_rule_catalogue.sql` (94 lines)

The 15 checks as `INSERT` statements - the rule code, name, family,
severity, what it does and why it matters.

## 3.2 Processing — run every time, by `flightperf build quality`

These live in `sql/pipeline/` specifically so `init` cannot run them by
accident.

### `sql/pipeline/010_quality_checks.sql` (247 lines)

The 15 checks themselves, separated by `-- >>> RULE_CODE` marker lines.
Python splits the file on those markers and runs each check separately,
because PostgreSQL will not accept several statements in one call when
parameters are involved.

Each one is the same shape:

```sql
INSERT INTO core.fact_flight_quality (flight_id, rule_code, severity, detail)
SELECT flight_id, 'COM_NO_TYPE', 'HARD', 'typecode is blank in the source file'
FROM core.fact_flight
WHERE type_icao IS NULL;
```

The interesting ones are the two overlap checks, which self-join the table
to find one aircraft apparently in two places at once:

```sql
FROM core.fact_flight a
JOIN core.fact_flight b
  ON b.icao24 = a.icao24
 AND a.flight_id < b.flight_id          -- stops each pair matching twice
 AND a.first_seen_utc < b.last_seen_utc -- the standard overlap test
 AND b.first_seen_utc < a.last_seen_utc
```

### `sql/pipeline/011_quality_scoring.sql` (42 lines)

One `UPDATE` that turns the individual failures into a verdict per flight,
using `count(*) FILTER (WHERE severity = 'HARD')` to count each kind.

---

# Part 4: The Python files

About 2,600 lines of pipeline code, plus 1,320 for the document generator. Most of it is plumbing - the analysis is in the SQL.

## 4.1 Core

### `config.py` (147 lines)

Loads `config/*.yml` and `.env`. Two things worth knowing:

**`require()` refuses to return an empty value.** The prices start as
`null`, and rather than defaulting to zero it raises. A missing carbon
price treated as zero produces a confident, wrong answer with no warning -
the worst thing this project could do.

**`server_is_reachable()`** opens a plain socket before trying to connect
properly. Without it, a connection to a database that is not running can
hang for a long time instead of failing quickly.

### `db.py` (116 lines)

Connecting, creating the database, and running the schema files in order.
`migrate()` is safe to run repeatedly because every statement is
`CREATE IF NOT EXISTS`, `CREATE OR REPLACE`, or an insert that ignores
duplicates.

### `seed.py` (57 lines)

Fills `dim_date` using `generate_series`. The one dimension that is
generated rather than downloaded.

### `cli.py` (435 lines)

The command line interface, built with Typer. Every command lives here,
and every one follows the same pattern: open the database, do the work,
close it, print a table of results.

## 4.2 Loading data

### `ingest/download.py` (117 lines)

Downloads a file and records where it came from. Three details worth
mentioning:

- Streams in 1 MB chunks, so a 200 MB file never sits in memory
- Writes to `filename.part` and renames at the end, so an interrupted download cannot leave a half-file that looks complete
- Computes a SHA-256 checksum and stores it in `dim_source`

### `ingest/airports.py` (169 lines)

Loads the airport CSV into a staging table with every column as text, then
filters and types it on the way into `dim_airport`.

**Why staging as text:** one malformed value cannot stop an 80,000-row file
from loading. The typing happens afterwards, where a bad row can be spotted
and dropped rather than killing the whole load.

It also warns if an airport listed in the config is missing from the
reference data - otherwise every flight to it would silently vanish later.

### `ingest/flights.py` (302 lines)

The biggest loader. Streams the gzip straight into PostgreSQL with `COPY` -
Python unzips a block at a time and passes it on, so the uncompressed
gigabyte never exists as a file.

Then one large SQL statement filters to French airports, deduplicates,
computes distances and inserts. The staging table is `UNLOGGED`, which tells
PostgreSQL not to write it to the write-ahead log - it is thrown away at the
end, so durability does not matter, and it loads noticeably faster.

### `ingest/aircraft.py` (141 lines)

The registry loader. Columns declared by position because the header row is
blank.

### `ingest/airlines.py` (136 lines)

Airline names. Small and straightforward.

### `ingest/enrich.py` (135 lines)

Shared logic called from two places: after loading reference data (to fix
flights already there) and after loading flights (to pick up reference data
already there). **This is what makes the commands order-independent.**

## 4.3 The fuel model

### `fuel/model.py` (263 lines)

The physics. For a given aircraft type and distance:

1. Look the type up in OpenAP - weights, cruise altitude, cruise speed, seats
2. Build a flight profile: climb at 1,800 ft/min, cruise, descend at 1,500 ft/min
3. Integrate fuel flow along it in 20 steps per phase, subtracting fuel as it burns
4. Repeat the whole calculation until the answer stops moving

**Why step 4 exists:** the weight depends on how much fuel is loaded, and
the fuel needed depends on the weight. That is circular. Two passes are
enough for a short flight where fuel is 5% of the weight; on a long sector
fuel can be a third of the takeoff mass and the first pass is badly wrong.

**What it deliberately leaves out:** taxi fuel, because OpenAP has no idle
model and inventing a number would be worse than leaving it out.

### `fuel/run.py` (275 lines)

Applies the model to the database. Builds the curve per type, then reads
each flight off it with linear interpolation in SQL.

Also contains `validate()`, which compares modelled cruise burn against the
published figures in `config/validation_reference.yml`.

## 4.4 Quality and costs

### `quality/checks.py` (142 lines)

Splits the checks SQL on its markers, runs each with the thresholds from
`params.yml`, then applies the scoring.

It also **cross-checks the catalogue against the code**: every rule defined
must have a check written, and every check must have a rule defined. A
mismatch raises. That is easy to get wrong and would otherwise go unnoticed.

### `kpi/costs.py` (158 lines)

Turns CO2 into money. Lists exactly which settings are missing rather than
running with defaults.

## 4.5 Supporting

### `scripts/update_summary.py` (1,320 lines)

Generates the Word reference document, reading the live figures straight
out of the database so it cannot go stale.

### `tests/` — 13 tests

Mostly on the fuel model: that a short flight burns a plausible amount,
that fuel per mile falls with distance, that the curve is monotonic (or
interpolation would be unsafe), and that takeoff weight never exceeds the
aircraft's maximum.

---

# Part 5: The decisions, and why

These are the questions an interviewer will actually ask.

## Why aviation and not something else?

The analysis applies to any moving asset that burns fuel and is tracked.
Aviation was chosen because the open data is far better - there is a public
file with one row per flight, which does not exist for most industries.

## Why only four data sources?

The first plan had seven, three of them behind a registration or an access
application. It was cut to four plain downloads that need no account.

**A finished narrow project beats an unfinished broad one.** Cutting scope
is a decision worth defending, not apologising for.

## Why PostgreSQL rather than a file-based database?

It started on DuckDB, which is a single file and needs no install. Moving
to PostgreSQL bought three things: it is what job adverts ask for, it is a
real server like the ones used at work, and Power BI connects to it
directly - which removed an entire step of exporting files for the
dashboard to read.

## Why is the logic in SQL rather than pandas?

Set-based work belongs in SQL: one statement computes the distance for all
87,903 flights. It is also readable, reviewable and version-controlled as
plain text. Python does the things SQL cannot - downloading files, and the
fuel physics.

## Why a star schema?

It stops the airport name being repeated on every flight row, it is what
Power BI expects, and it makes the grain explicit. Being clear that one row
of `fact_flight` means one flight is what stops numbers being double
counted later.

## Why keep failing flights instead of deleting them?

Because then you can count them. A pipeline that quietly drops rows cannot
tell you how many it dropped, and "how do you know your numbers are right"
is the first question anyone sensible asks.

## Why are the prices left empty?

So that the code fails loudly rather than treating a missing carbon price
as zero. Every assumption lives in `config/params.yml` where it can be
checked in one place, and anything unverified is `null` on purpose.

---

# Part 6: Problems hit, and how they were solved

**This section is interview gold.** Nobody believes a project that went
smoothly.

## `~` is not a regex operator in DuckDB

The airline is taken from the first three letters of the callsign, matched
with a regular expression. On DuckDB the `~` operator matched 319 rows
where `regexp_matches()` matched 83,752 - so the airline column came out
almost entirely empty, silently.

**Found by:** noticing that the top airlines looked implausible. The
busiest carrier at a French airport should be Air France, and it was not
in the list.

**Lesson:** sanity-check results against what you already know about the
world.

## `least()` and `greatest()` skip NULLs

The route label sorts the two airport codes alphabetically so that both
directions group together. But those functions ignore NULLs rather than
returning one, so a flight with a missing origin came out as the nonsense
route `LFPG-LFPG`.

**Fixed by** only building a route when both ends are known.

## The source file has duplicate rows

491 exactly duplicated rows out of 3.17 million - same aircraft, same
start time, every column identical. Found because they broke the primary
key.

**Fixed by** deduplicating on load, keeping the most complete row where
duplicates are not identical.

## The fuel model did not converge

Weight depends on fuel, fuel depends on weight. Two passes were enough for
short flights but badly wrong for long ones, where fuel is a third of the
takeoff mass.

**Fixed by** looping until the answer changes by less than 0.1%.

## The reserve fuel model was wrong in both directions

A flat 20% of trip fuel is far too much on a twelve-hour sector and too
little on a short hop.

**Fixed by** modelling it the way flight planning actually does: 5% of the
trip for contingency, plus 45 minutes of flying for diversion and holding.
That self-scales.

## The ingest commands were order-dependent

Loading the aircraft registry before the flights silently did nothing,
because there was nothing yet to fix.

**Fixed by** keeping the reference data in its own tables rather than
applying it once and throwing it away. Verified by rebuilding from scratch
in the order that used to fail.

## Two libraries had broken Windows builds

`psycopg` 3.3 and `scipy` 1.18 both ship Windows wheels whose DLLs fail to
load, with the unhelpful message "The operation completed successfully".

**Fixed by** pinning both below the broken version - **and writing the
reason in a comment next to the pin**, so nobody removes it later and
wonders why everything broke.

---

# Part 7: Questions you should expect

**"Walk me through the project."**
Use the 60-second version at the top.

**"What was the hardest part?"**
The data quality. Half the flights were unusable at first. Working out
*why*, and that most of it was fixable by joining a registry rather than
being a lost cause, was the difference between a real result and a toy.

**"How do you know your fuel numbers are right?"**
I do not know they are right, I know how wrong they are. I compared
modelled cruise burn against published figures for eight aircraft types.
Seven agree within 15%. The 777-300ER is 31% high, which matters because
long-haul dominates the total, so the headline CO2 figure is probably
somewhat overstated.

**"What would you do differently?"**
Get the aircraft registry in from the start - I spent effort on a data
quality problem that turned out to have a lookup-table answer. And I would
find a better performance reference for widebodies.

**"Why not use machine learning?"**
There was a candidate problem - predicting the 37% of missing aircraft
types. But the answer was knowable rather than predictable: every flight
carries a transponder code, and a public registry maps those to aircraft.
Looking something up beats predicting it. Prediction is for what you
genuinely cannot know.

**"What are the limitations?"**
Six, all documented.

1. One month of traffic - June 2022 - so it is a snapshot, not a trend.
2. Distances are straight lines, corrected upward by 2.96% from
   EUROCONTROL's 2022 en-route efficiency figure. That covers the en-route
   phase only, so the full extension is still understated.
3. Taxi fuel is not counted, because the performance model has no idle
   model and inventing a number would be worse.
4. Per-seat figures use installed seats, not passengers, because the data
   has no passenger counts.
5. The carbon figure is the market value of the emissions, not cash paid -
   free allocation is deliberately ignored, and in 2022 airlines still
   received a large share of allowances free.
6. The Boeing 777-300ER is modelled 31% above published cruise burn, which
   pushes the long-haul total up.

**"How would you scale this?"**
The pipeline already handles any month from 2019 to 2022 - it is one
command. A full year is about a million flights, which PostgreSQL handles
without changes. Beyond that I would partition `fact_flight` by month and
index the join columns.

**"What would you add next?"**
The real flight paths, from OpenSky's historical archive. That would
replace straight-line distances with actual routes and let me measure
routing inefficiency directly rather than applying a correction factor.

---

# Part 8: What is still to do

| Task | Blocked on |
|---|---|
| Fill in the carbon price, fuel price, route-extension factor | Research - each needs a source and a date |
| Run `build kpi` | The above |
| Build the Power BI dashboard | The above |
| Write up one quantified finding | The dashboard |
| Load June 2019 for a pre-COVID comparison | Nothing - one command |

---

# One last thing

The strongest position in an interview is being able to defend every
choice, including the ones that turned out badly. That requires actually
understanding the code, not just remembering this document.

So read the source files. Every one has comments explaining why it does
what it does, and the `why` is what you will be asked about. If you find
something in the code that this document does not explain, that is the
first thing to go and understand.
