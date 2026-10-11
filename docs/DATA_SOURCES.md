# Data sources

Everything in this project comes from four public files. All four are
direct downloads that need no account, no registration and no approval.

The `core.dim_source` table records, for every file actually used: the URL,
the download date, the file size, a SHA-256 checksum and the number of rows
loaded. Run `uv run flightperf sources` to see it. The point is to be able
to prove, months later, exactly which version of a dataset produced a given
number.

---

## 1. Airport reference data

| | |
|---|---|
| **Publisher** | OurAirports (David Megginson) |
| **Page** | https://ourairports.com/data/ |
| **File** | https://davidmegginson.github.io/ourairports-data/airports.csv |
| **Licence** | Public domain |
| **Size** | about 13 MB |
| **Rows** | roughly 80,000 worldwide, 17,109 kept |
| **Used for** | Airport names, countries and coordinates |

Every distance in the project is calculated from the coordinates in this
file, which is why they are downloaded rather than typed in by hand.

**What gets filtered out.** The file lists every airfield in the world,
including heliports, seaplane bases and closed strips. Only entries with a
proper 4-letter ICAO code and a real airport type are kept, because those
are the only ones the flight list can refer to.

**How to cite it:** OurAirports, airport database, public domain. Include
the download date from `dim_source`.

---

## 2. Flight list

| | |
|---|---|
| **Publisher** | The OpenSky Network, published via Zenodo |
| **Page** | https://zenodo.org/records/7923702 |
| **File** | `flightlist_YYYYMMDD_YYYYMMDD.csv.gz`, one per month |
| **Licence** | Creative Commons, non-commercial |
| **Size** | 150–220 MB compressed per month, 8.1 GB for the whole set |
| **Period** | January 2019 to December 2022 |
| **Used for** | The flights themselves |

One row per flight. The columns are:

```
callsign, number, icao24, registration, typecode,
origin, destination, firstseen, lastseen, day,
latitude_1, longitude_1, altitude_1,
latitude_2, longitude_2, altitude_2
```

The file is left compressed on disk. Python unzips it a block at a time and
streams it into PostgreSQL with `COPY`, so the uncompressed gigabyte never
has to exist as a file.

### What this data is, and what it is not

The flights are reconstructed from transponder signals picked up by
volunteer receivers. They are **not** taken from airline schedules. That has
consequences worth stating plainly:

- **The origin and destination are inferred**, by matching the start and end
  of each track to the nearest airport. Where receiver coverage is thin, the
  airport comes out blank or wrong.
- **The aircraft type is often missing.** In the June 2022 sample, 37% of
  flights touching French airports had no type code.
- **There is no flight path**, only the first and last position the aircraft
  was seen at. So the distance used is the straight line between the two
  airports, not the route actually flown.
- **There is no passenger count.** Anything "per seat" is based on how many
  seats the aircraft type has, not how many were sold.
- **A small number of rows are exact duplicates.** In June 2022 there were
  491 of them out of 3.17 million, and they are removed on load.

None of this is a fault in the file. It is what crowd-sourced tracking data
looks like, and measuring it is one of the things this project sets out to
do.

**How to cite it:** Strohmeier, M. et al., *Crowdsourced air traffic data
from the OpenSky Network 2019–2022*, Earth System Science Data. Include the
Zenodo record and the specific monthly file.

---

## 3. Aircraft registry

| | |
|---|---|
| **Publisher** | The OpenSky Network |
| **Page** | https://s3.opensky-network.org/data-samples/metadata/ |
| **File** | `aircraft-database-complete-2024-01.csv` |
| **Licence** | Free to use |
| **Size** | about 61 MB, 579,435 aircraft |
| **Used for** | Turning a transponder code into an aircraft type |

**This fixes the biggest hole in the data.** The flight list leaves the
aircraft type blank on 37% of rows, and without a type there is no fuel
figure. But every row carries a transponder code, and this file maps those
codes to aircraft.

Loading it recovered **32,389 of the 32,649** missing types, which took the
share of usable flights from 49.6% to **77.5%**.

