"""
Collect the published factors used to put a dollar (and human) value on pollution.

Nothing here is invented: every number comes from a named government or IPCC source,
recorded next to the value in the `impact_factors` table. Where a source only exists as a
PDF or web page, the values are typed in below with the exact table they come from.

What it collects:
  1. Health harm of air pollution -- EPA "benefit per ton" estimates: premature deaths
     per (short) ton of fine particles (PM2.5), SO2, NOx, ammonia (NH3) and VOCs, by
     industrial sector, for Indiana (power plants, boilers, engines) or EPA's "North" region
     (all other sectors; Indiana is in it). Low and high estimates come from two different
     medical studies. Downloaded as spreadsheets.
  2. Value of a statistical life -- EPA's $7.4 million (2006 dollars), used to turn
     estimated deaths into dollars.
  3. Climate harm -- EPA's 2023 social cost of CO2, methane and nitrous oxide (dollars of
     future damage per ton emitted), at three discount rates.
  4. Cost to reverse -- the IPCC's estimated cost of removing CO2 from the air with direct
     air capture at scale.
  5. Everyday comparisons -- EPA's Greenhouse Gas Equivalencies factors (cars, homes...).
  6. Inflation -- the Consumer Price Index (CPI-U) from the Bureau of Labor Statistics, so
     every dollar figure can be stated in DOLLAR_YEAR dollars.

Writes, into data/processed/ (CSV + table of the same name in indiana.sqlite):
  impact_factors   one row per scalar factor, with unit, dollar year and source
  health_factors   sector x pollutant x outcome: deaths per ton (low, high)
  cpi              year -> CPI-U annual average

    python3 scripts/build_impact_factors.py
"""

import argparse
import json
import urllib.request
from collections import defaultdict

from common import RAW_DIR, PROCESSED_DIR, STATE, connect, download, read_xlsx_rows, replace_table, \
    set_metadata, write_csv

IMPACT_RAW = RAW_DIR / "impacts"
DOLLAR_YEAR = 2024   # latest complete CPI year; all outputs are stated in these dollars

BPT_BASE = "https://gaftp.epa.gov/benmap/BPTs/archives/2023%20BPTs/"
BPT_FILES = {"pm": "2025_bpt_pm_incidence_results_newhif_wegu.xlsx",
             "o3": "2025_bpt_o3_incidence_results_newhif_wegu.xlsx"}
BPT_SOURCE = ("EPA, Estimating the Benefit per Ton of Reducing Directly-Emitted PM2.5, PM2.5 Precursors and "
              "Ozone Precursors from 21 Sectors (2023 BPT incidence-per-ton results, analysis year 2025)")
REGION = "NORTH"     # EPA's BPT region containing Indiana (East North Central); see TSD Figure 2

BLS_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
CPI_SERIES = "CUUR0000SA0"   # CPI-U, U.S. city average, all items, not seasonally adjusted
CPI_YEARS = [(2006, 2015), (2016, 2025)]   # the keyless BLS API returns up to 10 years per request

SCGHG_URL = "https://www.epa.gov/system/files/documents/2023-12/epa_scghg_2023_report_final.pdf"
# EPA (2023) Report on the Social Cost of Greenhouse Gases, Table A.5.1 (unrounded), 2020 dollars per
# metric ton, by emission year, at near-term Ramsey discount rates 2.5% / 2.0% / 1.5%.
# 2.0% is EPA's central rate; 2.5% gives the low and 1.5% the high value.
SCGHG = {
    #        CO2                  CH4                      N2O
    2020: ((117, 193, 337), (1257, 1648, 2305), (35232, 54139, 87284)),
    2021: ((119, 197, 341), (1324, 1723, 2391), (36180, 55364, 88869)),
    2022: ((122, 200, 346), (1390, 1799, 2478), (37128, 56590, 90454)),
    2023: ((125, 204, 351), (1457, 1874, 2564), (38076, 57816, 92040)),
    2024: ((128, 208, 356), (1524, 1950, 2650), (39024, 59041, 93625)),
    2025: ((130, 212, 360), (1590, 2025, 2737), (39972, 60267, 95210)),
}

