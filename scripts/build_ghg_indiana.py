"""
Download EPA's Greenhouse Gas Reporting Program (GHGRP) data for Indiana and link it to our facilities.

Large emitters (roughly 25,000+ metric tons of CO2-equivalent a year: power plants,
steel mills, landfills, refineries...) report their greenhouse gas emissions to EPA.
"CO2-equivalent" (CO2e) puts all gases on one scale: 1 ton of methane counts as 25 tons
of CO2, because it traps more heat.

Data availability: the newest public year is 2023. EPA has not released 2024 data and
has proposed ending reporting for most industries, so 2023 may stay the latest.

What this does, in order:
  1. Downloads EPA's "Data Summary Spreadsheets" zip (~30 MB) into data/raw/ghg/,
     unless already there (--force re-downloads), and unpacks the two files we use.
  2. From the "Direct Point Emitters" sheets (facilities at a fixed location), keeps
     Indiana facilities:
       - every facility that reported in any year 2011-2023, with its yearly totals
       - for 2023, the breakdown by gas (CO2, methane, nitrous oxide, fluorinated gases)
  3. Links each GHG facility to an ECHO facility (registry_id): by FRS ID first, then
     by ECHO's own list of GHG IDs.
  4. Writes, into data/processed/:
       ghg_indiana_facilities.csv + table ghg_facilities        (one row per facility)
       ghg_indiana_by_year.csv    + table ghg_emissions_by_year (one row per facility per year)

Not included: pipelines, gas utilities, oil & gas basins and fuel suppliers. EPA reports
those per company or region, not at a single mappable site.

Run build_echo_indiana.py first.
    python3 scripts/build_ghg_indiana.py
"""

import argparse
import re
import sys
import zipfile

from common import RAW_DIR, PROCESSED_DIR, STATE, connect, download, read_xlsx_rows, replace_table, \
    set_metadata, write_csv

ZIP_URL = "https://www.epa.gov/system/files/other-files/2024-10/2023_data_summary_spreadsheets.zip"
LATEST_YEAR = 2023
GHG_RAW = RAW_DIR / "ghg"
ZIP_PATH = GHG_RAW / "2023_data_summary_spreadsheets.zip"
BY_YEAR_XLSX = GHG_RAW / "ghgp_data_by_year_2023.xlsx"
LATEST_XLSX = GHG_RAW / "ghgp_data_2023.xlsx"
SHEET = "Direct Point Emitters"

# 2023 breakdown columns we keep: (our name, EPA header). All in metric tons CO2e except biogenic.
GAS_COLUMNS = [
    ("total_mt_co2e",      "Total reported direct emissions"),
    ("co2_mt",             "CO2 emissions (non-biogenic)"),
    ("methane_mt_co2e",    "Methane (CH4) emissions"),
    ("n2o_mt_co2e",        "Nitrous Oxide (N2O) emissions"),
    ("hfc_mt_co2e",        "HFC emissions"),
    ("pfc_mt_co2e",        "PFC emissions"),
    ("sf6_mt_co2e",        "SF6 emissions"),
    ("nf3_mt_co2e",        "NF3 emissions"),
    ("other_fluorinated_mt_co2e", "Other Fully Fluorinated GHG emissions"),
    ("hfe_mt_co2e",        "HFE emissions"),
    ("short_lived_mt_co2e", "Very Short-lived Compounds emissions"),
    ("other_ghg_mt_co2e",  "Other GHGs (metric tons CO2e)"),
    ("biogenic_co2_mt",    "Biogenic CO2 emissions (metric tons)"),  # from burning wood/biomass; NOT in the total
]


def sheet_rows(path):
    """Yield the sheet's data rows as {header: value}. The header row is the one starting 'Facility Id'
    (EPA puts a few title lines above it)."""
    header = None
    for cells in read_xlsx_rows(path, SHEET):
        if header is None:
            if cells.get("A", "").strip() == "Facility Id":
                header = {col: " ".join(name.split()) for col, name in cells.items()}
            continue
        # Blank cells are absent from the file, so fill every header (blank = "").
        yield {name: cells.get(c, "").strip() for c, name in header.items()}