**A quirk to know about:** the file's header row is completely blank, so the
columns have to be declared by position rather than read by name.

---

## 4. Airline names

| | |
|---|---|
| **Publisher** | OpenFlights |
| **Page** | https://github.com/jpatokal/openflights |
| **File** | `data/airlines.dat` |
| **Licence** | Open Database License |
| **Size** | about 400 KB, 5,774 airlines |
| **Used for** | Turning airline codes into names and countries |

The flight list has no airline column. The airline is worked out from the
first three letters of the callsign — `AFR1234` means Air France — and this
file turns those codes into names, so the dashboard reads *Air France*
rather than `AFR`.

323 of the 451 operators in the data are matched. The rest are mostly small
or recently formed carriers that the file does not list.

There is no header row, and missing values are written as `\N`.

---

## Prices and parameters

Three figures come from outside the data. They are not downloads — each was
looked up by hand and written into
[`config/params.yml`](../config/params.yml) with its source.

**All three are for June 2022, matching the month of the flight data.** The
money figures therefore mean *"what this traffic cost at the time, at market
prices"*, not what it would cost under today's rules.

### Carbon price — 82.79 EUR per tonne CO2

Volume-weighted average auction clearing price of the 13 general-allowance
(EUA) auctions held on the EEX common auction platform in June 2022,
covering 30.6 million allowances.

The single aviation-allowance (EUAA) auction that month cleared at 84.45.
EUA is used as the benchmark because it has 13 auctions behind it rather
than one.

> European Commission, *Auctions by the Common Auction Platform — April,
> May, June 2022*, Table 1 (published 05/08/2022).
> `https://climate.ec.europa.eu/system/files/2022-12/cap_report_202206_en.pdf`
> Accessed 2026-09-25.

### Jet A-1 price — 1,306 EUR per tonne

Mean of the five weekly "Europe & CIS" USD/mt values in the IATA Jet Fuel
Price Monitor (Platts data) for the weeks dated 3, 10, 17, 24 June and
1 July 2022 — every week overlapping June:

```
1357.50, 1431.25, 1419.88, 1406.00, 1285.25  ->  mean 1379.98 USD/t
```

Converted at the ECB June 2022 monthly average of 1.0566 USD/EUR (series
`EXR.M.USD.EUR.SP00.A`), giving 1,306 EUR/t.

- Consistency check: USD/mt ÷ USD/bbl = 7.89 in every week
- Using only the four weeks dated *in* June gives 1,329 EUR/t, 1.7% higher
- This is a refinery-gate spot price: it excludes into-plane fees and taxes
- "Europe & CIS" is IATA's regional basket, not France-specific

> Read from Wayback Machine snapshots of
> `iata.org/en/publications/economics/fuel-monitor/` (`table_MMDDYYYY.png`),
> e.g. snapshot `20220614033039` for the 10 June table.
> Accessed 2026-09-26.

### Route extension factor — 1.0296

Horizontal en-route flight efficiency of the actual trajectory (KEA),
Union-wide, 2022: **2.96% extra distance** over the great circle.

2022 was the worst year since 2016 against a target of 2.37%, partly
because of airspace closures following the invasion of Ukraine. That year
is used because it matches the flight data.

**This is en-route only.** It excludes the terminal area around each
airport, so it understates the full gate-to-gate extension. It is also
measured in Single European Sky airspace but applied here to non-European
legs as well.

> Performance Review Body, *Annual Monitoring Report 2022* (October 2023),
> Table 1.
> `https://transport.ec.europa.eu/system/files/2023-10/PRB_Annual_Monitoring_Report_2022.pdf`
> Accessed 2026-09-25.

### Calibration to ICAO — 16 aircraft types

OpenAP is a generic physics model, so its fuel burn is checked against
ICAO's published fuel table and corrected to it, type by type.

> ICAO, *Carbon Emissions Calculator Methodology*, Version 13.1 (August
> 2024), Appendix C "ICAO Fuel Consumption Table".
> `https://www.icao.int/sites/default/files/environmental-protection/ENVtools/SuportingMaterials/ICEC_Methodology_Passagers_v13_Final.pdf`
> Accessed 2026-10-11.

