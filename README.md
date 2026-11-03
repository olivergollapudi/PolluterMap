# PolluterMap — Indiana

An easy-to-read public map of regulated polluting facilities in Indiana.
Zoom in, click a facility, and see what it is, what it releases, and its
compliance record.

> **Status:** Phase 1 complete (EPA ECHO data → clean database → basic map).

## Project layout

```
PolluterMap/
├── data/
│   ├── raw/          Files downloaded straight from EPA, untouched.
│   │                 Large and re-downloadable, so NOT committed to git.
│   └── processed/    Cleaned, Indiana-only outputs our scripts produce
│                     (CSV, SQLite database, map data).
├── scripts/          Python scripts that download and clean the data.
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

(More sources are added in later phases.)

## Running locally from scratch

**You need:** Python 3 (tested on 3.14) and an internet connection. No packages
to install, no accounts, no API keys. About 1 GB of free disk space.

On a Mac, check that Python is installed with `python3 --version`. If it isn't,
install it from [python.org](https://www.python.org/downloads/).

All commands are run from the project folder (`cd PolluterMap`).

### 1. Build the data (~1–3 minutes)

```bash
python3 scripts/build_echo_indiana.py
```

This downloads EPA's ECHO file (~450 MB) and the Census industry lists into
`data/raw/`, filters to Indiana, and writes:

- `data/processed/echo_indiana.csv` — open in Excel/Numbers to browse
- `data/processed/echo_indiana.sqlite` — same data, queryable with SQL

Running it again reuses the downloaded files. To get EPA's latest weekly data,
add `--force`.

### 2. Export the map data (a few seconds)

```bash
python3 scripts/export_map_data.py
```

Writes `site/data/facilities.geojson`, the file the map loads.

### 3. View the map

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
sqlite3 -header -column data/processed/echo_indiana.sqlite "SELECT name, city, compliance_status FROM facilities WHERE is_major='Y' AND compliance_status='Significant Violation' LIMIT 10;"
```

## Known quirks in the EPA data

Found while building Phase 1 (ECHO data dated 2026-10-31):

- ~200 Indiana facilities have no Registry ID yet (mostly brand-new permits). Kept with a blank ID.
- "Major" and "active" flags are `Y` or blank, not `Y`/`N`. Blank is treated as `N`.
- `FAC_SNC_FLG` (serious violator) is `N` for every Indiana facility, contradicting other fields, so it's not used.
- Only ~23% of facilities have an industry code; codes mix several NAICS editions, and `11111` is a filler value that the build script removes.
- 9 facilities have coordinates outside Indiana; they're kept in the database but left off the map.