FACTORS = [
    # key, value, unit, dollar_year, source, url, note
    ("vsl", 7_400_000, "USD per statistical life", 2006,
     "EPA, Mortality Risk Valuation (central estimate)", "https://www.epa.gov/environmental-economics/mortality-risk-valuation",
     "Adjusted here to DOLLAR_YEAR with CPI-U; EPA's income-growth adjustment is not applied (makes this conservative)."),
    ("removal_cost_low", 100, "USD per metric ton CO2", None,
     "IPCC AR6 WGIII Ch.12 Executive Summary: DACCS cost USD100-300 (full range USD84-386) per tCO2",
     "https://www.ipcc.ch/report/ar6/wg3/downloads/report/IPCC_AR6_WGIII_Chapter12.pdf",
     "Projected cost of direct air capture and storage at scale; dollar year not stated; today's prices are higher."),
    ("removal_cost_high", 300, "USD per metric ton CO2", None, "IPCC AR6 WGIII Ch.12 (as above)",
     "https://www.ipcc.ch/report/ar6/wg3/downloads/report/IPCC_AR6_WGIII_Chapter12.pdf", ""),
    ("removal_cost_full_low", 84, "USD per metric ton CO2", None, "IPCC AR6 WGIII Ch.12 (full range)",
     "https://www.ipcc.ch/report/ar6/wg3/downloads/report/IPCC_AR6_WGIII_Chapter12.pdf", ""),
    ("removal_cost_full_high", 386, "USD per metric ton CO2", None, "IPCC AR6 WGIII Ch.12 (full range)",
     "https://www.ipcc.ch/report/ar6/wg3/downloads/report/IPCC_AR6_WGIII_Chapter12.pdf", ""),
    ("gwp_ch4", 25, "t CO2e per t CH4", None, "IPCC AR4 100-year GWP, as used by EPA GHGRP",
     "https://www.epa.gov/ghgreporting/data-sets", "Used to turn reported methane CO2e back into tons of methane."),
    ("gwp_n2o", 298, "t CO2e per t N2O", None, "IPCC AR4 100-year GWP, as used by EPA GHGRP",
     "https://www.epa.gov/ghgreporting/data-sets", ""),
    # EPA Greenhouse Gas Equivalencies Calculator - Calculations and References (page updated 2026-08-04)
    ("eq_car_year", 4.29, "t CO2e per gasoline passenger vehicle per year", None,
     "EPA Greenhouse Gas Equivalencies Calculator", "https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator-calculations-and-references", "2022 data"),
    ("eq_home_energy_year", 7.45, "t CO2 per home's energy use per year", None,
     "EPA Greenhouse Gas Equivalencies Calculator", "https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator-calculations-and-references", "2022 data"),
    ("eq_gallon_gasoline", 0.008887, "t CO2 per gallon of gasoline", None,
     "EPA Greenhouse Gas Equivalencies Calculator", "https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator-calculations-and-references", ""),
    ("eq_forest_acre_year", 1.00, "t CO2 absorbed per acre of average U.S. forest per year", None,
     "EPA Greenhouse Gas Equivalencies Calculator", "https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator-calculations-and-references", "2021-2022"),
    ("eq_coal_plant_year", 3_790_003.72, "t CO2 per average coal-fired power plant per year", None,
     "EPA Greenhouse Gas Equivalencies Calculator", "https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator-calculations-and-references", "2022 data"),
    ("eq_pound_coal", 0.0009, "t CO2 per pound of coal burned", None,
     "EPA Greenhouse Gas Equivalencies Calculator", "https://www.epa.gov/energy/greenhouse-gas-equivalencies-calculator-calculations-and-references", "2022 data"),
]


# ---------------------------------------------------------------- CPI

