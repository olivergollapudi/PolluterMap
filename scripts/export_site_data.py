"""
Export the data for the website (site/index.html, "Indiana's Deadliest Polluters").

Writes:
  - site/site-data.js: one record per polluter (see below)
  - site/zips.js: the center of every Indiana ZIP code (Census ZCTA gazetteer), so
    "polluters near 46204" works without any lookup service
  - site/towns.js: every incorporated Indiana city and town with its location and 2024
    population (Census), so a death count can be compared with a town in view
  - site/p/<name>-<id>.html: a page per polluter that search engines can read and link previews
    use, with a button to open it on the interactive map; site/p/index.html lists them all
  - site/sitemap.xml and site/robots.txt, pointing search engines at those pages

One record per polluter with an impact estimate: a readable name and owner, what kind of
place it is, how it pollutes (air, toxic chemicals, water, climate) and what that costs
(deaths, illness, dollars, offset cost, fines). Written as a small JavaScript file so the
site loads it like any other script.

    python3 scripts/export_site_data.py
"""

import csv
import html
import io
import json
import math
import re
import sqlite3
import zipfile
from urllib.parse import quote

from common import DB_PATH, RAW_DIR, ROOT, download

OUT = ROOT / "site" / "site-data.js"
ZIPS_OUT = ROOT / "site" / "zips.js"
PAGES = ROOT / "site" / "p"
SITE_URL = "https://indianapollution.org"   # the site's own address, for search engines and link previews
ZCTA_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_zcta_national.zip"
ZCTA_FILE = RAW_DIR / "census" / "2023_Gaz_zcta_national.zip"
TOWNS_OUT = ROOT / "site" / "towns.js"
# Census population estimates for cities and towns (July 1, 2024), and where each place is
POP_URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/cities/totals/sub-est2024_18.csv"
POP_FILE = RAW_DIR / "census" / "sub-est2024_18.csv"
PLACES_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_gaz_place_18.txt"
PLACES_FILE = RAW_DIR / "census" / "2024_gaz_place_18.txt"

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


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60]


def write_zips():
    """Indiana ZIP code centers. Indiana ZIPs run 46000-47999."""
    download(ZCTA_URL, ZCTA_FILE)
    with zipfile.ZipFile(ZCTA_FILE) as z:
        lines = z.read(z.namelist()[0]).decode("utf-8").splitlines()
    head = [h.strip() for h in lines[0].split("\t")]
    if head[:1] != ["GEOID"] or "INTPTLAT" not in head or "INTPTLONG" not in head:
        raise SystemExit(f"ERROR: the Census ZIP file's columns changed: {head}")
    zips = {}
    for line in lines[1:]:
        v = dict(zip(head, (x.strip() for x in line.split("\t"))))
        if "46000" <= v["GEOID"] <= "47999":
            zips[v["GEOID"]] = [round(float(v["INTPTLONG"]), 4), round(float(v["INTPTLAT"]), 4)]
    ZIPS_OUT.write_text("window.IN_ZIPS = " + json.dumps(zips, separators=(",", ":")) + ";\n", encoding="utf-8")
    print(f"Wrote {len(zips)} Indiana ZIP codes to {ZIPS_OUT.relative_to(ROOT)}")


def write_towns():
    """Indiana's incorporated cities and towns (Census summary level 162): name, location, population."""
    download(POP_URL, POP_FILE)
    download(PLACES_URL, PLACES_FILE)
    pops = list(csv.DictReader(io.StringIO(POP_FILE.read_text(encoding="latin-1"))))
    if not pops or not {"SUMLEV", "STATE", "PLACE", "NAME", "POPESTIMATE2024"} <= set(pops[0]):
        raise SystemExit("ERROR: the Census population file's columns changed")
    lines = PLACES_FILE.read_text(encoding="utf-8").splitlines()
    head = [h.strip() for h in lines[0].split("\t")]
    if not {"GEOID", "INTPTLAT", "INTPTLONG"} <= set(head):
        raise SystemExit(f"ERROR: the Census places file's columns changed: {head}")
    where = {}
    for line in lines[1:]:
        v = dict(zip(head, (x.strip() for x in line.split("\t"))))
        where[v["GEOID"]] = (round(float(v["INTPTLONG"]), 4), round(float(v["INTPTLAT"]), 4))
    towns = []
    for r in pops:
        geoid = r["STATE"] + r["PLACE"]
        if r["SUMLEV"] != "162" or geoid not in where or not int(r["POPESTIMATE2024"] or 0):
            continue
        name = re.sub(r" (city|town)( \(balance\))?$", "", r["NAME"])
        towns.append([name, *where[geoid], int(r["POPESTIMATE2024"])])
    TOWNS_OUT.write_text("window.IN_TOWNS = " + json.dumps(towns, separators=(",", ":")) + ";\n", encoding="utf-8")
    print(f"Wrote {len(towns)} Indiana cities and towns to {TOWNS_OUT.relative_to(ROOT)}")


