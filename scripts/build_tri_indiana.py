"""
Download EPA's Toxics Release Inventory (TRI) for Indiana and link it to our facilities.

TRI is where factories report how much of each listed toxic chemical they
released into the air, water and land each year. One TRI "form" = one facility,
one chemical, one year.

What this does, in order:
  1. Downloads the TRI Basic Data File for Indiana for each year FIRST_YEAR..LAST_YEAR
     into data/raw/tri/ (a few MB each), unless already there. --force re-downloads.
  2. Keeps the useful columns: chemical, hazard flags (carcinogen, PBT, PFAS), and
     where it went (stack/fugitive air, water, land, off-site).
  3. Converts everything to pounds (dioxins are reported in grams).
  4. Links each TRI facility to an ECHO facility (registry_id): first by the FRS ID
     TRI reports, then -- if that's blank or unknown -- by ECHO's own list of TRI IDs.
  5. Writes, into data/processed/:
       tri_indiana_releases.csv   + table tri_releases       (one row per form)
       tri_indiana_facility_years.csv + table tri_facility_years (totals per facility per year)

Run build_echo_indiana.py first (this script needs its "facilities" table to link to).
    python3 scripts/build_tri_indiana.py

Field definitions: https://www.epa.gov/toxics-release-inventory-tri-program/tri-basic-data-files-calendar-years-1987-present
"""

import argparse
import csv
import re
import sys
from collections import defaultdict

from common import RAW_DIR, PROCESSED_DIR, STATE, connect, download, replace_table, set_metadata, write_csv

FIRST_YEAR = 2015
LAST_YEAR = 2024  # newest year EPA has published as of Nov 2026; bump when the next one appears
URL = "https://data.epa.gov/efservice/downloads/tri/mv_tri_basic_download/{year}_{state}/csv"
TRI_RAW = RAW_DIR / "tri"

GRAMS_TO_POUNDS = 0.00220462262

# (our name, TRI column name with its "12. " number prefix removed, type)
COLUMNS = [
    ("year",                "YEAR",                   "int"),
    ("doc_ctrl_num",        "DOC_CTRL_NUM",           "text"),   # unique ID of the form
    ("trifd",               "TRIFD",                  "text"),   # TRI's own facility ID
    ("frs_id",              "FRS ID",                 "text"),   # = ECHO registry_id, when present
    ("facility_name",       "FACILITY NAME",          "text"),
    ("street",              "STREET ADDRESS",         "text"),
    ("city",                "CITY",                   "text"),
    ("county",              "COUNTY",                 "text"),
    ("zip",                 "ZIP",                    "text"),
    ("lat",                 "LATITUDE",               "float"),
    ("lon",                 "LONGITUDE",              "float"),
    ("parent_company",      "STANDARD PARENT CO NAME", "text"),
    ("federal_facility",    "FEDERAL FACILITY",       "flag"),
    ("industry_sector",     "INDUSTRY SECTOR",        "text"),
    ("primary_naics",       "PRIMARY NAICS",          "text"),
    ("chemical",            "CHEMICAL",               "text"),
    ("cas_number",          "CAS#",                   "text"),
    ("classification",      "CLASSIFICATION",         "text"),   # TRI / PBT / Dioxin
    ("is_metal",            "METAL",                  "flag"),
    ("is_carcinogen",       "CARCINOGEN",             "flag"),   # OSHA carcinogen
    ("is_pbt",              "PBT",                    "flag"),   # persistent, bioaccumulative, toxic
    ("is_pfas",             "PFAS",                   "flag"),   # "forever chemicals"
    ("is_clean_air_act_hap", "CLEAN AIR ACT CHEMICAL", "flag"),  # hazardous air pollutant
    ("form_type",           "FORM TYPE",              "text"),   # R = full report; A = short form, amounts NOT reported
    ("unit",                "UNIT OF MEASURE",        "text"),   # Pounds, or Grams for dioxins
    # Amounts below are in `unit` as reported; *_lb columns are added after conversion.
    ("fugitive_air",        "5.1 - FUGITIVE AIR",     "float"),  # leaks, vents, not through a stack
    ("stack_air",           "5.2 - STACK AIR",        "float"),
    ("water",               "5.3 - WATER",            "float"),  # direct to rivers/lakes
    # Land columns: we read the detailed sub-columns, because the summary columns
    # ("5.4 - UNDERGROUND", "5.5.1 - LANDFILLS", "5.5.3 - SURFACE IMPNDMNT") are often left at 0
    # when the amount was entered in the sub-columns. The sub-columns always add up to TRI's total.
    ("underground_class_1", "5.4.1 - UNDERGROUND CL I",   "float"),  # deep hazardous-waste wells
    ("underground_class_2_5", "5.4.2 - UNDERGROUND C II-V", "float"),
    ("rcra_landfill",       "5.5.1A - RCRA C LANDFILL",   "float"),  # permitted hazardous-waste landfill
    ("other_landfill",      "5.5.1B - OTHER LANDFILLS",   "float"),
    ("land_treatment",      "5.5.2 - LAND TREATMENT",     "float"),
    ("rcra_surface_impoundment", "5.5.3A - RCRA SURFACE IM", "float"),
    ("other_surface_impoundment", "5.5.3B - OTHER SURFACE I", "float"),  # e.g. waste ponds, coal ash ponds
    ("other_land_disposal", "5.5.4 - OTHER DISPOSAL",     "float"),
    ("on_site_releases",    "ON-SITE RELEASE TOTAL",  "float"),
    ("off_site_releases",   "OFF-SITE RELEASE TOTAL", "float"),  # sent elsewhere and released there
    ("total_releases",      "TOTAL RELEASES",         "float"),  # on-site + off-site
    ("total_transfers",     "6.2 - TOTAL TRANSFER",   "float"),  # all waste shipped off-site (incl. recycling)
]
LAND = ["underground_class_1", "underground_class_2_5", "rcra_landfill", "other_landfill", "land_treatment",
        "rcra_surface_impoundment", "other_surface_impoundment", "other_land_disposal"]
