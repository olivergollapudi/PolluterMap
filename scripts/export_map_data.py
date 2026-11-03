"""
Turn the Indiana facilities database into a GeoJSON file the web map can load.

GeoJSON is the standard text format for map data: a list of "features", each
with a geometry (here, a single point: [longitude, latitude] -- note the order)
and a set of "properties" (the facts shown in the popup).

What this does:
  1. Reads the facilities table from data/processed/indiana.sqlite (made by build_echo_indiana.py).
  2. Skips the few facilities whose coordinates fall outside Indiana.
  3. Adds, for facilities that have them, the newest year of each other source --
     toxic releases (TRI), risk scores (RSEI), greenhouse gases (GHG), air pollutants
     (NEI) and water discharges -- plus where the facility ranks in Indiana.
  4. Keeps only the fields the map needs, so the file stays small.
  5. Writes site/data/facilities.geojson.

Run from the project root, after the build scripts (build_all.py runs this last):
    python3 scripts/export_map_data.py

Note: data is attached by registry_id. When EPA has split one plant across several
registry IDs, each dot only shows the data filed under its own ID.
"""

import json
import sqlite3
from collections import defaultdict

from common import DB_PATH, ROOT

OUT = ROOT / "site" / "data" / "facilities.geojson"

PROGRAMS = [("air_program", "Air"), ("water_program", "Water"),
            ("waste_program", "Hazardous waste"), ("drinking_water_program", "Drinking water")]


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def by_registry(con, sql):
    """Run a query whose first column is registry_id; return {registry_id: row}."""
    return {r[0]: r for r in con.execute(sql) if r[0]}


def ranks(values):
    """{registry_id: value} -> {registry_id: rank}, 1 = largest. Zero/empty values aren't ranked."""
    ordered = sorted((v, k) for k, v in values.items() if v)
    ordered.reverse()
    return {k: i + 1 for i, (_, k) in enumerate(ordered)}


def r0(x):
    return None if x is None else round(x)