def deaths_words(n):
    """Same rounding as the site: 2,600 / 54 / 1.1 / under 0.1 / 0."""
    if not n:
        return "0"
    if n >= 100:
        return f"{float(f'{n:.2g}'):,.0f}"
    return f"{n:,.0f}" if n >= 10 else f"{n:.1f}" if n >= 0.1 else "under 0.1"


def money_words(n):
    return f"${n / 1e9:,.1f} billion" if n >= 1e9 else f"${n / 1e6:,.0f} million" if n >= 1e6 else f"${n:,.0f}"


def money_short(n):
    if n >= 1e9:
        return f"${n / 1e9:.{0 if n >= 1e10 else 1}f}B"
    if n >= 1e6:
        return f"${n / 1e6:.{0 if n >= 1e7 else 1}f}M"
    return f"${round(n / 1e3):,}K" if n >= 1e3 else f"${n:,.0f}"


def amount(n):
    """Pounds or tons: 19.2 million / 110,000 / 47."""
    return f"{n / 1e6:.1f} million" if n >= 1e6 else f"{float(f'{n:.2g}'):,.0f}" if n >= 100 else f"{n:,.0f}"


def per_day(n):
    if n / 365 >= 1.5:
        return f"about {round(n / 365)} deaths for every day of its pollution"
    if n / 365 >= 0.75:
        return "about one death for every day of its pollution"
    return f"one death for every {round(365 / n)} days of its pollution" if n >= 1 else None


def harms(f):
    """The polluter's harms as (big number, label, note), biggest first, from every dataset."""
    ill, t, w, out = f["ill"], f["toxic"], f["water"], []
    # round halves up, like the site does (Python's own rounding sends 398.5 to 398)
    count = lambda n: f"{int(n + 0.5):,}" if n >= 10 else f"{int(n * 10 + 0.5) / 10:.1f}".removesuffix(".0")
    if f["damage"] >= 1:
        out.append((money_short(f["damage"]), "in damage a year",
                    f"plus {money_short(f['offset_lo'])}–{money_short(f['offset_hi'])} to pull its CO2 back out of the air" if f["offset_hi"] else "health harm in dollars"))
    if ill["new_asthma_cases"] >= 1:
        out.append((count(ill["new_asthma_cases"]), "new asthma cases a year", ""))
    cancers = ill["lung_cancers"] + f["toxic_cancers"]
    if cancers >= 0.1:
        out.append((count(cancers), "cancers a year", "lung cancers from soot" + (", and cancers from toxic chemicals" if f["toxic_cancers"] >= 0.1 else "")))
    if ill["heart_attacks"] + ill["strokes"] >= 1:
        out.append((count(ill["heart_attacks"] + ill["strokes"]), "heart attacks and strokes a year", ""))
    if ill["er_visits"] + ill["hospital_admissions"] >= 1:
        out.append((count(ill["er_visits"] + ill["hospital_admissions"]), "ER visits and hospital stays a year", ""))
    if f["toxic_illnesses"] >= 1:
        out.append((count(f["toxic_illnesses"]), "other illnesses from toxic chemicals a year", f"mostly from {f['toxic_chem']}" if f["toxic_chem"] else ""))
    if t and t["lb"] >= 1:
        out.append((amount(t["lb"]), "lbs of toxic chemicals released", f"{amount(t['carcinogen_lb'])} lbs of them cause cancer" if t["carcinogen_lb"] >= 1 else ""))
    if f["co2e_t"] >= 1:
        out.append((amount(f["co2e_t"]), "tons of greenhouse gases a year", f"as much as {amount(f['cars'])} cars"))
    if w and w["exceedances"]:
        out.append((f"{w['exceedances']:,}", "water permit violations last year",
                    f"discharges into {w['impaired'].split(', ')[0]}, already listed as polluted" if w["impaired"] else ""))
    out.append((money_short(f["fines"]), "in fines over 5 years",
                (f"out of compliance {f['qtrs_violation']} of the last 12 quarters" if f["qtrs_violation"] else "")))
    return out