The table gives whole-trip fuel at set distances. The difference between
the 1,000 nm and 2,000 nm columns is 1,000 nm of extra cruise, so dividing
by 1,000 gives cruise fuel per nautical mile without assuming a cruise
speed. The 16 types checked cover 93% of the CO2. Before correction, 9 of
16 were within 15%; the Boeing 777 family came out 23–37% high and the
A320neo and A321neo 23–31% low. The figures and the arithmetic are in
`config/validation_reference.yml`, and the correction lowers total CO2 by
9.4%.

### Carbon market scope — 2022 rules, by direction

| Flight | Covered by |
|---|---|
| EEA → EEA | EU ETS |
| EEA → Switzerland | EU ETS |
| Switzerland → EEA | Swiss ETS |
| EEA → UK | EU ETS |
| UK → EEA | UK ETS |
| EEA ↔ outermost regions (Réunion, Canaries, Madeira…) | none |
| everything else | none |

> European Commission, *Scope of the EU ETS*: aviation "within the European
> Economic Area and departing flights to Switzerland and the United
> Kingdom".
> `https://climate.ec.europa.eu/eu-action/carbon-markets/eu-emissions-trading-system-eu-ets/scope-eu-ets_en`
>
> Transport & Environment, *2022 airlines' emission analysis —
> Methodological note* (April 2023), Table 1, which gives the 2022
> coverage by direction.
> `https://uploads.transportenvironment.org/production/files/ETS_2023_methodological_note_updated_11_April-1.pdf`
>
> Directive 2003/87/EC Article 28a, extended to 2023 by Regulation (EU)
> 2017/2392, for the outermost-region derogation.
> `https://eur-lex.europa.eu/eli/reg/2017/2392/oj/eng`
>
> All accessed 2026-10-11.

Only the EU ETS share is priced. Flights under the UK and Swiss systems
(1.8% of the CO2) are labelled in `carbon_market` but not costed.

### What is deliberately left out

**Free allocation is set to zero.** In 2022 airlines still received a large
share of their aviation allowances free of charge. Ignoring that is a
choice, not an oversight: the result is the **market value of the covered
emissions** — what they were worth — rather than the cash airlines actually
paid. Every place the carbon figure appears says so.

**UK and Swiss carbon prices.** CO2 covered by the UK and Swiss systems is
identified but not priced: the question here is what the EU market covers,
and each system has its own price.

---

## Deliberately not used

These were considered and dropped, to keep the project to a size that can
actually be finished:

| Source | Why not |
|---|---|
| OpenSky historical trajectories (Trino) | Needs an access application. Would give the real flight path and allow route-efficiency analysis, but the flight list already answers the main question. |
| EUROCONTROL R&D archive | Needs registration. A useful cross-check, not essential. |
| Copernicus ERA5 weather | Needs registration. Only fed the optional wind and contrail work. |
| DGAC traffic statistics | Only needed for passenger-based metrics, which this data cannot support anyway. |

If the project is extended later, OpenSky trajectories are the one worth
adding: they would slot into the same schema and bring back the
route-efficiency measurements.

---

## Downloading by hand

The pipeline downloads all four files itself. To fetch them manually instead:

```bash
curl -L -o data/raw/ourairports/airports.csv \
  "https://davidmegginson.github.io/ourairports-data/airports.csv"
```

```bash
curl -L -o data/raw/opensky/flightlist_20220601_20220630.csv.gz \
  "https://zenodo.org/records/7923702/files/flightlist_20220601_20220630.csv.gz?download=1"
```

```bash
curl -L -o data/raw/opensky/aircraft-database.csv \
  "https://s3.opensky-network.org/data-samples/metadata/aircraft-database-complete-2024-01.csv"
```

```bash
curl -L -o data/raw/openflights/airlines.dat \
  "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
```

Put them at those exact paths and the pipeline will use them instead of
downloading again. Do not unzip the second one.
