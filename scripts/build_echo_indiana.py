"""
Download EPA's ECHO Exporter file and turn it into a clean Indiana dataset.

What this does, in order:
  1. Downloads echo_exporter.zip (~450 MB) from EPA, plus the Census Bureau's
     NAICS industry-name lists, into data/raw/ -- unless they're already there.
     Use --force to re-download.
  2. Reads the big CSV inside the zip one row at a time (it's ~2 GB unzipped,
     so we never unzip it to disk or load it all into memory).
  3. Keeps only rows where the state is Indiana, and only the columns we need.
  4. Cleans values: Y/N flags, ISO dates, numbers, tidy county names, and drops
     the placeholder industry code 11111.
  5. Adds an "industry" name looked up from the facility's first NAICS code.
  6. Writes data/processed/echo_indiana.csv and the "facilities" table in
     data/processed/indiana.sqlite (the shared database; see common.py).

Uses only Python's standard library -- nothing to pip install.

Run from the project root:
    python3 scripts/build_echo_indiana.py
"""

import argparse
import csv
import io
import re
import sys
import zipfile
from datetime import date, datetime

from common import DB_PATH, RAW_DIR, PROCESSED_DIR, ROOT, STATE, connect, download, read_xlsx_rows, \
    replace_table, set_metadata, write_csv

DB_PATH_REL = DB_PATH.relative_to(ROOT)

ECHO_URL = "https://echo.epa.gov/files/echodownloads/echo_exporter.zip"
CSV_NAME_IN_ZIP = "ECHO_EXPORTER.csv"

# ECHO mixes codes from several NAICS editions, so we load two. 2022 titles win where both exist.
NAICS_URLS = {
    "2017": "https://www.census.gov/naics/2017NAICS/2-6%20digit_2017_Codes.xlsx",
    "2022": "https://www.census.gov/naics/2022NAICS/2-6%20digit_2022_Codes.xlsx",
}
# "11111" officially means Soybean Farming, but in ECHO it's attached to trucking firms, universities,
# mines... almost all hazardous-waste records. It's a filler value, so we treat it as "no code".
PLACEHOLDER_NAICS = {"11111"}

RAW_ZIP = RAW_DIR / "echo_exporter.zip"
OUT_CSV = PROCESSED_DIR / "echo_indiana.csv"

# Rough box around Indiana. Used only to flag suspicious coordinates, not to drop rows.
INDIANA_BOX = {"lat_min": 37.5, "lat_max": 42.0, "lon_min": -88.3, "lon_max": -84.6}