PAGE_CSS = """*{box-sizing:border-box}html,body{margin:0;background:#0d0e11;color:#f3efe8;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:#f3efe8;text-decoration-color:#9b968e;text-underline-offset:3px}
header{display:flex;align-items:center;gap:20px;padding:12px 24px;border-bottom:1px solid #2a2e37}
header .logo{font-weight:900;font-size:18px;text-decoration:none}header .logo span{color:#ff4b3a}header a.nav{color:#9b968e;text-decoration:none;font-size:15px}
main{max-width:900px;margin:0 auto;padding:28px 24px 64px}
.pill{display:inline-block;font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#fff;background:#c4281b;padding:4px 10px;border-radius:999px}
h1{font-size:clamp(30px,4.5vw,44px);line-height:1.08;margin:12px 0 6px;letter-spacing:-.02em}.meta{color:#9b968e;font-size:16px}
.big{font-size:clamp(64px,11vw,108px);font-weight:900;line-height:.95;margin-top:26px;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.label{font-size:15px;font-weight:800;color:#ff4b3a;text-transform:uppercase;letter-spacing:.08em;margin-top:8px}
.per{font-size:18px;margin-top:12px}.from{color:#9b968e;margin-top:6px}
.harms{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:0 16px;margin:26px 0}
.harm{border-top:1px solid #2a2e37;padding:14px 0}.harm b{display:block;font-size:30px;font-weight:900;font-variant-numeric:tabular-nums}
.harm span{display:block;font-weight:700;font-size:14px;margin-top:4px}.harm small{display:block;color:#9b968e;font-size:12px;margin-top:3px;line-height:1.4}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 24px}.btns a{border:1px solid #2a2e37;border-radius:999px;padding:8px 14px;text-decoration:none;font-size:14px}
.btns a.primary{background:#ff4b3a;border-color:#ff4b3a;color:#fff;font-weight:700}
.share{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:20px 0 4px}.share span{color:#9b968e;font-size:14px;margin-right:4px}
.share a,.share button{font:inherit;font-size:14px;color:#f3efe8;background:#1c1f26;border:1px solid #2a2e37;border-radius:999px;padding:7px 14px;text-decoration:none;cursor:pointer}
.share a:hover,.share button:hover{border-color:#9b968e}.share button{background:#ff4b3a;border-color:#ff4b3a;color:#fff;font-weight:700}
h2{font-size:18px;margin:26px 0 10px}.note{color:#9b968e;font-size:13px;line-height:1.55}
ol.list{padding:0;margin:0;list-style:none}ol.list li{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid #2a2e37}
ol.list .r{color:#9b968e;width:3em;flex:none}ol.list .n{flex:1}ol.list small{display:block;color:#9b968e}ol.list b{font-variant-numeric:tabular-nums}
@media(max-width:640px){header{padding:12px 16px;gap:12px}header .logo{font-size:15px}header a.nav{font-size:13px}main{padding:20px 16px 48px}}
"""


def page_shell(title, desc, url, body, image):
    """A page around body. image is its link-preview picture in p/img/, made by make_share_images.py."""
    e = html.escape
    img = f"{SITE_URL}/p/img/{image}.png"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(url)}">
