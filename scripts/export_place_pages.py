"""
A page for every Indiana county and every incorporated city and town, so people who search for
"pollution in Gary" or "Lake County Indiana air quality" land on a page about their own place.

  - site/county/<name>.html: the polluters in that county, their combined toll, and its rank
    among the 92 counties; site/county/index.html ranks all counties
  - site/town/<name>.html: the polluters within 10 miles of the town (the same circle the map
    draws), their combined toll, and nearby towns; site/town/index.html lists every town
  - data/processed/site_places.json: what each page's share image shows (make_share_images.py)

Pages use the same "Aerial" design as polluter pages (scripts/page_parts.py). Called by
export_site_data.py, which passes in the polluters and towns it has just built.
"""

import html
import json

import page_parts as pp
from common import ROOT
from export_site_data import SITE_URL, deaths_words, money_short, page_shell
from places import miles, slug

RADIUS = 10                                     # miles; matches the map's circle around a town
SHARE_DATA = ROOT / "data" / "processed" / "site_places.json"
e = html.escape


def town_url(name):
    return f"../town/{slug(name)}.html"


def big(t, fs):
    """The share image's big number: deaths, or the biggest other harm when deaths round to nothing."""
    if t["deaths"] >= 0.1:
        return {"value": deaths_words(t["deaths"]), "label": "estimated deaths a year"}
    if t["damage"] >= 1:
        return {"value": money_short(t["damage"]), "label": "in damage a year", "quiet": True}
    if t["lb"] >= 1:
        return {"value": pp.amount(t["lb"]), "label": "lbs of toxic chemicals a year", "quiet": True}
    if t["co2"] >= 1:
        return {"value": pp.amount(t["co2"]), "label": "tons of greenhouse gases a year", "quiet": True}
    return {"value": f"{t['n']:,}", "label": "polluters nearby" if t["n"] != 1 else "polluter nearby", "quiet": True}


def lead(t, who):
    """The lines under the big number."""
    pd = pp.per_day_line(t["deaths"])
    return [f"Linked to the pollution of {who}" + (f": {pd}." if pd else ".")]


def note(t):
    rng = f" Deaths could be {deaths_words(t['deaths_lo'])} to {deaths_words(t['deaths_hi'])}." if t["deaths"] >= 0.1 else ""
    return ('<section><p class="note">Estimates for one year of pollution (2023), in 2024 dollars: model estimates from EPA data and published '
            f'methods, not counted deaths.{rng} <a href="../#/method">How we estimate</a></p></section>')


def links_section(heading, towns):
    if not towns:
        return ""
    return f'<section><h2>{e(heading)}</h2><p class="towns">' + " · ".join(f'<a href="{town_url(x[0])}">{e(x[0])}</a>' for x in towns) + "</p></section>"


