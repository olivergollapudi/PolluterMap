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
  6. Writes data/processed/echo_indiana.csv and data/processed/echo_indiana.sqlite.

Uses only Python's standard library -- nothing to pip install.

Run from the project root:
    python3 scripts/build_echo_indiana.py
"""

import argparse
import csv
import io
import re
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime
from pathlib import Path

ECHO_URL = "https://echo.epa.gov/files/echodownloads/echo_exporter.zip"
CSV_NAME_IN_ZIP = "ECHO_EXPORTER.csv"
STATE = "IN"

# ECHO mixes codes from several NAICS editions, so we load two. 2022 titles win where both exist.
NAICS_URLS = {
    "2017": "https://www.census.gov/naics/2017NAICS/2-6%20digit_2017_Codes.xlsx",
    "2022": "https://www.census.gov/naics/2022NAICS/2-6%20digit_2022_Codes.xlsx",
}
# "11111" officially means Soybean Farming, but in ECHO it's attached to trucking firms, universities,
# mines... almost all hazardous-waste records. It's a filler value, so we treat it as "no code".
PLACEHOLDER_NAICS = {"11111"}

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
RAW_ZIP = RAW_DIR / "echo_exporter.zip"
OUT_CSV = ROOT / "data" / "processed" / "echo_indiana.csv"
OUT_DB = ROOT / "data" / "processed" / "echo_indiana.sqlite"

# Rough box around Indiana. Used only to flag suspicious coordinates, not to drop rows.
INDIANA_BOX = {"lat_min": 37.5, "lat_max": 42.0, "lon_min": -88.3, "lon_max": -84.6}

# The columns we keep: (our name, EPA's column name, type).
# Types: text, flag (Y/N), int, money, date, county, codes.
# EPA's meaning for every column is in data/raw/echo_exporter_columns.xlsx.
COLUMNS = [
    # --- Identity & location ---
    ("registry_id",          "REGISTRY_ID",                 "text"),   # FRS ID; Phase 2 joins on this
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
    # --- Link to EPA's full report for this facility ---
    ("epa_report_url",       "DFR_URL",                     "text"),
]

# Columns we compute ourselves (not in ECHO), added after the ones above.
DERIVED = ["industry", "coords_in_indiana"]

SQL_TYPES = {"text": "TEXT", "county": "TEXT", "codes": "TEXT", "naics": "TEXT", "flag": "TEXT",
             "int": "INTEGER", "float": "REAL", "money": "REAL", "date": "TEXT"}


# ---------------------------------------------------------------- download

def download(url, dest, force=False):
    if dest.exists() and not force:
        print(f"Using existing download: {dest.relative_to(ROOT)}  (use --force to re-download)")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    print(f"Downloading {url}")
    # census.gov rejects Python's default user agent, so identify as a normal client.
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PolluterMap data script)"})
    with urllib.request.urlopen(req) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total:3d}% of {total / 1e6:.0f} MB", end="", flush=True)
    tmp.replace(dest)  # only rename once complete, so a half download is never mistaken for a real one
    print("\n  done.")


# ---------------------------------------------------------------- industry names

def read_naics_xlsx(path):
    """Return {code: title} from a Census NAICS .xlsx (code in column B, title in column C).
    Excel files are zipped XML, so the standard library can read them without extra packages."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        shared = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t"))
                  for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    titles = {}
    for row in sheet.iter(f"{{{ns['m']}}}row"):
        cells = {}
        for c in row.findall("m:c", ns):
            v = c.find("m:v", ns)
            if v is None:
                continue
            col = re.match(r"[A-Z]+", c.get("r")).group()
            cells[col] = shared[int(v.text)] if c.get("t") == "s" else v.text
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
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # Build the database in a temp file and swap it in, so a crash never leaves a half-built DB.
    tmp_db = OUT_DB.with_suffix(".sqlite.tmp")
    tmp_db.unlink(missing_ok=True)
    con = sqlite3.connect(tmp_db)
    col_defs = ",\n  ".join([f"{ours} {SQL_TYPES[kind]}" for ours, _, kind in COLUMNS] + [f"{d} TEXT" for d in DERIVED])
    con.execute(f"CREATE TABLE facilities (\n  {col_defs}\n)")
    placeholders = ", ".join("?" for _ in fields)
    con.executemany(f"INSERT INTO facilities ({', '.join(fields)}) VALUES ({placeholders})",
                    ([r[k] for k in fields] for r in rows))
    # UNIQUE (not PRIMARY KEY) because blank IDs are allowed; SQLite lets many NULLs coexist in a UNIQUE column.
    con.execute("CREATE UNIQUE INDEX idx_registry_id ON facilities (registry_id)")
    con.execute("CREATE INDEX idx_major ON facilities (is_major)")
    con.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO metadata VALUES (?, ?)", [
        ("source_url", ECHO_URL),
        ("source_data_date", source_data_date()),
        ("built_on", date.today().isoformat()),
        ("state", STATE),
    ])
    con.commit()
    con.close()
    tmp_db.replace(OUT_DB)


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

    write_outputs(rows)
    print(f"Wrote {len(rows):,} {STATE} facilities to:")
    print(f"  {OUT_CSV.relative_to(ROOT)}  ({OUT_CSV.stat().st_size / 1e6:.1f} MB)")
    print(f"  {OUT_DB.relative_to(ROOT)}  ({OUT_DB.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
