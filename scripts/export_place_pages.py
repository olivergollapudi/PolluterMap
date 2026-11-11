"""
A page for every Indiana county and every incorporated city and town, so people who search for
"pollution in Gary" or "Lake County Indiana air quality" land on a page about their own place.

  - site/county/<name>.html: the polluters in that county, their combined toll, and its rank
    among the 92 counties; site/county/index.html ranks all counties
  - site/town/<name>.html: the polluters within 10 miles of the town (the same circle the map
    draws), their combined toll, and nearby towns; site/town/index.html lists every town
  - data/processed/site_places.json: what each page's share image shows (make_share_images.py)

Called by export_site_data.py, which passes in the polluters and towns it has just built.
"""

import html
import json

from common import ROOT
from export_site_data import SITE_URL, amount, deaths_words, money_short, page_shell, per_day
from places import miles, slug

RADIUS = 10                                     # miles; matches the map's circle around a town
SHARE_DATA = ROOT / "data" / "processed" / "site_places.json"
e = html.escape


def county_url(name):
    return f"../county/{slug(name)}.html"


def town_url(name):
    return f"../town/{slug(name)}.html"


def count(n):
    return f"{int(n + 0.5):,}" if n >= 10 else f"{int(n * 10 + 0.5) / 10:.1f}".removesuffix(".0")


def toll(fs):
    """Deaths and their sources, summed over a group of polluters."""
    t = {k: sum(f[k] for f in fs) for k in ("deaths", "air_deaths", "toxic_deaths", "climate_deaths", "deaths_lo", "deaths_hi")}
    return t


def totals(fs):
    """The group's other harms as (big number, label, note), like a polluter page's tiles."""
    out = []
    damage = sum(f["damage"] for f in fs)
    if damage >= 1:
        out.append((money_short(damage), "in damage a year", "health harm and climate damage in dollars"))
    asthma = sum(f["ill"]["new_asthma_cases"] for f in fs)
    if asthma >= 1:
        out.append((count(asthma), "new asthma cases a year", ""))
    cancers = sum(f["ill"]["lung_cancers"] + f["toxic_cancers"] for f in fs)
    if cancers >= 0.1:
        out.append((count(cancers), "cancers a year", "from soot and toxic chemicals"))
    heart = sum(f["ill"]["heart_attacks"] + f["ill"]["strokes"] for f in fs)
    if heart >= 1:
        out.append((count(heart), "heart attacks and strokes a year", ""))
    lb = sum(f["toxic"]["lb"] for f in fs if f["toxic"])
    if lb >= 1:
        cancer_lb = sum(f["toxic"]["carcinogen_lb"] for f in fs if f["toxic"])
        out.append((amount(lb), "lbs of toxic chemicals released", f"{amount(cancer_lb)} lbs of them cause cancer" if cancer_lb >= 1 else ""))
    co2 = sum(f["co2e_t"] for f in fs)
    if co2 >= 1:
        out.append((amount(co2), "tons of greenhouse gases a year", f"as much as {amount(sum(f['cars'] for f in fs))} cars"))
    water = sum(f["water"]["exceedances"] for f in fs if f["water"])
    if water:
        out.append((f"{water:,}", "water permit violations last year", ""))
    bad = sum(1 for f in fs if f["qtrs_violation"])
    out.append((money_short(sum(f["fines"] for f in fs)), "in fines over 5 years",
                f"{bad} of these {len(fs)} polluters broke environmental rules in the last 3 years" if bad else ""))
    return out


def big(t, fs):
    """The share image's big number: deaths, or the biggest other harm when deaths round to nothing."""
    if t["deaths"] >= 0.1:
        return {"value": deaths_words(t["deaths"]), "label": "estimated deaths a year"}
    v, label, _ = totals(fs)[0]
    return {"value": v, "label": label, "quiet": True}