<meta property="og:type" content="article"><meta property="og:site_name" content="Indiana's Deadliest Polluters">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}"><meta property="og:url" content="{e(url)}">
<meta property="og:image" content="{e(img)}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{e(desc)}">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{e(img)}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ccircle cx='8' cy='8' r='6' fill='%23ff4b3a'/%3E%3C/svg%3E">
<link rel="stylesheet" href="style.css">
</head><body>
<header><a class="logo" href="../">Indiana's <span>Deadliest</span> Polluters</a><a class="nav" href="../">Map</a><a class="nav" href="./">All polluters</a><a class="nav" href="../#/method">How we estimate</a></header>
<main>
{body}
</main>
<script>
// Share: the phone's own share sheet where there is one, else copy the link
const sheet = navigator.share && matchMedia("(pointer: coarse)").matches;
document.querySelectorAll("[data-share]").forEach(b => {{ if (sheet) b.textContent = "Share"; }});
document.querySelectorAll("[data-share]").forEach(b => b.addEventListener("click", async () => {{
  const data = {{ title: document.title, text: b.dataset.text, url: b.dataset.share }};
  if (sheet) {{ try {{ await navigator.share(data); }} catch (_) {{}} return; }}
  let ok = false;
  try {{ await navigator.clipboard.writeText(b.dataset.share); ok = true; }}
  catch (_) {{   // older browsers: copy from a hidden text box
    const t = Object.assign(document.createElement("textarea"), {{ value: b.dataset.share }});
    document.body.append(t); t.select(); try {{ ok = document.execCommand("copy"); }} catch (_) {{}} t.remove();
  }}
  b.textContent = ok ? "Link copied" : b.dataset.share;
}}));
</script>
</body></html>
"""


def write_pages(out):
    """A real page per polluter (search engines can read it, link previews use its title), an index
    of all of them, plus sitemap.xml and robots.txt. Old pages are removed so renamed polluters don't linger."""
    e = html.escape
    PAGES.mkdir(parents=True, exist_ok=True)
    for old in PAGES.glob("*.html"):
        old.unlink()
    (PAGES / "style.css").write_text(PAGE_CSS, encoding="utf-8")
    for f in out:
        url = f"{SITE_URL}/p/{f['slug']}.html"
        place = ", ".join(x for x in (f["city"], "Indiana") if x)
        if f["deaths"] >= 0.1:
            title = f"{f['name']} ({place}): an estimated {deaths_words(f['deaths'])} deaths a year from its pollution"
            desc = (f"#{f['rank']} of Indiana's deadliest polluters. {f['kind']} in {place}, linked to an estimated "
                    f"{deaths_words(f['deaths'])} deaths a year from air pollution, toxic chemicals and climate change, and "
                    f"{money_words(f['damage'])} in damage. From public EPA data.")
        else:
            title = f"{f['name']} ({place}): pollution, toxic releases and record"
            desc = f"{f['kind']} in {place}: its toxic releases, the illness linked to them, and its compliance record. From public EPA data."
        parts = [x for x in ((f["air_deaths"], "air pollution"), (f["toxic_deaths"], "toxic chemicals"), (f["climate_deaths"], "climate change")) if x[0]]
        pd = per_day(f["deaths"])
        if f["deaths"] >= 0.1:
            say = f"{f['name']} in {place} is linked to an estimated {deaths_words(f['deaths'])} deaths a year from its pollution."
        else:
            v, label, _ = harms(f)[0]
            say = f"{f['name']} in {place}: {v} {label}."
        q, qs = quote(url, safe=""), quote(say, safe="")
        share = (f'<div class="share"><span>Share this</span>'
                 f'<button type="button" data-share="{e(url)}" data-text="{e(say)}">Copy link</button>'
                 f'<a href="https://www.facebook.com/sharer/sharer.php?u={q}" rel="noopener" target="_blank">Facebook</a>'
                 f'<a href="https://x.com/intent/post?text={qs}&amp;url={q}" rel="noopener" target="_blank">X</a>'
                 f'<a href="https://www.reddit.com/submit?url={q}&amp;title={qs}" rel="noopener" target="_blank">Reddit</a>'
                 f'<a href="sms:?&amp;body={quote(say + " " + url, safe="")}">Text</a>'
                 f'<a href="mailto:?subject={quote(f["name"] + " and its pollution", safe="")}&amp;body={quote(say + chr(10) + chr(10) + url, safe="")}">Email</a></div>')
        body = "\n".join(x for x in (
            f'<span class="pill">#{f["rank"]} deadliest in Indiana</span>' if f["deaths"] >= 0.1 else "",
            f"<h1>{e(f['name'])}</h1>",
            f'<div class="meta">{e(" · ".join(x for x in (f["kind"], place, f["county"] and f["county"] + " County", f["owner"] and "owned by " + f["owner"]) if x))}</div>',
            f'<div class="big">{deaths_words(f["deaths"])}</div><div class="label">estimated deaths a year</div>',
            f'<div class="per">That\'s {pd}.</div>' if pd else "",
            f'<div class="from">From {e(", ".join(f"{deaths_words(v)} {k}" for v, k in parts))}.</div>' if parts else "",
            share,
            '<div class="harms">' + "".join(f'<div class="harm"><b>{e(v)}</b><span>{e(l)}</span>{f"<small>{e(n)}</small>" if n else ""}</div>' for v, l, n in harms(f)) + "</div>",
            f'<div class="btns"><a class="primary" href="../#/f/{e(f["key"])}">Open on the interactive map</a>'
            f'<a href="https://www.in.gov/idem/contact/file-a-complaint" rel="noopener">Report a problem to the state ↗</a>'
            f'<a href="https://www.in.gov/idem/public-notices" rel="noopener">Comment on its permits ↗</a>'
            + (f'<a href="{e(f["report"])}" rel="noopener">EPA\'s full report ↗</a>' if f["report"] else "") + "</div>",
            f'<p class="note">Estimates for one year of pollution (2023), in 2024 dollars: model estimates from EPA data and published '
            f'methods, not counted deaths. Deaths could be {deaths_words(f["deaths_lo"])} to {deaths_words(f["deaths_hi"])}. '
            f'{e(f["outlier"] or "")} <a href="../#/method">How we estimate</a></p>',
        ) if x)
        (PAGES / f"{f['slug']}.html").write_text(page_shell(title, desc, url, body, f["slug"]), encoding="utf-8")

    # All polluters, ranked: one page that links to every polluter page
    rows = "\n".join(
        f'<li><span class="r">{f["rank"]}</span><span class="n"><a href="{e(f["slug"])}.html">{e(f["name"])}</a>'
        f'<small>{e(" · ".join(x for x in (f["kind"], f["city"]) if x))}</small></span><b>{deaths_words(f["deaths"])}</b></li>' for f in out)
    total = sum(f["deaths"] for f in out)
    body = (f"<h1>Indiana's polluters, ranked by deaths</h1>"
            f'<div class="meta">All {len(out):,} Indiana polluters with an estimate, linked to about {deaths_words(total)} deaths a year '
            f"from air pollution, toxic chemicals and climate change. Estimated deaths a year, from one year of pollution (2023).</div>"
            f'<div class="btns" style="margin-top:16px"><a class="primary" href="../">Open the map</a></div><ol class="list">{rows}</ol>')
    (PAGES / "index.html").write_text(page_shell(
        "Indiana pollution: all polluters ranked by deaths | Indiana's Deadliest Polluters",
        f"Every Indiana polluter with an estimate, ranked by the deaths linked to its pollution: {len(out):,} power plants, "
        "steel mills, refineries and factories. From public EPA data.", f"{SITE_URL}/p/", body, "home"), encoding="utf-8")

    # Tell search engines what to read
    urls = [f"{SITE_URL}/", f"{SITE_URL}/p/"] + [f"{SITE_URL}/p/{f['slug']}.html" for f in out]
    (ROOT / "site" / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{e(u)}</loc></url>\n" for u in urls) + "</urlset>\n", encoding="utf-8")
    (ROOT / "site" / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")
    print(f"Wrote {len(out)} polluter pages and an index to {PAGES.relative_to(ROOT)}/, plus sitemap.xml and robots.txt")


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    fac = con.execute("""
        SELECT fi.*, g.facility_name AS ghg_name, n.facility_name AS nei_name, n.naics_description, n.lead_lb, n.total_hap_lb,
               e.industry AS echo_industry, e.compliance_status, e.qtrs_in_noncompliance_3yr, e.epa_report_url,
               e.street, e.county
        FROM facility_impacts fi
        LEFT JOIN ghg_facilities g ON g.ghg_facility_id = fi.ghg_facility_id
        LEFT JOIN nei_facilities n ON n.eis_facility_id = fi.nei_eis_id
        LEFT JOIN facilities e ON e.registry_id = fi.registry_id
        WHERE (fi.total_usd_central > 0 OR fi.toxic_illnesses > 0) AND fi.lat IS NOT NULL
        ORDER BY fi.total_deaths_central DESC""").fetchall()
    tri_rows = con.execute("""
        SELECT trifd, registry_id, facility_name, city, parent_company, total_releases_lb, air_lb, water_lb, land_lb,
               carcinogen_releases_lb, top_chemical, n_chemicals, lat, lon
        FROM tri_facility_years WHERE year = (SELECT MAX(year) FROM tri_facility_years)""").fetchall()
    tri = {r["registry_id"]: r for r in tri_rows if r["registry_id"]}
    # Yearly series for the trend lines
    ghg_years, tri_years = {}, {}
    for r in con.execute("SELECT ghg_facility_id, year, total_mt_co2e FROM ghg_emissions_by_year ORDER BY year"):
        if r["total_mt_co2e"] is not None:
            ghg_years.setdefault(r["ghg_facility_id"], []).append([r["year"], round(r["total_mt_co2e"])])
    for r in con.execute("SELECT trifd, year, total_releases_lb FROM tri_facility_years ORDER BY year"):
        tri_years.setdefault(r["trifd"], []).append([r["year"], round(r["total_releases_lb"] or 0)])
    # Toxic chemical risk (RSEI weighs each pound by toxicity and how many people live nearby):
    # each TRI facility's rank in Indiana, latest year
    rsei = con.execute("""SELECT trifd, rsei_score FROM rsei_facility_years
        WHERE year = (SELECT MAX(year) FROM rsei_facility_years) AND rsei_score > 0 ORDER BY rsei_score DESC""").fetchall()
    rsei_rank = {x["trifd"]: i + 1 for i, x in enumerate(rsei)}
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

    def chem_name(key):
        """TRACI name -> plain words: 'MERCURY(II)' -> 'mercury', 'CHROMIUM(VI)' -> 'hexavalent chromium'."""
        if not key:
            return None
        return "hexavalent chromium" if key == "CHROMIUM(VI)" else key.split("(")[0].strip().lower()

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
            "toxic_deaths": round(r["toxic_deaths_central"], 3), "toxic_cancers": round(r["toxic_cancers"], 2),
            "toxic_illnesses": round(r["toxic_illnesses"], 1), "toxic_chem": chem_name(r["toxic_top_chemical"]),
            "damage": round(r["total_usd_central"]), "damage_lo": round(r["total_usd_low"]), "damage_hi": round(r["total_usd_high"]),
            "health_usd": round(r["health_usd_central"] + r["toxic_usd_central"]), "climate_usd": round(r["climate_usd_central"]),
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
            "toxic_rank": rsei_rank.get(t["trifd"]) if t else None, "toxic_ranked": len(rsei),
            "lead_lb": round(r["lead_lb"] or 0), "hap_lb": round(r["total_hap_lb"] or 0),
            "water": None if not w else {"exceedances": w[0], "lb": round(w[1]), "impaired": title(impaired_water)},
            "co2e_t": round(r["co2e_t"] or 0), "cars": round(r["eq_cars"] or 0), "homes": round(r["eq_homes"] or 0),
            # accountability
            "status": r["compliance_status"], "qtrs_violation": r["qtrs_in_noncompliance_3yr"],
            "report": (r["epa_report_url"] or "").replace("http://", "https://", 1),
            "outlier": r["outlier_note"],
            "trend_ghg": ghg_years.get(r["ghg_facility_id"]), "trend_tri": tri_years.get(t["trifd"]) if t else None,
            # a stable ID for links, so shared links keep working when the ranking changes
            "key": r["registry_id"] or (f"nei{r['nei_eis_id']}" if r["nei_eis_id"] else None)
                   or (f"ghg{r['ghg_facility_id']}" if r["ghg_facility_id"] else None),
        })
    out.sort(key=lambda x: (-x["deaths"], -x["toxic_illnesses"]))
    seen = set()
    for i, f in enumerate(out):
        f["rank"] = i + 1
        f["id"] = i
        f["key"] = f["key"] or slugify(f"{f['name']} {f['city']}")
        while f["key"] in seen:
            f["key"] += "x"
        seen.add(f["key"])
        f["slug"] = f"{slugify(f['name'])}-{f['key']}"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.SITE_DATA = " + json.dumps({"facilities": out, "year": 2023, "dollar_year": 2024},
                                                        separators=(",", ":")) + ";\n", encoding="utf-8")
    print(f"Wrote {len(out)} polluters to {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e3:.0f} KB)")
    write_zips()
    write_towns()
    write_pages(out)
    for f in out[:8]:
        print(f"  #{f['rank']} {f['name']} ({f['kind']}, {f['owner']}) - {f['deaths']:.0f} deaths")


if __name__ == "__main__":
    main()