def fetch_cpi(force):
    by_year = defaultdict(list)
    for start, end in CPI_YEARS:
        path = IMPACT_RAW / f"cpi_{CPI_SERIES}_{start}_{end}.json"
        if force or not path.exists():
            print(f"Downloading CPI {start}-{end} from BLS")
            body = json.dumps({"seriesid": [CPI_SERIES], "startyear": str(start), "endyear": str(end)}).encode()
            req = urllib.request.Request(BLS_URL, data=body, headers={"Content-Type": "application/json",
                                                                      "User-Agent": "Mozilla/5.0 (PolluterMap data script)"})
            with urllib.request.urlopen(req) as resp:
                data = json.load(resp)
            if data.get("status") != "REQUEST_SUCCEEDED":
                raise SystemExit(f"ERROR: BLS request failed: {data.get('message')}")
            IMPACT_RAW.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        else:
            print(f"Using existing download: {path.name}")
        for x in json.loads(path.read_text())["Results"]["series"][0]["data"]:
            if x["period"].startswith("M") and x["period"] != "M13" and x["value"] != "-":
                by_year[int(x["year"])].append(float(x["value"]))
    # Annual average from monthly values; flag years with missing months
    return [{"year": y, "cpi_u": round(sum(v) / len(v), 3), "months": len(v)} for y, v in sorted(by_year.items())]


# ---------------------------------------------------------------- EPA benefit-per-ton

def sheet_dicts(path, sheet):
    rows = list(read_xlsx_rows(path, sheet))
    header = rows[0]
    return [{header[c]: v.strip() for c, v in r.items() if c in header} for r in rows[1:]]