def headline_html(t, where_words):
    """The big death count with where it comes from."""
    parts = [x for x in ((t["air_deaths"], "air pollution"), (t["toxic_deaths"], "toxic chemicals"), (t["climate_deaths"], "climate change")) if x[0] >= 0.05]
    pd = per_day(t["deaths"])
    return "\n".join(x for x in (
        f'<div class="big">{deaths_words(t["deaths"])}</div><div class="label">estimated deaths a year</div>',
        f'<div class="per">Linked to the pollution of {e(where_words)}' + (f": {pd}." if pd else ".") + "</div>",
        f'<div class="from">From {e(", ".join(f"{deaths_words(v)} {k}" for v, k in parts))}.</div>' if parts else "",
    ) if x)


def tiles(items):
    return '<div class="harms">' + "".join(
        f'<div class="harm"><b>{e(v)}</b><span>{e(l)}</span>{f"<small>{e(n)}</small>" if n else ""}</div>' for v, l, n in items) + "</div>"


def polluter_list(fs, dist=None):
    rows = []
    for i, f in enumerate(fs, 1):
        bits = [f["kind"], f["city"]] + ([f"{dist[f['id']]:.1f} mi away"] if dist else [])
        rows.append(f'<li><span class="r">{i}</span><span class="n"><a href="../p/{e(f["slug"])}.html">{e(f["name"])}</a>'
                    f'<small>{e(" · ".join(x for x in bits if x))}</small></span><b>{deaths_words(f["deaths"])}</b></li>')
    return f'<ol class="list">{"".join(rows)}</ol>'


def share_row(url, say):
    from urllib.parse import quote
    q, qs = quote(url, safe=""), quote(say, safe="")
    return (f'<div class="share"><span>Share this</span>'
            f'<button type="button" data-share="{e(url)}" data-text="{e(say)}">Copy link</button>'
            f'<a href="https://www.facebook.com/sharer/sharer.php?u={q}" rel="noopener" target="_blank">Facebook</a>'
            f'<a href="https://x.com/intent/post?text={qs}&amp;url={q}" rel="noopener" target="_blank">X</a>'
            f'<a href="https://www.reddit.com/submit?url={q}&amp;title={qs}" rel="noopener" target="_blank">Reddit</a>'
            f'<a href="sms:?&amp;body={quote(say + " " + url, safe="")}">Text</a>'
            f'<a href="mailto:?subject={quote("Pollution near you", safe="")}&amp;body={quote(say + chr(10) + chr(10) + url, safe="")}">Email</a></div>')


NOTE = ('<p class="note">Estimates for one year of pollution (2023), in 2024 dollars: model estimates from EPA data and published '
        'methods, not counted deaths. Deaths from air pollution and toxic chemicals happen mostly near the polluter; deaths from '
        'climate change happen worldwide over the coming decades. Deaths could be {lo} to {hi}. '
        '<a href="../#/method">How we estimate</a></p>')