def source_summaries(con):
    """Newest-year facts from each source, keyed by registry_id, plus metadata for the map."""
    extra = defaultdict(dict)
    meta = {}

    # --- TRI: toxic releases (pounds), newest year
    tri_year = one(con, "SELECT MAX(year) FROM tri_facility_years")
    tri = by_registry(con, f"""
        SELECT registry_id, SUM(total_releases_lb), SUM(air_lb), SUM(water_lb), SUM(land_lb),
               SUM(off_site_releases_lb), SUM(carcinogen_releases_lb), MAX(top_chemical), SUM(n_chemicals),
               SUM(n_form_a)
        FROM tri_facility_years WHERE year = {tri_year} GROUP BY registry_id""")
    tri_rank = ranks({k: r[1] for k, r in tri.items()})
    for k, r in tri.items():
        extra[k].update({"tri_lb": r0(r[1]), "tri_air_lb": r0(r[2]), "tri_water_lb": r0(r[3]),
                         "tri_land_lb": r0(r[4]), "tri_offsite_lb": r0(r[5]), "tri_carcinogen_lb": r0(r[6]),
                         "tri_top_chemical": r[7], "tri_chemicals": r[8], "tri_form_a": r[9],
                         "tri_rank": tri_rank.get(k)})
    meta["tri"] = {"year": tri_year, "ranked": len(tri_rank)}

    # --- RSEI: risk score, newest year
    rsei_year = one(con, "SELECT MAX(year) FROM rsei_facility_years")
    rsei = by_registry(con, f"""
        SELECT registry_id, SUM(rsei_score), SUM(cancer_score)
        FROM rsei_facility_years WHERE year = {rsei_year} GROUP BY registry_id""")
    rsei_rank = ranks({k: r[1] for k, r in rsei.items()})
    for k, r in rsei.items():
        if r[1]:
            extra[k].update({"rsei_score": r0(r[1]), "rsei_cancer_pct": r0(100 * r[2] / r[1]),
                             "rsei_rank": rsei_rank.get(k)})
    meta["rsei"] = {"year": rsei_year, "ranked": len(rsei_rank)}

    # --- GHG: greenhouse gases (metric tons CO2e), newest year
    ghg = by_registry(con, """
        SELECT registry_id, SUM(total_mt_co2e_2023) FROM ghg_facilities
        WHERE last_year_reported = 2023 GROUP BY registry_id""")
    ghg_rank = ranks({k: r[1] for k, r in ghg.items()})
    for k, r in ghg.items():
        if r[1]:
            extra[k].update({"ghg_t": r0(r[1]), "ghg_rank": ghg_rank.get(k)})
    meta["ghg"] = {"year": 2023, "ranked": len(ghg_rank)}

    # --- NEI: air pollutants (tons), 2023
    nei_year = one(con, "SELECT value FROM metadata WHERE source = 'nei' AND key = 'year'")
    nei = by_registry(con, """
        SELECT registry_id, SUM(pm25_tons), SUM(so2_tons), SUM(nox_tons), SUM(total_hap_lb)
        FROM nei_facilities WHERE in_echo = 'Y' GROUP BY registry_id""")
    pm_rank = ranks({k: r[1] for k, r in nei.items()})
    for k, r in nei.items():
        extra[k].update({"nei_pm25_t": None if r[1] is None else round(r[1], 1),
                         "nei_so2_t": None if r[2] is None else round(r[2], 1),
                         "nei_nox_t": None if r[3] is None else round(r[3], 1),
                         "nei_hap_lb": r0(r[4]), "nei_pm25_rank": pm_rank.get(k)})
    meta["nei"] = {"year": int(nei_year), "ranked": len(pm_rank)}

    # --- Water: discharges and permit-limit exceedances, newest complete year
    water_year = one(con, "SELECT MAX(year) FROM water_loadings")
    water = by_registry(con, f"""
        SELECT registry_id, SUM(total_twpe), SUM(total_load_lb), SUM(n_exceedances), SUM(n_late_reports)
        FROM water_permit_years WHERE year = {water_year} GROUP BY registry_id""")
    twpe_rank = ranks({k: r[1] for k, r in water.items()})
    impaired = by_registry(con, """
        SELECT registry_id, GROUP_CONCAT(DISTINCT receiving_water) FROM water_permits
        WHERE impaired_water = 'Y' GROUP BY registry_id""")
    for k, r in water.items():
        extra[k].update({"water_twpe": r0(r[1]), "water_lb": r0(r[2]), "water_exceedances": r[3] or 0,
                         "water_late_reports": r[4] or 0, "water_twpe_rank": twpe_rank.get(k)})
    for k, r in impaired.items():
        if k in extra and "water_twpe" in extra[k]:
            extra[k]["water_impaired"] = (r[1] or "yes")[:80]
    meta["water"] = {"year": water_year, "ranked": len(twpe_rank)}

    # Drop empty values so the file stays small
    for k in extra:
        extra[k] = {p: v for p, v in extra[k].items() if v not in (None, "")}
    return extra, meta


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM facilities WHERE coords_in_indiana = 'Y'").fetchall()
    skipped = one(con, "SELECT COUNT(*) FROM facilities WHERE coords_in_indiana = 'N'")
    data_date = one(con, "SELECT value FROM metadata WHERE source = 'echo' AND key = 'source_data_date'")
    con.row_factory = None
    extra, meta = source_summaries(con)

    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            # 5 decimal places is about 1 meter -- plenty, and keeps the file smaller.
            "geometry": {"type": "Point", "coordinates": [round(r["lon"], 5), round(r["lat"], 5)]},
            "properties": {
                "name": r["name"],
                "address": ", ".join(p for p in (r["street"], r["city"]) if p),
                "industry": r["industry"],
                "programs": ", ".join(label for col, label in PROGRAMS if r[col] == "Y"),
                "major": r["is_major"] == "Y",
                "active": r["is_active"] == "Y",
                "status": r["compliance_status"],
                "qtrs_violation": r["qtrs_in_noncompliance_3yr"],
                "report_url": (r["epa_report_url"] or "").replace("http://", "https://", 1),
                **extra.get(r["registry_id"], {}),
            },
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    geojson = {"type": "FeatureCollection", "source_data_date": data_date, "sources": meta, "features": features}
    OUT.write_text(json.dumps(geojson, separators=(",", ":")), encoding="utf-8")

    majors = sum(f["properties"]["major"] for f in features)
    active = sum(f["properties"]["active"] for f in features)
    print(f"Wrote {len(features):,} facilities ({majors:,} major, {active:,} active) to {OUT.relative_to(ROOT)}")
    for src, prop in [("tri", "tri_lb"), ("rsei", "rsei_score"), ("ghg", "ghg_t"), ("nei", "nei_pm25_t"), ("water", "water_twpe")]:
        n = sum(prop in f["properties"] for f in features)
        print(f"  {src} {meta[src]['year']}: on {n:,} dots ({meta[src]['ranked']:,} ranked in Indiana)")
    print(f"  {OUT.stat().st_size / 1e6:.1f} MB; skipped {skipped} with coordinates outside Indiana")


if __name__ == "__main__":
    main()
