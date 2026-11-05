"""
Export the data for the design mockup in site/mockup/ (not part of the main map).

One record per polluter with an impact estimate: a readable name and owner, what kind of
place it is, how it pollutes (air, toxic chemicals, water, climate) and what that costs
(deaths, illness, dollars, offset cost, fines). Written as a small JavaScript file so the
mockup works even when opened straight from disk.

    python3 scripts/export_mockup_data.py
"""

import json
import math
import re
import sqlite3

from common import DB_PATH, ROOT

OUT = ROOT / "site" / "mockup" / "mockup-data.js"

KIND = {   # EPA health-sector -> plain label
    "electricity generating units": "Power plant",
    "integrated iron and steel": "Steel mill",
    "electric arc furnaces and argon-oxy.": "Steel mill (electric arc)",
    "coke ovens": "Coke plant",
    "cement kilns": "Cement plant",
    "refineries": "Oil refinery",
    "iron and steel foundries": "Foundry",
    "pulp and paper": "Paper mill",
    "synthetic organic chemical": "Chemical plant",
    "secondary lead smelters": "Lead smelter",
    "oil and natural gas transmissions": "Gas pipeline station",
    "gasoline distribution bulk terminals": "Fuel terminal",
    "brick": "Brick plant",
}
KIND_BY_NEI_ID = {"8183111": "Aluminum smelter and power plant"}   # Alcoa Warrick: one row for both
KEEP_UPPER = {"LLC", "US", "USA", "BP", "AES", "IPL", "AEP", "NA", "LP", "II", "III", "IGCC", "SDI", "CF", "DBA"}


