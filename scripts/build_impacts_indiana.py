"""
Estimate each Indiana facility's yearly harm in dollars, deaths and everyday terms.

Combines what facilities emit (NEI air pollutants, GHGRP greenhouse gases) with the
published factors from build_impact_factors.py:

  Health  = tons of PM2.5, SO2, NOx, NH3, VOC  x  EPA cases-per-ton for the facility's
            sector in Indiana  ->  estimated premature deaths and illnesses (lung cancers,
            heart attacks, strokes, cardiac arrests, new asthma cases, ER visits, hospital
            admissions, lost work and school days);  deaths x value of a statistical
            life  ->  dollars. Covers fine-particle and ozone pollution only (see below).
  Climate = tons of CO2, methane, nitrous oxide (+ other gases)  x  EPA social cost of
            each gas  ->  dollars of future climate damage.
  Reverse = tons CO2-equivalent  x  IPCC cost of removing CO2 from the air.
  Everyday comparisons = CO2-equivalent as cars, homes, gallons of gasoline, forest acres.
  Climate deaths = tons CO2-equivalent  x  Bressler (2021) "mortality cost of carbon": expected
            heat-related deaths worldwide, 2020-2100, per ton (low estimate floored at 0).
  Toxic chemicals = each chemical released to air, water or land (TRI, NEI hazardous air
            pollutants, water discharge monitoring) x EPA TRACI cases per kg (build_toxic_impacts_indiana.py)
            -> cancer cases and other disease cases; cancer cases x the US ratio of cancer deaths to new
            cases -> toxic deaths; deaths x value of a statistical life -> dollars.
            Low = air and water releases; central = + chemicals spread on land or put in ponds;
            high = + landfills (partly contained, so only in the high estimate).
  Total death toll = air-pollution deaths + toxic-chemical deaths + climate deaths. (Climate
            dollars are not added again: EPA's social cost of carbon already values climate deaths.)

Every number is an estimate with a low / central / high range. "Central" health is the
midpoint of EPA's low and high estimates; central climate uses EPA's central 2% rate.

NOT included: deaths from noncancer toxic illness (TRACI counts cases, not deaths; lead's heart and
brain harm is in these cases); chemicals TRACI has no factor for (manganese, nitrates, acids,
ammonia); water permit violations as such (the extra pollution they release is in the discharge
loads); the dollar cost of nonfatal illness -- EPA's illness dollar values come in tables whose
dollar year is inconsistently documented, so illness is counted in cases, not dollars.

Each facility is matched to one of EPA's 21 industrial sectors from its industry code
(NAICS) and NEI source type; facilities that don't fit a sector (e.g. airports, factories
burning fuel) use EPA's Indiana "industrial boilers" values and are flagged as a proxy.

Writes data/processed/impacts_indiana_facilities.csv + table facility_impacts.

Run after build_echo, build_ghg, build_nei, build_toxic_impacts and build_impact_factors.
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
# What each pollutant turns into in the air: fine particles (pm25) and/or ozone
PATHWAYS = {"PM": ["pm25"], "SO2": ["pm25"], "NH3": ["pm25"], "NOX": ["pm25", "ozone"], "VOC": ["ozone"]}
ILLNESSES = ["lung_cancers", "heart_attacks", "strokes", "cardiac_arrests", "new_asthma_cases", "er_visits",
             "hospital_admissions", "lost_work_days", "lost_school_days"]
OTHER_GHG = ["hfc", "pfc", "sf6", "nf3", "other_fluorinated", "hfe", "short_lived", "other_ghg"]
PROXY_SECTOR = "industrial boilers"
SAME_SITE_KM = 1.0   # a GHG and an NEI record this close together with different IDs = same plant
TOXIC_SAME_SITE_KM = 3.0   # a TRI or water report this close to a row, sharing a name word = same plant
# Share of new cancer cases that end in death: US estimates for 2024, 611,720 deaths / 2,001,140 new
# cases (American Cancer Society, Cancer Facts & Figures 2024)
CANCER_DEATHS_PER_CASE = 611720 / 2001140
# Words too common in plant names to show two reports are the same plant
GENERIC = {"ENERGY", "INDIANA", "POWER", "PLANT", "STATION", "GENERATING", "GENERATION", "COMPANY", "CORP",
           "CORPORATION", "STEEL", "SERVICES", "PRODUCTS", "NORTH", "AMERICA", "FACILITY", "WORKS", "MILL",
           "ELECTRIC", "COUNTY", "INDUSTRIES", "MANUFACTURING", "INTERNATIONAL", "HARBOR", "DUKE", "CLEVELAND",
           "CLIFFS", "UNITED", "STATES", "DIVISION", "OPERATIONS", "WATER", "TREATMENT", "CUSTOM", "SERVICE",
           "BURNS", "CUMMINS", "CITY", "TOWN", "WASTEWATER", "MUNICIPAL", "SANITARY", "DISTRICT", "LLC"}
# Plants whose GHG and NEI records are farther apart than that, checked by hand against the street
# addresses in EPA's files: {GHG facility ID: NEI facility ID}. Deliberately NOT listed, because they
# are different plants: Nucor Fastener / Nucor Vulcraft (St. Joe), Duke's power plant at Purdue / Purdue.
SAME_PLANT = {
    "1005602": "8199711",    # Steel Dynamics Structural & Rail, 2601 County Road 700 E, Columbia City
    "1012923": "17996111",   # St. Paul compressor station / Rockies Express, 7791 N County Road 650 W
    "1003691": "4554311",    # Newton County Landfill, 2266 E County Road 500 S, Brook
    "1006783": "8198211",    # Tyson Fresh Meats, 2125 S County Road 125 W, Logansport
    "1001155": "7972111",    # Aurorium (NEI still says Vertellus), 1500 S Tibbs Ave, Indianapolis
    "1006058": "8207011",    # U.S. Gypsum, 11345 State Road 650, Shoals
    "1005214": "4999311",    # Decatur Hills, 2090 S County Road 280 E, Greensburg
    "1006000": "4552511",    # Naval Surface Warfare Center Crane, 300 State Road 361, Crane
    "1003713": "4553211",    # Indiana University, Bloomington (same name, 1.2 km apart, addresses differ)
    "1005820": "4746611",    # Centerpoint Landfill, Centerpoint (same name; the GHG record has no address)
    # These three sit within SAME_SITE_KM of a different company, which the location rule would pick
    "1002167": "4520511",    # Bartholomew County Landfill II, 811 E County Road 450 S (not AK Tube)
    "1002875": "8089111",    # General Motors Bedford, 105 GM Dr (not Cap Group)
    "1014580": "4025411",    # Azteca Milling, 15700 US Highway 41 N, Evansville (not Wansford rail yard)
}
# Within SAME_SITE_KM of another company's air record, but a different address, and they have no NEI
# record of their own: Heritage-Crystal Clean (not Marathon), Farbest Foods (not OFS Brands).
NOT_SAME_SITE = {"1012651", "1010674"}
# One site reported as two NEI facilities: {NEI facility ID: the NEI facility to combine it with}.
# Alcoa Warrick's single greenhouse-gas report covers both the smelter and its power plant (GHGRP
# subparts D, electricity generation, and F, aluminum), so splitting the air records would leave
# the power plant with no climate harm.
SAME_SITE_NEI = {"8183111": "8181811"}   # Warrick Power Plant -> Alcoa Warrick LLC
# PM2.5 values EPA flags as "National" outliers (over its 500-ton screening level), compared with the
# 2017 and 2020 NEI. All six rank in the top 40 of about 78,000 US facilities in the 2023 NEI.
#   Tons of PM2.5:                 2017   2020   2023
OUTLIER_CHECKED = {
    "7376511",   # Burns Harbor    3406   2825   3199
    "8192011",   # Gary Works      1956   1605   2018
    "7363111",   # Gibson          2146   1747   1125
    "3986511",   # Indiana Harbor  1304   1167   1056
    "8183111",   # Alcoa Warrick   1043    633    632
    "7248511",   # Cayuga           633    593    587
}


def outlier_note(eis_id, flags):
    """Plain-language note for a facility with a figure EPA flags. flags: e.g. ['PM25-PRI (National)']."""
    if eis_id in OUTLIER_CHECKED:
        return ("One of the largest sources of fine particles in the US. EPA flags its 2023 figure as unusually "
                "high, but it is in line with or below what the plant reported in 2017 and 2020.")
    if all(x.endswith("(Low)") for x in flags):
        return "EPA flags one of this facility's emission figures as unusually low, so this estimate may be too low."
    return "EPA flags one of this facility's emission figures as unusually high compared with facilities nationwide."


def name_words(s, close=False):
    """Distinctive words in a name. close=True (reports under 1 km apart) also allows town names and
    "generating station", which only show it's the same plant when the two are that close."""
    skip = GENERIC - {"GENERATING", "STATION"} if close else GENERIC
    return {w for w in re.findall(r"[A-Z]{4,}", (s or "").upper()) if w not in skip}


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
        hf = {(r["sector"], r["pollutant"], r["pathway"], r["outcome"]): (r["cases_per_ton_low"], r["cases_per_ton_high"])
              for r in con.execute("SELECT * FROM health_factors")}
        outcomes = sorted({o for (_, _, _, o) in hf})
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

    # Which row each NEI facility belongs to
    nei_keys = {r["eis_facility_id"]: key_for(r["registry_id"], f"nei:{r['eis_facility_id']}") for r in nei}
    unknown = [e for e in (*SAME_PLANT.values(), *SAME_SITE_NEI, *SAME_SITE_NEI.values()) if e not in nei_keys]
    if unknown:
        sys.exit(f"ERROR: SAME_PLANT or SAME_SITE_NEI lists NEI facilities that aren't in the data: {unknown}")
    # Registry IDs merged into a row, so that row also gets the penalties filed under them
    merged_registries = defaultdict(set)
    for from_id, to_id in SAME_SITE_NEI.items():
        merged_registries[nei_keys[to_id]].add(nei_keys[from_id])
        nei_keys[from_id] = nei_keys[to_id]

    # ---------------------------------------------------------------- health (NEI)
    sector_counts = defaultdict(int)
    for r in nei:
        k = nei_keys[r["eis_facility_id"]]
        sector, basis = health_sector(r["naics"], r["source_type"], f"{r['facility_name']} {r['company'] or ''}")
        sector_counts[(sector, basis)] += 1
        fallbacks = []
        for pol, col in POLLUTANTS.items():
            tons = r[col] or 0.0
            if not tons:
                continue
            rows[k][col] += tons
            for pathway in PATHWAYS[pol]:
                for outcome in outcomes:
                    factor = hf.get((sector, pol, pathway, outcome))
                    if factor is None:
                        # sector has no value for this pollutant: use Indiana industrial boilers
                        factor = hf.get((PROXY_SECTOR, pol, pathway, outcome))
                        if outcome == "deaths" and factor:
                            fallbacks.append(f"{pol}/{pathway}")
                    if factor is None:
                        continue
                    low = factor[0] or 0.0
                    high = factor[1] if factor[1] is not None else low   # EPA gives some illnesses one estimate
                    rows[k][f"{outcome}_low"] += tons * low
                    rows[k][f"{outcome}_high"] += tons * high
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
            info[k]["outlier_note"] = outlier_note(r["eis_facility_id"], outliers[r["eis_facility_id"]])

    # ---------------------------------------------------------------- climate (GHGRP)
    # EPA often files one plant's air-pollution (NEI) and greenhouse-gas records under different
    # registry IDs. If a GHG facility's ID has no NEI record but an NEI facility sits at the same
    # spot (within SAME_SITE_KM) with no GHG record yet, treat them as one plant. SAME_PLANT adds the
    # pairs that are farther apart, usually because one record's coordinates are off.
    nei_sites = [(nei_keys[r["eis_facility_id"]], r["lat"], r["lon"]) for r in nei]
    has_ghg, merged, confirmed = {nei_keys[e] for e in SAME_PLANT.values()}, 0, 0
    for r in ghg:
        k = key_for(r["registry_id"], f"ghg:{r['ghg_facility_id']}")
        if r["ghg_facility_id"] in SAME_PLANT:
            k = nei_keys[SAME_PLANT[r["ghg_facility_id"]]]
            confirmed += 1
            info[k]["merged_records"] = f"GHG facility {r['ghg_facility_id']} (registry {r['registry_id']}) matched by address or name"
            merged_registries[k].add(r["registry_id"])
        elif r["ghg_facility_id"] not in NOT_SAME_SITE and (k not in info or "health_sector" not in info.get(k, {})):
            near = min(((km_apart(r["lat"], r["lon"], lat, lon), nk) for nk, lat, lon in nei_sites
                        if nk not in has_ghg), default=(float("inf"), None))
            if near[0] <= SAME_SITE_KM:
                k = near[1]
                merged += 1
                info[k]["merged_records"] = f"GHG facility {r['ghg_facility_id']} (registry {r['registry_id']}) matched by location"
                merged_registries[k].add(r["registry_id"])
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
        # When several GHG reports land on one row, it is identified by the biggest
        biggest = {"ghg_facility_id": r["ghg_facility_id"], "_ghg_t": total} if total >= prev.get("_ghg_t", -1) else {}
        info[k] = {**prev, **biggest, "name": prev.get("name") or r["facility_name"],
                   "city": prev.get("city") or r["city"], "lat": prev.get("lat") or r["lat"], "lon": prev.get("lon") or r["lon"]}
    print(f"  {merged} greenhouse-gas records merged into the air-pollution record at the same location, "
          f"{confirmed} more from the checked list (SAME_PLANT)")

    # ---------------------------------------------------------------- toxic chemicals (TRACI)
    # Which row each TRI facility and water permit belongs to: the air-pollution record EPA links
    # it to, else its own registry ID if that already has a row, else a row within
    # TOXIC_SAME_SITE_KM whose name shares a distinctive word, else a row of its own.
    tri_to_nei = {r["tri_facility_id"]: nei_keys[r["eis_facility_id"]] for r in nei if r["tri_facility_id"]}
    row_of_registry = {k: k for k in rows}
    for k, regs in merged_registries.items():
        for x in regs:
            row_of_registry.setdefault(x, k)
    def row_names(k, close=False):
        e = echo.get(k)
        return name_words(f"{info.get(k, {}).get('name') or ''} {e['name'] if e else ''}", close)
    # A row can sit at two spots (EPA's registry and the air-pollution record disagree): try both
    sites = [(k, lat, lon, row_names(k) - name_words(i.get("city")), row_names(k, True))
             for k, i in info.items()
             for lat, lon in {(i.get("lat"), i.get("lon")), ((echo[k]["lat"], echo[k]["lon"]) if k in echo else (None, None))}
             if lat is not None]
    linked = defaultdict(int)
    def toxic_row(r):
        if r["source"] == "nei":
            return nei_keys[r["source_id"]]
        if r["source"] == "tri" and r["source_id"] in tri_to_nei:
            linked["air record"] += 1
            return tri_to_nei[r["source_id"]]
        if r["registry_id"] in row_of_registry:
            linked["registry ID"] += 1
            return row_of_registry[r["registry_id"]]
        mine, mine_close = name_words(r["facility_name"]) - name_words(r["city"]), name_words(r["facility_name"], True)
        if "CONTRACTOR" not in (r["facility_name"] or "").upper():
            dist = ((km_apart(r["lat"], r["lon"], lat, lon), k, bool(mine & words), bool(mine_close & close))
                    for k, lat, lon, words, close in sites)
            near = min(((d, k) for d, k, far_ok, close_ok in dist if far_ok or (close_ok and d <= 1.0)),
                       default=(float("inf"), None))
            if near[0] <= TOXIC_SAME_SITE_KM:
                linked["location and name"] += 1
                return near[1]
        linked["own row"] += 1
        return r["registry_id"] or f"{r['source']}:{r['source_id']}"
    try:
        toxic = con.execute("SELECT * FROM toxic_release_cases WHERE year = ?", (YEAR,)).fetchall()
    except sqlite3.OperationalError as e:
        sys.exit(f"ERROR: {e}. Run build_toxic_impacts_indiana.py first.")
    if not toxic:
        sys.exit(f"ERROR: no toxic release cases for {YEAR}. Run build_toxic_impacts_indiana.py first.")
    row_for_source = {}
    # cases[row][(chemical, compartment)][source] = [cancer, noncancer]
    cases = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0.0, 0.0])))
    for r in toxic:
        sid = (r["source"], r["source_id"])
        if sid not in row_for_source:
            row_for_source[sid] = toxic_row(r)
            k = row_for_source[sid]
            if k not in info:
                info[k] = {"name": r["facility_name"], "city": r["city"], "lat": r["lat"], "lon": r["lon"]}
        if r["traci_key"]:
            c = cases[row_for_source[sid]][(r["traci_key"], r["compartment"])][r["source"]]
            c[0] += r["cancer_cases"]
            c[1] += r["noncancer_cases"]
    print("  toxic reports joined to a plant by " + ", ".join(f"{how}: {n}" for how, n in linked.items()))
    for k, chems in cases.items():
        top = defaultdict(float)
        for (chem, comp), by_source in chems.items():
            # TRI and NEI (air) or TRI and DMR (water) can report the same release: keep the larger
            cancer, other = max(by_source.values(), key=lambda x: x[0] * 100 + x[1])
            levels = {"air": ("low", "central", "high"), "water": ("low", "central", "high"),
                      "land_open": ("central", "high"), "landfill": ("high",)}[comp]
            for lvl in levels:
                rows[k][f"toxic_cancers_{lvl}"] += cancer
                rows[k][f"toxic_illnesses_{lvl}"] += other
            if "central" in levels:
                top[chem] += cancer * 100 + other
        if top:
            info[k]["toxic_top_chemical"] = max(top, key=top.get)

    # ---------------------------------------------------------------- combine
    out = []
    for k, v in rows.items():
        i = info.get(k, {})
        e = echo.get(k)
        d_lo, d_hi = v.get("deaths_low", 0.0), v.get("deaths_high", 0.0)
        d_mid = (d_lo + d_hi) / 2
        co2e = v.get("co2e_t", 0.0)
        penalties = [(echo[x]["penalties_5yr_usd"] or 0) + (echo[x]["federal_case_penalties_usd"] or 0)
                     for x in {k} | merged_registries[k] if x in echo]
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
            "outlier_note": i.get("outlier_note"),               # the same thing in plain language, for the map
            "merged_records": i.get("merged_records"),
            **{col: v.get(col) or None for col in POLLUTANTS.values()},
            "deaths_low": d_lo, "deaths_central": d_mid, "deaths_high": d_hi,
            # nonfatal illness, central estimate (midpoint of EPA low/high; usually identical)
            **{ill: (v.get(f"{ill}_low", 0.0) + v.get(f"{ill}_high", 0.0)) / 2 for ill in ILLNESSES},
            "health_usd_low": d_lo * vsl, "health_usd_central": d_mid * vsl, "health_usd_high": d_hi * vsl,
            "co2e_t": co2e or None, "co2_t": v.get("co2_t") or None, "ch4_t": v.get("ch4_t") or None, "n2o_t": v.get("n2o_t") or None,
            "climate_usd_low": v.get("climate_usd_low", 0.0), "climate_usd_central": v.get("climate_usd_central", 0.0),
            "climate_usd_high": v.get("climate_usd_high", 0.0),
            "removal_usd_low": co2e * f["removal_cost_low"], "removal_usd_high": co2e * f["removal_cost_high"],
            "eq_cars": co2e / f["eq_car_year"], "eq_homes": co2e / f["eq_home_energy_year"],
            "eq_gallons_gasoline": co2e / f["eq_gallon_gasoline"], "eq_forest_acres": co2e / f["eq_forest_acre_year"],
            "eq_coal_plants": co2e / f["eq_coal_plant_year"],
            "penalties_5yr_usd": sum(penalties) if penalties else None,
            "toxic_top_chemical": i.get("toxic_top_chemical"),
            "toxic_cancers": v.get("toxic_cancers_central", 0.0), "toxic_illnesses": v.get("toxic_illnesses_central", 0.0),
        }
        for lvl in ("low", "central", "high"):
            row[f"toxic_deaths_{lvl}"] = v.get(f"toxic_cancers_{lvl}", 0.0) * CANCER_DEATHS_PER_CASE
            row[f"toxic_usd_{lvl}"] = row[f"toxic_deaths_{lvl}"] * vsl
        for lvl in ("low", "central", "high"):
            row[f"total_usd_{lvl}"] = row[f"health_usd_{lvl}"] + row[f"toxic_usd_{lvl}"] + row[f"climate_usd_{lvl}"]
        row["total_usd_per_day_central"] = row["total_usd_central"] / 365
        # Climate deaths: worldwide, heat-related, spread over 2020-2100 (Bressler 2021)
        for lvl in ("low", "central", "high"):
            row[f"climate_deaths_{lvl}"] = max(0.0, co2e * f[f"mcc_{lvl}"])
            row[f"total_deaths_{lvl}"] = row[f"deaths_{lvl}"] + row[f"toxic_deaths_{lvl}"] + row[f"climate_deaths_{lvl}"]
        out.append(row)
    out.sort(key=lambda r: (-r["total_usd_central"], -r["toxic_illnesses"]))

    # ---------------------------------------------------------------- report
    print("  health sectors used: " + ", ".join(f"{s} ({b}) {n}" for (s, b), n in sorted(sector_counts.items(), key=lambda x: -x[1])))
    tot = lambda col: sum(r[col] or 0 for r in out)
    print(f"  {len(out):,} facilities with an estimate; Indiana totals ({YEAR}, {dollar_year}$):")
    print(f"    premature deaths: {tot('deaths_low'):,.0f} - {tot('deaths_high'):,.0f} (central {tot('deaths_central'):,.0f})")
    print(f"    climate deaths (worldwide, 2020-2100): {tot('climate_deaths_low'):,.0f} - {tot('climate_deaths_high'):,.0f} "
          f"(central {tot('climate_deaths_central'):,.0f}); total death toll central {tot('total_deaths_central'):,.0f}")
    print(f"    toxic chemicals: cancer deaths {tot('toxic_deaths_low'):,.1f} - {tot('toxic_deaths_high'):,.1f} (central "
          f"{tot('toxic_deaths_central'):,.1f}); {tot('toxic_cancers'):,.1f} cancers and {tot('toxic_illnesses'):,.0f} other illnesses (central)")
    print("    illness: " + ", ".join(f"{ill.replace('_', ' ')} {tot(ill):,.0f}" for ill in ILLNESSES))
    print(f"    health damage: ${tot('health_usd_low') / 1e9:,.1f}B - ${tot('health_usd_high') / 1e9:,.1f}B")
    print(f"    climate damage: ${tot('climate_usd_low') / 1e9:,.1f}B - ${tot('climate_usd_high') / 1e9:,.1f}B (central ${tot('climate_usd_central') / 1e9:,.1f}B)")
    print("  top 8 by central estimated harm:")
    for r in out[:8]:
        print(f"    {(r['facility_name'] or '')[:38]:38} {r['city'] or '':14} deaths {r['deaths_low']:5.1f}-{r['deaths_high']:5.1f}  "
              f"health ${r['health_usd_central'] / 1e6:7,.0f}M  climate ${r['climate_usd_central'] / 1e6:7,.0f}M  [{r['health_sector']}]")

    cols = list(out[0].keys())
    types = {c: "TEXT" if c in ("registry_id", "in_echo", "facility_name", "city", "nei_eis_id", "ghg_facility_id",
                                "health_sector", "health_sector_basis", "health_factor_fallbacks",
                                "epa_outlier_values", "outlier_note", "merged_records", "toxic_top_chemical")
             else "INTEGER" if c == "year" else "REAL" for c in cols}
    print("Writing:")
    write_csv(PROCESSED_DIR / "impacts_indiana_facilities.csv", cols, out)
    w = connect()
    replace_table(w, "facility_impacts", [(c, types[c]) for c in cols], out, indexes=["(registry_id)"])
    set_metadata(w, "impacts", {"year": YEAR, "dollar_year": dollar_year, "vsl_usd": round(vsl),
                                "scc_central_usd": round(sc["co2"][1], 2),
                                "excludes": "noncancer toxic deaths, chemicals without a TRACI factor, nonfatal illness dollars"})
    w.commit()
    w.close()
    print("  table 'facility_impacts' in data/processed/indiana.sqlite")


if __name__ == "__main__":
    main()
