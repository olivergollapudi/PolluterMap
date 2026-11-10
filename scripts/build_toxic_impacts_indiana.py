"""
Score toxic chemical releases (to air, water and land) in disease cases, using EPA's TRACI.

TRACI 2.2 is EPA's Tool for the Reduction and Assessment of Chemical and other environmental
Impacts. Its human-health toxicity factors come from USEtox, the UN Environment / SETAC consensus
model: for each chemical and where it is released (air, fresh water, soil), the expected number of
cancer cases and of other (noncancer) disease cases in the whole population, per kg released. It
follows the chemical as it spreads through air, water, soil and the food chain, over an unlimited
time (metals never break down, so their factors count harm far into the future).

    cases = kg released x TRACI factor for that chemical and place

Releases scored, all for 2023 (the year the air-pollution and climate estimates use):
  - TRI toxic releases on site: air (stack + fugitive), water, and land.
    Land is split by how exposed it is:
      "land_open"  - spread on land, "other land disposal", and surface impoundments (ponds, such as
                      coal ash ponds, many of them unlined)
      "landfill"   - landfills other than hazardous-waste (RCRA Subtitle C) landfills; these are
                      partly contained, so build_impacts counts them only in the HIGH estimate
    Not scored: hazardous-waste landfills and impoundments and underground injection wells, which are
    built to keep waste away from people, and off-site transfers (they happen somewhere else).
  - NEI hazardous air pollutants (benzene, chromium, lead, mercury, ...), so facilities that don't
    file TRI reports are covered too.
  - Water discharge monitoring (DMR) loads of toxic metals, cyanide and benzene, from the EPA
    Loading Tool, which covers permit holders that don't file TRI (sewage plants, mines, ...).
    Loads EPA flags as potential outliers are left out.
When TRI and NEI (air) or TRI and DMR (water) report the same chemical for the same plant,
build_impacts_indiana.py keeps the larger figure, so nothing is counted twice.

Metals: TRI reports metal compounds as the weight of the metal. TRACI's metal factors are for the
dissolved metal ion (lead(II), zinc(II), ...). Chromium is scored as chromium(III), the common,
much less harmful form, unless the source says hexavalent (chromium(VI)). EPA marks all metal
factors "interim": usable, but less certain than the "recommended" ones.

No factor exists for some big releases (manganese, nitrates, sodium nitrite, carbonyl sulfide);
they are listed in the output but score zero.

Writes tables toxicity_factors and toxic_release_cases, and
data/processed/toxic_release_cases_indiana.csv.

Run after build_tri, build_nei and build_water.
    python3 scripts/build_toxic_impacts_indiana.py [--force]
"""

import re
import sqlite3
import sys
from collections import defaultdict

from common import DB_PATH, PROCESSED_DIR, RAW_DIR, connect, download, read_xlsx_rows, replace_table, set_metadata, write_csv

YEAR = 2023
TRACI_URL = "https://www.epa.gov/system/files/documents/2024-01/traci_2_2.xlsx"
TRACI_FILE = RAW_DIR / "traci" / "traci_2_2.xlsx"
KG_PER_LB = 0.45359237

# TRACI columns: (cancer, noncancer) cases per kg, by where the chemical is released.
# Air uses "continental rural air": TRACI's "urban air" factors are higher, so this is the cautious choice.
COLUMNS = {"air": ("O", "P"), "water": ("Q", "R"), "soil": ("U", "V")}
COMPARTMENT_FACTOR = {"air": "air", "water": "water", "land_open": "soil", "landfill": "soil"}
LAND_OPEN = ["land_treatment_lb", "other_land_disposal_lb", "other_surface_impoundment_lb"]
LANDFILL = ["other_landfill_lb"]

