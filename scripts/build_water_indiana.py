"""
Download Clean Water Act discharge data for Indiana: what facilities put into rivers,
and when they broke their permit limits.

Facilities with a water discharge (NPDES) permit file Discharge Monitoring Reports
(DMRs): measured amounts of each pollutant leaving each pipe ("outfall"), checked
against the permit's limits. EPA's Water Pollutant Loading Tool turns those readings
into yearly pounds per pollutant, and weights them by toxicity (a "toxic weighting
factor"; pounds x factor = "toxic-weighted pound equivalents", TWPE) -- the water
version of weighing impact, not just volume.

What this does, in order:
  1. Downloads, into data/raw/water/ (skipped if already there; --force re-downloads):
       - yearly loadings for FIRST_YEAR..LAST_YEAR from the Loading Tool web service
         (no key needed; ~12 MB per year)
       - Indiana's effluent violations file from ECHO (~40 MB zip, all years)
  2. Keeps:
       - loadings: one row per permit x outfall x pollutant x year, where a load or a
         limit exceedance was calculated
       - violations since FIRST_YEAR: every numeric limit exceedance (E90), plus counts of
         overdue/missing reports (D80/D90) per permit per year
  3. Links each permit to a registry_id: ECHO's own permit list first, then the FRS ID
     the Loading Tool reports, then the FRS crosswalk.
  4. Writes, into data/processed/ (CSV + table of the same name in indiana.sqlite):
       water_permits         one row per permit (facility, receiving water, impaired?)
       water_loadings        permit x outfall x pollutant x year
       water_exceedances     each time a discharge broke a permit limit
       water_permit_years    totals per permit per year (pounds, TWPE, exceedances, late reports)

Run build_echo_indiana.py and build_frs_indiana.py first.
    python3 scripts/build_water_indiana.py
"""

import argparse
import csv
import io
import sys
import zipfile
from collections import defaultdict
from datetime import datetime

from common import RAW_DIR, PROCESSED_DIR, STATE, connect, download, replace_table, set_metadata, write_csv

FIRST_YEAR = 2015
LAST_YEAR = 2025  # last complete calendar year
LOADINGS_URL = ("https://echodata.epa.gov/echo/dmr_rest_services.get_custom_data_annual"
                "?p_year={year}&p_st={state}&output=CSV")
VIOLATIONS_URL = f"https://echo.epa.gov/files/echodownloads/NPDES_by_state_year/{STATE}_NPDES_EFF_VIOLATIONS.zip"
WATER_RAW = RAW_DIR / "water"
KG_TO_LB = 2.20462262


