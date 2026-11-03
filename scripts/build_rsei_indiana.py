"""
Download EPA's RSEI (Risk-Screening Environmental Indicators) results for Indiana TRI facilities.

TRI tells us pounds. But a pound of zinc and a pound of benzene aren't equally harmful,
and a release next to a neighborhood matters more than one in an empty field. RSEI
weighs each TRI release:
  - RSEI Hazard = pounds x toxicity weight
  - RSEI Score  = Hazard x modeled exposure (where it goes: air, water...) x people exposed
Scores are unitless and only meaningful compared with each other (higher = more risk).
They're a screening tool, not a measured health risk.

Newest available: RSEI v2.3.12, covering TRI years through 2022 (published March 2024).

What this does, in order:
  1. Downloads the RSEI Public Release Data zip (~430 MB, all US, 1988-2022) into
     data/raw/rsei/, unless already there. --force re-downloads.
  2. Follows RSEI's chain of tables for Indiana facilities, FIRST_YEAR onward:
       Facility (TRI ID, state) -> Submissions (one per chemical per year)
       -> Releases (one per place it went) -> Elements (the scores)
  3. Adds up scores per facility per year, and per facility per chemical per year.
     Scores include releases and transfers attributed to the Indiana facility that made
     them, as EPA's EasyRSEI does. `pounds_incl_transfers` therefore also counts waste
     shipped off-site for recycling/energy recovery, so it's much larger than TRI's
     "total releases" -- use TRI for pounds, RSEI for scores.
  4. Links facilities to registry_id via their TRI ID (same as build_tri_indiana.py).
  5. Writes, into data/processed/ (CSV + table of the same name in indiana.sqlite):
       rsei_facility_years    facility x year
       rsei_chemical_years    facility x chemical x year

Run build_echo_indiana.py and build_tri_indiana.py first.
    python3 scripts/build_rsei_indiana.py
"""

import argparse
import csv
import io
import sys
import zipfile
from collections import defaultdict

from common import RAW_DIR, PROCESSED_DIR, STATE, connect, download, replace_table, set_metadata, write_csv

VERSION = "v2.3.12"
URL = "https://gaftp.epa.gov/rsei/Current_Version/V2312_RY2022/Public_Release_Data/RSEIv2312_Public_Release_Data.zip"
ZIP_PATH = RAW_DIR / "rsei" / "RSEIv2312_Public_Release_Data.zip"
FIRST_YEAR = 2013


def find_member(z, word):
    """Find the CSV inside the zip whose name contains `word` (e.g. 'facility')."""
    hits = [n for n in z.namelist() if n.lower().endswith(".csv") and word in n.lower().rsplit("/", 1)[-1]]
    if len(hits) != 1:
        sys.exit(f"ERROR: expected one '{word}' CSV in {ZIP_PATH.name}, found {hits}. Files: {z.namelist()}")
    return hits[0]


