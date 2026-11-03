# PolluterMap — Indiana

An easy-to-read public map of regulated polluting facilities in Indiana.
Zoom in, click a facility, and see what it is, what it releases, and its
compliance record.

> **Status:** Phase 1 complete (EPA ECHO data → clean database → basic map).
> Phase 2 data gathered (toxic releases, risk scores, greenhouse gases, air emissions, water
> discharges and violations, all linked to facilities); not on the map yet.

## Project layout

```
PolluterMap/
├── data/
│   ├── raw/          Files downloaded straight from EPA, untouched.
│   │                 Large and re-downloadable, so NOT committed to git.
│   └── processed/    Cleaned, Indiana-only outputs our scripts produce:
│                     one CSV per table (committed), plus indiana.sqlite holding all
│                     of them (rebuilt locally; too big for GitHub).
├── scripts/          Python scripts that download and clean the data.
│   ├── build_all.py            Runs everything below, in order.
│   ├── build_echo_indiana.py   ECHO facilities + compliance (run first).
│   ├── build_frs_indiana.py    FRS crosswalk: every EPA program's IDs → registry_id.
│   ├── build_tri_indiana.py    Toxics Release Inventory, linked to facilities.
│   ├── build_ghg_indiana.py    Greenhouse gas emissions, linked to facilities.
│   ├── build_nei_indiana.py    National Emissions Inventory (PM2.5, SO2, NOx...).
│   ├── build_water_indiana.py  Water discharges (loadings) and permit-limit violations.
│   ├── build_rsei_indiana.py   RSEI toxicity- and exposure-weighted risk scores.
│   ├── export_map_data.py      Database → GeoJSON for the map.
│   └── common.py               Shared helpers (downloading, Excel reading, database).
├── site/             The static web map (plain HTML/JS/CSS, no build step).
│   ├── index.html    The map page (MapLibre GL JS + OpenFreeMap basemap). If the basemap
│   │                 can't load (offline, or a network filter blocks it), the dots still
│   │                 show on a plain background.
│   ├── vendor/       A local copy of MapLibre 4.7.1 (from its official npm package), so the
│   │                 page doesn't depend on an outside download site.
│   └── data/         GeoJSON exported from data/processed/ for the map.
├── .gitignore        Tells git which files to skip (raw downloads, etc.).
└── README.md         This file.
```

### How data flows

```
EPA website  ──download──▶  data/raw/  ──clean & filter──▶  data/processed/  ──▶  site/ (map)
```

- **`data/raw/`** is treated as read-only. Scripts never edit these files;
  they only read them. If something looks wrong, delete the file and
  re-download.
- **`data/processed/`** can always be regenerated from `data/raw/` by
  re-running the scripts.
- **`site/`** is a static website: just files a browser opens. No server-side
  code, so it can eventually be hosted free on GitHub Pages.

## Data sources

