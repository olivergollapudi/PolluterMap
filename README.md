# PolluterMap — Indiana's Deadliest Polluters

A public map and leaderboard of Indiana's polluters, ranked by the deaths linked to their pollution
(air pollution, toxic chemicals and climate change), with the damage in dollars, what it would cost
to offset, and what's near you. Built only from public government data and published methods.

> **Status:** the site (`site/index.html`) is ready to publish with GitHub Pages; see
> [Publishing](#publishing). The earlier all-facilities map is kept at `site/classic/`.

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
│   ├── build_toxic_impacts_indiana.py  Toxic releases to air, water, land → disease cases (EPA TRACI).
│   ├── export_map_data.py      Database → GeoJSON for the map.
│   ├── export_site_data.py     Database → the site's data, ZIP centers, towns and share pages.
│   ├── make_share_images.py    The link-preview picture for every polluter page (needs Pillow).
│   ├── fonts/                  Inter, the font in those pictures (SIL Open Font License).
│   └── common.py               Shared helpers (downloading, Excel reading, database).
├── site/             The static website (plain HTML/JS/CSS, no build step). This folder is
│   │                 what gets published.
│   ├── index.html    "Indiana's Deadliest Polluters": map + deaths leaderboard, a page per
│   │                 polluter, and "How we estimate". MapLibre GL JS + OpenFreeMap's dark
│   │                 basemap; if OpenFreeMap can't load (offline, or a network filter blocks
│   │                 it), it falls back to OpenStreetMap's own tiles, then to a plain
│   │                 background; the dots always show.
│   ├── site-data.js  One record per polluter (deaths, damage, impacts, trends).
│   ├── zips.js       Center of each Indiana ZIP code (Census 2023 ZCTA gazetteer).
│   ├── towns.js      Every incorporated Indiana city and town: location and 2024 population
│   │                 (Census population estimates + 2024 places gazetteer).
│   ├── p/            A page per polluter that search engines can read and link previews use
│   │                 (with a button to open it on the map), and p/index.html listing them all.
│   │                 p/img/ holds each page's link-preview picture (1200x630), shown when a
│   │                 link is shared on Facebook, X, Reddit, iMessage and so on.
│   ├── sitemap.xml, robots.txt  Point search engines at the home page and every polluter page.
│   ├── classic/      The earlier map of all 58,000 regulated facilities (not linked from
│   │                 the main page).
│   ├── vendor/       A local copy of MapLibre 4.7.1 (from its official npm package), so the
│   │                 page doesn't depend on an outside download site.
│   └── data/         GeoJSON for the classic map. Each facility carries its newest year of
│                     TRI, RSEI, GHG, NEI and water data, its estimated harm and its rank.
├── .github/workflows/pages.yml  Publishes site/ to GitHub Pages on every push to main
│                     (only once the repository is public).
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
| 2 | [EPA benefit-per-ton estimates](https://www.epa.gov/benmap/sector-based-pm25-and-ozone-benefit-ton-estimates) | Premature deaths per ton of air pollutant, by sector, Indiana/North region (2023 BPT, analysis year 2025). |
| 2 | [EPA social cost of greenhouse gases](https://www.epa.gov/system/files/documents/2023-12/epa_scghg_2023_report_final.pdf) (2023) | Dollars of climate damage per ton of CO2, methane, N2O (Table A.5.1). |
| 2 | [EPA mortality risk valuation](https://www.epa.gov/environmental-economics/mortality-risk-valuation), [GHG equivalencies](https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator-calculations-and-references), [IPCC AR6 WGIII Ch. 12](https://www.ipcc.ch/report/ar6/wg3/downloads/report/IPCC_AR6_WGIII_Chapter12.pdf), [BLS CPI-U](https://www.bls.gov/cpi/) | Value of a statistical life; everyday comparisons; carbon-removal cost; inflation. |
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
| `impact_factors` | one published factor (social cost of carbon, value of a statistical life, removal cost, everyday-equivalence) with unit, dollar year and source | `build_impact_factors.py` |
| `health_factors` | sector × pollutant × pathway × outcome: EPA cases per ton (low, high) for Indiana — deaths, lung cancers, heart attacks, strokes, asthma, ER visits, hospital admissions, lost work/school days | `build_impact_factors.py` |
| `cpi` | year → Consumer Price Index, for converting dollars | `build_impact_factors.py` |
| `toxicity_factors` | chemical: EPA TRACI 2.2 cancer and noncancer cases per kg released to air, fresh water, soil | `build_toxic_impacts_indiana.py` |
| `toxic_release_cases` | report (TRI, NEI or water monitoring) × chemical × where released (2023): pounds, cancer cases, other disease cases | `build_toxic_impacts_indiana.py` |
| `facility_impacts` | facility (2023): estimated deaths and illnesses, health and climate damage in dollars, cost to remove its CO2, everyday comparisons | `build_impacts_indiana.py` |
| `metadata` | source × fact (URL, data date, build date) | all |

### How the impact estimates work

All dollars are 2024 dollars; every estimate has a low / central / high range.

- **Health (deaths, illness, dollars):** tons of fine particles (PM2.5), SO2, NOx, ammonia and VOCs
  (NEI 2023) × EPA's *benefit-per-ton* estimates of cases per ton for the facility's industrial
  sector in Indiana (or EPA's North region): premature deaths, lung cancers, nonfatal heart attacks,
  strokes, cardiac arrests, new asthma cases, ER visits, hospital admissions, lost work and school
  days. Covers fine-particle and ozone pollution. Dollars = deaths × EPA's value of a statistical
  life ($7.4M in 2006 dollars, $11.5M in 2024 dollars). Facilities outside EPA's 21 sectors use the
  Indiana "industrial boilers" values (`health_sector_basis = 'proxy'`).
- **Climate:** each greenhouse gas (GHGRP 2023) × EPA's 2023 social cost of that gas
  ($204 per ton of CO2 in 2020 dollars at the central 2% rate; 2.5% and 1.5% give the low and high).
- **Cost to reverse:** CO2-equivalent × the IPCC's projected cost of direct air capture at scale
  ($100–300 per ton); today's prices are higher.
- **Everyday comparisons:** CO2-equivalent ÷ EPA's Greenhouse Gas Equivalencies factors
  (e.g. 4.29 tons per car per year).
- **Climate deaths:** CO2-equivalent × the *mortality cost of carbon* from Bressler (2021,
  Nature Communications): 2.26 × 10⁻⁴ expected heat-related deaths worldwide, 2020–2100, per ton
  (one per 4,434 tons; range −1.71 to 6.78 × 10⁻⁴, with the negative low end floored at zero).
  Counts heat deaths only — not disease, food supply, flooding or conflict.
- **Toxic chemicals (deaths, illness, dollars):** every chemical a facility released in 2023 — TRI
  releases to air, water and land, NEI hazardous air pollutants (benzene, chromium, lead, mercury…),
  and toxic metals, cyanide and benzene in water discharge monitoring — × EPA's
  [TRACI 2.2](https://www.epa.gov/chemical-research/tool-reduction-and-assessment-chemicals-and-other-environmental-impacts-traci)
  human-health factors (from the UN Environment/SETAC USEtox model): cancer cases and other disease
  cases in the whole population per kg, following the chemical through air, water, soil and food.
  Cancer deaths = cancer cases × 0.306, the US ratio of cancer deaths to new cases (611,720 / 2,001,140,
  American Cancer Society, *Cancer Facts & Figures 2024*); dollars = deaths × value of a statistical
  life. Low = air and water releases; central = + chemicals spread on land or put in ponds (such as
  coal ash ponds); high = + non-hazardous landfills. Hazardous-waste landfills, injection wells and
  off-site transfers aren't counted. Where TRI and NEI (air) or TRI and water monitoring report the
  same chemical for one plant, the larger figure is used. Air uses TRACI's "rural air" factors, lower
  than its "urban air" ones. Chromium counts as chromium(III) unless reported as hexavalent. Metals
  dominate the result and EPA marks all metal factors "interim" (less certain); TRACI also counts
  harm over an unlimited time, since metals never break down. Indiana total, central: about 5.5
  deaths (4.7–12), 18 cancers and 3,600 other illnesses — small next to air pollution, because
  TRACI's per-pound factors are small for most chemicals.
- **Death toll** (the map's single "deaths" number) = air-pollution deaths + toxic-chemical cancer
  deaths + climate deaths, i.e.
  deaths linked to one year of a facility's pollution. Climate deaths are counted, not valued
  again in dollars — EPA's social cost of carbon already includes them.

**Not included, and why:**
- *Deaths from noncancer toxic illness:* TRACI counts cases, not deaths, so these (including lead's
  harm to the heart and brain) are counted as illnesses only and not valued in dollars.
- *Chemicals with no TRACI factor:* manganese (9 million lbs in 2023), nitrates, sulfuric and
  hydrochloric acid, ammonia, sodium nitrite and carbonyl sulfide score zero.
- *Water permit violations as such:* a violation isn't a pollutant; the extra pollution it releases is
  in the discharge loads above. Conventional water pollutants (sediment, nutrients, oxygen demand)
  have no TRACI factor.
- *Dollar cost of nonfatal illness:* illness is counted in cases, not dollars — EPA's illness dollar
  tables document their dollar year inconsistently (2015 vs 2016 vs 2019). Deaths are most of
  EPA's health dollars, so health dollars are slightly conservative.

Facilities whose estimate rests on a value EPA flags as a possible outlier are marked in
`epa_outlier_values`. Estimates are screening-level, for comparing facilities — not a measured count
of deaths caused by any one plant.

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
`data/processed/indiana.sqlite`, then the site's data files (`site/site-data.js`, `zips.js`,
`towns.js`, the share pages in `site/p/`) and `site/data/facilities.geojson` for the classic map.

Running it again reuses the downloaded files. To get EPA's latest data first,
add `--force`. Each script can also be run on its own, in the order listed in
`build_all.py` (later ones link to tables made by earlier ones).

### 2. View the site

What the site does:

- **The death total for what's on screen:** estimated deaths a year from the polluters in view, split
  into air pollution, toxic chemicals and climate change, with the damage in dollars, the cost to
  offset their CO2, and a town in view with about that many people. It updates as you pan, zoom or search.
- **Leaderboard + map:** the deadliest polluters in view, ranked (or ranked by damage); the top 5 are
  numbered on the map. Clicking a row or a dot links the two.
- **Search** by polluter, owner or town, an Indiana ZIP code, or a street address (looked up by the
  Census Bureau); the location button finds polluters near you.
- **A page per polluter:** its death count, its biggest harms as big numbers, trends, its record,
  what you can do (state complaint form, permit notices, public files) and a share button.

The classic map, at `/classic/`:

- **Dots or Heatmap** ("Show"). The heatmap glows where the chosen measure is concentrated and
  fades into clickable dots once you zoom in to town level.
- **Rank by** estimated total harm ($), estimated deaths (air pollution + climate), climate damage ($),
  toxic releases (lbs) or toxic risk score. Drives both the heatmap and the Top 20 list.
- **Running total** — the red box above the Top 20 list adds up the estimated deaths and damage for
  every facility in view, and updates as you pan and zoom.
- **Top 20 in view** — re-ranked every time you pan or zoom (all of Indiana zoomed out, a single
  neighborhood zoomed in). The 20 get larger dark dots; click a row to fly there and open it.
  The heatmap and Top 20 include every facility with data, whatever the "major only" filter says.
- **Click a dot** for its profile: compliance, estimated yearly harm, toxic releases, risk score,
  greenhouse gases, air pollutants and water discharges.

```bash
python3 -m http.server 8000 --directory site
```

Open <http://localhost:8000> (the classic map is at <http://localhost:8000/classic/>). Stop the
server with Ctrl+C.

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
- **Backup basemaps:** CARTO's free basemap was considered but not used — its terms (Sept 2026)
  require an API key. The OpenStreetMap tile fallback needs no key but is only meant for light
  use with attribution; re-check the [OSM tile usage policy](https://operations.osmfoundation.org/policies/tiles/)
  before public launch.
- **Database size:** with all sources, `indiana.sqlite` outgrew GitHub's 100 MB file limit, so
  only the CSVs are committed.

Found while adding impact estimates:

- **Split records again:** only 118 of 186 greenhouse-gas reporters share a Registry ID with their
  own air-emissions (NEI) record — e.g. the Gibson coal plant's two records have identical
  coordinates but different IDs. `build_impacts_indiana.py` merges records at the same location
  (within 1 km) and notes it in `merged_records` (40 merges, all checked against EPA's street
  addresses). `SAME_PLANT` lists 13 more pairs checked by hand: some are farther apart because one
  record's coordinates are off, and three sit within 1 km of a different company, which the location
  rule would pick. `NOT_SAME_SITE` keeps two plants off a neighbor's record, and `SAME_SITE_NEI`
  combines Alcoa Warrick's smelter and power plant, which share one greenhouse-gas report. A merged
  row also gets the penalties filed under the IDs merged into it. None of this changes state
  totals for deaths or dollars; it moves them between rows. Fourteen greenhouse-gas reporters still
  have no linked air-pollution record, so they show no air deaths. Some of those are mostly methane,
  but POET Shelbyville, Advanced Magnesium Alloys and Nestlé Anderson likely have air emissions that
  are missing.
- **Toxics and water reports on the site:** a report filed under a facility's own Registry ID is
  used as is. Otherwise `export_site_data.py` looks within 3 km for a report whose name shares a
  distinctive word (not the town name), and never uses a report twice, so some real matches are
  missed in favor of never showing another company's data.
- **Search by ZIP or address:** a 5-digit Indiana ZIP uses the ZIP's center from `zips.js`
  (no lookup). A street address is sent to the US Census Bureau's free geocoder when the visitor
  presses Enter; the Census service only allows browser requests as JSONP, so it's loaded as a
  script. Either way, the site shows polluters within 10 miles.
- **Comparisons:** "deaths for every day of its pollution" is a year's estimated deaths ÷ 365.
  The traffic comparison (in a polluter's "More details") uses air-pollution deaths only (local and near-term, unlike climate deaths)
  against Indiana's 898 traffic deaths in 2023 (NHTSA, *Overview of Motor Vehicle Traffic Crashes in
  2023*, Table 9, FARS 2023 annual report file). Trend lines use each polluter's greenhouse gas
  reports (2011–2023) and TRI toxic releases (2015–2024).
- **Town comparisons:** a death count (20 or more) is compared with the Indiana city or town
  whose 2024 population is closest to it, among the towns visible on the map (or, on a polluter's
  page, within 25 miles of the plant, widening to 50 and 100 miles if nothing is within 50%). Within
  25% it reads "as many people as live in …"; otherwise "N times" or "N% of" that town's population.
- **Links** use each polluter's EPA Registry ID (or its air or greenhouse-gas ID), not its
  rank, so shared links keep working after a data refresh.
- **Toxic reports in the death count:** `build_impacts_indiana.py` joins each TRI report and water
  permit to a plant by EPA's TRI–air-record link, then by Registry ID, then by location: within 3 km
  sharing a distinctive name word, or within 1 km sharing any word, town names included (EPA's
  registry and air records sometimes put one plant several km apart, so both spots are tried).
  Reports with no match become polluters of their own (about 360, mostly small platers, galvanizers
  and sewage plants), which brings the list to 1,200.
- **EPA outlier flags:** EPA flags nine Indiana facilities' figures. Six are the PM2.5 values of big
  steel mills and coal plants, over EPA's 500-ton "National" screening level. Those six rank in the
  top 40 of about 78,000 US facilities, and their 2023 values are in line with or below the 2017 and
  2020 NEI, so they look real rather than wrong; together they make up about half of Indiana's
  air-pollution deaths. Of the other three, Scepter's ammonia is flagged high (it has risen each
  inventory) and two VOC values are flagged low, so those two estimates may be too low. The map shows
  the matching note (`outlier_note`).
- **Social cost of carbon:** EPA's 2023 values are no longer used in federal rulemaking (2025),
  but remain the most recent peer-reviewed U.S. government estimate.
- **Carbon-removal prices:** current market prices are higher than the IPCC's at-scale projection;
  the market surveys (CDR.fyi) couldn't be checked from the network used to build this.

## Publishing

The site is published with **GitHub Pages**, free. Its own address is meant to be
<https://indianapollution.org> (set in `SITE_URL` in `export_site_data.py`, used for search engines and
link previews); until that domain is connected it's at <https://olivergollapudi.github.io/PolluterMap/>.
`.github/workflows/pages.yml` uploads the `site/` folder on every push to `main`. It only runs once
the repository is public (free GitHub Pages needs a public repository); while it's private, the
job is skipped.

To go live:

1. Make the repository public: GitHub → the repository → **Settings** → **General** → *Danger Zone*
   → **Change visibility** → Public. Everything in it (code, data, commit history, and the email on
   each commit) becomes public.
2. Turn on Pages: **Settings** → **Pages** → *Build and deployment* → Source: **GitHub Actions**.
3. Push to `main` (or re-run the "Publish site" workflow under **Actions**). The site appears at the
   address above within a minute or two.

To update the data, run `python3 scripts/build_all.py`, commit and push.