# TRACI lists metals by name ("LEAD(II)") rather than CAS number. Each metal's CAS number and TRI
# compound-category code -> the TRACI entry.
METALS = {
    "ZINC(II)": ("7440666", "N982"), "LEAD(II)": ("7439921", "N420"), "BARIUM(II)": ("7440393", "N040"),
    "CHROMIUM(III)": ("7440473", "N090", "16065831"), "VANADIUM(V)": ("7440622", "N770"),
    "COPPER(II)": ("7440508", "N100"), "ARSENIC(V)": ("7440382", "N020"), "CADMIUM(II)": ("7440439", "N078"),
    "MERCURY(II)": ("7439976", "N458"), "NICKEL(II)": ("7440020", "N495"), "CHROMIUM(VI)": ("18540299",),
}
METAL_BY_ID = {i: name for name, ids in METALS.items() for i in ids}
# Water monitoring parameters -> the same keys (metals by name, organics by CAS number)
WATER_PARAMS = [
    (r"^chromium, hexavalent", "CHROMIUM(VI)"), (r"^zinc, total", "ZINC(II)"), (r"^lead, total", "LEAD(II)"),
    (r"^barium, total", "BARIUM(II)"), (r"^chromium, total", "CHROMIUM(III)"), (r"^vanadium, total", "VANADIUM(V)"),
    (r"^copper, total", "COPPER(II)"), (r"^arsenic, total", "ARSENIC(V)"), (r"^cadmium, total", "CADMIUM(II)"),
    (r"^mercury, total", "MERCURY(II)"), (r"^nickel, total", "NICKEL(II)"), (r"^cyanide, total", "57125"),
    (r"^benzene$", "71432"),
]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None   # TRACI writes "n/a" where it has no factor


def load_factors():
    rows = list(read_xlsx_rows(TRACI_FILE, "Substances"))
    head = rows[0]
    if not (head.get("A", "").startswith("CAS") and "cont. rural air, cancer" in head.get("O", "")
            and "cont. freshwater, non-canc" in head.get("R", "") and "natural soil, non-canc" in head.get("V", "")):
        sys.exit("ERROR: TRACI's columns are not where expected. Check the 'Substances' sheet header before going on.")
    factors = {}
    for r in rows[1:]:
        cas, name = (r.get("A") or "").strip(), (r.get("C") or "").strip()
        key = name if cas == "x" else cas.lstrip("0")
        if not key:
            continue
        f = {"key": key, "name": name, "cancer_flag": r.get("Y"), "noncancer_flag": r.get("Z")}
        for comp, (c, n) in COLUMNS.items():
            f[f"{comp}_cancer"], f[f"{comp}_noncancer"] = num(r.get(c)), num(r.get(n))
        if any(f[f"{comp}_{k}"] for comp in COLUMNS for k in ("cancer", "noncancer")):
            factors[key] = f
    return factors


def chem_key(cas):
    """TRI/NEI chemical ID ('7439-92-1', 'N420', '7439921') -> TRACI key."""
    plain = (cas or "").replace("-", "").lstrip("0")
    return METAL_BY_ID.get(plain) or METAL_BY_ID.get(cas) or plain


