"""
Estimate each Indiana facility's yearly harm in dollars, deaths and everyday terms.

Combines what facilities emit (NEI air pollutants, GHGRP greenhouse gases) with the
published factors from build_impact_factors.py:

  Health  = tons of PM2.5, SO2, NOx, NH3, VOC  x  EPA deaths-per-ton for the facility's
            sector in Indiana  ->  estimated premature deaths;  x value of a statistical
            life  ->  dollars. Covers fine-particle and ozone pollution only (see below).
  Climate = tons of CO2, methane, nitrous oxide (+ other gases)  x  EPA social cost of
            each gas  ->  dollars of future climate damage.
  Reverse = tons CO2-equivalent  x  IPCC cost of removing CO2 from the air.
  Everyday comparisons = CO2-equivalent as cars, homes, gallons of gasoline, forest acres.

Every number is an estimate with a low / central / high range. "Central" health is the
midpoint of EPA's low and high estimates; central climate uses EPA's central 2% rate.

NOT included (no accepted per-ton dollar method): toxic chemicals (TRI/RSEI), water
discharges, nonfatal illness (asthma, hospital visits -- EPA counts these, but they're a
small share of the dollars), and harms outside these pollutants.

Each facility is matched to one of EPA's 21 industrial sectors from its industry code
(NAICS) and NEI source type; facilities that don't fit a sector (e.g. airports, factories
burning fuel) use EPA's Indiana "industrial boilers" values and are flagged as a proxy.

Writes data/processed/impacts_indiana_facilities.csv + table facility_impacts.

Run after build_echo, build_ghg, build_nei and build_impact_factors.
    python3 scripts/build_impacts_indiana.py
"""

import math
import re
import sqlite3
import sys
from collections import defaultdict

from common import DB_PATH, PROCESSED_DIR, connect, replace_table, set_metadata, write_csv

YEAR = 2023   # newest year both NEI and GHGRP cover
POLLUTANTS = {"PM": "pm25_tons", "SO2": "so2_tons", "NOX": "nox_tons", "NH3": "nh3_tons", "VOC": "voc_tons"}
OTHER_GHG = ["hfc", "pfc", "sf6", "nf3", "other_fluorinated", "hfe", "short_lived", "other_ghg"]
PROXY_SECTOR = "industrial boilers"
SAME_SITE_KM = 1.0   # a GHG and an NEI record this close together with different IDs = same plant


