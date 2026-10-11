# Roadmap

The plain-English version of what this project is and where it has got to.
Updated as the work moves forward.

For the detailed reference — every table, column, data source and quality
check — see `files/Project_Summary.docx`.

---

## The question

*Which flights in and out of France burn the most fuel, how much CO2 is
that, and what does it cost the airlines under Europe's carbon rules?*

---

## The data

Planes constantly broadcast where they are. Volunteers around the world
record those signals, and OpenSky bundles them into a monthly file — **one
row per flight**.

Each row says: this plane flew from Toulouse to Paris, at this time, and it
was an Airbus A320.

What it does **not** say is how much fuel that took. That is the gap this
project fills.

---

## The five steps

**1. Get the data.** Four files, all free downloads with no account needed.
One lists every airport in the world with its coordinates, one lists every
flight for a month, one maps transponder codes to aircraft types, and one
turns airline codes into names.

**2. Work out distances.** We know where each airport is, so we calculate
the distance between them. Toulouse to Paris is 309 nautical miles.

**3. Work out the fuel.** Given an aircraft type and a distance, an
open-source model called OpenAP says roughly how much fuel that burns.
Multiply fuel by 3.16 and you have CO2 — that part is just chemistry.

**4. Put a price on it.** Airlines in Europe have to buy a permit for every
tonne of CO2 they emit. So CO2 × carbon price = a real cost per flight.

**5. Show it.** A Power BI dashboard: which routes, which airlines, which
aircraft, ranked by fuel, emissions and cost.

---

## The catch, which is the interesting part

The data is messy. Nobody typed it in — it was worked out from radio
signals. In the June 2022 sample:

- 37% of flights do not say what aircraft it was
- 11% do not say where they took off from
- Some rows are exact duplicates

That cannot just be ignored. So before calculating anything, a set of checks
runs over every flight, marks it trustworthy or not, and **reports how many
had to be thrown out and why**.

That honesty is worth more than a clean-looking chart. Real data always
looks like this, and being able to say what you excluded and why is the part
employers actually care about.

---

## The tools, and why

| Tool | Job |
|---|---|
| **PostgreSQL** | Stores the data and does the calculations. A real database server, the kind used at work. |
| **SQL** | Where the actual logic lives — distances, the KPIs, the quality checks |
| **Python** | Downloads the files and runs the fuel physics, which SQL cannot do |
| **Power BI** | The dashboard people look at. Connects straight to PostgreSQL. |

---

## Where we are

| Part | Status |
|---|---|
| Database design | Done |
| Airport loader | Done — 17,109 airports |
| Flight loader | Done — 87,903 June 2022 flights |
| Aircraft registry | Done — recovers 32,389 missing aircraft types |
| Airline names | Done — 323 of 451 operators named |
| Source tracking | Done — URL, date, size, checksum per file |
| Quality checks | Done — 15 checks, 77.5% of flights usable |
| Fuel model | Done — validated, 7 of 8 types within 15% |
| Carbon costs | Done — priced at June 2022 market prices |
| Power BI dashboard | **Waiting on you** — needs the desktop app |

**Right now:** everything that can be done without outside input is done.
The pipeline runs end to end from an empty database: three reference files
and one month of flights in, fuel, CO2 and costs out.

The headline numbers for June 2022:

- **87,903** flights touching French airports
- **77.5%** of them usable after the quality checks
- **559,128 tonnes** of fuel and **1.77 million tonnes** of CO2, across the
  59,772 flights that could be modelled
- **EUR 730 million** of fuel, and **EUR 38 million** of carbon at June 2022
  allowance prices
- Busiest route by emissions: **New York JFK to Paris CDG**
- **Air France** accounts for the largest share
- **74% of the CO2 falls outside the EU carbon market**, because the
  long-haul flights that dominate emissions are not covered

The prices are now researched and in `config/params.yml`, each with its
source and the date it was read. One thing is left that has to be done by
hand: **the Power BI dashboard**, because it is a desktop app.