| Phase | Source | Notes |
|-------|--------|-------|
| 1 | [EPA ECHO Exporter](https://echo.epa.gov/tools/data-downloads) | One row per regulated facility nationwide, refreshed weekly; we filter to Indiana. Column definitions: `data/raw/echo_exporter_columns.xlsx`. |
| 1 | [Census NAICS code lists](https://www.census.gov/naics/) (2017 + 2022) | Turns industry codes into names. |
| 2 | [EPA Toxics Release Inventory](https://www.epa.gov/toxics-release-inventory-tri-program/tri-basic-data-files-calendar-years-1987-present) | Indiana, 2015–2024. Pounds of each toxic chemical released to air, water and land. |
| 2 | [EPA Greenhouse Gas Reporting Program](https://www.epa.gov/ghgreporting/data-sets) | Indiana large emitters, 2011–2023 totals + 2023 breakdown by gas. 2024 not released by EPA. |
| 2 | [EPA Facility Registry Service](https://www.epa.gov/frs/epa-state-combined-csv-download-files) | Indiana crosswalk of program IDs → Registry ID. Used to link NEI and water permits. |
| 2 | [EPA RSEI](https://www.epa.gov/rsei/ways-get-rsei-results) v2.3.12 | Risk scores for TRI releases (toxicity × exposure × people), 2013–2022 — the newest RSEI covers only through 2022. |
| 2 | [EPA National Emissions Inventory](https://www.epa.gov/air-emissions-inventories/2023-air-emissions-data) 2023 | Air pollutants (PM2.5, SO2, NOx, CO, VOC, ammonia, lead, ~180 toxic air pollutants) for 948 Indiana facilities. |
| 2 | [ECHO Water Pollutant Loading Tool](https://echo.epa.gov/trends/loading-tool) + [NPDES violations](https://echo.epa.gov/tools/data-downloads/icis-npdes-dmr-and-limit-data-set) | Yearly pounds and toxic-weighted pounds discharged per pollutant (2015–2025), and every permit-limit exceedance since 2015. |

(More sources are added in later phases.)

## What's in the database

`data/processed/indiana.sqlite` (each table also saved as a CSV next to it; the
`.sqlite` file isn't in git — run `build_all.py` to create it).
Everything links on **`registry_id`** — EPA's Facility Registry ID.

| Table | One row per | Made by |
|---|---|---|
| `facilities` | regulated facility (58,453) | `build_echo_indiana.py` |
| `tri_releases` | facility × chemical × year (TRI "form") | `build_tri_indiana.py` |
| `tri_facility_years` | facility × year, with totals (air/water/land, carcinogens, PFAS…) | `build_tri_indiana.py` |
| `ghg_facilities` | greenhouse-gas reporting facility, with 2023 breakdown by gas | `build_ghg_indiana.py` |
| `ghg_emissions_by_year` | facility × year total (metric tons CO2e) | `build_ghg_indiana.py` |
| `frs_program_links` | registry ID × program × program ID (39 EPA programs) | `build_frs_indiana.py` |
| `nei_facilities` | air-emissions facility, with headline pollutants as columns (2023) | `build_nei_indiana.py` |
| `nei_emissions` | facility × pollutant (2023) | `build_nei_indiana.py` |
| `water_permits` | water discharge permit (receiving water, watershed, impaired?) | `build_water_indiana.py` |
| `water_loadings` | permit × outfall × pollutant × year: pounds and toxic-weighted pounds (TWPE) | `build_water_indiana.py` |
| `water_exceedances` | each time a discharge broke a permit limit (since 2015) | `build_water_indiana.py` |
| `water_permit_years` | permit × year totals: pounds, TWPE, exceedances, late reports | `build_water_indiana.py` |
| `rsei_facility_years` | TRI facility × year: RSEI score, cancer/non-cancer score, hazard | `build_rsei_indiana.py` |
| `rsei_chemical_years` | TRI facility × chemical × year scores | `build_rsei_indiana.py` |
| `metadata` | source × fact (URL, data date, build date) | all |

Example — Indiana's top toxic releasers in 2024, with their compliance status:

```bash
sqlite3 -header -column data/processed/indiana.sqlite "SELECT t.facility_name, t.city, ROUND(t.total_releases_lb) AS lbs, f.compliance_status FROM tri_facility_years t JOIN facilities f USING (registry_id) WHERE t.year = 2024 ORDER BY lbs DESC LIMIT 10;"
```

## Running locally from scratch

**You need:** Python 3 (tested on 3.14) and an internet connection. No packages
to install, no accounts, no API keys. About 2 GB of free disk space.

On a Mac, check that Python is installed with `python3 --version`. If it isn't,
install it from [python.org](https://www.python.org/downloads/).

All commands are run from the project folder (`cd PolluterMap`).

### 1. Build all the data

```bash
python3 scripts/build_all.py
```

This downloads ~1 GB from EPA and the Census Bureau into `data/raw/` (the RSEI
file alone is 430 MB and EPA's server is slow — allow up to an hour the first
time), filters
everything to Indiana, links it together, and writes the CSVs and
`data/processed/indiana.sqlite`, then `site/data/facilities.geojson` for the map.

Running it again reuses the downloaded files. To get EPA's latest data first,
add `--force`. Each script can also be run on its own, in the order listed in
`build_all.py` (later ones link to tables made by earlier ones).

### 2. View the map

```bash
python3 -m http.server 8000 --directory site
```

Open <http://localhost:8000>. Stop the server with Ctrl+C.

The map has to be opened through this local server, not by double-clicking
`index.html`: browsers block pages opened straight from disk from loading
data files. It's still a static site — the server just hands files to the browser.

### Optional: look at the data with SQL

`sqlite3` comes with macOS:

```bash
sqlite3 -header -column data/processed/indiana.sqlite "SELECT name, city, compliance_status FROM facilities WHERE is_major='Y' AND compliance_status='Significant Violation' LIMIT 10;"
```

## Known quirks in the EPA data

Found while building Phase 1 (ECHO data dated 2026-10-31):

- ~200 Indiana facilities have no Registry ID yet (mostly brand-new permits). Kept with a blank ID.
- "Major" and "active" flags are `Y` or blank, not `Y`/`N`. Blank is treated as `N`.
- `FAC_SNC_FLG` (serious violator) is `N` for every Indiana facility, contradicting other fields, so it's not used.
- Only ~23% of facilities have an industry code; codes mix several NAICS editions, and `11111` is a filler value that the build script removes.
- 9 facilities have coordinates outside Indiana; they're kept in the database but left off the map.

Found in Phase 2:

- **TRI land columns:** the summary columns for landfills, surface impoundments and underground
  injection are often 0 even when the detailed sub-columns hold the amount. We sum the
  sub-columns, which always match TRI's own on-site total.
- **TRI "Form A":** ~10% of TRI reports use the short form, which certifies the amount was small
  but doesn't report it. Their zeros mean "not reported", not "none" (`form_type = 'A'`).
- **TRI units:** dioxins are reported in grams; the `*_lb` columns convert everything to pounds.
- **TRI 2024:** 19 reports have no FRS ID; they're linked through ECHO's list of TRI IDs instead.
- **GHG:** 2023 is the newest public year. EPA hasn't released 2024 and has proposed ending
  reporting for most industries. Pipelines, gas utilities and fuel suppliers are reported per
  company, not per site, so they're not included.

Found while adding RSEI, NEI and water data:

- **Duplicate Registry IDs.** EPA sometimes gives one plant several Registry IDs (e.g. Haynes
  International in Kokomo has 4+ ECHO records at one address, two flagged "major"). The build
  scripts link each source to the ID our ECHO table uses where possible, but a plant's data can
  still be split across records. ~105 addresses have both a "major" record and a separate TRI
  record. Merging them is a judgment call left for the map design step.
- **NEI** doesn't report Registry IDs; it's linked through the FRS crosswalk. 373 of 948 NEI
  facilities (mostly airports and rail yards) aren't in ECHO's facility list at all.
- **RSEI** lags TRI: the newest RSEI (v2.3.12, March 2024) covers TRI through 2022. RSEI scores
  are for comparing facilities, not measured health risk. RSEI's `pounds_incl_transfers` also
  counts waste shipped off-site for recycling, so it's ~2.5× TRI's "total releases" — use TRI
  for pounds and RSEI for scores.
- **Water loadings:** EPA's Loading Tool can't calculate a load for ~half of reported pollutant
  rows (e.g. only concentration or flow was measured); those are skipped. EPA flags some loads
  as potential outliers (`potential_outlier`); worth excluding from rankings.
- **Clean Air Markets** (power plant hourly emissions) was not added: its downloads now require
  an API key. NEI + GHG cover the same plants' annual pollution.
- **Database size:** with all sources, `indiana.sqlite` outgrew GitHub's 100 MB file limit, so
  only the CSVs are committed.