def km_apart(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return float("inf")
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def health_sector(naics, source_type, name):
    """Pick the EPA benefit-per-ton sector that best matches an NEI facility. Returns (sector, basis)."""
    n, s, name = naics or "", (source_type or "").lower(), (name or "").upper()
    if n.startswith("2211") or "electricity generation" in s:
        return "electricity generating units", "matched"
    if "coke battery" in s:
        return "coke ovens", "matched"
    if n in ("324199", "331110", "331111") and "steel mill" in s or n in ("331110", "331111"):
        # Indiana's electric-arc-furnace mills (scrap-based) vs integrated mills (blast furnaces, coke)
        if re.search(r"NUCOR|STEEL DYNAMICS", name):
            return "electric arc furnaces and argon-oxy.", "matched"
        return "integrated iron and steel", "matched"
    if n.startswith("33151") or "foundries, iron and steel" in s:
        return "iron and steel foundries", "matched"
    if n == "327310" or "portland cement" in s:
        return "cement kilns", "matched"
    if n == "324110" or "petroleum refinery" in s:
        return "refineries", "matched"
    if n.startswith("322"):
        return "pulp and paper", "matched"
    if n.startswith("3251"):
        return "synthetic organic chemical", "matched"
    if n == "331492" and re.search(r"LEAD|ECOBAT|QUEMETCO", name):
        return "secondary lead smelters", "matched"
    if n.startswith("211"):
        return "oil and natural gas", "matched"
    if n.startswith("4862") or "compressor station" in s:
        return "oil and natural gas transmissions", "matched"
    if n == "424710" or "bulk terminal" in s:
        return "gasoline distribution bulk terminals", "matched"
    if n in ("327120", "327331") or "brick" in s:
        return "brick", "matched"
    return PROXY_SECTOR, "proxy"


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        f = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM impact_factors")}
        hf = {(r["sector"], r["pollutant"], r["outcome"]): (r["deaths_per_ton_low"], r["deaths_per_ton_high"])
              for r in con.execute("SELECT * FROM health_factors")}
        cpi = {r["year"]: r["cpi_u"] for r in con.execute("SELECT year, cpi_u FROM cpi")}
        nei = con.execute("SELECT * FROM nei_facilities").fetchall()
        # EPA's own "this value looks implausible" flags on the pollutants we use
        outliers = defaultdict(list)
        for r in con.execute("""SELECT eis_facility_id, pollutant_code, outlier_flag FROM nei_emissions
                                WHERE pollutant_code IN ('PM25-PRI','SO2','NOX','NH3','VOC') AND outlier_flag <> 'No'"""):
            outliers[r[0]].append(f"{r[1]} ({r[2]})")
        ghg = con.execute(f"SELECT * FROM ghg_facilities WHERE total_mt_co2e_{YEAR} IS NOT NULL").fetchall()
        echo = {r["registry_id"]: r for r in con.execute(
            "SELECT registry_id, name, city, lat, lon, penalties_5yr_usd, federal_case_penalties_usd FROM facilities "
            "WHERE registry_id IS NOT NULL")}
    except sqlite3.OperationalError as e:
        sys.exit(f"ERROR: {e}. Run build_echo, build_ghg, build_nei and build_impact_factors first.")

    dollar_year = int(f["dollar_year"])
    vsl = f["vsl"] * cpi[dollar_year] / cpi[2006]
    to_dollars_2020 = cpi[dollar_year] / cpi[2020]
    sc = {gas: [f[f"scghg_{gas}_{YEAR}_{lvl}"] * to_dollars_2020 for lvl in ("low", "central", "high")]
          for gas in ("co2", "ch4", "n2o")}
    print(f"  value of a statistical life: ${vsl / 1e6:.2f}M ({dollar_year}$); "
          f"social cost of CO2 ({YEAR} emissions): ${sc['co2'][0]:.0f} / ${sc['co2'][1]:.0f} / ${sc['co2'][2]:.0f} per ton")

    rows = defaultdict(lambda: defaultdict(float))
    info = {}

    def key_for(registry_id, fallback):
        return registry_id or fallback

    # ---------------------------------------------------------------- health (NEI)
    sector_counts = defaultdict(int)
    for r in nei:
        k = key_for(r["registry_id"], f"nei:{r['eis_facility_id']}")
        sector, basis = health_sector(r["naics"], r["source_type"], f"{r['facility_name']} {r['company'] or ''}")
        sector_counts[(sector, basis)] += 1
        fallbacks = []
        deaths = [0.0, 0.0]
        for pol, col in POLLUTANTS.items():
            tons = r[col] or 0.0
            if not tons:
                continue
            rows[k][col] += tons
            outcomes = ["ozone_mortality"] if pol == "VOC" else ["pm25_mortality", "ozone_mortality"] if pol == "NOX" else ["pm25_mortality"]
            for outcome in outcomes:
                factor = hf.get((sector, pol, outcome))
                if factor is None or factor[0] is None or factor[1] is None:
                    factor = hf.get((PROXY_SECTOR, pol, outcome))   # sector has no value for this pollutant
                    fallbacks.append(f"{pol}/{outcome.split('_')[0]}")
                if factor is None:
                    continue
                deaths[0] += tons * factor[0]
                deaths[1] += tons * factor[1]
        rows[k]["deaths_low"] += deaths[0]
        rows[k]["deaths_high"] += deaths[1]
        prev = info.get(k, {})
        # When several NEI records share one registry ID, keep the sector of the biggest emitter
        if not prev or (r["pm25_tons"] or 0) > prev.get("_pm", -1):
            info[k] = {**prev, "_pm": r["pm25_tons"] or 0, "health_sector": sector, "health_sector_basis": basis,
                       "nei_eis_id": r["eis_facility_id"], "name": prev.get("name") or r["facility_name"],
                       "city": prev.get("city") or r["city"], "lat": prev.get("lat") or r["lat"], "lon": prev.get("lon") or r["lon"]}
        if fallbacks:
            info[k]["health_factor_fallbacks"] = ", ".join(sorted(set(fallbacks)))
        if outliers.get(r["eis_facility_id"]):
            info[k]["epa_outlier_values"] = "; ".join(outliers[r["eis_facility_id"]])

    # ---------------------------------------------------------------- climate (GHGRP)
    # EPA often files one plant's air-pollution (NEI) and greenhouse-gas records under different
    # registry IDs. If a GHG facility's ID has no NEI record but an NEI facility sits at the same
    # spot (within SAME_SITE_KM) with no GHG record yet, treat them as one plant.
    nei_sites = [(key_for(r["registry_id"], f"nei:{r['eis_facility_id']}"), r["lat"], r["lon"]) for r in nei]
    has_ghg, merged = set(), 0
    for r in ghg:
        k = key_for(r["registry_id"], f"ghg:{r['ghg_facility_id']}")
        if k not in info or "health_sector" not in info.get(k, {}):
            near = min(((km_apart(r["lat"], r["lon"], lat, lon), nk) for nk, lat, lon in nei_sites
                        if nk not in has_ghg), default=(float("inf"), None))
            if near[0] <= SAME_SITE_KM:
                k = near[1]
                merged += 1
                info[k]["merged_records"] = f"GHG facility {r['ghg_facility_id']} (registry {r['registry_id']}) matched by location"
        has_ghg.add(k)
        total = r[f"total_mt_co2e_{YEAR}"] or 0.0
        co2 = r[f"co2_mt_{YEAR}"] or 0.0
        ch4_t = (r[f"methane_mt_co2e_{YEAR}"] or 0.0) / f["gwp_ch4"]
        n2o_t = (r[f"n2o_mt_co2e_{YEAR}"] or 0.0) / f["gwp_n2o"]
        other = sum(r[f"{g}_mt_co2e_{YEAR}"] or 0.0 for g in OTHER_GHG)   # valued at the CO2 rate (approximation)
        rows[k]["co2e_t"] += total
        rows[k]["co2_t"] += co2
        rows[k]["ch4_t"] += ch4_t
        rows[k]["n2o_t"] += n2o_t
        for i, lvl in enumerate(("low", "central", "high")):
            rows[k][f"climate_usd_{lvl}"] += (co2 + other) * sc["co2"][i] + ch4_t * sc["ch4"][i] + n2o_t * sc["n2o"][i]
        prev = info.get(k, {})
        info[k] = {**prev, "ghg_facility_id": r["ghg_facility_id"], "name": prev.get("name") or r["facility_name"],
                   "city": prev.get("city") or r["city"], "lat": prev.get("lat") or r["lat"], "lon": prev.get("lon") or r["lon"]}
    print(f"  {merged} greenhouse-gas records merged into the air-pollution record at the same location")

    # ---------------------------------------------------------------- combine
    out = []
    for k, v in rows.items():
        i = info.get(k, {})
        e = echo.get(k)
        d_lo, d_hi = v.get("deaths_low", 0.0), v.get("deaths_high", 0.0)
        d_mid = (d_lo + d_hi) / 2
        co2e = v.get("co2e_t", 0.0)
        row = {
            "registry_id": None if ":" in k else k,
            "in_echo": "Y" if e else "N",
            "facility_name": (e["name"] if e else None) or i.get("name"),
            "city": (e["city"] if e else None) or i.get("city"),
            "lat": (e["lat"] if e else None) or i.get("lat"),
            "lon": (e["lon"] if e else None) or i.get("lon"),
            "year": YEAR,
            "nei_eis_id": i.get("nei_eis_id"), "ghg_facility_id": i.get("ghg_facility_id"),
            "health_sector": i.get("health_sector"), "health_sector_basis": i.get("health_sector_basis"),
            "health_factor_fallbacks": i.get("health_factor_fallbacks"),
            "epa_outlier_values": i.get("epa_outlier_values"),   # EPA flagged an input as a possible outlier
            "merged_records": i.get("merged_records"),
            **{col: v.get(col) or None for col in POLLUTANTS.values()},
            "deaths_low": d_lo, "deaths_central": d_mid, "deaths_high": d_hi,
            "health_usd_low": d_lo * vsl, "health_usd_central": d_mid * vsl, "health_usd_high": d_hi * vsl,
            "co2e_t": co2e or None, "co2_t": v.get("co2_t") or None, "ch4_t": v.get("ch4_t") or None, "n2o_t": v.get("n2o_t") or None,
            "climate_usd_low": v.get("climate_usd_low", 0.0), "climate_usd_central": v.get("climate_usd_central", 0.0),
            "climate_usd_high": v.get("climate_usd_high", 0.0),
            "removal_usd_low": co2e * f["removal_cost_low"], "removal_usd_high": co2e * f["removal_cost_high"],
            "eq_cars": co2e / f["eq_car_year"], "eq_homes": co2e / f["eq_home_energy_year"],
            "eq_gallons_gasoline": co2e / f["eq_gallon_gasoline"], "eq_forest_acres": co2e / f["eq_forest_acre_year"],
            "eq_coal_plants": co2e / f["eq_coal_plant_year"],
            "penalties_5yr_usd": ((e["penalties_5yr_usd"] or 0) + (e["federal_case_penalties_usd"] or 0)) if e else None,
        }
        for lvl in ("low", "central", "high"):
            row[f"total_usd_{lvl}"] = row[f"health_usd_{lvl}"] + row[f"climate_usd_{lvl}"]
        row["total_usd_per_day_central"] = row["total_usd_central"] / 365
        out.append(row)
    out.sort(key=lambda r: -r["total_usd_central"])

    # ---------------------------------------------------------------- report
    print("  health sectors used: " + ", ".join(f"{s} ({b}) {n}" for (s, b), n in sorted(sector_counts.items(), key=lambda x: -x[1])))
    tot = lambda col: sum(r[col] or 0 for r in out)
    print(f"  {len(out):,} facilities with an estimate; Indiana totals ({YEAR}, {dollar_year}$):")
    print(f"    premature deaths: {tot('deaths_low'):,.0f} - {tot('deaths_high'):,.0f} (central {tot('deaths_central'):,.0f})")
    print(f"    health damage: ${tot('health_usd_low') / 1e9:,.1f}B - ${tot('health_usd_high') / 1e9:,.1f}B")
    print(f"    climate damage: ${tot('climate_usd_low') / 1e9:,.1f}B - ${tot('climate_usd_high') / 1e9:,.1f}B (central ${tot('climate_usd_central') / 1e9:,.1f}B)")
    print("  top 8 by central estimated harm:")
    for r in out[:8]:
        print(f"    {r['facility_name'][:38]:38} {r['city'] or '':14} deaths {r['deaths_low']:5.1f}-{r['deaths_high']:5.1f}  "
              f"health ${r['health_usd_central'] / 1e6:7,.0f}M  climate ${r['climate_usd_central'] / 1e6:7,.0f}M  [{r['health_sector']}]")

    cols = list(out[0].keys())
    types = {c: "TEXT" if c in ("registry_id", "in_echo", "facility_name", "city", "nei_eis_id", "ghg_facility_id",
                                "health_sector", "health_sector_basis", "health_factor_fallbacks",
                                "epa_outlier_values", "merged_records")
             else "INTEGER" if c == "year" else "REAL" for c in cols}
    print("Writing:")
    write_csv(PROCESSED_DIR / "impacts_indiana_facilities.csv", cols, out)
    w = connect()
    replace_table(w, "facility_impacts", [(c, types[c]) for c in cols], out, indexes=["(registry_id)"])
    set_metadata(w, "impacts", {"year": YEAR, "dollar_year": dollar_year, "vsl_usd": round(vsl),
                                "scc_central_usd": round(sc["co2"][1], 2),
                                "excludes": "toxics (TRI/RSEI), water, nonfatal illness"})
    w.commit()
    w.close()
    print("  table 'facility_impacts' in data/processed/indiana.sqlite")


if __name__ == "__main__":
    main()
