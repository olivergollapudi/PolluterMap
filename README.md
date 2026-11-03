# PolluterMap — Indiana

An easy-to-read public map of regulated polluting facilities in Indiana.
Zoom in, click a facility, and see what it is, what it releases, and its
compliance record.

> **Status:** Phase 1 complete (EPA ECHO data → clean database → basic map).
> Phase 2 data gathered (toxic releases + greenhouse gases, linked to facilities); not on the map yet.

## Project layout

```
PolluterMap/
├── data/
│   ├── raw/          Files downloaded straight from EPA, untouched.
│   │                 Large and re-downloadable, so NOT committed to git.
│   └── processed/    Cleaned, Indiana-only outputs our scripts produce:
│                     one CSV per table, plus indiana.sqlite holding all of them.
├── scripts/          Python scripts that download and clean the data.
│   ├── build_all.py            Runs everything below, in order.
│   ├── build_echo_indiana.py   ECHO facilities + compliance (run first).
│   ├── build_tri_indiana.py    Toxics Release Inventory, linked to facilities.
│   ├── build_ghg_indiana.py    Greenhouse gas emissions, linked to facilities.
│   ├── export_map_data.py      Database → GeoJSON for the map.
│   └── common.py               Shared helpers (downloading, Excel reading, database).
├── site/             The static web map (plain HTML/JS/CSS, no build step).
│   ├── index.html    The map page (MapLibre GL JS + OpenFreeMap basemap).
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

(More sources are added in later phases.)

## What's in the database

`data/processed/indiana.sqlite` (each table also saved as a CSV next to it).
Everything links on **`registry_id`** — EPA's Facility Registry ID.

| Table | One row per | Made by |
|---|---|---|
| `facilities` | regulated facility (58,453) | `build_echo_indiana.py` |
| `tri_releases` | facility × chemical × year (TRI "form") | `build_tri_indiana.py` |
| `tri_facility_years` | facility × year, with totals (air/water/land, carcinogens, PFAS…) | `build_tri_indiana.py` |
| `ghg_facilities` | greenhouse-gas reporting facility, with 2023 breakdown by gas | `build_ghg_indiana.py` |
| `ghg_emissions_by_year` | facility × year total (metric tons CO2e) | `build_ghg_indiana.py` |
| `metadata` | source × fact (URL, data date, build date) | all |

Example — Indiana's top toxic releasers in 2024, with their compliance status:

```bash
sqlite3 -header -column data/processed/indiana.sqlite "SELECT t.facility_name, t.city, ROUND(t.total_releases_lb) AS lbs, f.compliance_status FROM tri_facility_years t JOIN facilities f USING (registry_id) WHERE t.year = 2024 ORDER BY lbs DESC LIMIT 10;"
```

## Running locally from scratch

**You need:** Python 3 (tested on 3.14) and an internet connection. No packages
to install, no accounts, no API keys. About 1 GB of free disk space.

On a Mac, check that Python is installed with `python3 --version`. If it isn't,
install it from [python.org](https://www.python.org/downloads/).

All commands are run from the project folder (`cd PolluterMap`).

### 1. Build all the data (~2–3 minutes the first time)

```bash
python3 scripts/build_all.py
```

This downloads ~500 MB from EPA and the Census Bureau into `data/raw/`, filters
everything to Indiana, links it together, and writes the CSVs and
`data/processed/indiana.sqlite`, then `site/data/facilities.geojson` for the map.

Running it again reuses the downloaded files. To get EPA's latest data first,
add `--force`. Each script can also be run on its own (`build_echo_indiana.py`
must have run at least once before the TRI and GHG scripts).

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
