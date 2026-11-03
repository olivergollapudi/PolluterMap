"""
Turn the Indiana facilities database into a GeoJSON file the web map can load.

GeoJSON is the standard text format for map data: a list of "features", each
with a geometry (here, a single point: [longitude, latitude] -- note the order)
and a set of "properties" (the facts shown in the popup).

What this does:
  1. Reads the facilities table from data/processed/indiana.sqlite (made by build_echo_indiana.py).
  2. Skips the few facilities whose coordinates fall outside Indiana.
  3. Keeps only the fields the map needs, so the file stays small.
  4. Writes site/data/facilities.geojson.

Run from the project root, after build_echo_indiana.py:
    python3 scripts/export_map_data.py
"""

import json
import sqlite3

from common import DB_PATH, ROOT

OUT = ROOT / "site" / "data" / "facilities.geojson"

PROGRAMS = [("air_program", "Air"), ("water_program", "Water"),
            ("waste_program", "Hazardous waste"), ("drinking_water_program", "Drinking water")]


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM facilities WHERE coords_in_indiana = 'Y'").fetchall()
    skipped = con.execute("SELECT COUNT(*) FROM facilities WHERE coords_in_indiana = 'N'").fetchone()[0]
    data_date = con.execute("SELECT value FROM metadata WHERE source = 'echo' AND key = 'source_data_date'").fetchone()[0]

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
            },
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    geojson = {"type": "FeatureCollection", "source_data_date": data_date, "features": features}
    OUT.write_text(json.dumps(geojson, separators=(",", ":")), encoding="utf-8")

    majors = sum(f["properties"]["major"] for f in features)
    active = sum(f["properties"]["active"] for f in features)
    print(f"Wrote {len(features):,} facilities ({majors:,} major, {active:,} active) to {OUT.relative_to(ROOT)}")
    print(f"  {OUT.stat().st_size / 1e6:.1f} MB; skipped {skipped} with coordinates outside Indiana")


if __name__ == "__main__":
    main()