def per_ton(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None   # drop "Inf"/NaN


def read_bpt():
    """Deaths per short ton, keyed (sector, pollutant, outcome) -> {'low':..,'high':..,'geography':..}."""
    out = defaultdict(dict)
    pm = IMPACT_RAW / BPT_FILES["pm"]
    o3 = IMPACT_RAW / BPT_FILES["o3"]

    # Fine particles (PM2.5) formed from direct PM2.5, SO2, NOx and NH3: all-cause mortality.
    for level in ("low", "high"):
        for r in sheet_dicts(pm, f"most_sectors_{level}"):
            if r["sectorname"].endswith(f"- {REGION}") and r["Endpoint"].startswith("Mortality"):
                sector = r["sectorname"].rsplit(" - ", 1)[0]
                for pol in ("PM", "SO2", "NOX", "NH3"):
                    out[(sector, pol, "pm25_mortality")][level] = per_ton(r.get(pol))
                    out[(sector, pol, "pm25_mortality")]["geography"] = f"EPA {REGION} region"
        for sheet, sector, key in ((f"boilers_{level}", "industrial boilers", "State"),
                                   (f"ICE_{level}", "internal combustion engines", "State"),
                                   (f"EGU_{level}", "electricity generating units", "State.Region")):
            for r in sheet_dicts(pm, sheet):
                if r.get(key) == STATE and r["Endpoint"].startswith("Mortality"):
                    for pol, col in (("PM", "PM"), ("PM", "PM2.5"), ("SO2", "SO2"), ("NOX", "NOX"), ("NOX", "NOx"), ("NH3", "NH3")):
                        if col in r:
                            out[(sector, pol, "pm25_mortality")][level] = per_ton(r.get(col))
                            out[(sector, pol, "pm25_mortality")]["geography"] = STATE

    # Ozone formed from NOx and VOC: respiratory mortality. EPA pairs short-term effects with
    # the low PM estimate and long-term effects with the high one.
    for level, ozone in (("low", "short"), ("high", "long")):
        for r in sheet_dicts(o3, f"most_sectors_{ozone}"):
            if r["sectorname"].endswith(f"- {REGION}") and "ortality" in r["Endpoint"]:
                sector = r["sectorname"].rsplit(" - ", 1)[0]
                for pol in ("NOX", "VOC"):
                    out[(sector, pol, "ozone_mortality")][level] = per_ton(r.get(pol))
                    out[(sector, pol, "ozone_mortality")]["geography"] = f"EPA {REGION} region"
        for sheet, sector, key in ((f"boilers_{ozone}", "industrial boilers", "State"),
                                   (f"ICE_{ozone}", "internal combustion engines", "State"),
                                   (f"EGU_{ozone}", "electricity generating units", "State.Region")):
            for r in sheet_dicts(o3, sheet):
                if r.get(key) == STATE and "ortality" in r["Endpoint"]:
                    for pol in ("NOX", "VOC"):
                        if pol in r:
                            out[(sector, pol, "ozone_mortality")][level] = per_ton(r.get(pol))
                            out[(sector, pol, "ozone_mortality")]["geography"] = STATE

    rows = []
    for (sector, pol, outcome), v in sorted(out.items()):
        if v.get("low") is None and v.get("high") is None:
            continue
        rows.append({"sector": sector, "pollutant": pol, "outcome": outcome,
                     "deaths_per_ton_low": v.get("low"), "deaths_per_ton_high": v.get("high"),
                     "geography": v["geography"], "unit": "premature deaths per short ton emitted"})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-download even if files exist")
    args = parser.parse_args()

    for f in BPT_FILES.values():
        download(BPT_BASE + f, IMPACT_RAW / f, force=args.force)
    cpi = fetch_cpi(args.force)
    cpi_by_year = {c["year"]: c["cpi_u"] for c in cpi}
    if DOLLAR_YEAR not in cpi_by_year:
        raise SystemExit(f"ERROR: no CPI for {DOLLAR_YEAR}")
    health = read_bpt()
    print(f"  {len(health)} health factors (sector x pollutant x outcome); CPI {cpi[0]['year']}-{cpi[-1]['year']}")

    factors = [dict(zip(("key", "value", "unit", "dollar_year", "source", "url", "note"), f)) for f in FACTORS]
    for year, gases in SCGHG.items():
        for gas, values in zip(("co2", "ch4", "n2o"), gases):
            for rate, label, v in zip(("2.5%", "2.0%", "1.5%"), ("low", "central", "high"), values):
                factors.append({"key": f"scghg_{gas}_{year}_{label}", "value": v,
                                "unit": f"USD per metric ton {gas.upper()} emitted in {year} ({rate} discount rate)",
                                "dollar_year": 2020, "source": "EPA (2023) Report on the Social Cost of Greenhouse Gases, Table A.5.1",
                                "url": SCGHG_URL, "note": "2.0% is EPA's central estimate"})
    for f in factors:
        f["note"] = (f["note"] or "").replace("DOLLAR_YEAR", str(DOLLAR_YEAR))
    factors.append({"key": "dollar_year", "value": DOLLAR_YEAR, "unit": "year all impact dollars are stated in",
                    "dollar_year": None, "source": "BLS CPI-U (CUUR0000SA0) annual average", "url": BLS_URL, "note": ""})

    fac_cols = [("key", "TEXT"), ("value", "REAL"), ("unit", "TEXT"), ("dollar_year", "INTEGER"),
                ("source", "TEXT"), ("url", "TEXT"), ("note", "TEXT")]
    health_cols = [("sector", "TEXT"), ("pollutant", "TEXT"), ("outcome", "TEXT"), ("deaths_per_ton_low", "REAL"),
                   ("deaths_per_ton_high", "REAL"), ("geography", "TEXT"), ("unit", "TEXT")]
    cpi_cols = [("year", "INTEGER"), ("cpi_u", "REAL"), ("months", "INTEGER")]

    print("Writing:")
    write_csv(PROCESSED_DIR / "impact_factors.csv", [c for c, _ in fac_cols], factors)
    write_csv(PROCESSED_DIR / "impact_health_factors.csv", [c for c, _ in health_cols], health)
    write_csv(PROCESSED_DIR / "impact_cpi.csv", [c for c, _ in cpi_cols], cpi)
    con = connect()
    replace_table(con, "impact_factors", fac_cols, factors, indexes=["UNIQUE (key)"])
    replace_table(con, "health_factors", health_cols, health, indexes=["UNIQUE (sector, pollutant, outcome)"])
    replace_table(con, "cpi", cpi_cols, cpi, indexes=["UNIQUE (year)"])
    set_metadata(con, "impact_factors", {"bpt_source": BPT_SOURCE, "bpt_url": BPT_BASE, "dollar_year": DOLLAR_YEAR})
    con.commit()
    con.close()
    print("  tables 'impact_factors', 'health_factors' and 'cpi' in data/processed/indiana.sqlite")


if __name__ == "__main__":
    main()