def write_place_pages(out, towns):
    """out: the polluters (with county set). towns: [name, lon, lat, population, county].
    Returns the pages' addresses (for the sitemap) and the town and county rankings (for ranks.js)."""
    shares, urls = [], []
    by_county = {}
    for f in out:
        by_county.setdefault(f["county"], []).append(f)
    for fs in by_county.values():
        fs.sort(key=lambda f: -f["deaths"])
    tolls = {c: pp.sum_toll(fs) for c, fs in by_county.items()}
    counties = sorted(by_county, key=lambda c: -tolls[c]["deaths"])
    towns_in = {}
    for t in towns:
        towns_in.setdefault(t[4], []).append(t)

    # the polluters near each town, and the towns ranked by them
    near_of, dist_of = {}, {}
    for name, lon, lat, pop, c in towns:
        dist = {f["id"]: miles(lat, lon, f["lat"], f["lon"]) for f in out}
        dist_of[name] = dist
        near_of[name] = sorted((f for f in out if dist[f["id"]] <= RADIUS), key=lambda f: (-f["deaths"], dist[f["id"]]))
    town_deaths = {t[0]: sum(f["deaths"] for f in near_of[t[0]]) for t in towns}
    town_order = sorted(town_deaths, key=lambda n: -town_deaths[n])
    town_rank = {n: i for i, n in enumerate(town_order)}

    for d in ("county", "town"):
        (ROOT / "site" / d).mkdir(parents=True, exist_ok=True)
        for old in (ROOT / "site" / d).glob("*.html"):
            old.unlink()

    # --- a page per county
    for i, c in enumerate(counties):
        fs, t = by_county[c], tolls[c]
        url = f"{SITE_URL}/county/{slug(c)}.html"
        big_town = max(towns_in.get(c, []), key=lambda x: x[3], default=None)
        n, s = len(fs), "s" if len(fs) != 1 else ""
        title = f"Pollution in {c} County, Indiana: {n} polluter{s} linked to an estimated {deaths_words(t['deaths'])} deaths a year"
        desc = (f"{c} County ranks #{i + 1} of Indiana's 92 counties for deaths linked to pollution. Its {n} power plants, factories "
                f"and other polluters are linked to an estimated {deaths_words(t['deaths'])} deaths a year. From public EPA data.")
        say = f"Polluters in {c} County, Indiana are linked to an estimated {deaths_words(t['deaths'])} deaths a year."
        b = big(t, fs)
        inner = "".join((
            f'<div class="crumbs"><a href="./">Indiana</a> › {e(c)} County</div>',
            f'<span class="pill">#{i + 1} of 92 counties</span>',
            f"<h1>Pollution in {e(c)} County</h1>",
            f'<div class="meta">{n} polluter{s} · {len(towns_in.get(c, []))} cities and towns</div>',
            pp.headline(b["value"], b["label"], not b.get("quiet"), lead(t, f"the {n} polluter{s} in {c} County") if t["deaths"] >= 0.1 else []),
            pp.share_row(url, say, f"Pollution in {c} County"),
        ))
        lat, lon = sum(f["lat"] for f in fs) / n, sum(f["lon"] for f in fs) / n
        body = "\n".join((
            pp.hero("county", round(lat, 4), round(lon, 4), inner), pp.dots_json(fs), '<div class="wrap">',
            pp.causes(t), pp.damage_squares(t), pp.sick_tiles(t),
            f'<section><h2>{e(c)} County\'s polluters, deadliest first</h2><p class="sub">Bars split by cause. Hover for detail.</p>{pp.bars([(f, None) for f in fs])}</section>',
            pp.facts(pp.record_rows(t)),
            pp.strip("counties", i, "counties", len(counties)),
            links_section(f"Cities and towns in {c} County", sorted(towns_in.get(c, []), key=lambda x: -x[3])),
            pp.actions(f"../#/?near={big_town[0]}" if big_town else "../"),
            note(t), "</div>",
        ))
        (ROOT / "site" / "county" / f"{slug(c)}.html").write_text(page_shell(title, desc, url, body, f"county-{slug(c)}", detail=True), encoding="utf-8")
        urls.append(url)
        shares.append({"image": f"county-{slug(c)}", "title": f"Pollution in {c} County", "county": c,
                       "sub": f"#{i + 1} of Indiana's 92 counties · {n} polluter{s}", "lit": [f["id"] for f in fs], **b})

    # --- a page per city and town
    for name, lon, lat, pop, c in towns:
        near, dist = near_of[name], dist_of[name]
        url = f"{SITE_URL}/town/{slug(name)}.html"
        n, s = len(near), "s" if len(near) != 1 else ""
        others = sorted(towns, key=lambda x: miles(lat, lon, x[2], x[1]))[1:9]
        crumbs = f'<div class="crumbs"><a href="../county/">Indiana</a> › <a href="../county/{slug(c)}.html">{e(c)} County</a> › {e(name)}</div>'
        if near:
            t = pp.sum_toll(near)
            b = big(t, near)
            title = f"Pollution near {name}, Indiana: {n} polluter{s} within {RADIUS} miles, linked to an estimated {deaths_words(t['deaths'])} deaths a year"
            desc = (f"The power plants, factories and other polluters within {RADIUS} miles of {name}, Indiana, ranked by the deaths "
                    f"linked to their pollution: an estimated {deaths_words(t['deaths'])} deaths a year in all. From public EPA data.")
            say = f"Polluters within {RADIUS} miles of {name}, Indiana are linked to an estimated {deaths_words(t['deaths'])} deaths a year."
            lines = lead(t, f"{n} polluter{s} within {RADIUS} miles") if t["deaths"] >= 0.1 else []
            shown, heading = [(f, dist[f["id"]]) for f in near], f"The polluters within {RADIUS} miles, deadliest first"
            shares.append({"image": f"town-{slug(name)}", "title": f"Polluters near {name}", "sub": f"{n} within {RADIUS} miles · {c} County",
                           "lit": [f["id"] for f in near], "center": [lon, lat], **b})
        else:
            closest = sorted(out, key=lambda f: dist[f["id"]])[:5]
            t = pp.sum_toll([])
            far = dist[closest[0]["id"]]
            b = {"value": f"{far:.0f} mi", "label": "to the closest polluter", "quiet": True}
            title = f"Pollution near {name}, Indiana: the closest polluters"
            desc = f"No polluter with an estimate is within {RADIUS} miles of {name}, Indiana. Here are the closest ones and the harm linked to them. From public EPA data."
            say = f"The polluters closest to {name}, Indiana, and the harm linked to them."
            lines = [f"No polluter we have an estimate for is within {RADIUS} miles of {e(name)}. The closest is {e(closest[0]['name'])}."]
            shown, heading = [(f, dist[f["id"]]) for f in closest], "The closest polluters"
            near = closest
            shares.append({"image": f"town-{slug(name)}", "title": f"Pollution near {name}", "sub": f"{c} County",
                           "lit": [closest[0]["id"]], "center": [lon, lat], **b})
        ranked = town_deaths[name] >= 0.1
        inner = "".join((
            crumbs,
            f'<span class="pill">#{town_rank[name] + 1} of {len(towns)} towns</span>' if ranked else "",
            f"<h1>Pollution near {e(name)}</h1>",
            f'<div class="meta">{e(c)} County · population {pop:,}</div>',
            pp.headline(b["value"], b["label"], not b.get("quiet"), lines),
            pp.share_row(url, say, f"Pollution near {name}"),
        ))
        body = "\n".join((
            pp.hero("town", lat, lon, inner), pp.dots_json(near), '<div class="wrap">',
            pp.causes(t), pp.damage_squares(t), pp.sick_tiles(t),
            f'<section><h2>{heading}</h2><p class="sub">Bars split by cause. Hover for detail.</p>{pp.bars(shown)}</section>',
            pp.facts(pp.record_rows(t)) if t["n"] else "",
            pp.strip("towns", town_rank[name], "cities and towns", len(towns)) if ranked else "",
            links_section("Nearby towns", others),
            pp.actions(f"../#/?near={name}"),
            note(t), "</div>",
        ))
        (ROOT / "site" / "town" / f"{slug(name)}.html").write_text(page_shell(title, desc, url, body, f"town-{slug(name)}", detail=True), encoding="utf-8")
        urls.append(url)

    # --- the lists: counties ranked, then every town
    rows = "".join(
        f'<li><span class="r">{i + 1}</span><span class="n"><a href="{slug(c)}.html">{e(c)} County</a>'
        f'<small>{len(by_county[c])} polluters</small></span><b>{deaths_words(tolls[c]["deaths"])}</b></li>' for i, c in enumerate(counties))
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
    ranks = {"counties": [[f"{c} County", round(tolls[c]["deaths"], 2)] for c in counties],
             "towns": [[n, round(town_deaths[n], 2)] for n in town_order]}
    return [f"{SITE_URL}/county/", f"{SITE_URL}/town/"] + urls, ranks
