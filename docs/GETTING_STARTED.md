# Getting started

How to run the project, and how to look at the data once it is running.

For what the project actually *is*, read [roadmap.md](roadmap.md) first.

---

## Every day: three steps

**1. Open a terminal in the project folder.**

```bash
cd /d/Projects/Flight_Energy_Performance
```

**2. Turn on the environment.** Once per terminal window.

```bash
source .venv/Scripts/activate
```

The prompt gains `(flightperf)`. In PowerShell the command is
`.venv\Scripts\Activate.ps1` instead.

**3. Check it works.**

```bash
flightperf status
```

Thirteen tables, with `fact_flight` at 87,903 rows. If that appears, the
app is running and the data is there.

---

## Without activating

Everything also works by putting `uv run` in front, from any terminal:

```bash
uv run flightperf status
```

`uv run` means "run this inside the project's own library folder". Both
ways do the same thing.

---

## The commands

| Command | What it does |
|---|---|
| `flightperf status` | Row counts for every table |
| `flightperf sources` | Where every file came from: URL, date, size, checksum |
| `flightperf init` | Create the database and its tables. Safe to rerun. |
| `flightperf ingest airports` | Airport names and coordinates, 13 MB |
| `flightperf ingest aircraft` | Aircraft registry, 61 MB. Fills in missing types. |
| `flightperf ingest airlines` | Airline names, 400 KB |
| `flightperf ingest flights --year 2022 --month 6` | One month of flights, 200 MB |
| `flightperf build quality` | Run the 15 data quality checks |
| `flightperf build fuel` | Work out fuel and CO2 |
| `flightperf build validate` | Check the fuel model against published figures |
| `flightperf build kpi` | Work out the costs. Needs prices in params.yml first. |

---

## Rebuilding from scratch

Only needed after changing the code, or to load a different month. Takes
about five minutes; the downloads are cached so later runs are quicker.

```bash
flightperf init
flightperf ingest airports
flightperf ingest aircraft
flightperf ingest airlines
flightperf ingest flights --year 2022 --month 6
flightperf build quality
flightperf build fuel
```

The four `ingest` commands can run in any order - the reference data is
stored, so loading the aircraft registry before the flights is not wasted.

The flight list covers January 2019 to December 2022, so any month in that
range works.

---

## Looking at the data

### The easy way: pgAdmin

PostgreSQL installed a browser for the database. Search the Start menu for
**pgAdmin 4** and connect with:

| | |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Username | `postgres` |
| Password | `postgres` |

Then open **flightperf → Schemas → core → Tables**.

### The useful way: write SQL

Start with `core.v_flight_kpi`. It is a view with everything joined
together, one row per flight, all the KPIs worked out.

**Which routes produce the most CO2**

```sql
SELECT origin_city, destination_city, count(*) AS flights,
       round(sum(co2_tonnes)::numeric) AS co2_tonnes
FROM core.v_flight_kpi
WHERE co2_kg IS NOT NULL
GROUP BY 1, 2
ORDER BY co2_tonnes DESC
LIMIT 20;
```

**Which airlines**

```sql
SELECT coalesce(operator_name, operator_icao) AS airline,
       count(*) AS flights,
       round(sum(co2_tonnes)::numeric) AS co2_tonnes
FROM core.v_flight_kpi
WHERE co2_kg IS NOT NULL AND operator_icao IS NOT NULL
GROUP BY 1
ORDER BY co2_tonnes DESC
LIMIT 15;
```

**Which aircraft are most efficient on short routes**

Per seat-kilometre, so a big aircraft is not automatically worse.

```sql
SELECT type_icao, typical_seats, count(*) AS flights,
       round(avg(co2_kg_per_ask)::numeric, 4) AS co2_kg_per_seat_km
FROM core.v_flight_kpi
WHERE co2_kg_per_ask IS NOT NULL
  AND great_circle_nm < 600
GROUP BY 1, 2
HAVING count(*) > 300
ORDER BY 4;
```

**What the quality checks threw out, and why**

```sql
SELECT r.rule_name, q.severity, count(*) AS flights
FROM core.fact_flight_quality q
JOIN core.dim_quality_rule r ON r.rule_code = q.rule_code
GROUP BY 1, 2
ORDER BY 3 DESC;
```

**French domestic flights only**

```sql
SELECT count(*) AS flights,
       round(sum(co2_tonnes)::numeric) AS co2_tonnes,
       round(avg(great_circle_nm)::numeric) AS avg_distance_nm
FROM core.v_flight_kpi
WHERE is_domestic_fr AND co2_kg IS NOT NULL;
```

**How many aircraft types had to be looked up rather than reported**

```sql
SELECT type_source, count(*)
FROM core.fact_flight
GROUP BY 1;
```

---

## When something goes wrong

| Message | What it means |
|---|---|
| `PostgreSQL is not answering on localhost:5432` | The database service is not running. Open Services and start `postgresql-x64-17`. |
| `command not found: flightperf` | The environment is not activated. Run the `source` command, or put `uv run` in front. |
| `password authentication failed` | `.env` needs `PGPASSWORD=postgres`. |
| `build kpi` says "Not ready yet" | Expected. It needs the carbon price, fuel price and route-extension factor filling into `config/params.yml`. |
| `No airports loaded yet` | Run `flightperf ingest airports` first. |

---

## Running the tests

```bash
uv run pytest -q
```

Thirteen tests, mostly on the fuel model. They do not need the database.

## Rebuilding the reference document

```bash
uv run python scripts/update_summary.py
```

Rewrites `files/Project_Summary.docx`, reading the current figures straight
out of the database.