def title(s):
    """'CLEVELAND-CLIFFS BURNS HARBOR LLC' -> 'Cleveland-Cliffs Burns Harbor LLC'."""
    if not s:
        return s
    if s != s.upper():          # already mixed case: just tidy "INC"
        return re.sub(r"\bINC\b", "Inc", s.strip())
    words = []
    for w in s.split():
        core = re.sub(r"[^A-Z]", "", w)
        words.append(w if core in KEEP_UPPER else "-".join(p.capitalize() for p in w.lower().split("-")))
    return " ".join(words).replace(" ,", ",")


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    fac = con.execute("""
        SELECT fi.*, g.facility_name AS ghg_name, n.facility_name AS nei_name, n.naics_description,
               e.industry AS echo_industry, e.compliance_status, e.qtrs_in_noncompliance_3yr, e.epa_report_url,
               e.street, e.county
        FROM facility_impacts fi
        LEFT JOIN ghg_facilities g ON g.ghg_facility_id = fi.ghg_facility_id
        LEFT JOIN nei_facilities n ON n.eis_facility_id = fi.nei_eis_id
        LEFT JOIN facilities e ON e.registry_id = fi.registry_id
        WHERE fi.total_usd_central > 0 AND fi.lat IS NOT NULL
        ORDER BY fi.total_deaths_central DESC""").fetchall()
    tri_rows = con.execute("""
        SELECT trifd, registry_id, facility_name, city, parent_company, total_releases_lb, air_lb, water_lb, land_lb,
               carcinogen_releases_lb, top_chemical, n_chemicals, lat, lon
        FROM tri_facility_years WHERE year = (SELECT MAX(year) FROM tri_facility_years)""").fetchall()
    tri = {r["registry_id"]: r for r in tri_rows if r["registry_id"]}
    water_rows = con.execute("""
        SELECT p.registry_id, p.npdes_id, p.facility_name, p.city, p.lat, p.lon, p.impaired_water, p.receiving_water,
               y.n_exceedances, y.total_load_lb
        FROM water_permits p JOIN water_permit_years y USING (npdes_id)
        WHERE y.year = (SELECT MAX(year) FROM water_loadings)""").fetchall()

    GENERIC = {"ENERGY", "INDIANA", "POWER", "PLANT", "STATION", "GENERATING", "GENERATION", "COMPANY", "CORP",
               "CORPORATION", "STEEL", "SERVICES", "PRODUCTS", "NORTH", "AMERICA", "FACILITY", "WORKS", "MILL",
               "ELECTRIC", "COUNTY", "INDUSTRIES", "MANUFACTURING", "INTERNATIONAL", "HARBOR", "DUKE", "CLEVELAND",
               "CLIFFS", "UNITED", "STATES", "DIVISION", "OPERATIONS", "WATER", "TREATMENT", "CUSTOM", "SERVICE",
               "BURNS", "CUMMINS"}   # Cummins has several separate plants in and around Columbus
    words = lambda s: {w for w in re.findall(r"[A-Z]{4,}", (s or "").upper()) if w not in GENERIC}

    def near(rows, lat, lon, names, city, claimed):
        """Reports for the same plant filed under another ID. EPA often splits one plant across
        several registry IDs, so look within 3 km (big plants cover a lot of ground) for a report
        whose name shares a distinctive word. Being close isn't enough: factories sit next to each
        other. A shared town name doesn't count ("Goshen Muni" airport is not a Goshen factory), and
        a report that belongs to another row (in `claimed`) is never used twice. Contractors working
        at a plant only get their own reports, not the plant's."""
        if any("CONTRACTOR" in (n or "").upper() for n in names):
            return []
        mine = set().union(*(words(n) for n in names)) - words(city)
        out = []
        for x in rows:
            if x["lat"] is None or x["lon"] is None or x[claimed["key"]] in claimed["ids"]:
                continue
            dy = (x["lat"] - lat) * 111.0
            dx = (x["lon"] - lon) * 111.0 * math.cos(math.radians(lat))
            if dx * dx + dy * dy <= 9.0 and mine & (words(x["facility_name"]) - words(x["city"])):
                out.append(x)
        return out

    # Reports filed under a row's own registry ID are that row's; the rest are up for matching,
    # biggest polluters first
    row_ids = {r["registry_id"] for r in fac if r["registry_id"]}
    claimed_tri = {"key": "trifd", "ids": {x["trifd"] for x in tri_rows if x["registry_id"] in row_ids}}
    claimed_water = {"key": "npdes_id", "ids": {x["npdes_id"] for x in water_rows if x["registry_id"] in row_ids}}

    out = []
    for r in fac:
        names = (r["facility_name"], r["ghg_name"], r["nei_name"])
        t = tri.get(r["registry_id"])
        if t is None:
            t = max(near(tri_rows, r["lat"], r["lon"], names, r["city"], claimed_tri),
                    key=lambda x: x["total_releases_lb"] or 0, default=None)
            if t:
                claimed_tri["ids"].add(t["trifd"])
        permits = [x for x in water_rows if r["registry_id"] and x["registry_id"] == r["registry_id"]]
        if not permits:
            permits = near(water_rows, r["lat"], r["lon"], names, r["city"], claimed_water)
            claimed_water["ids"].update(x["npdes_id"] for x in permits)
        w = (sum(x["n_exceedances"] or 0 for x in permits), sum(x["total_load_lb"] or 0 for x in permits)) if permits else None
        impaired_water = next((x["receiving_water"] for x in permits if x["impaired_water"] == "Y"), None)
        kind = KIND_BY_NEI_ID.get(r["nei_eis_id"]) or KIND.get(r["health_sector"]) or title(r["naics_description"] or r["echo_industry"] or "") or "Industrial facility"
        # Name: the greenhouse-gas report's names are the most readable, then TRI, then NEI, then ECHO
        name = r["ghg_name"] or (t["facility_name"] if t else None) or r["nei_name"] or r["facility_name"]
        name = title(name)
        if kind == "Power plant" and not re.search(r"plant|station|generat|energy|power|facility", name, re.I):
            name = f"{name} power plant"
        owner = title(t["parent_company"]) if t and t["parent_company"] else None
        if not owner and r["nei_name"] and " - " in r["nei_name"]:
            owner = title(r["nei_name"].split(" - ")[0])
        out.append({
            "name": name, "owner": owner, "kind": kind, "city": title(r["city"]), "county": title(r["county"]),
            "lat": round(r["lat"], 5), "lon": round(r["lon"], 5),
            # what it costs
            "deaths": round(r["total_deaths_central"], 2), "deaths_lo": round(r["total_deaths_low"], 2),
            "deaths_hi": round(r["total_deaths_high"], 2),
            "air_deaths": round(r["deaths_central"], 2), "climate_deaths": round(r["climate_deaths_central"], 2),
            "damage": round(r["total_usd_central"]), "damage_lo": round(r["total_usd_low"]), "damage_hi": round(r["total_usd_high"]),
            "health_usd": round(r["health_usd_central"]), "climate_usd": round(r["climate_usd_central"]),
            "offset_lo": round(r["removal_usd_low"]), "offset_hi": round(r["removal_usd_high"]),
            "ill": {k: round(r[k], 1) for k in ("lung_cancers", "heart_attacks", "strokes", "new_asthma_cases",
                                                "er_visits", "hospital_admissions", "lost_work_days", "lost_school_days")},
            "fines": round(r["penalties_5yr_usd"] or 0),
            # how it pollutes
            "air": {"pm25_t": r["pm25_tons"], "so2_t": r["so2_tons"], "nox_t": r["nox_tons"]},
            "toxic": None if not t else {"lb": round(t["total_releases_lb"]), "air_lb": round(t["air_lb"]),
                                         "water_lb": round(t["water_lb"]), "land_lb": round(t["land_lb"]),
                                         "carcinogen_lb": round(t["carcinogen_releases_lb"]),
                                         "top": " ".join((t["top_chemical"] or "").split(" (")[0].split()), "n": t["n_chemicals"]},
            "water": None if not w else {"exceedances": w[0], "lb": round(w[1]), "impaired": title(impaired_water)},
            "co2e_t": round(r["co2e_t"] or 0), "cars": round(r["eq_cars"] or 0), "homes": round(r["eq_homes"] or 0),
            # accountability
            "status": r["compliance_status"], "qtrs_violation": r["qtrs_in_noncompliance_3yr"],
            "report": (r["epa_report_url"] or "").replace("http://", "https://", 1),
            "outlier": r["outlier_note"],
        })
    out.sort(key=lambda x: -x["deaths"])
    for i, f in enumerate(out):
        f["rank"] = i + 1
        f["id"] = i
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.MOCKUP_DATA = " + json.dumps({"facilities": out, "year": 2023, "dollar_year": 2024},
                                                        separators=(",", ":")) + ";\n", encoding="utf-8")
    print(f"Wrote {len(out)} polluters to {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e3:.0f} KB)")
    for f in out[:8]:
        print(f"  #{f['rank']} {f['name']} ({f['kind']}, {f['owner']}) - {f['deaths']:.0f} deaths")


if __name__ == "__main__":
    main()
