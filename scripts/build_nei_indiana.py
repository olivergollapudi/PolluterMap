"""
Download EPA's 2023 National Emissions Inventory (NEI) for Indiana facilities and link them.

TRI covers listed toxic chemicals. The NEI covers the everyday air pollutants behind
most air-pollution health harm -- fine particles (PM2.5), sulfur dioxide (SO2),
nitrogen oxides (NOx), carbon monoxide, volatile organic compounds, ammonia, lead --
plus ~180 hazardous air pollutants, for many more facilities than TRI (including
airports, rail yards and compressor stations). EPA publishes it every three years;
2023 is the newest.

What this does, in order:
  1. Downloads the 2023 NEI "facility-level by pollutant" summary (~40 MB zip, all US)
     into data/raw/nei/, unless already there. --force re-downloads.
  2. Keeps Indiana rows: one per facility per pollutant.
  3. Links each NEI facility to a registry_id through the FRS crosswalk (program "EIS"),
     falling back to the facility's TRI ID. NEI itself doesn't report Registry IDs.
  4. Writes, into data/processed/:
       nei_indiana_emissions.csv  + table nei_emissions  (facility x pollutant)
       nei_indiana_facilities.csv + table nei_facilities (one row per facility, with the
                                                          main pollutants as columns)

Run build_echo_indiana.py and build_frs_indiana.py first.
    python3 scripts/build_nei_indiana.py
"""

import argparse
import csv
import io
import sys
import zipfile
from collections import defaultdict

from common import RAW_DIR, PROCESSED_DIR, STATE, connect, download, replace_table, set_metadata, write_csv

YEAR = 2023
URL = "https://gaftp.epa.gov/air/nei/2023/data_summaries/eis_report_38234_2023NEI_facility_summary_21jul2026.zip"
ZIP_PATH = RAW_DIR / "nei" / "2023NEI_facility_summary.zip"
TONS_TO_POUNDS = 2000.0

# The headline pollutants, as columns on nei_facilities: (our column, NEI pollutant code).
# Criteria pollutants are reported in tons; lead in pounds.
KEY_POLLUTANTS = [
    ("pm25_tons", "PM25-PRI"),   # fine particles: the deadliest common air pollutant
    ("pm10_tons", "PM10-PRI"),
    ("so2_tons", "SO2"),
    ("nox_tons", "NOX"),
    ("co_tons", "CO"),
    ("voc_tons", "VOC"),
    ("nh3_tons", "NH3"),         # ammonia
    ("lead_lb", "7439921"),
]


def read_indiana(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.startswith("emis_sum_fac") and n.endswith(".csv"))
        with z.open(name) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""))
            needed = ["state", "eis facility id", "pollutant code", "total emissions", "emissions uom", "tri facility id"]
            missing = [c for c in needed if c not in reader.fieldnames]
            if missing:
                sys.exit(f"ERROR: NEI file is missing expected columns: {missing}")
            for r in reader:
                if r["state"] == STATE:
                    yield {k: v.strip() for k, v in r.items()}