AMOUNTS = ["fugitive_air", "stack_air", "water"] + LAND + ["on_site_releases", "off_site_releases",
                                                            "total_releases", "total_transfers"]

SQL_TYPES = {"int": "INTEGER", "float": "REAL", "text": "TEXT", "flag": "TEXT"}


def clean(v, kind):
    v = v.strip()
    if kind == "flag":
        return "Y" if v.upper() in ("YES", "Y") else "N"
    if v == "":
        return None
    if kind == "int":
        return int(v)
    if kind == "float":
        return float(v)
    return v


def read_year(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        # TRI headers look like "37. CHEMICAL"; strip the number so names are stable.
        reader.fieldnames = [re.sub(r"^\d+\.\s*", "", h).strip() for h in reader.fieldnames]
        missing = [tri for _, tri, _ in COLUMNS if tri not in reader.fieldnames]
        if missing:
            sys.exit(f"ERROR: {path.name} is missing expected columns: {missing}. EPA may have changed the format.")
        for raw in reader:
            row = {ours: clean(raw[tri], kind) for ours, tri, kind in COLUMNS}
            factor = GRAMS_TO_POUNDS if (row["unit"] or "").lower() == "grams" else 1.0
            for a in AMOUNTS:
                row[f"{a}_lb"] = (row[a] or 0.0) * factor
            row["air_lb"] = row["fugitive_air_lb"] + row["stack_air_lb"]
            row["land_lb"] = sum(row[f"{a}_lb"] for a in LAND)
            yield row


def link_to_echo(rows, con):
    """Set registry_id and how it was matched on each row."""
    try:
        registry_ids = {r for (r,) in con.execute("SELECT registry_id FROM facilities WHERE registry_id IS NOT NULL")}
        tri_map = {}
        for reg, ids in con.execute("SELECT registry_id, tri_ids FROM facilities WHERE tri_ids IS NOT NULL"):
            for t in ids.split():
                tri_map[t] = reg
    except Exception:
        sys.exit("ERROR: no 'facilities' table yet. Run scripts/build_echo_indiana.py first.")

    for row in rows:
        if row["frs_id"] in registry_ids:
            row["registry_id"], row["match_method"] = row["frs_id"], "frs_id"
        elif row["trifd"] in tri_map:
            row["registry_id"], row["match_method"] = tri_map[row["trifd"]], "echo_tri_ids"
        else:
            row["registry_id"], row["match_method"] = None, "unmatched"


def facility_years(rows):
    """Roll forms up to one row per TRI facility per year."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["trifd"], r["year"])].append(r)
    out = []
    for (trifd, year), forms in sorted(groups.items()):
        first = forms[0]
        s = lambda col, pred=lambda f: True: sum(f[col] for f in forms if pred(f))
        out.append({
            "trifd": trifd, "year": year,
            "registry_id": first["registry_id"], "match_method": first["match_method"], "frs_id": first["frs_id"],
            "facility_name": first["facility_name"], "city": first["city"], "parent_company": first["parent_company"],
            "industry_sector": first["industry_sector"], "lat": first["lat"], "lon": first["lon"],
            "n_chemicals": len({f["chemical"] for f in forms}),
            "n_form_a": sum(f["form_type"] == "A" for f in forms),
            "total_releases_lb": s("total_releases_lb"),
            "on_site_releases_lb": s("on_site_releases_lb"),
            "off_site_releases_lb": s("off_site_releases_lb"),
            "air_lb": s("air_lb"),
            "water_lb": s("water_lb"),
            "land_lb": s("land_lb"),
            "total_transfers_lb": s("total_transfers_lb"),
            "carcinogen_releases_lb": s("total_releases_lb", lambda f: f["is_carcinogen"] == "Y"),
            "pbt_releases_lb": s("total_releases_lb", lambda f: f["is_pbt"] == "Y"),
            "pfas_releases_lb": s("total_releases_lb", lambda f: f["is_pfas"] == "Y"),
            "top_chemical": max(forms, key=lambda f: f["total_releases_lb"])["chemical"],
        })
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-download TRI files even if they exist")
    args = parser.parse_args()

    rows = []
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        path = TRI_RAW / f"tri_{year}_{STATE}.csv"
        download(URL.format(year=year, state=STATE), path, force=args.force)
        year_rows = list(read_year(path))
        print(f"  {year}: {len(year_rows):,} forms from {len({r['trifd'] for r in year_rows}):,} facilities")
        rows += year_rows

    # Sanity check: the parts should add up to TRI's own on-site total (allowing for rounding).
    parts = ["fugitive_air_lb", "stack_air_lb", "water_lb", "land_lb"]
    off = sum(abs(sum(r[p] for p in parts) - r["on_site_releases_lb"]) > 1 for r in rows)
    if off:
        print(f"  note: {off} forms where air + water + land differs from TRI's on-site total by > 1 lb")

    con = connect()
    link_to_echo(rows, con)
    fy = facility_years(rows)

    # Report how well the join worked, by facility (latest year).
    latest = [f for f in fy if f["year"] == LAST_YEAR]
    by = defaultdict(int)
    for f in latest:
        by[f["match_method"]] += 1
    print(f"  {LAST_YEAR} facilities linked to ECHO: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))

    release_cols = [(ours, SQL_TYPES[kind]) for ours, _, kind in COLUMNS] + \
        [(f"{a}_lb", "REAL") for a in AMOUNTS] + [("air_lb", "REAL"), ("land_lb", "REAL"),
                                                  ("registry_id", "TEXT"), ("match_method", "TEXT")]
    fy_cols = [(k, "INTEGER" if k in ("year", "n_chemicals", "n_form_a") else
                "REAL" if k.endswith("_lb") or k in ("lat", "lon") else "TEXT") for k in fy[0]]

    print("Writing:")
    write_csv(PROCESSED_DIR / "tri_indiana_releases.csv", [c for c, _ in release_cols], rows)
    write_csv(PROCESSED_DIR / "tri_indiana_facility_years.csv", [c for c, _ in fy_cols], fy)
    replace_table(con, "tri_releases", release_cols, rows,
                  indexes=["UNIQUE (doc_ctrl_num)", "(registry_id)", "(year)"])
    replace_table(con, "tri_facility_years", fy_cols, fy, indexes=["UNIQUE (trifd, year)", "(registry_id)"])
    set_metadata(con, "tri", {"source_url": URL.format(year="YYYY", state=STATE),
                              "years": f"{FIRST_YEAR}-{LAST_YEAR}"})
    con.commit()
    con.close()
    print("  tables 'tri_releases' and 'tri_facility_years' in data/processed/indiana.sqlite")


if __name__ == "__main__":
    main()