def number(v):
    """EPA writes 'confidential' for some values; those become blank (None), not 0."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-download the GHG zip even if it exists")
    args = parser.parse_args()

    download(ZIP_URL, ZIP_PATH, force=args.force)
    with zipfile.ZipFile(ZIP_PATH) as z:
        for xlsx in (BY_YEAR_XLSX, LATEST_XLSX):
            if args.force or not xlsx.exists():
                xlsx.write_bytes(z.read(xlsx.name))

    # --- every Indiana facility, with its total for each year it reported
    facilities, by_year = {}, []
    for r in sheet_rows(BY_YEAR_XLSX):
        if r.get("State") != STATE:
            continue
        fid = r["Facility Id"]
        years = {}
        for h, v in r.items():
            m = re.fullmatch(r"(\d{4}) Total reported direct emissions", h)
            if m and v != "":
                years[int(m.group(1))] = number(v)
        for y, total in sorted(years.items()):
            by_year.append({"ghg_facility_id": fid, "year": y, "total_mt_co2e": total})
        facilities[fid] = {
            "ghg_facility_id": fid,
            "frs_id": r.get("FRS Id") or None,
            "facility_name": r.get("Facility Name"),
            "address": r.get("Address"),
            "city": r.get("City"),
            "zip": r.get("Zip Code"),
            "county": r.get("County"),
            "lat": number(r.get("Latitude")),
            "lon": number(r.get("Longitude")),
            "primary_naics": r.get("Primary NAICS Code"),
            "industry_sectors": r.get("Latest Reported Industry Type (sectors)"),
            "industry_subparts": r.get("Latest Reported Industry Type (subparts)"),
            "first_year_reported": min(years) if years else None,
            "last_year_reported": max(years) if years else None,
        }

    # --- 2023 breakdown by gas
    found_2023 = 0
    for r in sheet_rows(LATEST_XLSX):
        if r.get("State") != STATE:
            continue
        missing = [h for _, h in GAS_COLUMNS if h not in r]
        if missing:
            sys.exit(f"ERROR: {LATEST_XLSX.name} is missing expected columns: {missing}")
        f = facilities.get(r["Facility Id"])
        if f is None:  # shouldn't happen: the by-year file lists every facility
            print(f"  warning: {r['Facility Id']} {r.get('Facility Name')} is in the {LATEST_YEAR} file only")
            continue
        found_2023 += 1
        for ours, h in GAS_COLUMNS:
            f[f"{ours}_{LATEST_YEAR}"] = number(r.get(h))

    # --- link to ECHO
    con = connect()
    try:
        registry_ids = {r for (r,) in con.execute("SELECT registry_id FROM facilities WHERE registry_id IS NOT NULL")}
        ghg_map = {g: reg for reg, ids in con.execute("SELECT registry_id, ghg_ids FROM facilities WHERE ghg_ids IS NOT NULL")
                   for g in ids.split()}
    except Exception:
        sys.exit("ERROR: no 'facilities' table yet. Run scripts/build_echo_indiana.py first.")
    for f in facilities.values():
        if f["frs_id"] in registry_ids:
            f["registry_id"], f["match_method"] = f["frs_id"], "frs_id"
        elif f["ghg_facility_id"] in ghg_map:
            f["registry_id"], f["match_method"] = ghg_map[f["ghg_facility_id"]], "echo_ghg_ids"
        else:
            f["registry_id"], f["match_method"] = None, "unmatched"

    rows = list(facilities.values())
    reporting_latest = [f for f in rows if f["last_year_reported"] == LATEST_YEAR]
    print(f"  {len(rows)} Indiana facilities reported at some point 2011-{LATEST_YEAR}; "
          f"{len(reporting_latest)} reported in {LATEST_YEAR} ({found_2023} with gas breakdown)")
    methods = {}
    for f in rows:
        methods[f["match_method"]] = methods.get(f["match_method"], 0) + 1
    print("  linked to ECHO: " + ", ".join(f"{k} {v}" for k, v in sorted(methods.items())))

    fac_cols = [("ghg_facility_id", "TEXT"), ("frs_id", "TEXT"), ("registry_id", "TEXT"), ("match_method", "TEXT"),
                ("facility_name", "TEXT"), ("address", "TEXT"), ("city", "TEXT"), ("zip", "TEXT"), ("county", "TEXT"),
                ("lat", "REAL"), ("lon", "REAL"), ("primary_naics", "TEXT"), ("industry_sectors", "TEXT"),
                ("industry_subparts", "TEXT"), ("first_year_reported", "INTEGER"), ("last_year_reported", "INTEGER")] + \
               [(f"{ours}_{LATEST_YEAR}", "REAL") for ours, _ in GAS_COLUMNS]
    year_cols = [("ghg_facility_id", "TEXT"), ("year", "INTEGER"), ("total_mt_co2e", "REAL")]

    print("Writing:")
    write_csv(PROCESSED_DIR / "ghg_indiana_facilities.csv", [c for c, _ in fac_cols], rows)
    write_csv(PROCESSED_DIR / "ghg_indiana_by_year.csv", [c for c, _ in year_cols], by_year)
    replace_table(con, "ghg_facilities", fac_cols, rows, indexes=["UNIQUE (ghg_facility_id)", "(registry_id)"])
    replace_table(con, "ghg_emissions_by_year", year_cols, by_year, indexes=["UNIQUE (ghg_facility_id, year)"])
    set_metadata(con, "ghg", {"source_url": ZIP_URL, "latest_year": LATEST_YEAR,
                              "note": "EPA data as reported by facilities as of 2024-08-16; 2024 data not released"})
    con.commit()
    con.close()
    print("  tables 'ghg_facilities' and 'ghg_emissions_by_year' in data/processed/indiana.sqlite")


if __name__ == "__main__":
    main()
