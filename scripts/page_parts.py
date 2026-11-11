"""
The building blocks of polluter, town and county pages ("Aerial" design): a satellite picture
behind the headline, where the deaths come from, the damage next to the fines (squares drawn to
scale), the illness it causes, a ranking strip, and what you can do.

Everything here returns HTML. The pictures that need the reader's screen size (the satellite
picture, the ranking strip) are drawn by site/p/page.js from data attributes these blocks set.
"""

import html
import json
import math
from urllib.parse import quote

from export_site_data import amount, deaths_words, money_short

e = html.escape
CAUSES = (("air_deaths", "air pollution", "air"), ("toxic_deaths", "toxic chemicals", "toxic"), ("climate_deaths", "climate change (worldwide)", "climate"))


def count(n):
    """Illness counts: 827 / 7.4 / 0.4."""
    return f"{int(n + 0.5):,}" if n >= 10 else f"{int(n * 10 + 0.5) / 10:.1f}".removesuffix(".0")


def miles(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def town_compare(n, lat, lon, towns):
    """Compare a yearly death count with a real Indiana town nearby (same rule as the map):
    'about the population of Kouts', '3.2× the population of Valparaiso'."""
    if not n >= 20:
        return None
    for radius in (25, 50, 100):
        near = [t for t in towns if miles(lat, lon, t[2], t[1]) <= radius]
        if not near:
            continue
        t = min(near, key=lambda t: abs(math.log(t[3] / n)))
        r = n / t[3]
        if abs(math.log(r)) <= math.log(1.5) or radius == 100:
            if 0.8 <= r <= 1.25:
                return f"about the population of {t[0]}"
            if r > 1.25:
                k = f"{r:.1f}".removesuffix(".0") if r < 10 else f"{round(r)}"
                return f"{k}× the population of {t[0]}"
            return f"{max(1, round(100 * r))}% of the population of {t[0]}"
    return None


def per_day_line(n):
    if n / 365 >= 1.5:
        return f"about {round(n / 365)} deaths for every day of pollution"
    if n / 365 >= 0.75:
        return "about one death for every day of pollution"
    return f"one death every {round(365 / n)} days" if n >= 1 else None


def sum_toll(fs):
    """Deaths, damage and illness added up over one or more polluters."""
    s = lambda k: sum(f[k] or 0 for f in fs)
    ill = lambda *ks: sum(f["ill"][k] for f in fs for k in ks)
    return {
        "deaths": s("deaths"), "deaths_lo": s("deaths_lo"), "deaths_hi": s("deaths_hi"),
        "air_deaths": s("air_deaths"), "toxic_deaths": s("toxic_deaths"), "climate_deaths": s("climate_deaths"),
        "damage": s("damage"), "fines": s("fines"), "offset_lo": s("offset_lo"), "offset_hi": s("offset_hi"),
        "asthma": ill("new_asthma_cases"), "er": ill("er_visits", "hospital_admissions"), "heart": ill("heart_attacks", "strokes"),
        "cancers": ill("lung_cancers") + s("toxic_cancers"), "school": ill("lost_school_days"), "work": ill("lost_work_days"),
        "toxic_ill": s("toxic_illnesses"), "lb": sum(f["toxic"]["lb"] for f in fs if f["toxic"]),
        "cancer_lb": sum(f["toxic"]["carcinogen_lb"] for f in fs if f["toxic"]),
        "co2": s("co2e_t"), "cars": s("cars"), "water": sum(f["water"]["exceedances"] for f in fs if f["water"]),
        "bad": sum(1 for f in fs if f["qtrs_violation"]), "n": len(fs),
    }


# ------------------------------------------------------------------------------ the blocks

def hero(kind, lat, lon, inner):
    """The headline over a satellite picture; page.js fills in the picture."""
    return (f'<div class="hero" data-kind="{kind}" data-lat="{lat}" data-lon="{lon}"><div class="tiles"></div><div class="shade"></div>'
            f'<svg class="over" aria-hidden="true"></svg><div class="wrap"><div class="text">{inner}</div></div>'
            '<div class="credit">Imagery: USGS The National Map</div></div>')


def dots_json(fs):
    return ('<script type="application/json" id="dots">'
            + json.dumps([[f["lon"], f["lat"], round(f["deaths"], 2), f["name"]] for f in fs], separators=(",", ":")).replace("</", "<\\/")
            + "</script>")


def headline(value, label, is_deaths, lines):
    out = f'<div class="big num">{e(value)}</div><div class="label{"" if is_deaths else " plain"}">{e(label)}</div>'
    return out + "".join(f'<p class="lead">{x}</p>' for x in lines if x)


def share_row(url, say, subject):
    q, qs = quote(url, safe=""), quote(say, safe="")
    return (f'<div class="share"><button type="button" data-share="{e(url)}" data-text="{e(say)}">Copy link</button>'
            f'<a href="https://www.facebook.com/sharer/sharer.php?u={q}" rel="noopener" target="_blank">Facebook</a>'
            f'<a href="https://x.com/intent/post?text={qs}&amp;url={q}" rel="noopener" target="_blank">X</a>'
            f'<a href="https://www.reddit.com/submit?url={q}&amp;title={qs}" rel="noopener" target="_blank">Reddit</a>'
            f'<a href="sms:?&amp;body={quote(say + " " + url, safe="")}">Text</a>'
            f'<a href="mailto:?subject={quote(subject, safe="")}&amp;body={quote(say + chr(10) + chr(10) + url, safe="")}">Email</a></div>')


def causes(t):
    """Where the deaths come from: one bar split by cause, labeled."""
    parts = [(t[k], label, cls) for k, label, cls in CAUSES if t[k] >= 0.05]
    if not parts or t["deaths"] < 0.1:
        return ""
    bar = "".join(f'<div class="{cls}" style="flex:{v:.3f}" data-tip="{e(deaths_words(v))} deaths a year from {e(label)}"></div>' for v, label, cls in parts)
    key = "".join(f'<span><i class="{cls}"></i><b>{e(deaths_words(v))}</b> {e(label)}</span>' for v, label, cls in parts)
    return (f'<section><div class="eyebrow">Where the deaths come from</div><div class="stack">{bar}</div><div class="legend">{key}</div>'
            '<p class="note">Air pollution and toxic chemicals kill mostly nearby. Climate deaths happen worldwide, over the coming decades.</p></section>')


def damage_squares(t):
    """A year of damage next to five years of fines, as squares drawn to scale."""
    if t["damage"] < 1:
        return ""
    side = lambda v: max(4, math.sqrt(v / t["damage"]) * 280)
    fines = (f'<figure><div class="sq fines" style="width:{side(t["fines"]):.1f}px;height:{side(t["fines"]):.1f}px" data-tip="{money_short(t["fines"])} in fines over 5 years"></div>'
             f'<figcaption><b>{money_short(t["fines"])}</b>in fines, over 5 years</figcaption></figure>') if t["fines"] else ""
    pct = t["fines"] / t["damage"] * 100
    line = (f"Five years of fines come to {pct:.2f}% of one year's damage." if pct < 0.1 else f"Five years of fines come to {pct:.2g}% of one year's damage."
            ) if t["fines"] else "No fines in the last five years."
    offset = f" Pulling its carbon dioxide back out of the air would cost another {money_short(t['offset_lo'])}–{money_short(t['offset_hi'])}." if t["offset_hi"] >= 1e5 else ""
    return (f'<section><h2>The damage, and the fines</h2><p class="sub">Squares are to scale.</p><div class="squares">'
            f'<figure><div class="sq" style="width:280px;height:280px" data-tip="{money_short(t["damage"])} in damage a year"></div>'
            f'<figcaption><b>{money_short(t["damage"])}</b>in damage, every year</figcaption></figure>{fines}</div>'
            f'<p class="sub">{line} Damage is what the deaths, illness and climate harm cost, in 2024 dollars.{offset}</p></section>')


def sick_tiles(t):
    """Up to four tiles of illness, biggest kinds of harm first."""
    dots = lambda n: '<div class="dots">' + "<i></i>" * min(120, round(n)) + "</div>" if n >= 1 else ""
    options = [
        (t["asthma"] >= 1, count(t["asthma"]), "new asthma cases a year", "children and adults, from soot and smog", dots(t["asthma"])),
        (t["er"] >= 1, count(t["er"]), "ER visits and hospital stays a year", "mostly for breathing and heart problems", ""),
        (t["cancers"] >= 0.1, count(t["cancers"]), "cancers a year", "lung cancers from soot, and cancers from toxic chemicals", ""),
        (t["school"] >= 1, amount(t["school"]), "days of school missed a year",
         f"by children home sick from the pollution. Adults miss another {amount(t['work'])} days of work.", ""),
        (t["heart"] >= 1, count(t["heart"]), "heart attacks and strokes a year", "", ""),
        (t["toxic_ill"] >= 1, count(t["toxic_ill"]), "other illnesses from toxic chemicals a year", "such as nerve, kidney and breathing harm", ""),
    ]
    tiles = [x[1:] for x in options if x[0]][:4]
    if not tiles:
        return ""
    return (f'<section><div class="eyebrow">The people made sick, every year</div><div class="tiles4" style="--n:{len(tiles)}">'
            + "".join(f'<div class="tile"><b class="num">{e(v)}</b><span>{e(l)}</span>{f"<small>{e(n)}</small>" if n else ""}{viz}</div>' for v, l, n, viz in tiles)
            + "</div></section>")


def short(n):
    """7.6M / 410K / 950, for chart labels."""
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e4 else f"{n:,.0f}"


def spark(series, label, unit):
    """A small trend line, as SVG."""
    pts = [p for p in (series or []) if p[1] is not None]
    if len(pts) < 3 or not (pts[0][1] or pts[-1][1]):
        return ""
    W, H = 320, 90
    x0, x1, top = pts[0][0], pts[-1][0], max(p[1] for p in pts) * 1.08 or 1
    X = lambda x: 6 + (x - x0) / ((x1 - x0) or 1) * (W - 60)
    Y = lambda y: H - 18 - y / top * (H - 28)
    first, last = pts[0], pts[-1]
    change = (last[1] - first[1]) / first[1] * 100 if first[1] else None
    trend = ("About flat" if abs(change) < 5 else f"Up {round(change)}%" if change > 0 else f"Down {round(-change)}%") + f" since {first[0]}" if change is not None else f"Since {first[0]}"
    poly = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
    hits = "".join(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="7" fill="transparent" data-tip="{x}: {e(amount(y))} {unit}"/>' for x, y in pts)
    return (f'<div class="trend"><h3>{e(label)}</h3><p>{trend}</p><svg viewBox="0 0 {W} {H}" role="img" aria-label="{e(label)}: {trend}">'
            f'<line x1="6" x2="{W - 54}" y1="{H - 18}" y2="{H - 18}" stroke="var(--line)"/>'
            f'<polyline points="{poly}" fill="none" stroke="var(--red)" stroke-width="2" stroke-linejoin="round"/>'
            f'<circle cx="{X(last[0]):.1f}" cy="{Y(last[1]):.1f}" r="4" fill="var(--red)"/>'
            f'<text x="{X(last[0]) + 7:.1f}" y="{Y(last[1]) + 4:.1f}" font-size="11" fill="var(--ink)">{short(last[1])}</text>'
            f'<text x="6" y="{H - 3}" font-size="10" fill="var(--muted)">{first[0]}</text>'
            f'<text x="{W - 54}" y="{H - 3}" font-size="10" fill="var(--muted)" text-anchor="end">{last[0]}</text>{hits}</svg></div>')


def facts(rows, trends=""):
    """The rest of the record, as rows of big number + words, beside any trend lines."""
    rows = [r for r in rows if r]
    if not rows and not trends:
        return ""
    body = "".join(f'<div class="fact"><b class="num">{e(v)}</b><span>{e(l)}{f"<small>{e(n)}</small>" if n else ""}</span></div>' for v, l, n in rows)
    return (f'<section><h2>The rest of the record</h2><div class="record{" two" if trends else ""}"><div class="facts">{body}</div>'
            + (f'<div class="trends">{trends}</div>' if trends else "") + "</div></section>")


def record_rows(t, f=None):
    """Toxic releases, greenhouse gases, water and compliance, for one polluter (f) or a group."""
    rows = []
    if t["lb"] >= 1:
        top = f" Mostly {f['toxic']['top'].lower()}." if f and f["toxic"] and f["toxic"]["top"] else ""
        rows.append((amount(t["lb"]), "lbs of toxic chemicals released a year", (f"{amount(t['cancer_lb'])} lbs of them cause cancer." if t["cancer_lb"] >= 1 else "") + top))
    if t["co2"] >= 1:
        rows.append((amount(t["co2"]), "tons of greenhouse gases a year", f"as much as {amount(t['cars'])} cars"))
    if f and f.get("lead_lb", 0) >= 1:
        rows.append((amount(f["lead_lb"]), "lbs of lead into the air a year", "lead harms children's brains at any level"))
    if t["water"]:
        impaired = f and f["water"] and f["water"]["impaired"]
        rows.append((f"{t['water']:,}", "water permit violations last year", f"discharges into {impaired.split(', ')[0]}, already listed as polluted" if impaired else ""))
    if f:
        if f["qtrs_violation"]:
            rows.append((f"{f['qtrs_violation']} of 12", "quarters out of compliance", f"in the last 3 years · EPA status: {f['status'].lower()}" if f["status"] else "in the last 3 years"))
    elif t["bad"]:
        rows.append((f"{t['bad']} of {t['n']}", "polluters broke environmental rules", "in the last 3 years"))
    return rows


def bars(rows):
    """Polluters as ranked bars, split by cause. rows: [(polluter, miles or None)]."""
    if not rows:
        return ""
    top = max(f["deaths"] for f, _ in rows) or 1
    out = []
    for f, d in rows:
        segs = "".join(f'<div class="{cls}" style="flex:{f[k]:.3f}" data-tip="{e(deaths_words(f[k]))} from {e(label)}"></div>' for k, label, cls in CAUSES if f[k] > 0)
        sub = " · ".join(x for x in (f["kind"], f["city"] if f["city"] != "Unknown" else "", f"{d:.1f} mi" if d is not None else "") if x)
        out.append(f'<div class="row"><div class="n"><a href="../p/{e(f["slug"])}.html">{e(f["name"])}</a><small>{e(sub)}</small></div>'
                   f'<div class="v num">{deaths_words(f["deaths"])}</div>'
                   f'<div class="t"><div class="track" style="width:{max(0.6, f["deaths"] / top * 100):.1f}%">{segs}</div></div></div>')
    return '<div class="bars">' + "".join(out) + "</div>"


def strip(list_name, index, what, total):
    return (f'<section><h2>Against Indiana\'s {total:,} {what}</h2><p class="sub">Each bar is one {what.rstrip("s") if what != "cities and towns" else "town"}, '
            f'deadliest on the left (log scale). This one is in red.</p><div class="strip" data-list="{list_name}" data-index="{index}"></div>'
            f'<div class="legend between"><span>Deadliest</span><span class="me">▼ #{index + 1} of {total:,}</span><span>Least deadly</span></div></section>')


def actions(map_href, extra=()):
    links = [f'<a class="primary" href="{e(map_href)}">See it on the map</a>',
             '<a href="https://www.in.gov/idem/contact/file-a-complaint" rel="noopener" target="_blank">Report a problem to the state ↗</a>',
             '<a href="https://www.in.gov/idem/public-notices" rel="noopener" target="_blank">Comment on permits ↗</a>', *extra]
    return f'<section><h2>What you can do</h2><div class="btns">{"".join(links)}</div></section>'


PAGE_CSS = """*{box-sizing:border-box}
:root{--bg:#0d0e11;--panel:#15171c;--line:#2a2e37;--ink:#f3efe8;--muted:#9b968e;--red:#ff4b3a;--deep:#c4281b;--air:#ff4b3a;--toxic:#b48cff;--climate:#f2a65a;--dot:#3a3532;color-scheme:dark}
html,body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--ink);text-decoration-color:var(--muted);text-underline-offset:3px}
.num{font-variant-numeric:tabular-nums}
header{display:flex;align-items:center;gap:20px;padding:12px 24px;border-bottom:1px solid var(--line);position:relative;z-index:2;background:var(--bg)}
header .logo{font-weight:900;font-size:18px;text-decoration:none}header .logo span{color:var(--red)}header a.nav{color:var(--muted);text-decoration:none;font-size:15px}
main{max-width:900px;margin:0 auto;padding:28px 24px 64px}
.wrap{max-width:1040px;margin:0 auto;padding:0 24px}
.pill{display:inline-block;font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#fff;background:var(--deep);padding:5px 11px;border-radius:999px}
.crumbs{color:var(--muted);font-size:14px;margin-bottom:14px}.crumbs a{color:var(--muted)}
h1{font-size:clamp(32px,5vw,54px);line-height:1.04;letter-spacing:-.025em;margin:14px 0 8px}
h2{font-size:22px;letter-spacing:-.01em;margin:0 0 6px}
.meta{color:var(--muted);font-size:16px;line-height:1.5}.meta a{color:var(--muted)}
.sub{color:var(--muted);font-size:15px;line-height:1.5;margin:0 0 18px}
.eyebrow{font-size:13px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:14px}
.big{font-size:clamp(72px,12vw,150px);font-weight:900;line-height:.9;letter-spacing:-.035em;margin-top:40px}
.label{font-size:15px;font-weight:800;color:var(--red);text-transform:uppercase;letter-spacing:.1em;margin-top:12px}.label.plain{color:var(--ink)}
.lead{font-size:19px;line-height:1.45;margin:14px 0 0}
section{padding:48px 0;border-top:1px solid var(--line)}
.note{color:var(--muted);font-size:13px;line-height:1.55}
/* the satellite headline */
.hero{position:relative;min-height:620px;overflow:hidden;background:#0b1520}
.hero .tiles{position:absolute;inset:0;filter:saturate(.55) brightness(.8) contrast(1.05)}.hero .tiles img{position:absolute;width:256px;height:256px}
.hero .shade{position:absolute;inset:0;background:linear-gradient(90deg,rgba(13,14,17,.97) 0%,rgba(13,14,17,.88) 36%,rgba(13,14,17,.25) 66%,rgba(13,14,17,.1) 100%),linear-gradient(0deg,#0d0e11 0%,rgba(13,14,17,0) 26%)}
.hero svg.over{position:absolute;inset:0;width:100%;height:100%}
.hero .wrap{position:relative;padding-top:40px;padding-bottom:64px;pointer-events:none}.hero .text{max-width:580px;pointer-events:auto}
.hero .credit{position:absolute;right:10px;bottom:8px;font-size:11px;color:rgba(255,255,255,.55)}
.share{display:flex;flex-wrap:wrap;gap:8px;margin-top:26px}
.share a,.share button{font:inherit;font-size:14px;color:var(--ink);background:rgba(28,31,38,.85);border:1px solid var(--line);border-radius:999px;padding:7px 14px;text-decoration:none;cursor:pointer}
.share a:hover,.share button:hover{border-color:var(--muted)}.share button{background:var(--red);border-color:var(--red);color:#fff;font-weight:700}
/* charts */
.stack{display:flex;gap:2px;height:44px;border-radius:8px;overflow:hidden}.stack div{min-width:3px}
.air{background:var(--air)}.toxic{background:var(--toxic)}.climate{background:var(--climate)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:14px;color:var(--muted);margin:12px 0}.legend b{color:var(--ink)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}.legend.between{justify-content:space-between}.legend .me{color:var(--ink)}
.squares{display:flex;align-items:flex-end;gap:28px;flex-wrap:wrap;margin-bottom:18px}.squares figure{margin:0}
.sq{background:var(--red);border-radius:4px;max-width:calc(100vw - 48px);max-height:calc(100vw - 48px)}.sq.fines{background:var(--ink);border-radius:2px}
.squares figcaption{font-size:14px;color:var(--muted);margin-top:10px}.squares figcaption b{display:block;color:var(--ink);font-size:24px;font-weight:900}
.tiles4{display:grid;grid-template-columns:repeat(var(--n,4),1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.tile{background:var(--bg);padding:22px 20px;min-height:180px;display:flex;flex-direction:column}
.tile b{font-size:40px;font-weight:900;letter-spacing:-.02em}.tile span{font-weight:700;margin-top:4px}.tile small{color:var(--muted);font-size:13px;margin-top:6px;line-height:1.4}
.tile .dots{display:flex;flex-wrap:wrap;gap:2px;margin-top:auto;padding-top:14px}.tile .dots i{width:6px;height:6px;border-radius:50%;background:var(--red)}
.record.two{display:grid;grid-template-columns:1.3fr 1fr;gap:40px}
.fact{display:grid;grid-template-columns:140px 1fr;gap:16px;padding:14px 0;border-bottom:1px solid var(--line);align-items:baseline}.fact:first-child{border-top:1px solid var(--line)}
.fact b{font-size:26px;font-weight:900;text-align:right}.fact span{font-size:15px;line-height:1.45}.fact small{display:block;color:var(--muted);font-size:13px}
.trend{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 16px 10px;margin-bottom:14px}.trend h3{margin:0 0 2px;font-size:15px}.trend p{margin:0 0 8px;color:var(--muted);font-size:13px}.trend svg{width:100%;display:block}
.bars{font-size:14px}.bars .row{display:grid;grid-template-columns:280px 1fr 64px;grid-template-areas:"n t v";gap:4px 14px;align-items:center;padding:5px 0}
.bars .n{grid-area:n;min-width:0}.bars .t{grid-area:t}.bars .v{grid-area:v;white-space:nowrap}
.bars .n a{text-decoration:none;font-weight:600}.bars .n a:hover{text-decoration:underline}.bars .n small{display:block;color:var(--muted);font-size:12px}
.bars .track{height:14px;display:flex;gap:2px}.bars .track div{height:100%}.bars .track div:last-child{border-radius:0 4px 4px 0}.bars .v{text-align:right;font-weight:800}
.rankstrip{width:100%;height:110px;display:block}
.btns{display:flex;flex-wrap:wrap;gap:8px}.btns a{border:1px solid var(--line);border-radius:999px;padding:9px 15px;text-decoration:none;font-size:14px}
.btns a.primary{background:var(--red);border-color:var(--red);color:#fff;font-weight:700}
.towns{line-height:1.9;color:var(--muted)}.towns a{white-space:nowrap}
.tip{position:fixed;pointer-events:none;z-index:60;background:#000;color:#fff;border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:13px;display:none;max-width:260px}
/* list pages */
ol.list{padding:0;margin:0;list-style:none}ol.list li{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--line)}
ol.list .r{color:var(--muted);width:3em;flex:none}ol.list .n{flex:1}ol.list small{display:block;color:var(--muted)}ol.list b{font-variant-numeric:tabular-nums}
main h2{font-size:18px;margin:26px 0 10px}
@media(max-width:760px){header{padding:12px 16px;gap:12px}header .logo{font-size:15px}header a.nav{font-size:13px}main{padding:20px 16px 48px}.wrap{padding:0 16px}
.hero{min-height:0}.hero .shade{background:linear-gradient(0deg,#0d0e11 0%,rgba(13,14,17,.92) 62%,rgba(13,14,17,.2) 100%)}.hero .wrap{padding-top:250px;padding-bottom:40px}
.big{margin-top:24px}.lead{font-size:17px}section{padding:36px 0}
.tiles4{grid-template-columns:repeat(min(var(--n,4),2),1fr)}.tile{min-height:0;padding:18px 16px}.tile b{font-size:32px}
.record.two{grid-template-columns:1fr;gap:20px}.fact{grid-template-columns:100px 1fr}.fact b{font-size:22px}
.bars .row{grid-template-columns:1fr auto;grid-template-areas:"n v" "t t";padding:8px 0}}
"""
