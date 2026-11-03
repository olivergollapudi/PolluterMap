"""
Download EPA's Facility Registry Service (FRS) file for Indiana: the master crosswalk
of facility IDs across EPA programs.

Every EPA program gives facilities its own ID (TRI uses "46312LTVST3001D", the air
emissions inventory uses "4884111", water permits use "IN0000001"...). FRS ties them
all to one Registry ID -- the `registry_id` used everywhere in this project. Sources
that don't report a Registry ID themselves (like the National Emissions Inventory)
are linked through this table.

What this does:
  1. Downloads the FRS Indiana state file (~35 MB zip) into data/raw/frs/, unless
     already there. --force re-downloads.
  2. From its program file, keeps one row per (registry_id, program, program ID).
  3. Writes data/processed/frs_indiana_program_links.csv + table frs_program_links.

    python3 scripts/build_frs_indiana.py

Program codes (e.g. EIS = emissions inventory, TRIS = TRI, NPDES = water permits,
SEMS = Superfund, EIA-860 = power plants, RMP = chemical accident risk) are listed in
the PDF inside the zip.
"""

import argparse
import csv
import io
import zipfile
from datetime import datetime

from common import RAW_DIR, PROCESSED_DIR, STATE, connect, download, replace_table, set_metadata, write_csv

URL = f"https://ordsext.epa.gov/FLA/www3/state_files/state_combined_{STATE.lower()}.zip"
ZIP_PATH = RAW_DIR / "frs" / f"state_combined_{STATE.lower()}.zip"
PROGRAM_FILE = f"{STATE}_PROGRAM_FILE.CSV"

COLUMNS = [("registry_id", "TEXT"), ("program", "TEXT"), ("program_id", "TEXT"),
           ("program_facility_name", "TEXT"), ("city", "TEXT"), ("last_reported", "TEXT")]


def iso_date(v):
    """FRS dates look like 30-SEP-26."""
    try:
        return datetime.strptime(v.strip(), "%d-%b-%y").date().isoformat()
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-download the FRS file even if it exists")
    args = parser.parse_args()

    download(URL, ZIP_PATH, force=args.force)
    rows, seen = [], set()
    with zipfile.ZipFile(ZIP_PATH) as z:
        data_date = max(i.date_time for i in z.infolist() if i.filename.endswith(".CSV"))
        with z.open(PROGRAM_FILE) as f:
            for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")):
                key = (r["REGISTRY_ID"].strip(), r["PGM_SYS_ACRNM"].strip(), r["PGM_SYS_ID"].strip())
                if not all(key) or key in seen:
                    continue
                seen.add(key)
                rows.append({"registry_id": key[0], "program": key[1], "program_id": key[2],
                             "program_facility_name": r["PRIMARY_NAME"].strip() or None,
                             "city": r["CITY_NAME"].strip() or None,
                             "last_reported": iso_date(r["LAST_REPORTED_DATE"])})

    programs = {}
    for r in rows:
        programs[r["program"]] = programs.get(r["program"], 0) + 1
    print(f"  {len(rows):,} program links for {len({r['registry_id'] for r in rows}):,} registry IDs "
          f"across {len(programs)} programs")

    print("Writing:")
    write_csv(PROCESSED_DIR / "frs_indiana_program_links.csv", [c for c, _ in COLUMNS], rows)
    con = connect()
    replace_table(con, "frs_program_links", COLUMNS, rows,
                  indexes=["UNIQUE (registry_id, program, program_id)", "(program, program_id)"])
    set_metadata(con, "frs", {"source_url": URL, "source_data_date": f"{data_date[0]}-{data_date[1]:02d}-{data_date[2]:02d}"})
    con.commit()
    con.close()
    print("  table 'frs_program_links' in data/processed/indiana.sqlite")


if __name__ == "__main__":
    main()