def write_place_pages(out, towns):
    """out: the polluters (with county set). towns: [name, lon, lat, population, county]."""
    shares, urls = [], []
    by_county = {}
    for f in out:
        by_county.setdefault(f["county"], []).append(f)
    counties = sorted(by_county, key=lambda c: -sum(f["deaths"] for f in by_county[c]))
    rank = {c: i + 1 for i, c in enumerate(counties)}
    towns_in = {}
    for t in towns:
        towns_in.setdefault(t[4], []).append(t)

    for d in ("county", "town"):
        (ROOT / "site" / d).mkdir(parents=True, exist_ok=True)
        for old in (ROOT / "site" / d).glob("*.html"):
            old.unlink()

    # --- a page per county
    for c in counties:
        fs = by_county[c]
        t = toll(fs)
        url = f"{SITE_URL}/county/{slug(c)}.html"
        big_town = max(towns_in.get(c, []), key=lambda x: x[3], default=None)
        n = len(fs)
        title = f"Pollution in {c} County, Indiana: {n} polluters linked to an estimated {deaths_words(t['deaths'])} deaths a year"
        desc = (f"{c} County ranks #{rank[c]} of Indiana's 92 counties for deaths linked to pollution. Its {n} power plants, factories "
                f"and other polluters are linked to an estimated {deaths_words(t['deaths'])} deaths a year. From public EPA data.")
        say = f"Polluters in {c} County, Indiana are linked to an estimated {deaths_words(t['deaths'])} deaths a year."
        town_links = " · ".join(f'<a href="{town_url(x[0])}">{e(x[0])}</a>' for x in sorted(towns_in.get(c, []), key=lambda x: -x[3]))
        body = "\n".join(x for x in (
            f'<div class="crumbs"><a href="./">Indiana counties</a> › {e(c)} County</div>',
            f'<span class="pill">#{rank[c]} of 92 counties</span>',
            f"<h1>Pollution in {e(c)} County</h1>",
            f'<div class="meta">{n} polluter{"s" if n != 1 else ""} with an estimate</div>',
            headline_html(t, f"{n} polluter{'s' if n != 1 else ''} in {c} County"),
            share_row(url, say),
            tiles(totals(fs)),
            f'<div class="btns"><a class="primary" href="../#/?near={e(big_town[0])}">See it on the map</a>' if big_town else '<div class="btns">',
            '<a href="https://www.in.gov/idem/contact/file-a-complaint" rel="noopener">Report a problem to the state ↗</a></div>',
            f"<h2>{e(c)} County's polluters, deadliest first</h2>",
            polluter_list(fs),
            f'<h2>Cities and towns in {e(c)} County</h2><p class="towns">{town_links}</p>' if town_links else "",
            NOTE.format(lo=deaths_words(t["deaths_lo"]), hi=deaths_words(t["deaths_hi"])),
        ) if x)
        (ROOT / "site" / "county" / f"{slug(c)}.html").write_text(
            page_shell(title, desc, url, body, f"county-{slug(c)}"), encoding="utf-8")
        urls.append(url)
        shares.append({"image": f"county-{slug(c)}", "title": f"Pollution in {c} County", "county": c,
                       "sub": f"#{rank[c]} of Indiana's 92 counties · {n} polluter{'s' if n != 1 else ''}", "lit": [f["id"] for f in fs], **big(t, fs)})

    # --- a page per city and town
    for name, lon, lat, pop, c in towns:
        dist = {f["id"]: miles(lat, lon, f["lat"], f["lon"]) for f in out}
        near = sorted((f for f in out if dist[f["id"]] <= RADIUS), key=lambda f: (-f["deaths"], dist[f["id"]]))
        url = f"{SITE_URL}/town/{slug(name)}.html"
        n = len(near)
        others = sorted(towns, key=lambda x: miles(lat, lon, x[2], x[1]))[1:7]
        crumbs = f'<div class="crumbs"><a href="../county/">Indiana</a> › <a href="{county_url(c)}">{e(c)} County</a> › {e(name)}</div>'
        meta = f'<div class="meta">{e(c)} County · population {pop:,}</div>'
        nearby = f'<h2>Nearby towns</h2><p class="towns">{" · ".join(f"<a href={chr(34)}{town_url(x[0])}{chr(34)}>{e(x[0])}</a>" for x in others)}</p>'
        if near:
            t = toll(near)
            title = f"Pollution near {name}, Indiana: {n} polluters within {RADIUS} miles, linked to an estimated {deaths_words(t['deaths'])} deaths a year"
            desc = (f"The power plants, factories and other polluters within {RADIUS} miles of {name}, Indiana, ranked by the deaths "
                    f"linked to their pollution: an estimated {deaths_words(t['deaths'])} deaths a year in all. From public EPA data.")
            say = f"Polluters within {RADIUS} miles of {name}, Indiana are linked to an estimated {deaths_words(t['deaths'])} deaths a year."
            main = "\n".join((
                headline_html(t, f"{n} polluter{'s' if n != 1 else ''} within {RADIUS} miles of {name}"),
                share_row(url, say), tiles(totals(near)),
                f'<div class="btns"><a class="primary" href="../#/?near={e(name)}">See them on the map</a>'
                '<a href="https://www.in.gov/idem/contact/file-a-complaint" rel="noopener">Report a problem to the state ↗</a></div>',
                f"<h2>Polluters within {RADIUS} miles, deadliest first</h2>", polluter_list(near, dist),
            ))
            shares.append({"image": f"town-{slug(name)}", "title": f"Polluters near {name}", "sub": f"{n} within {RADIUS} miles · {c} County",
                           "lit": [f["id"] for f in near], "center": [lon, lat], **big(t, near)})
            lo, hi = t["deaths_lo"], t["deaths_hi"]
        else:
            closest = sorted(out, key=lambda f: dist[f["id"]])[:5]
            title = f"Pollution near {name}, Indiana: the closest polluters"
            desc = f"No polluter with an estimate is within {RADIUS} miles of {name}, Indiana. Here are the closest ones and the harm linked to them. From public EPA data."
            say = f"The polluters closest to {name}, Indiana, and the harm linked to them."
            main = "\n".join((
                f'<div class="per">No polluter we have an estimate for is within {RADIUS} miles of {e(name)}. '
                f'The closest is {closest[0]["name"]}, {dist[closest[0]["id"]]:.0f} miles away.</div>',
                share_row(url, say),
                f'<div class="btns"><a class="primary" href="../#/?near={e(name)}">See it on the map</a></div>',
                "<h2>The closest polluters</h2>", polluter_list(closest, dist),
            ))
            shares.append({"image": f"town-{slug(name)}", "title": f"Pollution near {name}", "sub": f"{c} County",
                           "value": f"{dist[closest[0]['id']]:.0f} mi", "label": "to the closest polluter", "lit": [closest[0]["id"]], "center": [lon, lat], "quiet": True})
            lo = hi = 0
        body = "\n".join(x for x in (crumbs, f"<h1>Pollution near {e(name)}</h1>", meta, main, nearby,
                                     NOTE.format(lo=deaths_words(lo), hi=deaths_words(hi)) if near else NOTE.split(" Deaths could")[0] + ' <a href="../#/method">How we estimate</a></p>') if x)
        (ROOT / "site" / "town" / f"{slug(name)}.html").write_text(page_shell(title, desc, url, body, f"town-{slug(name)}"), encoding="utf-8")
        urls.append(url)

    # --- the lists: counties ranked, then every town
    rows = "".join(
        f'<li><span class="r">{rank[c]}</span><span class="n"><a href="{slug(c)}.html">{e(c)} County</a>'
        f'<small>{len(by_county[c])} polluters</small></span><b>{deaths_words(sum(f["deaths"] for f in by_county[c]))}</b></li>' for c in counties)
    body = ("<h1>Pollution in Indiana's counties</h1>"
            '<div class="meta">The 92 counties ranked by the estimated deaths a year linked to the polluters in them. '
            'Pick a county, or <a href="../town/">find your city or town</a>.</div>'
            f'<ol class="list" style="margin-top:20px">{rows}</ol>')
    (ROOT / "site" / "county" / "index.html").write_text(page_shell(
        "Indiana pollution by county: all 92 counties ranked by deaths | Indiana's Deadliest Polluters",
        "Every Indiana county ranked by the deaths linked to the power plants, factories and other polluters in it. From public EPA data.",
        f"{SITE_URL}/county/", body, "home"), encoding="utf-8")
    letters = {}
    for t in sorted(towns):
        letters.setdefault(t[0][0].upper(), []).append(t)
    body = ("<h1>Pollution near Indiana's cities and towns</h1>"
            f'<div class="meta">Pick a place to see the polluters within {RADIUS} miles of it. Or see <a href="../county/">all 92 counties</a>.</div>'
            + "".join(f'<h2>{k}</h2><p class="towns">' + " · ".join(f'<a href="{slug(t[0])}.html">{e(t[0])}</a>' for t in v) + "</p>"
                      for k, v in letters.items()))
    (ROOT / "site" / "town" / "index.html").write_text(page_shell(
        "Indiana pollution near you: every city and town | Indiana's Deadliest Polluters",
        f"Find your Indiana city or town to see the polluters within {RADIUS} miles and the deaths linked to them. From public EPA data.",
        f"{SITE_URL}/town/", body, "home"), encoding="utf-8")
    SHARE_DATA.write_text(json.dumps(shares, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(counties)} county pages and {len(towns)} town pages, with lists, to site/county/ and site/town/")
    return [f"{SITE_URL}/county/", f"{SITE_URL}/town/"] + urls