def main():
    download(TRACI_URL, TRACI_FILE, force="--force" in sys.argv[1:])
    factors = load_factors()
    print(f"  TRACI: {len(factors):,} chemicals with human-health factors")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    out, unscored = [], defaultdict(float)

    def add(source, source_id, registry_id, name, city, lat, lon, chemical, key, comp, lb):
        if not lb or lb <= 0:
            return
        f = factors.get(key)
        kg = lb * KG_PER_LB
        if not f:
            unscored[chemical] += lb
        fc = COMPARTMENT_FACTOR[comp]
        out.append({
            "source": source, "source_id": source_id, "registry_id": registry_id, "facility_name": name, "city": city,
            "lat": lat, "lon": lon, "year": YEAR, "chemical": chemical, "traci_key": key if f else None,
            "traci_name": f["name"] if f else None, "compartment": comp, "lb": lb,
            "cancer_cases": kg * (f[f"{fc}_cancer"] or 0) if f else 0.0,
            "noncancer_cases": kg * (f[f"{fc}_noncancer"] or 0) if f else 0.0,
            "factor_status": " / ".join(x for x in (f["cancer_flag"], f["noncancer_flag"]) if x and x != "n/a") if f else "no factor",
        })

    try:
        # TRI
        for r in con.execute("SELECT * FROM tri_releases WHERE year = ?", (YEAR,)):
            key = chem_key(r["cas_number"])
            args = ("tri", r["trifd"], r["registry_id"], r["facility_name"], r["city"], r["lat"], r["lon"], r["chemical"], key)
            add(*args, "air", r["air_lb"])
            add(*args, "water", r["water_lb"])
            add(*args, "land_open", sum(r[c] or 0 for c in LAND_OPEN))
            add(*args, "landfill", sum(r[c] or 0 for c in LANDFILL))
        # NEI hazardous air pollutants (amount_lb is in pounds for every pollutant)
        for r in con.execute("""SELECT e.eis_facility_id, e.pollutant_code, e.pollutant, e.amount_lb, n.registry_id, n.facility_name,
                                       n.city, n.lat, n.lon
                                FROM nei_emissions e JOIN nei_facilities n USING (eis_facility_id)
                                WHERE e.pollutant_type IN ('HAP', 'CAP/HAP') AND e.amount_lb > 0"""):
            add("nei", r["eis_facility_id"], r["registry_id"], r["facility_name"], r["city"], r["lat"], r["lon"],
                r["pollutant"], chem_key(r["pollutant_code"]), "air", r["amount_lb"])
        # Water monitoring: sum outfalls per parameter, then keep the biggest parameter per chemical
        # (a permit can report both "zinc, total" and "zinc, total recoverable")
        loads = defaultdict(float)
        flagged = 0
        for r in con.execute("""SELECT l.npdes_id, l.parameter, l.load_lb, l.potential_outlier FROM water_loadings l
                                WHERE l.year = ? AND l.load_lb > 0""", (YEAR,)):
            key = next((k for pat, k in WATER_PARAMS if re.search(pat, r["parameter"].lower())), None)
            if not key:
                continue
            if r["potential_outlier"] == "Yes":
                flagged += 1
                continue
            loads[(r["npdes_id"], key, r["parameter"])] += r["load_lb"]
        best = {}
        for (npdes, key, param), lb in loads.items():
            if lb > best.get((npdes, key), ("", 0))[1]:
                best[(npdes, key)] = (param, lb)
        permits = {r["npdes_id"]: r for r in con.execute("SELECT * FROM water_permits")}
        for (npdes, key), (param, lb) in best.items():
            p = permits.get(npdes)
            if p:
                add("dmr", npdes, p["registry_id"], p["facility_name"], p["city"], p["lat"], p["lon"], param, key, "water", lb)
    except sqlite3.OperationalError as e:
        sys.exit(f"ERROR: {e}. Run build_tri, build_nei and build_water first.")

    # ---------------------------------------------------------------- report
    print(f"  {len(out):,} chemical releases scored ({flagged} water loads EPA flags as potential outliers left out)")
    for src in ("tri", "nei", "dmr"):
        rs = [r for r in out if r["source"] == src]
        print(f"    {src}: {len({r['source_id'] for r in rs}):,} facilities, cancer cases {sum(r['cancer_cases'] for r in rs):,.1f}, "
              f"other disease cases {sum(r['noncancer_cases'] for r in rs):,.0f} (before removing overlaps)")
    by_chem = defaultdict(lambda: [0.0, 0.0])
    for r in out:
        by_chem[r["traci_name"] or "-"][0] += r["cancer_cases"]
        by_chem[r["traci_name"] or "-"][1] += r["noncancer_cases"]
    print("  biggest chemicals (cancer, other): " + ", ".join(
        f"{k} {c:,.1f}/{n:,.0f}" for k, (c, n) in sorted(by_chem.items(), key=lambda x: -(x[1][0] * 100 + x[1][1]))[:8]))
    print("  largest releases with no TRACI factor: " + ", ".join(
        f"{k[:30]} {v / 1e6:,.1f}M lb" for k, v in sorted(unscored.items(), key=lambda x: -x[1])[:6]))

    cols = list(out[0].keys())
    text = {"source", "source_id", "registry_id", "facility_name", "city", "chemical", "traci_key", "traci_name", "compartment", "factor_status"}
    types = [(c, "TEXT" if c in text else "INTEGER" if c == "year" else "REAL") for c in cols]
    fcols = list(next(iter(factors.values())).keys())
    print("Writing:")
    write_csv(PROCESSED_DIR / "toxic_release_cases_indiana.csv", cols, out)
    w = connect()
    replace_table(w, "toxicity_factors", [(c, "REAL" if c.endswith("cancer") else "TEXT") for c in fcols], list(factors.values()),
                  indexes=["(key)"])
    replace_table(w, "toxic_release_cases", types, out, indexes=["(source, source_id)", "(registry_id)"])
    set_metadata(w, "toxics", {"year": YEAR, "method": "EPA TRACI 2.2 (USEtox 1.0) human-health factors", "url": TRACI_URL})
    w.commit()
    w.close()
    print("  tables 'toxicity_factors' and 'toxic_release_cases' in data/processed/indiana.sqlite")


if __name__ == "__main__":
    main()