# The columns we keep: (our name, EPA's column name, type).
# Types: text, flag (Y/N), int, money, date, county, codes.
# EPA's meaning for every column is in data/raw/echo_exporter_columns.xlsx.
COLUMNS = [
    # --- Identity & location ---
    ("registry_id",          "REGISTRY_ID",                 "text"),   # FRS ID; TRI and GHG join on this
    ("name",                 "FAC_NAME",                    "text"),
    ("street",               "FAC_STREET",                  "text"),
    ("city",                 "FAC_CITY",                    "text"),
    ("zip",                  "FAC_ZIP",                     "text"),
    ("county",               "FAC_COUNTY",                  "county"),
    ("county_fips",          "FAC_FIPS_CODE",               "text"),
    ("lat",                  "FAC_LAT",                     "float"),
    ("lon",                  "FAC_LONG",                    "float"),
    # --- Industry (codes only; ECHO has no industry names) ---
    ("naics_codes",          "FAC_NAICS_CODES",             "naics"),  # space-separated if several
    ("sic_codes",            "FAC_SIC_CODES",               "codes"),  # older system, fallback
    # --- Status ---
    ("is_major",             "FAC_MAJOR_FLAG",              "flag"),
    ("is_active",            "FAC_ACTIVE_FLAG",             "flag"),
    # --- Which programs regulate it ---
    ("air_program",          "AIR_FLAG",                    "flag"),   # Clean Air Act
    ("water_program",        "NPDES_FLAG",                  "flag"),   # Clean Water Act discharge permit
    ("waste_program",        "RCRA_FLAG",                   "flag"),   # hazardous waste (RCRA)
    ("drinking_water_program", "SDWIS_FLAG",                "flag"),   # Safe Drinking Water Act
    # --- Compliance / violations ---
    ("compliance_status",    "FAC_COMPLIANCE_STATUS",       "text"),
    ("qtrs_in_noncompliance_3yr", "FAC_QTRS_WITH_NC",       "int"),    # out of last 12 quarters
    ("compliance_history_3yr", "FAC_3YR_COMPLIANCE_HISTORY", "text"),  # 12 chars, oldest->newest: S/V/U/_
    ("programs_with_serious_violation", "FAC_PROGRAMS_WITH_SNC", "int"),
    ("air_high_priority_violator", "CAA_HPV_FLAG",          "flag"),
    ("informal_actions_5yr", "FAC_INFORMAL_COUNT",          "int"),    # e.g. notices of violation
    ("formal_actions_5yr",   "FAC_FORMAL_ACTION_COUNT",     "int"),
    ("last_formal_action_date", "FAC_DATE_LAST_FORMAL_ACTION", "date"),
    # --- Inspections ---
    ("inspections_5yr",      "FAC_INSPECTION_COUNT",        "int"),
    ("last_inspection_date", "FAC_DATE_LAST_INSPECTION",    "date"),
    # --- Penalties ---
    ("penalties_5yr_usd",    "FAC_TOTAL_PENALTIES",         "money"),  # state + program penalties
    ("penalty_count_5yr",    "FAC_PENALTY_COUNT",           "int"),
    ("last_penalty_date",    "FAC_DATE_LAST_PENALTY",       "date"),
    ("last_penalty_usd",     "FAC_LAST_PENALTY_AMT",        "money"),
    ("federal_cases_5yr",    "FEC_NUMBER_OF_CASES",         "int"),
    ("federal_case_penalties_usd", "FEC_TOTAL_PENALTIES",   "money"),  # federal enforcement cases
    # --- Other EPA programs' IDs for this facility (used to cross-check the TRI and GHG joins) ---
    ("tri_ids",              "TRI_IDS",                     "codes"),  # Toxics Release Inventory facility IDs
    ("ghg_ids",              "GHG_IDS",                     "codes"),  # Greenhouse Gas Reporting Program IDs
    # --- Link to EPA's full report for this facility ---
    ("epa_report_url",       "DFR_URL",                     "text"),
]

# Columns we compute ourselves (not in ECHO), added after the ones above.
DERIVED = ["industry", "coords_in_indiana"]

SQL_TYPES = {"text": "TEXT", "county": "TEXT", "codes": "TEXT", "naics": "TEXT", "flag": "TEXT",
             "int": "INTEGER", "float": "REAL", "money": "REAL", "date": "TEXT"}


# ---------------------------------------------------------------- industry names

def read_naics_xlsx(path):
    """Return {code: title} from a Census NAICS .xlsx (code in column B, title in column C)."""
    titles = {}
    for cells in read_xlsx_rows(path):
        code, title = cells.get("B", "").strip(), cells.get("C", "").strip()
        if re.fullmatch(r"\d{2,6}|\d{2}-\d{2}", code) and title:
            # Census appends a superscript "T" to some titles (a trilateral-agreement marker).
            titles[code] = re.sub(r"(?<=[a-z)])T$", "", title).strip()
    return titles


def load_naics_titles():
    titles = {}
    for year in ("2017", "2022"):  # later year overwrites
        titles.update(read_naics_xlsx(RAW_DIR / f"naics_{year}.xlsx"))
    # Some sectors span several 2-digit codes (Manufacturing is listed as "31-33").
    for span in [c for c in titles if "-" in c]:
        lo, hi = map(int, span.split("-"))
        for n in range(lo, hi + 1):
            titles.setdefault(str(n), titles[span])
    return titles


def industry_name(naics_codes, titles):
    """Name of the facility's first listed NAICS code; the bare code if it's from an older edition."""
    if not naics_codes:
        return None
    first = naics_codes.split()[0]
    return titles.get(first, f"NAICS {first}")


# ---------------------------------------------------------------- cleaning