def number(v):
    try:
        return float(v)
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-download the NEI file even if it exists")
    args = parser.parse_args()

    download(URL, ZIP_PATH, force=args.force)

    con = connect()
    try:
        eis_map = dict(con.execute("SELECT program_id, registry_id FROM frs_program_links WHERE program = 'EIS'"))
        registry_ids = {r for (r,) in con.execute("SELECT registry_id FROM facilities WHERE registry_id IS NOT NULL")}
        tri_map = {t: reg for reg, ids in con.execute("SELECT registry_id, tri_ids FROM facilities WHERE tri_ids IS NOT NULL")
                   for t in ids.split()}
    except Exception:
        sys.exit("ERROR: run scripts/build_echo_indiana.py and scripts/build_frs_indiana.py first.")

    emissions, facilities = [], {}
    for r in read_indiana(ZIP_PATH):
        fid = r["eis facility id"]
        amount = number(r["total emissions"])
        unit = r["emissions uom"]
        emissions.append({
            "eis_facility_id": fid,
            "pollutant_code": r["pollutant code"],
            "pollutant": r["pollutant desc"],
            "pollutant_type": r["pollutant type(s)"],   # CAP = criteria pollutant, HAP = hazardous air pollutant
            "hap_type": r["hap type"] or None,
            "amount": amount,
            "unit": unit,
            "amount_lb": None if amount is None else amount * (TONS_TO_POUNDS if unit == "TON" else 1.0),
            "outlier_flag": r["outlier?"],               # EPA's own flag for suspiciously high/low values
        })
        if fid not in facilities:
            facilities[fid] = {
                "eis_facility_id": fid,
                "tri_facility_id": r["tri facility id"] or None,
                "facility_name": r["site name"],
                "company": r["company name"] or None,
                "naics": r["primary naics code"] or None,
                "naics_description": r["primary naics description"] or None,
                "source_type": r["facility source type"] or None,
                "city": r["city"] or None,
                "county": r["county"] or None,
                "lat": number(r["site latitude"]),
                "lon": number(r["site longitude"]),
            }

    # Link to registry_id. FRS sometimes gives a plant's emissions-inventory record its own,
    # duplicate Registry ID (e.g. Burns Harbor steel mill has two). Prefer the ID our ECHO
    # facilities table uses, so all of a plant's data lands on the same map dot.
    methods = defaultdict(int)
    for f in facilities.values():
        reg = eis_map.get(f["eis_facility_id"])
        if reg in registry_ids:
            f["registry_id"], f["match_method"] = reg, "frs_eis"
        elif f["tri_facility_id"] in tri_map:
            f["registry_id"], f["match_method"] = tri_map[f["tri_facility_id"]], "echo_tri_ids"
        elif reg:
            f["registry_id"], f["match_method"] = reg, "frs_eis_not_in_echo"
        else:
            f["registry_id"], f["match_method"] = None, "unmatched"
        f["in_echo"] = "Y" if f["registry_id"] in registry_ids else "N"
        methods[f["match_method"]] += 1
    for e in emissions:
        e["registry_id"] = facilities[e["eis_facility_id"]]["registry_id"]

    # Headline pollutants as columns, plus total hazardous air pollutants in pounds
    by_fac = defaultdict(dict)
    hap_lb = defaultdict(float)
    for e in emissions:
        by_fac[e["eis_facility_id"]][e["pollutant_code"]] = e["amount"]
        if "HAP" in e["pollutant_type"] and e["amount_lb"] is not None:
            hap_lb[e["eis_facility_id"]] += e["amount_lb"]
    for fid, f in facilities.items():
        for col, code in KEY_POLLUTANTS:
            f[col] = by_fac[fid].get(code)
        f["total_hap_lb"] = hap_lb.get(fid, 0.0)

    rows = list(facilities.values())
    print(f"  {len(rows):,} Indiana facilities, {len(emissions):,} facility-pollutant rows ({YEAR})")
    print("  linked to registry_id: " + ", ".join(f"{k} {v}" for k, v in sorted(methods.items())) +
          f"; {sum(f['in_echo'] == 'Y' for f in rows)} are in the ECHO facilities table")

    fac_cols = [("eis_facility_id", "TEXT"), ("registry_id", "TEXT"), ("match_method", "TEXT"), ("in_echo", "TEXT"),
                ("tri_facility_id", "TEXT"), ("facility_name", "TEXT"), ("company", "TEXT"), ("naics", "TEXT"),
                ("naics_description", "TEXT"), ("source_type", "TEXT"), ("city", "TEXT"), ("county", "TEXT"),
                ("lat", "REAL"), ("lon", "REAL")] + [(c, "REAL") for c, _ in KEY_POLLUTANTS] + [("total_hap_lb", "REAL")]
    em_cols = [("eis_facility_id", "TEXT"), ("registry_id", "TEXT"), ("pollutant_code", "TEXT"), ("pollutant", "TEXT"),
               ("pollutant_type", "TEXT"), ("hap_type", "TEXT"), ("amount", "REAL"), ("unit", "TEXT"),
               ("amount_lb", "REAL"), ("outlier_flag", "TEXT")]

    print("Writing:")
    write_csv(PROCESSED_DIR / "nei_indiana_facilities.csv", [c for c, _ in fac_cols], rows)
    write_csv(PROCESSED_DIR / "nei_indiana_emissions.csv", [c for c, _ in em_cols], emissions)
    replace_table(con, "nei_facilities", fac_cols, rows, indexes=["UNIQUE (eis_facility_id)", "(registry_id)"])
    replace_table(con, "nei_emissions", em_cols, emissions,
                  indexes=["UNIQUE (eis_facility_id, pollutant_code)", "(registry_id)", "(pollutant_code)"])
    set_metadata(con, "nei", {"source_url": URL, "year": YEAR})
    con.commit()
    con.close()
    print("  tables 'nei_facilities' and 'nei_emissions' in data/processed/indiana.sqlite")


if __name__ == "__main__":
    main()
