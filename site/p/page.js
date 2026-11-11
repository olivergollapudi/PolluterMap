// The interactive parts of polluter, town and county pages (made by scripts/export_site_data.py):
//  - the satellite picture behind the headline (USGS National Map imagery: public domain, no key),
//    with rings around a plant, or the 10-mile circle and each polluter's dot for a town or county
//  - the strip ranking every polluter, town or county (data in ranks.js), with this one in red
//  - hover notes on charts, and the share button
// Everything the page says is already in its HTML; this only draws pictures.
(function () {
  "use strict";
  const TILE = (z, x, y) => `https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/${z}/${y}/${x}`;
  const proj = (lon, lat, z) => { const n = 2 ** z * 256, r = lat * Math.PI / 180;
    return [(lon + 180) / 360 * n, (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * n]; };
  const mPerPx = (lat, z) => 156543.03 * Math.cos(lat * Math.PI / 180) / 2 ** z;
  const MILE = 1609.34;
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const deaths = (n) => !n ? "0" : n >= 100 ? Math.round(+n.toPrecision(2)).toLocaleString("en-US")
    : n >= 10 ? Math.round(n).toLocaleString("en-US") : n >= 0.1 ? n.toFixed(1) : "under 0.1";
  const SVG = "http://www.w3.org/2000/svg";

  // ---------------------------------------------------------------- satellite hero
  function hero(el) {
    const lon = +el.dataset.lon, lat = +el.dataset.lat, kind = el.dataset.kind;
    const dots = JSON.parse((document.getElementById("dots") || {}).textContent || "[]");   // [lon, lat, deaths, name]
    const tiles = el.querySelector(".tiles"), svg = el.querySelector("svg.over");
    const w = el.clientWidth, h = el.clientHeight, wide = innerWidth > 760;
    const cx = wide ? w * .72 : w / 2, cy = wide ? h * .5 : Math.min(150, h / 2);
    let z = kind === "polluter" ? 15 : kind === "town" ? 12 : 12;
    if (kind === "town" && !wide) z = 11;
    if (kind === "town") {   // zoom out if a listed polluter (the closest ones, for a town with none nearby) is off the picture
      const fits = () => dots.every(d => { const [a, b] = proj(d[0], d[1], z), [c, e] = proj(lon, lat, z);
        return Math.abs(a - c) < (wide ? w * .26 : w * .45) && Math.abs(b - e) < (wide ? h * .45 : 140); });
      while (z > 8 && !fits()) z--;
    }
    if (kind === "county" && dots.length > 1) {   // zoom out until every polluter fits in the picture's side
      const fw = wide ? w * .5 : w * .9, fh = wide ? h * .82 : 260;
      while (z > 8) {
        const p = dots.map(d => proj(d[0], d[1], z)), xs = p.map(q => q[0]), ys = p.map(q => q[1]);
        if (Math.max(...xs) - Math.min(...xs) < fw && Math.max(...ys) - Math.min(...ys) < fh) break;
        z--;
      }
    }
    const [px, py] = proj(lon, lat, z);
    for (let x = Math.floor((px - cx) / 256); x <= Math.floor((px - cx + w) / 256); x++)
      for (let y = Math.floor((py - cy) / 256); y <= Math.floor((py - cy + h) / 256); y++) {
        const img = new Image();
        img.alt = ""; img.decoding = "async"; img.src = TILE(z, x, y);
        img.style.left = (x * 256 - px + cx) + "px"; img.style.top = (y * 256 - py + cy) + "px";
        img.onerror = () => img.remove();
        tiles.append(img);
      }
    const xy = (lo, la) => { const [qx, qy] = proj(lo, la, z); return [qx - px + cx, qy - py + cy]; };
    const mi = MILE / mPerPx(lat, z);
    let g = "";
    if (kind === "polluter") {
      g += `<defs><radialGradient id="glow"><stop offset="0" stop-color="#ff4b3a" stop-opacity=".55"/><stop offset="1" stop-color="#ff4b3a" stop-opacity="0"/></radialGradient></defs>`;
      g += `<circle cx="${cx}" cy="${cy}" r="${mi * .9}" fill="url(#glow)"/>`;
      for (const m of [.5, 1]) g += `<circle cx="${cx}" cy="${cy}" r="${mi * m}" fill="none" stroke="#fff" stroke-opacity=".5" stroke-dasharray="4 6"/>`
        + `<text x="${cx + mi * m * .71 + 6}" y="${cy - mi * m * .71}" fill="#fff" fill-opacity=".8" font-size="12">${m === 1 ? "1 mile" : "½ mile"}</text>`;
      g += `<circle cx="${cx}" cy="${cy}" r="9" fill="#ff4b3a" stroke="#fff" stroke-width="2"/>`;
    } else {
      if (kind === "town") g += `<circle cx="${cx}" cy="${cy}" r="${mi * 10}" fill="rgba(255,75,58,.06)" stroke="#fff" stroke-opacity=".6" stroke-dasharray="5 7"/>`
        + `<text x="${cx}" y="${Math.max(14, cy - mi * 10 - 8)}" text-anchor="middle" fill="#fff" fill-opacity=".8" font-size="12">10 miles</text>`;
      for (const [lo, la, d, name] of [...dots].sort((a, b) => a[2] - b[2])) {
        const [x, y] = xy(lo, la), r = 3 + Math.min(20, Math.pow(d || 0, .42));
        g += `<circle cx="${x}" cy="${y}" r="${r * 2.2}" fill="#ff4b3a" fill-opacity=".14"/>`
          + `<circle cx="${x}" cy="${y}" r="${r}" fill="#ff4b3a" fill-opacity=".92" stroke="#0d0e11" stroke-width="1.5" data-tip="${esc(name)}: ${deaths(d)} deaths a year"/>`;
      }
      if (kind === "town") g += `<circle cx="${cx}" cy="${cy}" r="5" fill="#fff"/>`;
    }
    svg.innerHTML = g;
  }

  // ---------------------------------------------------------------- ranking strip
  function strip(el) {
    const all = (window.RANKS || {})[el.dataset.list] || [], me = +el.dataset.index;
    if (!all.length) { el.remove(); return; }
    const W = 1000, H = 110, top = 24, max = Math.log10(all[0][1] + 1), bw = W / all.length;
    const hgt = (v) => Math.max(1.5, Math.log10(v + 1) / max * (H - top)), mx = me * bw + bw / 2;
    let s = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="ranking" class="rankstrip">`;
    all.forEach(([name, v], i) => { s += `<rect x="${(i * bw).toFixed(2)}" y="${H - hgt(v)}" width="${Math.max(bw - (bw > 3 ? 1 : 0), .6)}" height="${hgt(v)}" rx="${bw > 4 ? 2 : 0}" fill="var(--dot)" data-tip="#${i + 1} ${esc(name)}: ${deaths(v)} deaths a year"/>`; });
    if (all[me]) s += `<rect x="${mx - 3}" y="${H - hgt(all[me][1])}" width="6" height="${hgt(all[me][1])}" fill="var(--red)" data-tip="#${me + 1} ${esc(all[me][0])}: ${deaths(all[me][1])} deaths a year"/><path d="M${mx - 7} 4 L${mx + 7} 4 L${mx} 14 Z" fill="var(--red)"/>`;
    el.innerHTML = s + "</svg>";
  }

  // ---------------------------------------------------------------- hover notes
  const tip = document.createElement("div");
  tip.className = "tip";
  document.body.append(tip);
  document.addEventListener("pointermove", (e) => {
    const t = e.target.closest && e.target.closest("[data-tip]");
    if (!t) { tip.style.display = "none"; return; }
    tip.textContent = t.dataset.tip; tip.style.display = "block";
    tip.style.left = Math.min(innerWidth - 270, e.clientX + 14) + "px"; tip.style.top = (e.clientY + 14) + "px";
  });

  // ---------------------------------------------------------------- share: the phone's share sheet, else copy the link
  const sheet = navigator.share && matchMedia("(pointer: coarse)").matches;
  document.querySelectorAll("[data-share]").forEach(b => {
    if (sheet) b.textContent = "Share";
    b.addEventListener("click", async () => {
      if (sheet) { try { await navigator.share({ title: document.title, text: b.dataset.text, url: b.dataset.share }); } catch (_) {} return; }
      let ok = false;
      try { await navigator.clipboard.writeText(b.dataset.share); ok = true; }
      catch (_) {   // older browsers: copy from a hidden text box
        const t = Object.assign(document.createElement("textarea"), { value: b.dataset.share });
        document.body.append(t); t.select(); try { ok = document.execCommand("copy"); } catch (_) {} t.remove();
      }
      b.textContent = ok ? "Link copied" : b.dataset.share;
    });
  });

  document.querySelectorAll(".hero[data-lat]").forEach(hero);
  document.querySelectorAll(".strip[data-list]").forEach(strip);
})();