def clean(value, kind):
    v = value.strip()
    if kind == "flag":
        # ECHO uses "Y"/"N" for some flags but "Y"/blank for others (e.g. major, active).
        return "Y" if v.upper() in ("Y", "YES") else "N"
    if v == "":
        return None
    if kind == "int":
        return int(float(v))
    if kind in ("float", "money"):
        return float(v)
    if kind == "date":
        # ECHO dates are MM/DD/YYYY; ISO (YYYY-MM-DD) sorts correctly as text.
        return datetime.strptime(v, "%m/%d/%Y").date().isoformat()
    if kind == "county":
        # County is inconsistent in the source: "POSEY" vs "SULLIVAN COUNTY".
        v = v.upper()
        return v[:-len(" COUNTY")] if v.endswith(" COUNTY") else v
    if kind == "codes":
        return " ".join(v.split())
    if kind == "naics":
        return " ".join(c for c in v.split() if c not in PLACEHOLDER_NAICS) or None
    return v


def in_indiana_box(lat, lon):
    if lat is None or lon is None:
        return False
    b = INDIANA_BOX
    return b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]


def read_indiana_rows(naics_titles):
    with zipfile.ZipFile(RAW_ZIP) as z, z.open(CSV_NAME_IN_ZIP) as f:
        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""))

        # Stop loudly if EPA renamed or dropped a column, instead of silently writing blanks.
        missing = [epa for _, epa, _ in COLUMNS if epa not in reader.fieldnames]
        if missing:
            sys.exit(f"ERROR: ECHO file is missing expected columns: {missing}\n"
                     "EPA may have changed the format. Check the data dictionary before editing COLUMNS.")

        scanned = 0
        for raw in reader:
            scanned += 1
            if scanned % 500_000 == 0:
                print(f"  scanned {scanned:,} rows nationwide...", flush=True)
            if raw["FAC_STATE"].strip() != STATE:
                continue
            row = {}
            for ours, epa, kind in COLUMNS:
                try:
                    row[ours] = clean(raw[epa], kind)
                except ValueError:
                    # Unparseable value: keep it out rather than guess, and say so.
                    print(f"  warning: {raw['REGISTRY_ID']} has bad {epa}={raw[epa]!r}; stored as blank")
                    row[ours] = None
            # Our own derived columns.
            row["industry"] = industry_name(row["naics_codes"], naics_titles)
            row["coords_in_indiana"] = "Y" if in_indiana_box(row["lat"], row["lon"]) else "N"
            yield row
        print(f"  scanned {scanned:,} rows nationwide.")


# ---------------------------------------------------------------- output

def source_data_date():
    """When EPA generated the CSV (its timestamp inside the zip), not when we downloaded it."""
    with zipfile.ZipFile(RAW_ZIP) as z:
        return date(*z.getinfo(CSV_NAME_IN_ZIP).date_time[:3]).isoformat()


def write_outputs(rows):
    fields = [ours for ours, _, _ in COLUMNS] + DERIVED
    write_csv(OUT_CSV, fields, rows)

    con = connect()
    columns = [(ours, SQL_TYPES[kind]) for ours, _, kind in COLUMNS] + [(d, "TEXT") for d in DERIVED]
    # UNIQUE (not PRIMARY KEY) because blank IDs are allowed; SQLite lets many NULLs coexist in a UNIQUE column.
    replace_table(con, "facilities", columns, rows, indexes=["UNIQUE (registry_id)", "(is_major)"])
    set_metadata(con, "echo", {"source_url": ECHO_URL, "source_data_date": source_data_date()})
    con.commit()
    con.close()
    print(f"  table 'facilities' in {DB_PATH_REL}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-download the ECHO zip even if it exists")
    args = parser.parse_args()

    download(ECHO_URL, RAW_ZIP, force=args.force)
    for year, url in NAICS_URLS.items():
        download(url, RAW_DIR / f"naics_{year}.xlsx", force=args.force)
    naics_titles = load_naics_titles()
    print(f"Loaded {len(naics_titles):,} NAICS industry names.")

    print(f"Filtering to {STATE} ...")
    rows = list(read_indiana_rows(naics_titles))

    # Some facilities (mostly brand-new permits) have no Registry ID yet; they're kept with a blank ID.
    # Every *real* ID should appear exactly once.
    ids = [r["registry_id"] for r in rows if r["registry_id"]]
    if len(ids) != len(set(ids)):
        sys.exit(f"ERROR: {len(ids) - len(set(ids))} duplicate registry IDs in {STATE}; expected one row per facility.")
    print(f"  {len(rows) - len(ids)} facilities have no Registry ID yet (kept, ID left blank).")

    print(f"Writing {len(rows):,} {STATE} facilities:")
    write_outputs(rows)


if __name__ == "__main__":
    main()