---

## What happens next, in order

1. **Build the dashboard.** Power BI, connected straight to PostgreSQL.
   Three pages: overview, efficiency, and data quality.
2. **Write up the finding.** The strongest one so far: 74% of the CO2 from
   French air traffic falls outside the EU carbon market, because the
   long-haul flights that dominate emissions are not covered.
3. **Settle the scope question.** Whether flights to the UK and
   Switzerland were covered in 2022 moves that 74% figure directly, so it
   needs a source.
4. **Cite the eight cruise-burn figures** in validation_reference.yml.
5. **Load June 2019 too**, to compare a pre-COVID month against 2022.

### How to run the whole thing

```bash
uv run flightperf init
uv run flightperf ingest airports
uv run flightperf ingest aircraft
uv run flightperf ingest airlines
uv run flightperf ingest flights --year 2022 --month 6
uv run flightperf build quality
uv run flightperf build fuel
uv run flightperf build validate
uv run flightperf build kpi          # needs the prices first
```

The ingest commands can be run in any order.

---

## Decisions made along the way

Worth remembering the reasons, because these are the questions an
interviewer asks.

**Aviation instead of shipping.** The analysis works the same way for any
moving asset that burns fuel and gets tracked — ships report through AIS and
face their own version of the same carbon rules. Aviation was chosen because
the open data is much better.

**Four data sources instead of seven.** The first plan needed three
registrations and an access application. It was cut to plain downloads that
need no account - two carrying the data, two reference lookups. A finished
narrow project beats an unfinished broad one.

**PostgreSQL instead of DuckDB.** DuckDB is a single file and needs no
install, which was convenient. PostgreSQL is what job adverts ask for, it is
a real server, and Power BI connects to it directly — which removed the step
of exporting files for the dashboard to read.

**No Docker.** It would let anyone run the project without installing
anything, but that is a nice-to-have for a solo project and it is another
tool to learn.

**No machine learning, for now.** There is a genuine ML problem sitting in
the data — predicting the 37% of missing aircraft types — which would make
this an MLOps project as well. That is worth doing *after* the dashboard is
finished, not instead of it.

---

## Known limitations

These get stated wherever the numbers appear, rather than hidden.

- **Distances are straight lines** between airports, not the route actually
  flown. They are corrected upward using EUROCONTROL's 2022 en-route flight
  efficiency figure (KEA, +2.96%), which covers the en-route phase only and
  therefore still understates the full gate-to-gate extension.
- **No passenger numbers.** Anything "per seat" uses how many seats the
  aircraft has, not how many were sold.
- **One month of traffic is loaded: June 2022.** The dataset offers any
  month from January 2019 to December 2022, but the analysis currently
  covers June 2022 only — 87,903 flights over 30 days. Say "one month",
  not "four years".
- **Everything is priced at June 2022 market prices**, matching the month
  of the flight data. So the money figures mean "what this traffic cost at
  the time, at market prices" - not what it would cost under today's rules.
- **The carbon figure is the market value of the emissions, not cash paid.**
  In 2022 airlines still received a large share of their allowances free of
  charge, and that is deliberately ignored, so this is what the emissions
  were worth rather than what anyone handed over.
- **A third of flights arrived with no aircraft type.** The aircraft
  registry recovered 99% of those, so only 260 flights are now dropped for
  that reason. The remaining gap is flights with no departure or arrival
  airport, which cannot be recovered the same way.
- **Taxi fuel is not counted.** OpenAP has no model for engines at idle,
  and inventing a number would be worse than leaving it out. Real block
  fuel is a few percent higher than these figures.
- **The Boeing 777-300ER runs 31% high** in OpenAP compared with published
  cruise figures. Every other type checked agrees within 15%. This is
  reported by `build validate` rather than hidden, and it matters because
  long-haul flights dominate the CO2 total.
- **Only 53 of 260 aircraft types can be modelled**, though those 53 cover
  88% of flights. The rest are mostly small private aircraft.