def num(v):
    v = (v or "").strip().replace(",", "")
    if v in ("", "--"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def read_loadings(path):
    """The Loading Tool CSV starts with a few description lines before the real header."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith('"Year"'))
    except StopIteration:
        sys.exit(f"ERROR: {path.name} doesn't look like a Loading Tool download:\n" + "\n".join(lines[:5]))
    reader = csv.DictReader(lines[start:])
    needed = ["Year", "NPDES Permit Number", "FRS ID", "Parameter Code", "Parameter Description",
              "Pollutant Load (kg/yr)", "Toxic Weighting Factor (TWF)", "Max Allowable Load (kg/yr)",
              "Load Over Limit (Option 1) (kg/yr)", "Outfall Number"]
    missing = [c for c in needed if c not in reader.fieldnames]
    if missing:
        sys.exit(f"ERROR: {path.name} is missing expected columns: {missing}")
    return list(reader)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-download even if files exist")
    args = parser.parse_args()

    con = connect()
    try:
        echo_permits = {p: reg for reg, ids in con.execute("SELECT registry_id, npdes_ids FROM facilities WHERE npdes_ids IS NOT NULL")
                        for p in ids.split()}
        registry_ids = {r for (r,) in con.execute("SELECT registry_id FROM facilities WHERE registry_id IS NOT NULL")}
        frs_permits = dict(con.execute("SELECT program_id, registry_id FROM frs_program_links WHERE program = 'NPDES'"))
    except Exception:
        sys.exit("ERROR: run scripts/build_echo_indiana.py and scripts/build_frs_indiana.py first.")

    # ---------------------------------------------------------------- loadings
    permits, loadings = {}, []
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        path = WATER_RAW / f"loadings_{year}_{STATE}.csv"
        download(LOADINGS_URL.format(year=year, state=STATE), path, force=args.force)
        rows = read_loadings(path)
        kept = 0
        for r in rows:
            pid = r["NPDES Permit Number"].strip()
            # Permit details: keep the most recent year's version
            permits[pid] = {
                "npdes_id": pid,
                "loading_tool_frs_id": r["FRS ID"].strip() or None,
                "facility_name": r["Facility Name"].strip(),
                "city": r["City"].strip() or None,
                "facility_type": r["Facility Type Indicator"].strip() or None,  # POTW = sewage plant
                "major": "Y" if r["Major/Non-Major Status"].strip() == "Major" else "N",
                "watershed": r["Watershed Name"].strip() or None,
                "huc12": r["HUC 12 Code"].strip() or None,
                "receiving_water": r["Water Body Name"].strip() or None,
                "impaired_water": "Y" if r["Listed for Impairment"].strip().lower() in ("yes", "y") else "N",
                "lat": num(r["Facility Latitude"]),
                "lon": num(r["Facility Longitude"]),
                "last_year_in_loadings": year,
            }
            load_kg = num(r["Pollutant Load (kg/yr)"])
            over_kg = num(r["Load Over Limit (Option 1) (kg/yr)"])
            if load_kg is None and not over_kg:
                continue  # nothing calculated for this pollutant (e.g. only flow or pH measured)
            twf = num(r["Toxic Weighting Factor (TWF)"])
            load_lb = None if load_kg is None else load_kg * KG_TO_LB
            allow_kg = num(r["Max Allowable Load (kg/yr)"])
            loadings.append({
                "npdes_id": pid, "year": year, "outfall": r["Outfall Number"].strip(),
                "parameter_code": r["Parameter Code"].strip(), "parameter": r["Parameter Description"].strip(),
                "load_lb": load_lb,
                "toxic_weighting_factor": twf,
                "twpe": None if load_lb is None or twf is None else load_lb * twf,
                "allowable_lb": None if allow_kg is None else allow_kg * KG_TO_LB,
                "over_limit_lb": None if over_kg is None else over_kg * KG_TO_LB,
                "potential_outlier": r.get("Potential Outlier?", "").strip() or None,
            })
            kept += 1
        print(f"  {year}: {len(rows):,} loading rows, {kept:,} with a calculated load")

    # ---------------------------------------------------------------- violations
    vpath = WATER_RAW / f"{STATE}_NPDES_EFF_VIOLATIONS.zip"
    download(VIOLATIONS_URL, vpath, force=args.force)
    exceedances = []
    late_reports = defaultdict(int)
    with zipfile.ZipFile(vpath) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as f:
            for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")):
                try:
                    end = datetime.strptime(r["MONITORING_PERIOD_END_DATE"], "%m/%d/%Y").date()
                except ValueError:
                    continue
                if end.year < FIRST_YEAR:
                    continue
                pid = r["NPDES_ID"].strip()
                code = r["VIOLATION_CODE"].strip()
                if code in ("D80", "D90"):  # report overdue / not received
                    late_reports[(pid, end.year)] += 1
                elif code == "E90":         # measured discharge exceeded a numeric limit
                    exceedances.append({
                        "npdes_id": pid, "period_end": end.isoformat(), "year": end.year,
                        "outfall": r["PERM_FEATURE_NMBR"].strip(),
                        "parameter_code": r["PARAMETER_CODE"].strip(), "parameter": r["PARAMETER_DESC"].strip(),
                        "limit_type": r["STATISTICAL_BASE_SHORT_DESC"].strip() or None,  # e.g. MO AVG, DAILY MX
                        "limit_value": num(r["LIMIT_VALUE_STANDARD_UNITS"]),
                        "reported_value": num(r["DMR_VALUE_STANDARD_UNITS"]),
                        "unit": r["STANDARD_UNIT_DESC"].strip() or None,
                        "exceedance_pct": num(r["EXCEEDENCE_PCT"]),
                        "significant": r["RNC_DETECTION_DESC"].strip() or None,  # why EPA flagged it as significant, if it did
                        "violation_id": r["NPDES_VIOLATION_ID"].strip(),
                    })
    # The file can list the same violation under several record versions; keep one.
    exceedances = list({e["violation_id"]: e for e in exceedances}.values())
    for e in exceedances:
        permits.setdefault(e["npdes_id"], {"npdes_id": e["npdes_id"]})
    for pid, _ in late_reports:
        permits.setdefault(pid, {"npdes_id": pid})
    print(f"  violations since {FIRST_YEAR}: {len(exceedances):,} limit exceedances, "
          f"{sum(late_reports.values()):,} overdue/missing reports")

    # ---------------------------------------------------------------- link permits to registry_id
    methods = defaultdict(int)
    for p in permits.values():
        pid, lt = p["npdes_id"], p.get("loading_tool_frs_id")
        if pid in echo_permits:
            p["registry_id"], p["match_method"] = echo_permits[pid], "echo_npdes_ids"
        elif lt in registry_ids:
            p["registry_id"], p["match_method"] = lt, "loading_tool_frs_id"
        elif frs_permits.get(pid) in registry_ids:
            p["registry_id"], p["match_method"] = frs_permits[pid], "frs_npdes"
        else:
            p["registry_id"], p["match_method"] = lt or frs_permits.get(pid), "not_in_echo"
        methods[p["match_method"]] += 1
    print(f"  {len(permits):,} permits linked: " + ", ".join(f"{k} {v}" for k, v in sorted(methods.items())))

    # ---------------------------------------------------------------- per permit per year
    py = defaultdict(lambda: {"total_load_lb": 0.0, "total_twpe": 0.0, "over_limit_lb": 0.0,
                              "n_exceedances": 0, "params_exceeded": set(), "max_exceedance_pct": None,
                              "n_late_reports": 0})
    for l in loadings:
        s = py[(l["npdes_id"], l["year"])]
        s["total_load_lb"] += l["load_lb"] or 0.0
        s["total_twpe"] += l["twpe"] or 0.0
        s["over_limit_lb"] += l["over_limit_lb"] or 0.0
    for e in exceedances:
        s = py[(e["npdes_id"], e["year"])]
        s["n_exceedances"] += 1
        s["params_exceeded"].add(e["parameter"])
        if e["exceedance_pct"] is not None:
            s["max_exceedance_pct"] = max(s["max_exceedance_pct"] or 0, e["exceedance_pct"])
    for (pid, year), n in late_reports.items():
        py[(pid, year)]["n_late_reports"] += n
    permit_years = []
    for (pid, year), s in sorted(py.items()):
        if year > LAST_YEAR + 1:
            continue
        permit_years.append({"npdes_id": pid, "registry_id": permits[pid]["registry_id"], "year": year,
                             **{k: v for k, v in s.items() if k != "params_exceeded"},
                             "n_params_exceeded": len(s["params_exceeded"]),
                             "params_exceeded": "; ".join(sorted(s["params_exceeded"])) or None})

    # ---------------------------------------------------------------- write
    for l in loadings:
        l["registry_id"] = permits[l["npdes_id"]]["registry_id"]
    for e in exceedances:
        e["registry_id"] = permits[e["npdes_id"]]["registry_id"]

    permit_cols = [("npdes_id", "TEXT"), ("registry_id", "TEXT"), ("match_method", "TEXT"),
                   ("loading_tool_frs_id", "TEXT"), ("facility_name", "TEXT"), ("city", "TEXT"),
                   ("facility_type", "TEXT"), ("major", "TEXT"), ("watershed", "TEXT"), ("huc12", "TEXT"),
                   ("receiving_water", "TEXT"), ("impaired_water", "TEXT"), ("lat", "REAL"), ("lon", "REAL"),
                   ("last_year_in_loadings", "INTEGER")]
    load_cols = [("npdes_id", "TEXT"), ("registry_id", "TEXT"), ("year", "INTEGER"), ("outfall", "TEXT"),
                 ("parameter_code", "TEXT"), ("parameter", "TEXT"), ("load_lb", "REAL"),
                 ("toxic_weighting_factor", "REAL"), ("twpe", "REAL"), ("allowable_lb", "REAL"),
                 ("over_limit_lb", "REAL"), ("potential_outlier", "TEXT")]
    exc_cols = [("violation_id", "TEXT"), ("npdes_id", "TEXT"), ("registry_id", "TEXT"), ("period_end", "TEXT"),
                ("year", "INTEGER"), ("outfall", "TEXT"), ("parameter_code", "TEXT"), ("parameter", "TEXT"),
                ("limit_type", "TEXT"), ("limit_value", "REAL"), ("reported_value", "REAL"), ("unit", "TEXT"),
                ("exceedance_pct", "REAL"), ("significant", "TEXT")]
    py_cols = [("npdes_id", "TEXT"), ("registry_id", "TEXT"), ("year", "INTEGER"), ("total_load_lb", "REAL"),
               ("total_twpe", "REAL"), ("over_limit_lb", "REAL"), ("n_exceedances", "INTEGER"),
               ("n_params_exceeded", "INTEGER"), ("params_exceeded", "TEXT"), ("max_exceedance_pct", "REAL"),
               ("n_late_reports", "INTEGER")]

    print("Writing:")
    tables = [("water_permits", permit_cols, list(permits.values()), ["UNIQUE (npdes_id)", "(registry_id)"]),
              ("water_loadings", load_cols, loadings, ["(npdes_id, year)", "(registry_id)"]),
              ("water_exceedances", exc_cols, exceedances, ["UNIQUE (violation_id)", "(npdes_id, year)", "(registry_id)"]),
              ("water_permit_years", py_cols, permit_years, ["UNIQUE (npdes_id, year)", "(registry_id)"])]
    for name, cols, rows, idx in tables:
        write_csv(PROCESSED_DIR / f"{name.replace('water_', 'water_indiana_')}.csv", [c for c, _ in cols], rows)
        replace_table(con, name, cols, rows, indexes=idx)
    set_metadata(con, "water", {"loadings_source": LOADINGS_URL.format(year="YYYY", state=STATE),
                                "violations_source": VIOLATIONS_URL, "years": f"{FIRST_YEAR}-{LAST_YEAR}",
                                "loading_options": "Loading Tool defaults: non-detects = 0, estimation off, DMR data only"})
    con.commit()
    con.close()
    print("  tables " + ", ".join(f"'{t[0]}'" for t in tables) + " in data/processed/indiana.sqlite")


if __name__ == "__main__":
    main()