def read_csv(z, word, needed):
    name = find_member(z, word)
    f = io.TextIOWrapper(z.open(name), encoding="utf-8", errors="replace", newline="")
    reader = csv.DictReader(f)
    missing = [c for c in needed if c not in reader.fieldnames]
    if missing:
        sys.exit(f"ERROR: {name} is missing expected columns {missing}; it has {reader.fieldnames}")
    return reader


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-download the RSEI zip even if it exists")
    args = parser.parse_args()

    download(URL, ZIP_PATH, force=args.force)

    con = connect()
    try:
        tri_to_registry = dict(con.execute(
            "SELECT trifd, registry_id FROM tri_facility_years WHERE registry_id IS NOT NULL GROUP BY trifd"))
        registry_ids = {r for (r,) in con.execute("SELECT registry_id FROM facilities WHERE registry_id IS NOT NULL")}
        echo_tri = {t: reg for reg, ids in con.execute("SELECT registry_id, tri_ids FROM facilities WHERE tri_ids IS NOT NULL")
                    for t in ids.split()}
    except Exception:
        sys.exit("ERROR: run scripts/build_echo_indiana.py and scripts/build_tri_indiana.py first.")

    with zipfile.ZipFile(ZIP_PATH) as z:
        print("  zip contains: " + ", ".join(n.rsplit("/", 1)[-1] for n in z.namelist()))

        facilities = {}
        for r in read_csv(z, "facility", ["FacilityNumber", "FacilityID", "State", "FRSID", "FacilityName"]):
            if r["State"].strip() == STATE:
                facilities[r["FacilityNumber"]] = {"trifd": r["FacilityID"].strip(), "frs_id": r["FRSID"].strip() or None,
                                                   "facility_name": r["FacilityName"].strip(), "city": r.get("City", "").strip()}
        print(f"  {len(facilities):,} Indiana facilities in RSEI")

        chem_reader = read_csv(z, "chemical", ["ChemicalNumber"])
        name_col = next((c for c in ("Chemical", "ChemName", "ChemicalName", "FullChemicalName") if c in chem_reader.fieldnames), None)
        chemicals = {r["ChemicalNumber"]: (r[name_col].strip() if name_col else r["ChemicalNumber"]) for r in chem_reader}

        submissions = {}
        for r in read_csv(z, "submission", ["SubmissionNumber", "FacilityNumber", "ChemicalNumber", "SubmissionYear"]):
            if r["FacilityNumber"] in facilities and int(r["SubmissionYear"]) >= FIRST_YEAR:
                submissions[r["SubmissionNumber"]] = (r["FacilityNumber"], int(r["SubmissionYear"]), r["ChemicalNumber"])
        print(f"  {len(submissions):,} Indiana chemical reports since {FIRST_YEAR}")

        releases = {}
        for r in read_csv(z, "release", ["ReleaseNumber", "SubmissionNumber"]):
            if r["SubmissionNumber"] in submissions:
                releases[r["ReleaseNumber"]] = r["SubmissionNumber"]
        print(f"  {len(releases):,} release records; scanning scores (the largest file, takes a minute)...")

        fac_year = defaultdict(lambda: defaultdict(float))
        chem_year = defaultdict(lambda: defaultdict(float))
        scanned = 0
        for r in read_csv(z, "element", ["ReleaseNumber", "Score", "CScore", "NCScore", "Hazard", "PoundsPT"]):
            scanned += 1
            if scanned % 5_000_000 == 0:
                print(f"    {scanned:,} score rows scanned...", flush=True)
            sub = releases.get(r["ReleaseNumber"])
            if sub is None:
                continue
            fac_num, year, chem_num = submissions[sub]
            vals = {"rsei_score": num(r["Score"]), "cancer_score": num(r["CScore"]),
                    "noncancer_score": num(r["NCScore"]), "rsei_hazard": num(r["Hazard"]),
                    "pounds_incl_transfers": num(r["PoundsPT"])}
            for k, v in vals.items():
                fac_year[(fac_num, year)][k] += v
                chem_year[(fac_num, year, chem_num)][k] += v
        print(f"    {scanned:,} score rows scanned.")

    def link(fac):
        reg = tri_to_registry.get(fac["trifd"]) or echo_tri.get(fac["trifd"])
        if reg:
            return reg, "tri_id"
        if fac["frs_id"] in registry_ids:
            return fac["frs_id"], "rsei_frs_id"
        return fac["frs_id"], "not_in_echo"

    links = {n: link(f) for n, f in facilities.items()}
    n_chem = defaultdict(int)
    for fn, y, _c in chem_year:
        n_chem[(fn, y)] += 1
    fy_rows = []
    for (fac_num, year), v in sorted(fac_year.items(), key=lambda kv: (facilities[kv[0][0]]["trifd"], kv[0][1])):
        f = facilities[fac_num]
        fy_rows.append({"trifd": f["trifd"], "registry_id": links[fac_num][0], "match_method": links[fac_num][1],
                        "facility_name": f["facility_name"], "city": f["city"], "year": year,
                        **{k: round(x, 4) for k, x in v.items()}, "n_chemicals": n_chem[(fac_num, year)]})
    cy_rows = [{"trifd": facilities[fn]["trifd"], "registry_id": links[fn][0], "year": y,
                "chemical": chemicals.get(c, c), **{k: round(x, 4) for k, x in v.items()}}
               for (fn, y, c), v in sorted(chem_year.items(), key=lambda kv: (facilities[kv[0][0]]["trifd"], kv[0][1], kv[0][2]))]

    methods = defaultdict(int)
    for reg, m in {links[fn] for fn, _ in fac_year}:
        methods[m] += 1
    print(f"  facilities with scores linked: " + ", ".join(f"{k} {v}" for k, v in sorted(methods.items())))

    score_cols = [("rsei_score", "REAL"), ("cancer_score", "REAL"), ("noncancer_score", "REAL"),
                  ("rsei_hazard", "REAL"), ("pounds_incl_transfers", "REAL")]
    fy_cols = [("trifd", "TEXT"), ("registry_id", "TEXT"), ("match_method", "TEXT"), ("facility_name", "TEXT"),
               ("city", "TEXT"), ("year", "INTEGER")] + score_cols + [("n_chemicals", "INTEGER")]
    cy_cols = [("trifd", "TEXT"), ("registry_id", "TEXT"), ("year", "INTEGER"), ("chemical", "TEXT")] + score_cols

    print("Writing:")
    write_csv(PROCESSED_DIR / "rsei_indiana_facility_years.csv", [c for c, _ in fy_cols], fy_rows)
    write_csv(PROCESSED_DIR / "rsei_indiana_chemical_years.csv", [c for c, _ in cy_cols], cy_rows)
    replace_table(con, "rsei_facility_years", fy_cols, fy_rows, indexes=["UNIQUE (trifd, year)", "(registry_id)"])
    replace_table(con, "rsei_chemical_years", cy_cols, cy_rows, indexes=["(trifd, year)", "(registry_id)"])
    set_metadata(con, "rsei", {"source_url": URL, "version": VERSION, "years": f"{FIRST_YEAR}-2022"})
    con.commit()
    con.close()
    print("  tables 'rsei_facility_years' and 'rsei_chemical_years' in data/processed/indiana.sqlite")


if __name__ == "__main__":
    main()
