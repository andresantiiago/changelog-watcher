/* Changelog Watcher – single page app (no build step). */
"use strict";

const S = { meta: null, envs: [], projects: [], refresh: null, charts: [], envFilter: null, nsFilter: "" };
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Labels are getters: translated with the language loaded from /api/meta.
const label = (labelText, shortText) => ({ get label() { return t(labelText); }, get short() { return t(shortText); } });
const STATUS = {
  up_to_date: label("Up to date", "Up to date"),
  patch: label("Bugfix available", "Bugfix"),
  minor: label("Minor available", "Minor"),
  major: label("Major available", "Major"),
  unknown: label("Unknown", "Unknown"),
};
const STATUS_KEYS = ["up_to_date", "patch", "minor", "major", "unknown"];
const STATUS_VAR = { up_to_date: "--st-up", patch: "--st-patch", minor: "--st-minor", major: "--st-major", unknown: "--st-unknown" };
const RELATION = {
  get same() { return t("Same"); },
  get source_ahead() { return t("Source ahead"); },
  get target_ahead() { return t("Target ahead"); },
  get only_source() { return t("Only in source"); },
  get only_target() { return t("Only in target"); },
  get unknown() { return t("No version"); },
};

// ------------------------------------------------------------------ utils
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function statusColor(s) { return cssVar(STATUS_VAR[s] || "--st-unknown"); }
function envColor(envId) {
  const idx = S.envs.findIndex((e) => e.id === Number(envId));
  return cssVar(`--s${(idx < 0 ? 0 : idx % 8) + 1}`);
}
function envName(id) { return (S.envs.find((e) => e.id === Number(id)) || {}).name || `#${id}`; }
function fmtDate(iso, withTime = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  return withTime ? d.toLocaleString(locale(), { dateStyle: "short", timeStyle: "short" }) : d.toLocaleDateString(locale());
}
function rel(iso) {
  if (!iso) return t("never");
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return t("just now");
  if (s < 3600) return t("{n} min ago", { n: Math.round(s / 60) });
  if (s < 86400) return t("{n} h ago", { n: Math.round(s / 3600) });
  return t("{n} days ago", { n: Math.round(s / 86400) });
}
function pct(r) { return r == null ? "—" : `${Math.round(r * 100)}%`; }
function days(n) {
  if (!n) return t("{n} days", { n: 0 });
  if (n >= 365) return t("{n} years", { n: num(n / 365, 1) });
  if (n >= 60) return t("{n} months", { n: Math.round(n / 30) });
  return Math.round(n) === 1 ? t("{n} day", { n: 1 }) : t("{n} days", { n: Math.round(n) });
}
function num(n, d = 0) { return Number(n || 0).toLocaleString(locale(), { maximumFractionDigits: d, minimumFractionDigits: d }); }
function badge(status, text) {
  return `<span class="badge st-${esc(status)}" title="${esc(STATUS[status]?.label || status)}"><span class="dot"></span>${esc(text || STATUS[status]?.short || status)}</span>`;
}
function ver(v) { return v ? `<span class="ver">${esc(v)}</span>` : `<span class="muted">—</span>`; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

async function api(path, opts = {}) {
  const init = { headers: {}, ...opts };
  if (opts.body !== undefined && typeof opts.body !== "string") {
    init.body = JSON.stringify(opts.body);
    init.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    let msg = data?.detail ?? data;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function showError(text, title = t("Error details")) {
  modal(title, `<pre class="log">${esc(text)}</pre><p class="small muted" style="margin-bottom:0">${t("Full history in")} <a href="#/logs" data-close>Logs</a>.</p>`);
}
function toast(msg, error = false, onClick = null) {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error click" : "");
  el.textContent = msg;
  if (error) { el.title = t("Click to see details"); el.addEventListener("click", () => { el.remove(); onClick ? onClick() : showError(msg); }); }
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), error ? 7000 : 3500);
}

function repoUrl(p) {
  const r = p.repository || "";
  switch (p.source_type) {
    case "github_releases": case "github_tags":
      return r.startsWith("http") ? r : `https://github.com/${r}`;
    case "gitlab_releases": case "gitlab_tags":
      return r.startsWith("http") ? r : `${p.source_url || "https://gitlab.com"}/${r}`;
    case "dockerhub": {
      const name = r.replace(/^docker\.io\//, "").split(":")[0];
      return name.includes("/") ? `https://hub.docker.com/r/${name}` : `https://hub.docker.com/_/${name}`;
    }
    default:
      return r.startsWith("http") ? r : null;
  }
}

// --------------------------------------------------------------- markdown
function inlineMd(s) {
  let out = esc(s);
  const codes = [];
  out = out.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  out = out.replace(/!\[([^\]]*)\]\((https?:[^)\s]+)\)/g, (_, alt, url) => `<a href="${url}" target="_blank" rel="noopener">[${t("image")}${alt ? ": " + alt : ""}]</a>`);
  out = out.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, (_, t, url) => `<a href="${url}" target="_blank" rel="noopener">${t}</a>`);
  out = out.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (_, pre, url) => `${pre}<a href="${url}" target="_blank" rel="noopener">${url}</a>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|\s)_([^_]+)_(?=\s|$|[.,;:])/g, "$1<em>$2</em>");
  out = out.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
  return out;
}
function md(text) {
  if (!text) return `<p class="muted">${t("No release notes.")}</p>`;
  const lines = String(text).replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`);
      continue;
    }
    if (/^\s*\|/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r, idx) => !(idx === 1 && /^[\s|:-]+$/.test(r)));
      const [head, ...rest] = body;
      out.push(`<div class="table-wrap"><table><thead><tr>${cells(head).map((c) => `<th>${inlineMd(c)}</th>`).join("")}</tr></thead><tbody>${rest.map((r) => `<tr>${cells(r).map((c) => `<td>${inlineMd(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { out.push(`<h${Math.min(h[1].length + 1, 4)}>${inlineMd(h[2])}</h${Math.min(h[1].length + 1, 4)}>`); i++; continue; }
    if (/^\s*[-*+•]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+•]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*+•]\s+/, ""));
      out.push(`<ul>${items.map((t) => `<li>${inlineMd(t)}</li>`).join("")}</ul>`);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*[-*+•]\s|\s*\|)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${inlineMd(para.join(" "))}</p>`);
  }
  return `<div class="md">${out.join("")}</div>`;
}

// ----------------------------------------------------------------- charts
function registerChart(render) {
  S.charts.push(render);
  render();
}
window.addEventListener("resize", debounce(() => S.charts.forEach((r) => r()), 150));

function tooltipFor(el) {
  let tip = el.querySelector(".tooltip");
  if (!tip) { tip = document.createElement("div"); tip.className = "tooltip"; el.appendChild(tip); }
  return {
    show(html, x, y) {
      tip.innerHTML = html;
      tip.style.display = "block";
      const w = tip.offsetWidth, hgt = tip.offsetHeight, W = el.clientWidth;
      let left = x + 14;
      if (left + w > W) left = Math.max(0, x - w - 14);
      tip.style.left = `${left}px`;
      tip.style.top = `${Math.max(0, y - hgt / 2)}px`;
    },
    hide() { tip.style.display = "none"; },
  };
}

/** Horizontal 100% stacked bars, one row per environment. */
function stackedBars(el, rows, keys, colorOf, labelOf, onPick = null) {
  registerChart(() => {
    if (!el.isConnected) return;
    const W = Math.max(el.clientWidth, 280);
    const labelW = Math.min(150, Math.max(90, W * 0.3));
    const maxChars = Math.floor((labelW - 10) / 7);
    const short = (t) => (t.length > maxChars ? `${t.slice(0, maxChars - 1)}…` : t);
    const rowH = 28, gap = 14, barW = W - labelW - 50;
    const H = rows.length * (rowH + gap);
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t("Status distribution by environment")}">`;
    rows.forEach((row, ri) => {
      const y = ri * (rowH + gap);
      const total = keys.reduce((a, k) => a + (row.values[k] || 0), 0);
      svg += `<text class="lbl" x="0" y="${y + rowH / 2 + 4}"><title>${esc(row.label)}</title>${esc(short(row.label))}</text>`;
      svg += `<text x="${W}" y="${y + rowH / 2 + 4}" text-anchor="end">${total}</text>`;
      if (!total) {
        svg += `<rect x="${labelW}" y="${y}" width="${barW}" height="${rowH}" rx="4" fill="var(--surface-2)"/>`;
        return;
      }
      let x = labelW;
      const segs = keys.filter((k) => row.values[k]);
      segs.forEach((k, si) => {
        const v = row.values[k];
        const w = (v / total) * barW;
        const ww = Math.max(w - (si < segs.length - 1 ? 2 : 0), 1);
        const first = si === 0, last = si === segs.length - 1;
        const r = 4;
        // rounded only at the outer ends
        const path = `M${x + (first ? r : 0)},${y} h${ww - (first ? r : 0) - (last ? r : 0)} ${last ? `a${r},${r} 0 0 1 ${r},${r} v${rowH - 2 * r} a${r},${r} 0 0 1 -${r},${r}` : `v${rowH}`} h-${ww - (first ? r : 0) - (last ? r : 0)} ${first ? `a${r},${r} 0 0 1 -${r},-${r} v-${rowH - 2 * r} a${r},${r} 0 0 1 ${r},-${r}` : `v-${rowH}`}z`;
        svg += `<path d="${path}" fill="${colorOf(k)}" data-row="${ri}" data-key="${k}"/>`;
        if (ww > 26) {
          const ink = k === "patch" ? "#0b0b0b" : "#ffffff";
          svg += `<text x="${x + ww / 2}" y="${y + rowH / 2 + 4}" text-anchor="middle" style="fill:${ink};font-weight:600;pointer-events:none">${v}</text>`;
        }
        x += w;
      });
    });
    svg += "</svg>";
    el.innerHTML = svg;
    const tip = tooltipFor(el);
    $$("path[data-key]", el).forEach((p) => {
      p.addEventListener("mousemove", (ev) => {
        const row = rows[p.dataset.row], k = p.dataset.key;
        const total = keys.reduce((a, kk) => a + (row.values[kk] || 0), 0);
        const b = el.getBoundingClientRect();
        tip.show(`<div class="t">${esc(row.label)}</div><div class="r"><span><i style="background:${colorOf(k)}"></i>${esc(labelOf(k))}</span><b>${row.values[k]} (${Math.round((row.values[k] / total) * 100)}%)</b></div>`, ev.clientX - b.left, ev.clientY - b.top);
      });
      p.addEventListener("mouseleave", () => tip.hide());
      if (onPick) { p.style.cursor = "pointer"; p.addEventListener("click", () => onPick(rows[p.dataset.row], p.dataset.key)); }
    });
  });
}

/** Vertical bars, single series. */
function columnChart(el, items, { color = "var(--seq)", height = 200, unit = "" } = {}) {
  registerChart(() => {
    if (!el.isConnected) return;
    const W = Math.max(el.clientWidth, 260), H = height, padB = 26, padT = 18;
    const max = Math.max(1, ...items.map((i) => i.value));
    const slot = W / items.length;
    const bw = Math.min(56, slot * 0.6);
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${t("Bar chart")}">`;
    svg += `<line class="axis-line" x1="0" x2="${W}" y1="${H - padB}" y2="${H - padB}"/>`;
    items.forEach((it, i) => {
      const h = ((H - padB - padT) * it.value) / max;
      const x = i * slot + (slot - bw) / 2, y = H - padB - h;
      if (it.value > 0) {
        const r = Math.min(4, h);
        svg += `<path d="M${x},${H - padB} v-${h - r} a${r},${r} 0 0 1 ${r},-${r} h${bw - 2 * r} a${r},${r} 0 0 1 ${r},${r} v${h - r}z" fill="${color}" data-i="${i}"/>`;
      }
      svg += `<rect x="${i * slot}" y="0" width="${slot}" height="${H - padB}" fill="transparent" data-i="${i}"/>`;
      svg += `<text class="lbl" x="${x + bw / 2}" y="${y - 5}" text-anchor="middle">${it.value}</text>`;
      svg += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle">${esc(it.label)}</text>`;
    });
    svg += "</svg>";
    el.innerHTML = svg;
    const tip = tooltipFor(el);
    $$("[data-i]", el).forEach((r) => {
      r.addEventListener("mousemove", (ev) => {
        const it = items[r.dataset.i];
        const b = el.getBoundingClientRect();
        tip.show(`<div class="t">${esc(it.label)}</div><div class="r"><span>${esc(it.hint || t("Applications"))}</span><b>${it.value}${unit}</b></div>`, ev.clientX - b.left, ev.clientY - b.top);
      });
      r.addEventListener("mouseleave", () => tip.hide());
    });
  });
}

/** Multi-series line chart over dates with crosshair tooltip. */
function lineChart(el, series, { height = 220, yMax = null, yFmt = (v) => v, label = "" } = {}) {
  registerChart(() => {
    if (!el.isConnected) return;
    const all = series.flatMap((s) => s.points);
    if (!all.length) { el.innerHTML = `<div class="empty">${t("No history yet – points are recorded on every refresh.")}</div>`; return; }
    const W = Math.max(el.clientWidth, 280), H = height, padL = 44, padR = 12, padT = 10, padB = 24;
    const xs = [...new Set(all.map((p) => p.x))].sort();
    const t0 = new Date(xs[0]).getTime(), t1 = new Date(xs[xs.length - 1]).getTime();
    const span = Math.max(t1 - t0, 86400000);
    const max = yMax ?? Math.max(1, ...all.map((p) => p.y)) * 1.1;
    const X = (x) => padL + ((new Date(x).getTime() - t0) / span) * (W - padL - padR);
    const Y = (y) => padT + (1 - y / max) * (H - padT - padB);
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">`;
    for (let i = 0; i <= 4; i++) {
      const v = (max / 4) * i, y = Y(v);
      svg += `<line class="grid-line" x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}"/>`;
      svg += `<text x="${padL - 6}" y="${y + 4}" text-anchor="end">${esc(yFmt(v))}</text>`;
    }
    const ticks = Math.min(xs.length, Math.max(2, Math.floor((W - padL) / 90)));
    for (let i = 0; i < ticks; i++) {
      const t = t0 + (span * i) / Math.max(ticks - 1, 1);
      svg += `<text x="${padL + ((t - t0) / span) * (W - padL - padR)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === ticks - 1 ? "end" : "middle"}">${new Date(t).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })}</text>`;
    }
    series.forEach((s) => {
      const pts = s.points.filter((p) => p.y != null).sort((a, b) => (a.x < b.x ? -1 : 1));
      if (!pts.length) return;
      if (pts.length === 1) {
        svg += `<circle cx="${X(pts[0].x)}" cy="${Y(pts[0].y)}" r="4" fill="${s.color}" stroke="var(--surface)" stroke-width="2"/>`;
      } else {
        svg += `<path d="${pts.map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("")}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
      }
    });
    svg += `<line id="xh" class="axis-line" x1="0" x2="0" y1="${padT}" y2="${H - padB}" style="display:none"/>`;
    svg += `<g id="dots"></g><rect x="${padL}" y="0" width="${W - padL - padR}" height="${H - padB}" fill="transparent" id="hit"/>`;
    svg += "</svg>";
    el.innerHTML = svg;
    const tip = tooltipFor(el);
    const hit = $("#hit", el), xh = $("#xh", el), dots = $("#dots", el);
    hit.addEventListener("mousemove", (ev) => {
      const b = el.getBoundingClientRect();
      const scale = W / b.width;
      const mx = (ev.clientX - b.left) * scale;
      let best = xs[0];
      xs.forEach((x) => { if (Math.abs(X(x) - mx) < Math.abs(X(best) - mx)) best = x; });
      xh.setAttribute("x1", X(best)); xh.setAttribute("x2", X(best)); xh.style.display = "";
      let rows = "", d = "";
      series.forEach((s) => {
        const p = s.points.find((pp) => pp.x === best);
        if (!p || p.y == null) return;
        d += `<circle cx="${X(best)}" cy="${Y(p.y)}" r="4" fill="${s.color}" stroke="var(--surface)" stroke-width="2"/>`;
        rows += `<div class="r"><span><i style="background:${s.color}"></i>${esc(s.name)}</span><b>${esc(yFmt(p.y, true))}</b></div>`;
      });
      dots.innerHTML = d;
      tip.show(`<div class="t">${new Date(best).toLocaleDateString(locale())}</div>${rows}`, X(best) / scale, ev.clientY - b.top);
    });
    hit.addEventListener("mouseleave", () => { tip.hide(); xh.style.display = "none"; dots.innerHTML = ""; });
  });
}

function legend(items, line = false) {
  return `<div class="legend">${items.map((i) => `<span><i class="${line ? "line" : ""}" style="background:${i.color}"></i>${esc(i.label)}</span>`).join("")}</div>`;
}

// ------------------------------------------------------------------ modal
function modal(title, body, { onSubmit, submitLabel = t("Save"), wide = false } = {}) {
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<form class="modal" ${wide ? 'style="width:min(1000px,100%)"' : ""} novalidate>
    <header><h2>${esc(title)}</h2><button type="button" class="btn ghost" data-close aria-label="${t("Close")}">✕</button></header>
    <div class="body">${body}</div>
    <footer><button type="button" class="btn" data-close>${t("Cancel")}</button>${onSubmit ? `<button class="btn primary" type="submit">${esc(submitLabel)}</button>` : ""}</footer>
  </form>`;
  const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  ov.addEventListener("mousedown", (e) => { if (e.target === ov) close(); });
  $$("[data-close]", ov).forEach((b) => b.addEventListener("click", close));
  const form = $("form", ov);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("button[type=submit]", form);
    btn.disabled = true;
    try {
      await onSubmit(form);
      close();
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });
  document.body.appendChild(ov);
  const first = $("input, select, textarea", ov);
  if (first) first.focus();
  return { el: ov, form, close };
}

function formData(form) {
  const out = {};
  $$("[name]", form).forEach((el) => {
    if (el.dataset.skip) return;
    const name = el.name;
    let v;
    if (el.type === "checkbox") v = el.checked;
    else if (el.type === "number") v = el.value === "" ? null : Number(el.value);
    else v = el.value.trim() === "" ? null : el.value.trim();
    out[name] = v;
  });
  return out;
}
const opt = (value, label, selected) => `<option value="${esc(value)}" ${String(selected) === String(value) ? "selected" : ""}>${esc(label)}</option>`;
const field = (label, input, hint = "", cls = "") => `<label class="field ${cls}">${esc(label)}${input}${hint ? `<span class="hint">${hint}</span>` : ""}</label>`;
const inp = (name, value, attrs = "") => `<input name="${name}" value="${esc(value ?? "")}" ${attrs}>`;

// ---------------------------------------------------------------- loaders
async function loadBase() {
  const [meta, envs, projects] = await Promise.all([api("/api/meta"), api("/api/environments"), api("/api/projects")]);
  S.meta = meta; S.envs = envs; S.projects = projects;
  if (S.lang !== meta.language) { S.lang = meta.language; applyStatic(); }
}

// ------------------------------------------------------------- refreshing
async function startRefresh(projectIds) {
  try {
    const st = await api("/api/refresh", { method: "POST", body: { project_ids: projectIds || null } });
    S.refresh = st;
    renderRefreshInfo();
    toast(st.message || t("Refresh started"));
    pollRefresh();
  } catch (e) { toast(e.message, true); }
}
async function pollRefresh() {
  const st = await api("/api/refresh/status");
  S.refresh = st;
  renderRefreshInfo();
  if (st.running) { setTimeout(pollRefresh, 2000); return; }
  const errs = st.projects_failed + st.detections_failed;
  toast(t("Refresh finished: {n} new versions", { n: st.new_releases }) + (errs ? t(", {n} error(s)", { n: errs }) : ""), errs > 0, () => showError(st.errors.join("\n\n"), t("Refresh errors")));
  await loadBase();
  route();
}
function renderRefreshInfo() {
  const st = S.refresh;
  const btn = $("#refresh-btn");
  const icon = $("svg", btn);
  if (st?.running) {
    btn.disabled = true; icon.classList.add("spin"); $("span", btn).textContent = t("Refreshing…");
  } else {
    btn.disabled = false; icon.classList.remove("spin"); $("span", btn).textContent = t("Check for updates");
  }
  $("#refresh-info").innerHTML = st?.finished_at
    ? `${t("Last refresh {when}", { when: rel(st.finished_at) })}${st.success === false ? ` · <span class="err" data-err-refresh>${t("{n} error(s)", { n: st.errors.length })}</span>` : ""}`
    : "";
}
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-err]");
  if (el) return showError(el.dataset.err);
  if (e.target.closest("[data-err-refresh]") && S.refresh) showError(S.refresh.errors.join("\n\n") || t("No details."), t("Errors of the last refresh"));
});

/** Jump to the applications list with a filter ({status, env}) or to another route ({href}). */
function goTo(go) {
  if (go.href) { location.hash = go.href; return; }
  const st = viewApps.state ||= { q: "", status: "", tag: "", env: "" };
  Object.assign(st, { q: "", tag: "", status: go.status || "", env: go.env ? String(go.env) : "" });
  location.hash = "#/apps";
}
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-go]");
  if (el && !e.target.closest("a,button")) goTo(JSON.parse(el.dataset.go));
});
document.addEventListener("keydown", (e) => {
  const el = e.target.closest?.("[data-go]");
  if (el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); goTo(JSON.parse(el.dataset.go)); }
});

// ================================================================ VIEWS
function envFilterBar(onChange, selected) {
  const items = [{ id: "", name: t("All environments") }, ...S.envs];
  const html = `<div class="seg" role="group" aria-label="${t("Environment")}">${items.map((e) => `<button type="button" data-env="${e.id}" class="${String(selected ?? "") === String(e.id) ? "on" : ""}">${esc(e.name)}</button>`).join("")}</div>`;
  return { html, bind(root) { $$("[data-env]", root).forEach((b) => b.addEventListener("click", () => onChange(b.dataset.env ? Number(b.dataset.env) : null))); } };
}

function kpi(label, value, foot = "", extra = "", cls = "", go = null) {
  const nav = go ? ` data-go="${esc(JSON.stringify(go))}" role="link" tabindex="0"` : "";
  return `<div class="card kpi ${cls}"${nav}><div class="label">${label}</div><div class="value num">${value}</div>${extra}${foot ? `<div class="foot">${foot}</div>` : ""}</div>`;
}

function updatesCell(s) {
  const parts = [];
  if (s.latest_patch) parts.push(`<span><span class="k">bugfix</span><b>${esc(s.latest_patch)}</b></span>`);
  if (s.latest_minor) parts.push(`<span><span class="k">minor</span><b>${esc(s.latest_minor)}</b></span>`);
  if (s.latest_major) parts.push(`<span><span class="k">major</span><b>${esc(s.latest_major)}</b></span>`);
  return parts.length ? `<div class="upd">${parts.join("")}</div>` : `<span class="muted">—</span>`;
}

const EOL = {
  eol: { get label() { return t("EOL reached"); }, cls: "eol" },
  soon: { get label() { return t("EOL soon"); }, cls: "soon" },
  ok: { get label() { return t("Supported"); }, cls: "ok" },
};
/** "in 42 days" / "10 days ago" / "today" for a deployment's end of life. */
function eolWhen(s) {
  if (s.eol_days_left == null) return s.eol_status === "eol" ? t("already reached") : (s.eol_status === "ok" ? t("no date announced") : "—");
  const n = s.eol_days_left;
  return `${fmtDate(s.eol_date)} · ${n > 0 ? t("in {n} days", { n: num(n) }) : n === 0 ? t("today") : t("{n} days ago", { n: num(-n) })}`;
}
/** Chip shown next to the version when the cycle in use is close to (or past) its end of life. */
function eolChip(s) {
  if (s.eol_status !== "eol" && s.eol_status !== "soon") return "";
  const title = esc(`${s.eol_product} ${s.eol_cycle}: ${t("end of life {when}", { when: eolWhen(s) })}`);
  const label = EOL[s.eol_status].label;
  return s.eol_link
    ? `<a class="chip eol-${s.eol_status}" href="${esc(s.eol_link)}" target="_blank" rel="noopener noreferrer" title="${title}">${label} ↗</a>`
    : `<span class="chip eol-${s.eol_status}" title="${title}">${label}</span>`;
}

// ------------------------------------------------------------- dashboard
async function viewDashboard(view) {
  const q = `?${S.envFilter ? `environment_id=${S.envFilter}&` : ""}${S.nsFilter ? `namespace=${encodeURIComponent(S.nsFilter)}` : ""}`;
  const [d, hist] = await Promise.all([api(`/api/dashboard${q}`), api(`/api/history?days=180${S.envFilter ? `&environment_id=${S.envFilter}` : ""}`)]);
  // Namespace chosen for another environment: the select can't show (or clear) it, so drop it and reload.
  if (S.nsFilter && !d.namespaces.includes(S.nsFilter)) { S.nsFilter = ""; return viewDashboard(view); }
  const o = d.overall;
  const filter = envFilterBar((id) => { S.envFilter = id; route(); }, S.envFilter);
  if (!S.envs.length) {
    view.innerHTML = `<div class="card empty">${t("No environment yet. <a href=\"#/environments\">Add an environment</a>, then <a href=\"#/apps\">the applications</a>.")}</div>`;
    return;
  }
  const ratio = o.up_to_date_ratio;
  const kpisHidden = localStorage.getItem("cw-kpis-hidden") === "1";
  view.innerHTML = `
    <div class="filters">${filter.html}${d.namespaces.length ? `<select id="ns-filter" aria-label="Namespace"><option value="">${t("All namespaces")}</option>${d.namespaces.map((n) => opt(n, n, S.nsFilter)).join("")}</select>` : ""}<div class="spacer" style="flex:1"></div>
      <button type="button" class="btn ghost sm kpi-toggle" id="kpi-toggle" aria-expanded="${!kpisHidden}" aria-controls="kpis"><span>${t("Indicators")}</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></button></div>
    <div class="grid kpis" id="kpis" ${kpisHidden ? "hidden" : ""}>
      ${kpi(t("Environment up to date"), pct(ratio), t("{n} of {total} with a known version", { n: o.counts.up_to_date, total: o.known }), `<div class="meter"><i style="width:${(ratio || 0) * 100}%"></i></div>`, "hero", { status: "up_to_date", env: S.envFilter })}
      ${kpi(t("Monitored deployments"), num(o.total), t("{apps} applications · {n} without an identified version", { apps: d.projects, n: o.counts.unknown }), "", "", { env: S.envFilter })}
      ${kpi(`${badge("patch", t("Bugfix"))} ${t("pending")}`, num(o.patch_pending), t("{n} with only a bugfix pending", { n: o.counts.patch }), "", "", { status: "has_patch", env: S.envFilter })}
      ${kpi(`${badge("minor", t("Minor"))} ${t("pending")}`, num(o.minor_pending), t("{n} with minor as the largest jump", { n: o.counts.minor }), "", "", { status: "has_minor", env: S.envFilter })}
      ${kpi(`${badge("major", t("Major"))} ${t("pending")}`, num(o.major_pending), t("{n} deployments", { n: o.counts.major }), "", "", { status: "has_major", env: S.envFilter })}
      ${kpi(t("Average age"), o.libyears_avg == null ? "—" : `${num(o.libyears_avg, 2)} <span class="small muted">libyears</span>`, t("average, per deployment, of the gap between the version in use and the latest"), "", "", { status: "outdated", env: S.envFilter })}
      ${kpi(t("Average delay"), days(o.avg_outdated_days), t("largest delay: {d}", { d: days(o.max_outdated_days) }), "", "", { status: "outdated", env: S.envFilter })}
      ${kpi(t("Differences between environments"), num(d.drift_count), `<a href="#/compare">${t("compare environments")}</a>`, "", "", { href: "#/compare" })}
    </div>
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><div class="card-head"><h2>${t("Status by environment")}</h2><span class="sub">${t("largest pending update of each deployment")}</span></div><div class="chart" id="ch-status"></div>${legend(STATUS_KEYS.map((k) => ({ label: STATUS[k].label, color: statusColor(k) })))}</div>
      <div class="card"><div class="card-head"><h2>${t("How long they have been outdated")}</h2><span class="sub">${t("since the first newer version was published")}</span></div><div class="chart" id="ch-age"></div></div>
    </div>
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><div class="card-head"><h2>${t("% up to date over time")}</h2></div><div class="chart" id="ch-trend"></div><div id="lg-trend"></div></div>
      <div class="card"><div class="card-head"><h2>${t("Environment aging (libyears)")}</h2><span class="sub">${t("average per deployment · the higher, the older")}</span></div><div class="chart" id="ch-lib"></div><div id="lg-lib"></div></div>
    </div>
    ${d.eol.length ? `<div class="card" style="margin-top:16px">
      <div class="card-head"><h2>${t("End of life (EOL)")}</h2><span class="sub">${t("cycles in use without support or losing it within {n} days", { n: num(S.meta.eol?.warning_days) })} · <a href="#" id="eol-all">${t("see all")}</a></span></div>
      <div class="table-wrap"><table>
        <thead><tr><th>${t("Application")}</th><th>${t("Environment")}</th><th>${t("In use")}</th><th>${t("Cycle")}</th><th>${t("Status")}</th><th>${t("End of life")}</th></tr></thead>
        <tbody>${d.eol.map((s) => `<tr class="clickable" data-href="#/apps/${s.project_id}"><td><b>${esc(s.project)}</b></td><td>${esc(s.environment)}</td><td>${ver(s.current_version)}</td>
          <td>${esc(s.eol_product)} <b>${esc(s.eol_cycle)}</b>${s.eol_cycle_latest ? ` <span class="small muted">(${t("latest {v}", { v: esc(s.eol_cycle_latest) })})</span>` : ""}</td><td>${eolChip(s)}</td><td class="nowrap">${eolWhen(s)}</td></tr>`).join("")}</tbody>
      </table></div>
    </div>` : ""}
    <div class="card" style="margin-top:16px">
      <div class="card-head"><h2>${t("Most outdated")}</h2><a class="small" href="#/apps">${t("see all")}</a></div>
      <div class="table-wrap"><table>
        <thead><tr><th>${t("Application")}</th><th>${t("Environment")}</th><th>Status</th><th>${t("Current")}</th><th>${t("Available")}</th><th>${t("Outdated for")}</th><th class="num">${t("Versions behind")}</th><th class="num">Libyears</th></tr></thead>
        <tbody>${d.most_outdated.map((s) => {
          const w = o.max_outdated_days ? (s.outdated_days / o.max_outdated_days) * 100 : 0;
          return `<tr class="clickable" data-href="#/apps/${s.project_id}"><td><b>${esc(s.project)}</b></td><td>${esc(s.environment)}</td><td>${badge(s.status)}</td><td>${ver(s.current_version)}</td><td>${updatesCell(s)}</td>
          <td><div class="row" style="flex-wrap:nowrap"><div class="bar-track" style="flex:1"><i style="width:${w}%"></i></div><span class="small nowrap num">${days(s.outdated_days)}</span></div></td>
          <td class="num">${s.versions_behind}</td><td class="num">${s.libyears == null ? "—" : num(s.libyears, 2)}</td></tr>`;
        }).join("") || `<tr><td colspan="8" class="empty">${t("Nothing outdated 🎉")}</td></tr>`}</tbody>
      </table></div>
    </div>`;
  const kpiBtn = $("#kpi-toggle", view);
  kpiBtn.addEventListener("click", () => {
    const hide = !$("#kpis", view).hidden;
    $("#kpis", view).hidden = hide;
    kpiBtn.setAttribute("aria-expanded", String(!hide));
    try { localStorage.setItem("cw-kpis-hidden", hide ? "1" : "0"); } catch { /* ignore */ }
  });
  filter.bind(view);
  $("#ns-filter", view)?.addEventListener("change", (e) => { S.nsFilter = e.target.value; route(); });
  bindRowLinks(view);
  $("#eol-all", view)?.addEventListener("click", (e) => { e.preventDefault(); goTo({ status: "eol_any", env: S.envFilter }); });

  stackedBars($("#ch-status", view), d.environments.map((e) => ({ label: e.environment, envId: e.environment_id, values: e.counts })), STATUS_KEYS, statusColor, (k) => STATUS[k].label, (row, k) => goTo({ status: k, env: row.envId }));
  columnChart($("#ch-age", view), Object.entries(o.age_buckets).map(([label, value]) => ({ label, value, hint: t("Outdated deployments") })));

  const byEnv = {};
  hist.forEach((h) => { (byEnv[h.environment_id] ||= []).push(h); });
  const trend = Object.entries(byEnv).map(([id, pts]) => ({ name: envName(id), color: envColor(id), points: pts.map((p) => ({ x: p.day, y: p.up_to_date_ratio == null ? null : p.up_to_date_ratio * 100 })) }));
  const lib = Object.entries(byEnv).map(([id, pts]) => ({ name: envName(id), color: envColor(id), points: pts.map((p) => ({ x: p.day, y: p.libyears_avg })) }));
  lineChart($("#ch-trend", view), trend, { yMax: 100, yFmt: (v) => `${Math.round(v)}%`, label: t("Share up to date") });
  lineChart($("#ch-lib", view), lib, { yFmt: (v, tip) => (tip ? `${num(v, 2)} libyears` : num(v, 1)), label: t("Average libyears") });
  if (trend.length > 1) { $("#lg-trend", view).innerHTML = legend(trend.map((s) => ({ label: s.name, color: s.color })), true); $("#lg-lib", view).innerHTML = legend(lib.map((s) => ({ label: s.name, color: s.color })), true); }
}

function bindRowLinks(root) {
  $$("tr[data-href]", root).forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("a,button,input,select,.err")) return;
    location.hash = tr.dataset.href;
  }));
}

// ---------------------------------------------------------- applications
async function viewApps(view) {
  const statuses = await api("/api/status");
  const byKey = {};
  statuses.forEach((s) => { byKey[`${s.project_id}:${s.environment_id}`] = s; });
  const allTags = [...new Set(S.projects.flatMap((p) => p.tags || []))].sort();
  const namespaces = [...new Set(statuses.map((s) => s.namespace).filter(Boolean))].sort();
  if (!namespaces.includes(S.nsFilter)) S.nsFilter = "";
  const st = viewApps.state ||= { q: "", status: "", tag: "", env: "" };
  if (st.env && !S.envs.some((e) => String(e.id) === st.env)) st.env = "";
  view.innerHTML = `
    <div class="filters">
      <input type="search" id="f-q" placeholder="${t("Search application…")}" value="${esc(st.q)}">
      <select id="f-status"><option value="">${t("All statuses")}</option>${STATUS_KEYS.map((k) => opt(k, STATUS[k].label, st.status)).join("")}<option value="outdated" ${st.status === "outdated" ? "selected" : ""}>${t("Any update")}</option>${opt("has_patch", t("Any bugfix pending"), st.status)}${opt("has_minor", t("Any minor pending"), st.status)}${opt("has_major", t("Any major pending"), st.status)}${opt("eol_any", t("End of life soon or reached"), st.status)}${opt("eol", t("End of life reached"), st.status)}</select>
      ${S.envs.length > 1 ? `<select id="f-env" aria-label="${t("Environment")}"><option value="">${t("All environments")}</option>${S.envs.map((e) => opt(e.id, e.name, st.env)).join("")}</select>` : ""}
      ${allTags.length ? `<select id="f-tag"><option value="">${t("All tags")}</option>${allTags.map((t) => opt(t, t, st.tag)).join("")}</select>` : ""}
      ${namespaces.length ? `<select id="f-ns" aria-label="Namespace"><option value="">${t("All namespaces")}</option>${namespaces.map((n) => opt(n, n, S.nsFilter)).join("")}</select>` : ""}
      <div class="spacer" style="flex:1"></div>
      <button class="btn primary" id="new-app" type="button">${t("+ New application")}</button>
    </div>
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>${t("Application")}</th><th>Namespace</th>${S.envs.map((e) => `<th>${esc(e.name)}</th>`).join("")}<th>${t("Latest")}</th><th></th></tr></thead>
      <tbody id="apps-body"></tbody>
    </table></div></div>`;
  const body = $("#apps-body", view);
  const draw = () => {
    const rows = S.projects.filter((p) => {
      if (st.q && !`${p.name} ${p.repository} ${(p.tags || []).join(" ")}`.toLowerCase().includes(st.q.toLowerCase())) return false;
      if (st.tag && !(p.tags || []).includes(st.tag)) return false;
      if (S.nsFilter && !statuses.some((s) => s.project_id === p.id && s.namespace === S.nsFilter)) return false;
      const ss = S.envs.filter((e) => !st.env || String(e.id) === st.env).map((e) => byKey[`${p.id}:${e.id}`]).filter(Boolean);
      if (st.env && !ss.length) return false;
      if (st.status === "outdated") return ss.some((s) => ["patch", "minor", "major"].includes(s.status));
      if (st.status === "eol_any") return ss.some((s) => s.eol_status === "eol" || s.eol_status === "soon");
      if (st.status === "eol") return ss.some((s) => s.eol_status === "eol");
      if (st.status.startsWith("has_")) return ss.some((s) => s[`latest_${st.status.slice(4)}`]);
      if (st.status) return ss.some((s) => s.status === st.status);
      return true;
    });
    body.innerHTML = rows.map((p) => {
      const any = S.envs.map((e) => byKey[`${p.id}:${e.id}`]).find(Boolean);
      const nss = [...new Set(S.envs.map((e) => byKey[`${p.id}:${e.id}`]?.namespace).filter(Boolean))];
      return `<tr class="clickable" data-href="#/apps/${p.id}">
        <td><div><b>${esc(p.name)}</b> ${p.enabled ? "" : '<span class="chip">${t("disabled")}</span>'}</div>
          <div class="small muted">${esc(S.meta.source_types[p.source_type] || p.source_type)} · ${esc(p.repository)}</div>
          ${p.last_error ? `<div class="err" data-err="${esc(p.last_error)}" title="${t("Click to see the error")}">⚠ ${esc(p.last_error.slice(0, 80))}</div>` : ""}
          ${(p.tags || []).length ? `<div class="pill-row" style="margin-top:4px">${p.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>` : ""}</td>
        <td>${nss.length ? nss.map((n) => `<span class="chip">${esc(n)}</span>`).join(" ") : '<span class="muted">—</span>'}</td>
        ${S.envs.map((e) => {
          const s = byKey[`${p.id}:${e.id}`];
          if (!s) return `<td class="muted small">${t("not deployed")}</td>`;
          return `<td><div class="cell-ver">${ver(s.current_version)} ${badge(s.status)} ${eolChip(s)} ${updatesCell(s)}
            ${s.detection_error ? `<span class="err" data-err="${esc(s.detection_error)}" title="${t("Click to see the error")}">${t("⚠ detection failed")}</span>` : ""}</div></td>`;
        }).join("")}
        <td>${ver(any?.latest_version)}<div class="small muted">${any?.latest_version_date ? fmtDate(any.latest_version_date) : ""}</div></td>
        <td class="nowrap right"><button class="btn sm" data-refresh="${p.id}" type="button" title="${t("Fetch releases")}">↻</button> <button class="btn sm" data-edit="${p.id}" type="button">${t("Edit")}</button></td>
      </tr>`;
    }).join("") || `<tr><td colspan="${S.envs.length + 4}" class="empty">${S.projects.length ? t("No application matches these filters.") : t("No application added yet.")}</td></tr>`;
    bindRowLinks(body);
    $$("[data-edit]", body).forEach((b) => b.addEventListener("click", () => projectForm(S.projects.find((p) => p.id === Number(b.dataset.edit)))));
    $$("[data-refresh]", body).forEach((b) => b.addEventListener("click", () => startRefresh([Number(b.dataset.refresh)])));
  };
  draw();
  $("#f-q", view).addEventListener("input", debounce((e) => { st.q = e.target.value; draw(); }, 150));
  $("#f-status", view).addEventListener("change", (e) => { st.status = e.target.value; draw(); });
  $("#f-ns", view)?.addEventListener("change", (e) => { S.nsFilter = e.target.value; draw(); });
  $("#f-env", view)?.addEventListener("change", (e) => { st.env = e.target.value; draw(); });
  $("#f-tag", view)?.addEventListener("change", (e) => { st.tag = e.target.value; draw(); });
  $("#new-app", view).addEventListener("click", () => projectForm());
}

/** ``p`` is an existing project, or (with isNew) a prefill such as a discovery suggestion.
 * ``hint`` ({image, chart}) feeds the source suggestion. */
function projectForm(p = null, isNew = !p, hint = {}) {
  if (isNew) p = { source_type: "github_releases", version_scheme: "semver", detection_mode: "none", workload_kind: "deployment", enabled: true, tags: [], deployments: [], ...p };
  const deps = Object.fromEntries((p.deployments || []).map((d) => [d.environment_id, d]));
  const st = S.meta.source_types;
  const body = `
    <fieldset><legend>${t("Application")}</legend><div class="form-grid">
      ${field(t("Name *"), inp("name", p.name, "required"))}
      ${field("Tags", inp("tags", (p.tags || []).join(", "), `placeholder="${t("infra, observability")}"`), t("comma separated"))}
      ${field(t("Description"), inp("description", p.description), "", "full")}
      <label class="check full"><input type="checkbox" name="enabled" ${p.enabled ? "checked" : ""}>${t(" Monitoring enabled")}</label>
    </div></fieldset>
    <fieldset><legend>${t("Changelog / releases source")}</legend><div class="form-grid">
      ${field(t("Source type"), `<select name="source_type">${Object.entries(st).map(([k, v]) => opt(k, v, p.source_type)).join("")}</select>`)}
      ${field(t("Repository / URL *"), inp("repository", p.repository, 'required placeholder="argoproj/argo-cd"'), `<span id="repo-hint"></span> · <a href="#" id="repo-suggest-btn">${t("suggest source")}</a>`)}
      <div class="full" id="repo-suggest" hidden></div>
      <div data-show="helm_index">${field(t("Chart name"), inp("chart_name", p.chart_name))}</div>
      <div data-show="helm_index"><label class="check" style="margin-top:22px"><input type="checkbox" name="use_app_version" ${p.use_app_version ? "checked" : ""}>${t(" Compare by appVersion")}</label></div>
      <div data-show="github_releases github_tags gitlab_releases gitlab_tags">${field(t("API URL (optional)"), inp("source_url", p.source_url, 'placeholder="https://github.empresa.com/api/v3"'), t("GitHub Enterprise or self-hosted GitLab"))}</div>
      ${field(t("Tag pattern (regex)"), inp("tag_pattern", p.tag_pattern, 'placeholder="^v?(\\d+\\.\\d+\\.\\d+)$"'), t("optional – filters monorepo tags, e.g. <code>^chart-(.*)$</code>"))}
      ${field(t("CHANGELOG URL"), inp("changelog_url", p.changelog_url, 'placeholder="https://github.com/org/repo/blob/main/CHANGELOG.md"'), t("optional – fills in notes when the source has none"), "full")}
      ${field(t("Version scheme"), `<select name="version_scheme">${opt("semver", "SemVer – major.minor.patch", p.version_scheme)}${opt("major_patch", t("major.bugfix (e.g. PostgreSQL 16.2)"), p.version_scheme)}</select>`)}
      <label class="check"><input type="checkbox" name="include_prerelease" ${p.include_prerelease ? "checked" : ""}> ${t("Consider pre-releases (rc, beta…)")}</label>
    </div></fieldset>
    ${S.meta.eol?.enabled ? `<fieldset><legend>${t("End of life (EOL)")}</legend><div class="form-grid">
      ${field(t("Product on endoflife.date"), `${inp("eol_product", p.eol_product, `list="eol-products" autocomplete="off" placeholder="${esc(p.eol_data?.product && !p.eol_product ? t("automatic: {product}", { product: p.eol_data.product }) : (S.meta.eol.auto_match ? t("automatic by name/repository") : t("e.g. postgresql")))}"`)}<datalist id="eol-products"></datalist>`, `${t("e.g. <code>postgresql</code>, <code>kubernetes</code>, <code>redis</code> · empty = search by name · <code>none</code> disables")} · <a href="https://endoflife.date" target="_blank" rel="noopener">endoflife.date</a> <span id="eol-hint"></span>`, "full")}
    </div></fieldset>` : ""}
    <fieldset><legend>${t("Discover the version in use via Prometheus")}</legend><div class="form-grid">
      ${field(t("Detection mode"), `<select name="detection_mode">${Object.entries(S.meta.detection_modes).map(([k, v]) => opt(k, v, p.detection_mode)).join("")}</select>`, t("uses <code>kube_pod_container_info</code> from kube-state-metrics"), "full")}
      <div data-mode="workload">${field(t("Workload type"), `<select name="workload_kind">${["deployment", "statefulset", "daemonset", "cronjob"].map((k) => opt(k, k, p.workload_kind)).join("")}</select>`)}</div>
      <div data-mode="workload helm image query">${field("Namespace", inp("workload_namespace", p.workload_namespace))}</div>
      <div data-mode="workload helm query">${field(t("Workload / Helm release name"), inp("workload_name", p.workload_name), t("in Helm mode, every workload of the release is considered"))}</div>
      <div data-mode="workload helm image">${field("Container", inp("container_name", p.container_name), t("optional"))}</div>
      <div data-mode="helm image" class="full">${field(t("Image"), inp("image_pattern", p.image_pattern, 'placeholder="quay.io/argoproj/argocd"'), t("image repository or regex – in Helm mode (optional) picks which image of the release sets the version"))}</div>
      <div data-mode="query" class="full">${field(t("PromQL query"), `<textarea name="promql_query" placeholder='kube_pod_container_info{namespace="{namespace}", container="app"}'>${esc(p.promql_query || "")}</textarea>`, t("the series must have the <code>image</code> (or <code>version</code>) label; {namespace} and {name} are replaced"))}</div>
      <div data-mode="workload helm image query" class="full">${field(t("Version pattern in the image tag (regex)"), inp("image_version_pattern", p.image_version_pattern), t("optional, e.g. <code>^v?(\\d+\\.\\d+\\.\\d+)</code>"))}</div>
    </div></fieldset>
    <fieldset><legend>${t("Environments")}</legend>
      ${S.envs.length ? `<div class="table-wrap"><table><thead><tr><th>${t("Environment")}</th><th>${t("Deployed")}</th><th>${t("Version source")}</th><th>${t("Manual version")}</th><th>${t("Namespace (override)")}</th><th>${t("Workload (override)")}</th></tr></thead><tbody>
      ${S.envs.map((e) => {
        const d = deps[e.id];
        const src = d?.version_source || (p.detection_mode !== "none" && e.prometheus_url ? "prometheus" : "manual");
        return `<tr data-dep="${e.id}"><td><b>${esc(e.name)}</b>${e.prometheus_url ? "" : `<div class="small muted">${t("no Prometheus")}</div>`}</td>
          <td><input type="checkbox" data-skip="1" data-f="on" ${d || isNew ? "checked" : ""}></td>
          <td><select data-skip="1" data-f="version_source">${opt("manual", t("Manual"), src)}${opt("prometheus", "Prometheus", src)}</select></td>
          <td><input data-skip="1" data-f="manual_version" value="${esc(d?.manual_version || "")}" placeholder="1.2.3" style="width:110px"></td>
          <td><input data-skip="1" data-f="namespace_override" value="${esc(d?.namespace_override || "")}" style="width:140px"></td>
          <td><input data-skip="1" data-f="workload_name_override" value="${esc(d?.workload_name_override || "")}" style="width:140px"></td></tr>`;
      }).join("")}</tbody></table></div>` : `<p class="muted">${t("Add environments to set the versions in use.")}</p>`}
    </fieldset>`;
  const m = modal(isNew ? t("New application") : t("Edit {name}", { name: p.name }), body, {
    wide: true,
    onSubmit: async (form) => {
      const data = formData(form);
      data.tags = (data.tags || "").split(",").map((t) => t.trim()).filter(Boolean);
      data.deployments = {};
      $$("tr[data-dep]", form).forEach((tr) => {
        const get = (f) => $(`[data-f="${f}"]`, tr);
        if (!get("on").checked) return;
        data.deployments[tr.dataset.dep] = {
          version_source: get("version_source").value,
          manual_version: get("manual_version").value.trim() || null,
          namespace_override: get("namespace_override").value.trim() || null,
          workload_name_override: get("workload_name_override").value.trim() || null,
        };
      });
      const saved = isNew ? await api("/api/projects", { method: "POST", body: data }) : await api(`/api/projects/${p.id}`, { method: "PUT", body: data });
      toast(isNew ? t("Application created – fetching releases…") : t("Application updated"));
      await loadBase();
      setTimeout(() => pollRefresh(), 1500);
      if (isNew) location.hash = `#/apps/${saved.id}`; else route();
    },
  });
  const form = m.form;
  const hints = {
    github_releases: "owner/repo", github_tags: "owner/repo", gitlab_releases: "grupo/projeto", gitlab_tags: "grupo/projeto",
    helm_index: t("Helm repository URL, e.g. https://argoproj.github.io/argo-helm"), dockerhub: t("image, e.g. library/nginx or bitnami/redis"),
    changelog: t("URL of the CHANGELOG.md (raw or github.com/.../blob/...)"),
    webpage: t("URL of the page listing the versions – use the tag pattern to ignore other versions mentioned"),
  };
  const sync = () => {
    const src = $("[name=source_type]", form).value, mode = $("[name=detection_mode]", form).value;
    $$("[data-show]", form).forEach((el) => { el.style.display = el.dataset.show.split(" ").includes(src) ? "" : "none"; });
    $$("[data-mode]", form).forEach((el) => { el.style.display = el.dataset.mode.split(" ").includes(mode) ? "" : "none"; });
    $("#repo-hint", form).textContent = hints[src] || "";
  };
  $("[name=source_type]", form).addEventListener("change", sync);
  $("[name=detection_mode]", form).addEventListener("change", sync);
  sync();

  const box = $("#repo-suggest", form);
  const suggest = async () => {
    box.hidden = false;
    box.innerHTML = `<span class="small muted">${t("Looking for sources…")}</span>`;
    const q = new URLSearchParams({ name: $("[name=name]", form).value, image: hint.image || $("[name=image_pattern]", form).value, chart: hint.chart || "" });
    try {
      const { candidates } = await api(`/api/suggest-source?${q}`);
      box.innerHTML = candidates.length
        ? `<div class="small muted" style="margin-bottom:4px">${t("Likely sources – click to use:")}</div><div class="pill-row">${candidates.map((c, i) =>
          `<button type="button" class="btn sm" data-cand="${i}">${esc(st[c.source_type] || c.source_type)} · <b>${esc(c.repository)}</b>${c.chart_name ? ` (${esc(c.chart_name)})` : ""} <span class="muted">– ${esc(c.reason)}</span></button>`).join("")}</div>`
        : `<span class="small muted">${t("No source found – enter the repository manually.")}</span>`;
      $$("[data-cand]", box).forEach((b) => b.addEventListener("click", () => {
        const c = candidates[Number(b.dataset.cand)];
        $("[name=source_type]", form).value = c.source_type;
        $("[name=repository]", form).value = c.repository;
        $("[name=chart_name]", form).value = c.chart_name || "";
        $("[name=use_app_version]", form).checked = !!c.use_app_version;
        sync();
        box.hidden = true;
      }));
    } catch (err) {
      box.innerHTML = `<span class="small muted">${t("Suggestion failed: {error}", { error: esc(err.message) })}</span>`;
    }
  };
  $("#repo-suggest-btn", form).addEventListener("click", (e) => { e.preventDefault(); suggest(); });
  if (S.meta.eol?.enabled) {
    api(`/api/eol/products${isNew ? "" : `?project_id=${p.id}`}`).then((r) => {
      $("#eol-products", form).innerHTML = r.products.map((x) => `<option value="${esc(x)}">`).join("");
      if (r.error) $("#eol-hint", form).textContent = `· ${r.error}`;
      else if (r.suggested && !p.eol_product) $("#eol-hint", form).textContent = `· ${t("found automatically: {product}", { product: r.suggested })}`;
    }).catch(() => { /* autocomplete only */ });
  }
  if (isNew && !p.repository && (hint.image || hint.chart)) suggest();
}

// ------------------------------------------------------------ app detail
async function viewAppDetail(view, id) {
  const [p, statuses, releases] = await Promise.all([api(`/api/projects/${id}`), api("/api/status"), api(`/api/projects/${id}/releases?limit=200`)]);
  const mine = statuses.filter((s) => s.project_id === Number(id));
  $("#page-title").textContent = p.name;
  const url = repoUrl(p);
  view.innerHTML = `
    <div class="card">
      <div class="card-head"><div>
        <div class="row"><h2 style="margin:0">${esc(p.name)}</h2>${(p.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>
        <div class="small muted" style="margin-top:4px">${esc(S.meta.source_types[p.source_type] || p.source_type)} · ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(p.repository)}</a>` : esc(p.repository)} · ${t("{n} known versions", { n: p.release_count })} · ${t("checked {when}", { when: rel(p.last_checked_at) })}</div>
        ${p.description ? `<p style="margin:8px 0 0">${esc(p.description)}</p>` : ""}
        ${p.last_error ? `<div class="err" data-err="${esc(p.last_error)}" style="margin-top:6px">⚠ ${esc(p.last_error)}</div>` : ""}
        ${p.eol_error ? `<div class="err" data-err="${esc(p.eol_error)}" style="margin-top:6px">⚠ ${esc(p.eol_error)}</div>` : ""}
      </div>
      <div class="row"><button class="btn" id="d-refresh" type="button">${t("↻ Fetch releases")}</button><button class="btn" id="d-edit" type="button">${t("Edit")}</button><button class="btn danger" id="d-del" type="button">${t("Delete")}</button></div></div>
    </div>
    <div class="grid cols-${Math.min(Math.max(mine.length, 1), 3)}" style="margin-top:16px">
      ${mine.map((s) => `<div class="card">
        <div class="card-head"><h2>${esc(s.environment)}</h2>${badge(s.status, STATUS[s.status].label)}</div>
        <div class="row" style="align-items:baseline"><span class="small muted">${t("In use")}</span><span class="ver" style="font-size:20px;font-weight:600">${esc(s.current_version || "—")}</span><span class="small muted">${s.current_release_date ? t("published on {date}", { date: fmtDate(s.current_release_date) }) : ""}</span></div>
        <div class="small muted" style="margin:6px 0 10px">${t("Source:")} ${s.version_source === "prometheus" ? `Prometheus${s.detected_at ? ` (${rel(s.detected_at)})` : ""}` : t("manual")}${s.detected_image ? `<br><span class="mono">${esc(s.detected_image)}</span>` : ""}</div>
        ${s.detection_error ? `<div class="err" data-err="${esc(s.detection_error)}" style="margin-bottom:8px">⚠ ${esc(s.detection_error)}</div>` : ""}
        <table><tbody>
          <tr><td class="muted">Bugfix</td><td>${s.latest_patch ? `${s.latest_patch_url ? `<a href="${esc(s.latest_patch_url)}" target="_blank" rel="noopener">${esc(s.latest_patch)}</a>` : esc(s.latest_patch)} <span class="small muted">(${t("{n} behind", { n: s.patch_behind })})</span>` : "—"}</td></tr>
          <tr><td class="muted">Minor</td><td>${s.latest_minor ? `${s.latest_minor_url ? `<a href="${esc(s.latest_minor_url)}" target="_blank" rel="noopener">${esc(s.latest_minor)}</a>` : esc(s.latest_minor)} <span class="small muted">(${t("{n} behind", { n: s.minor_behind })})</span>` : "—"}</td></tr>
          <tr><td class="muted">Major</td><td>${s.latest_major ? `${s.latest_major_url ? `<a href="${esc(s.latest_major_url)}" target="_blank" rel="noopener">${esc(s.latest_major)}</a>` : esc(s.latest_major)} <span class="small muted">(${t("{n} behind", { n: s.major_behind })})</span>` : "—"}</td></tr>
          <tr><td class="muted">${t("Outdated for")}</td><td>${s.outdated_since ? `${days(s.outdated_days)} <span class="small muted">(desde ${fmtDate(s.outdated_since)})</span>` : "—"}</td></tr>
          <tr><td class="muted">Libyears</td><td class="num">${s.libyears == null ? "—" : num(s.libyears, 2)}</td></tr>
          ${s.eol_product ? `<tr><td class="muted">${t("End of life")}</td><td>${s.eol_cycle ? `${esc(s.eol_product)} <b>${esc(s.eol_cycle)}</b> · ${eolWhen(s)} ${eolChip(s)}` : `<span class="muted">${t("cycle of {version} not found in {product}", { version: esc(s.current_version || '?'), product: esc(s.eol_product) })}</span>`}</td></tr>` : ""}
        </tbody></table>
        <div class="row" style="margin-top:10px">
          ${s.versions_behind ? `<button class="btn sm" data-notes="${s.environment_id}" type="button">${t("See pending changelog ({n})", { n: s.versions_behind })}</button>` : ""}
          ${p.detection_mode !== "none" ? `<button class="btn sm" data-detect="${s.environment_id}" type="button">${t("Test detection")}</button>` : ""}
        </div>
        <div data-detect-out="${s.environment_id}" class="small" style="margin-top:8px"></div>
      </div>`).join("") || `<div class="card empty">${t("This application is not deployed in any environment. Edit it to set where it runs.")}</div>`}
    </div>
    <div class="card" style="margin-top:16px">
      <div class="card-head"><h2 id="notes-title">${t("Published versions")}</h2><span class="sub" id="notes-sub">${t("{n} versions", { n: releases.length })}</span></div>
      <div id="notes"></div>
    </div>`;
  const renderReleases = (list, current) => {
    $("#notes", view).innerHTML = list.map((r, i) => `<div class="release">
      <div class="head"><b class="ver">${esc(r.tag)}</b>${r.title && r.title !== r.tag ? `<span>${esc(r.title)}</span>` : ""}${r.prerelease ? '<span class="chip">pre-release</span>' : ""}
      ${current && r.version === current ? `<span class="chip">${t("in use")}</span>` : ""}<span class="small muted">${fmtDate(r.published_at)}</span>${r.url ? `<a class="small" href="${esc(r.url)}" target="_blank" rel="noopener">${t("open ↗")}</a>` : ""}</div>
      ${r.notes ? `<details ${i < 3 ? "open" : ""}><summary>${t("Release notes")}</summary><div class="notes">${md(r.notes)}</div></details>` : ""}
    </div>`).join("") || `<div class="empty">${t("No version found yet. Click “Fetch releases”.")}</div>`;
  };
  renderReleases(releases.slice(0, 50));
  $("#d-refresh", view).addEventListener("click", () => startRefresh([p.id]));
  $("#d-edit", view).addEventListener("click", () => projectForm(p));
  $("#d-del", view).addEventListener("click", async () => {
    if (!confirm(t("Delete {name}? Its release history is deleted too.", { name: p.name }))) return;
    await api(`/api/projects/${p.id}`, { method: "DELETE" });
    toast(t("Application deleted"));
    await loadBase();
    location.hash = "#/apps";
  });
  $$("[data-notes]", view).forEach((b) => b.addEventListener("click", async () => {
    const s = mine.find((x) => x.environment_id === Number(b.dataset.notes));
    const list = await api(`/api/projects/${p.id}/changelog?from=${encodeURIComponent(s.current_version)}`);
    $("#notes-title", view).textContent = t("Pending changelog in {env}", { env: s.environment });
    $("#notes-sub", view).innerHTML = `${esc(s.current_version)} → ${esc(s.latest_version)} · ${t("{n} versions", { n: list.length })} · <a href="#" id="show-all">${t("see all")}</a>`;
    renderReleases(list, s.current_version);
    $("#show-all", view).addEventListener("click", (e) => { e.preventDefault(); $("#notes-title", view).textContent = t("Published versions"); $("#notes-sub", view).textContent = t("{n} versions", { n: releases.length }); renderReleases(releases.slice(0, 50)); });
    $("#notes", view).scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  $$("[data-detect]", view).forEach((b) => b.addEventListener("click", async () => {
    const out = $(`[data-detect-out="${b.dataset.detect}"]`, view);
    out.textContent = t("Querying Prometheus…");
    const r = await api(`/api/projects/${p.id}/detect?environment_id=${b.dataset.detect}`, { method: "POST" });
    out.innerHTML = r.ok
      ? `✅ ${t("Version")} <b class="ver">${esc(r.version)}</b> (${esc(r.image)})${Object.keys(r.images).length > 1 ? `<div>${Object.entries(r.images).map(([img, n]) => `<div class="mono">${n}× ${esc(img)}</div>`).join("")}</div>` : "<br>"}<span class="mono muted">${esc(r.query)}</span>`
      : `<span class="err">⚠ ${esc(r.error)}</span>${r.query ? `<br><span class="mono muted">${esc(r.query)}</span>` : ""}`;
  }));
}

// --------------------------------------------------------------- compare
async function viewCompare(view) {
  if (S.envs.length < 2) { view.innerHTML = `<div class="card empty">${t("Add at least two environments to compare.")}</div>`; return; }
  const st = viewCompare.state ||= { source: S.envs[S.envs.length - 1].id, target: S.envs[0].id, only: true };
  const [cmp, mat] = await Promise.all([
    api(`/api/compare?source=${st.source}&target=${st.target}&only_differences=${st.only}`),
    api("/api/matrix"),
  ]);
  const sName = envName(st.source), tName = envName(st.target);
  const relText = (r) => {
    if (r.relation === "source_ahead") return `<span class="diff-ahead">${t("{env} ahead", { env: esc(sName) })}</span> ${t("by {n} version(s)", { n: r.releases_between })}`;
    if (r.relation === "target_ahead") return `<span class="diff-behind">${t("{env} behind", { env: esc(sName) })}</span> ${t("by {n} version(s)", { n: r.releases_between })}`;
    if (r.relation === "only_source") return `<span class="muted">${t("only in {env}", { env: esc(sName) })}</span>`;
    if (r.relation === "only_target") return `<span class="muted">${t("only in {env}", { env: esc(tName) })}</span>`;
    return esc(RELATION[r.relation]);
  };
  const drift = mat.rows.filter((r) => r.drift);
  view.innerHTML = `
    <div class="card">
      <div class="filters" style="margin:0">
        ${field(t("Source"), `<select id="c-src">${S.envs.map((e) => opt(e.id, e.name, st.source)).join("")}</select>`)}
        <button class="btn ghost" id="c-swap" type="button" title="${t("Swap")}" style="margin-top:18px">⇄</button>
        ${field(t("Target"), `<select id="c-tgt">${S.envs.map((e) => opt(e.id, e.name, st.target)).join("")}</select>`)}
        <label class="check" style="margin-top:18px"><input type="checkbox" id="c-only" ${st.only ? "checked" : ""}>${t(" Differences only")}</label>
        <div style="flex:1"></div>
        <div class="pill-row" style="margin-top:18px">${Object.entries(cmp.counts).map(([k, v]) => `<span class="chip">${esc(RELATION[k])}: ${v}</span>`).join("")}</div>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h2>${esc(sName)} × ${esc(tName)}</h2></div>
      <div class="table-wrap"><table>
        <thead><tr><th>${t("Application")}</th><th>${esc(sName)}</th><th>${esc(tName)}</th><th>${t("Difference")}</th><th>${t("Latest")}</th><th>${t("Versions between the environments")}</th></tr></thead>
        <tbody>${cmp.rows.map((r) => `<tr><td><a href="#/apps/${r.project_id}"><b>${esc(r.project)}</b></a></td><td>${ver(r.source_version)}</td><td>${ver(r.target_version)}</td><td>${relText(r)}</td><td>${ver(r.latest_version)}</td>
          <td>${r.releases.length ? `<details><summary>${t("{n} version(s)", { n: r.releases.length })}</summary><div class="small">${r.releases.map((x) => `<div>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.tag)}</a>` : esc(x.tag)} <span class="muted">${fmtDate(x.published_at)}</span></div>`).join("")}</div></details>` : '<span class="muted">—</span>'}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">${t("The environments run the same versions 🎉")}</td></tr>`}</tbody>
      </table></div>
    </div>
    <div class="card">
      <div class="card-head"><h2>${t("Version matrix")}</h2><span class="sub">${t("{n} applications with different versions across environments", { n: drift.length })}</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th>${t("Application")}</th>${mat.environments.map((e) => `<th>${esc(e.name)}</th>`).join("")}<th>${t("Latest")}</th></tr></thead>
        <tbody>${mat.rows.map((r) => `<tr><td><a href="#/apps/${r.project_id}">${esc(r.project)}</a> ${r.drift ? `<span class="chip">${t("divergent")}</span>` : ""}</td>
          ${mat.environments.map((e) => { const c = r.environments[e.id]; return `<td>${c ? `${ver(c.version)} ${badge(c.status)}` : '<span class="muted small">—</span>'}</td>`; }).join("")}
          <td>${ver(r.latest_version)}</td></tr>`).join("")}</tbody>
      </table></div>
    </div>`;
  $("#c-src", view).addEventListener("change", (e) => { st.source = Number(e.target.value); route(); });
  $("#c-tgt", view).addEventListener("change", (e) => { st.target = Number(e.target.value); route(); });
  $("#c-only", view).addEventListener("change", (e) => { st.only = e.target.checked; route(); });
  $("#c-swap", view).addEventListener("click", () => { [st.source, st.target] = [st.target, st.source]; route(); });
}

// -------------------------------------------------------------- releases
async function viewReleases(view) {
  const st = viewReleases.state ||= { days: 7 };
  // only what is new for the deployed versions: hides "already in use" and older lines
  const list = (await api(`/api/releases/recent?days=${st.days}`)).filter((r) => r.level !== "up_to_date" && r.level !== "older");
  view.innerHTML = `
    <div class="filters"><div class="seg">${[7, 14, 30, 90].map((d) => `<button type="button" data-days="${d}" class="${st.days === d ? "on" : ""}">${t("{n} days", { n: d })}</button>`).join("")}</div>
      <span class="muted">${t("{n} versions pending update", { n: list.length })}</span></div>
    <div class="card">${list.map((r) => `<div class="release">
      <div class="head"><a href="#/apps/${r.project_id}"><b>${esc(r.project)}</b></a><span class="ver">${esc(r.tag)}</span>${r.level === "older" ? badge("unknown", t("Previous line")) : badge(r.level, r.level === "up_to_date" ? t("Already in use") : r.level === "unknown" ? t("No version in use") : t("{level} for the environment", { level: STATUS[r.level].short }))}
      <span class="small muted">${fmtDate(r.published_at)}</span>${r.url ? `<a class="small" href="${esc(r.url)}" target="_blank" rel="noopener">${t("open ↗")}</a>` : ""}</div>
      <div class="small muted">${Object.entries(r.deployed).map(([env, v]) => `${esc(env)}: <span class="ver">${esc(v || "?")}</span>`).join(" · ") || t("not deployed")}</div>
      ${r.notes ? `<details><summary>${t("Release notes")}</summary><div class="notes">${md(r.notes)}</div></details>` : ""}
    </div>`).join("") || `<div class="empty">${t("No new pending version in the period.")}</div>`}</div>`;
  $$("[data-days]", view).forEach((b) => b.addEventListener("click", () => { st.days = Number(b.dataset.days); route(); }));
}

// --------------------------------------------------------------- reports
async function viewReports(view, id) {
  const reports = await api("/api/reports");
  const current = id ? await api(`/api/reports/${id}`) : reports[0] ? await api(`/api/reports/${reports[0].id}`) : null;
  view.innerHTML = `
    <div class="filters">
      <button class="btn" id="r-gen" type="button">${t("Generate preview")}</button>
      <button class="btn primary" id="r-send" type="button">${t("Generate and send to webhooks")}</button>
      <span class="muted small">${t("Automatic delivery:")} <code>${esc(S.meta.schedule.report_cron)}</code> (${esc(S.meta.schedule.timezone)})${S.meta.schedule.next_runs?.weekly_report ? ` · ${t("next {when}", { when: fmtDate(S.meta.schedule.next_runs.weekly_report, true) })}` : ""}</span>
    </div>
    <div class="grid" style="grid-template-columns:minmax(220px,300px) minmax(0,1fr)" id="r-grid">
      <div class="card"><h3>${t("History")}</h3>${reports.map((r) => `<a href="#/reports/${r.id}" style="display:block;padding:8px;border-radius:8px;${current && current.id === r.id ? "background:var(--accent-wash)" : ""}">
        <b>${fmtDate(r.created_at, true)}</b> <span class="chip">${r.trigger === "schedule" ? t("scheduled") : t("manual")}</span>
        <div class="small muted">${t("{n} new versions", { n: r.new_releases })} · ${t("{n} outdated", { n: r.outdated })} · ${r.delivery.length ? t("{ok}/{total} deliveries ok", { ok: r.delivery.filter((d) => d.ok).length, total: r.delivery.length }) : t("not sent")}</div></a>`).join("") || `<div class="muted">${t("No report generated.")}</div>`}</div>
      <div class="card">${current ? `
        <div class="card-head"><h2>${t("Report #{id}", { id: current.id })}</h2><div class="row">
          <a class="btn sm" href="/api/reports/${current.id}/markdown">${t("Download .md")}</a>
          <button class="btn sm" id="r-resend" type="button">${t("Send")}</button>
          <button class="btn sm danger" id="r-del" type="button">${t("Delete")}</button></div></div>
        ${current.delivery.length ? `<div class="pill-row" style="margin-bottom:10px">${current.delivery.map((d) => `<span class="chip" title="${esc(d.detail || "")}">${d.ok ? "✅" : "❌"} ${esc(d.webhook)}${d.skipped ? t(" (skipped)") : ""}</span>`).join("")}</div>` : ""}
        ${md(current.markdown)}` : `<div class="empty">${t("Generate the first report.")}</div>`}</div>
    </div>`;
  if (window.innerWidth < 860) $("#r-grid", view).style.gridTemplateColumns = "1fr";
  const gen = async (send) => {
    const r = await api("/api/reports", { method: "POST", body: { send } });
    if (send) {
      const ok = r.delivery.filter((d) => d.ok).length;
      toast(r.delivery.length ? t("Sent to {ok}/{total} webhook(s)", { ok, total: r.delivery.length }) : t("No active webhook configured"), ok < r.delivery.length || !r.delivery.length);
    }
    location.hash = `#/reports/${r.id}`;
  };
  $("#r-gen", view).addEventListener("click", () => gen(false).catch((e) => toast(e.message, true)));
  $("#r-send", view).addEventListener("click", () => gen(true).catch((e) => toast(e.message, true)));
  $("#r-resend", view)?.addEventListener("click", async () => {
    const r = await api(`/api/reports/${current.id}/send`, { method: "POST" });
    toast(r.delivery.length ? t("Sent: {ok}/{total}", { ok: r.delivery.filter((d) => d.ok).length, total: r.delivery.length }) : t("No active webhook"), !r.delivery.length);
    route();
  });
  $("#r-del", view)?.addEventListener("click", async () => {
    if (!confirm(t("Delete this report?"))) return;
    await api(`/api/reports/${current.id}`, { method: "DELETE" });
    location.hash = "#/reports";
  });
}

// ---------------------------------------------------------- environments
async function viewEnvironments(view) {
  view.innerHTML = `
    <div class="filters"><div style="flex:1"></div><button class="btn primary" id="e-new" type="button">${t("+ New environment")}</button></div>
    <div class="grid cols-3">${S.envs.map((e, i) => `<div class="card">
      <div class="card-head"><div class="row"><i style="width:10px;height:10px;border-radius:3px;background:${envColor(e.id)};display:inline-block"></i><h2 style="margin:0">${esc(e.name)}</h2></div><span class="sub">${t("order {n}", { n: e.sort_order })}</span></div>
      ${e.description ? `<p class="muted" style="margin-top:0">${esc(e.description)}</p>` : ""}
      <div class="small"><span class="muted">Prometheus:</span> ${e.prometheus_url ? `<span class="mono">${esc(e.prometheus_url)}</span>` : `<span class="muted">${t("not configured (manual versions only)")}</span>`}</div>
      ${e.prometheus_url ? `<div class="small"><span class="muted">${t("Authentication:")}</span> ${esc(e.prometheus_auth)}${e.prometheus_extra_selector ? ` · <span class="muted">${t("selector:")}</span> <span class="mono">${esc(e.prometheus_extra_selector)}</span>` : ""}</div>` : ""}
      <div class="row" style="margin-top:12px">
        ${e.prometheus_url ? `<button class="btn sm" data-test="${e.id}" type="button">${t("Test connection")}</button>` : ""}
        <button class="btn sm" data-edit="${e.id}" type="button">${t("Edit")}</button>
        <button class="btn sm danger" data-del="${e.id}" type="button">${t("Delete")}</button>
      </div>
      <div class="small" data-out="${e.id}" style="margin-top:8px"></div>
    </div>`).join("") || `<div class="card empty">${t("No environments. Create, for example, “development” and “production”.")}</div>`}</div>`;
  $("#e-new", view).addEventListener("click", () => envForm());
  $$("[data-edit]", view).forEach((b) => b.addEventListener("click", () => envForm(S.envs.find((e) => e.id === Number(b.dataset.edit)))));
  $$("[data-del]", view).forEach((b) => b.addEventListener("click", async () => {
    const e = S.envs.find((x) => x.id === Number(b.dataset.del));
    if (!confirm(t("Delete the environment {name}? The versions recorded in it are deleted.", { name: e.name }))) return;
    await api(`/api/environments/${e.id}`, { method: "DELETE" });
    await loadBase(); route();
  }));
  $$("[data-test]", view).forEach((b) => b.addEventListener("click", async () => {
    const out = $(`[data-out="${b.dataset.test}"]`, view);
    out.textContent = t("Testing…");
    const r = await api(`/api/environments/${b.dataset.test}/test`, { method: "POST" });
    out.innerHTML = r.ok ? `✅ ${t("Connected – {n} containers visible in <code>kube_pod_container_info</code>", { n: r.containers })}` : `<span class="err">⚠ ${esc(r.error)}</span>`;
  }));
}

function envForm(e = null) {
  const isNew = !e;
  e = e || { prometheus_auth: "none", prometheus_verify_tls: true, sort_order: S.envs.length };
  const body = `<div class="form-grid">
    ${field(t("Name *"), inp("name", e.name, "required"))}
    ${field(t("Display order"), inp("sort_order", e.sort_order, 'type="number"'))}
    ${field(t("Description"), inp("description", e.description), "", "full")}
    ${field(t("Prometheus URL"), inp("prometheus_url", e.prometheus_url, 'placeholder="http://prometheus-operated.monitoring:9090"'), t("accepts Thanos/Mimir/VictoriaMetrics (compatible API)"), "full")}
    ${field(t("Authentication"), `<select name="prometheus_auth">${opt("none", t("None"), e.prometheus_auth)}${opt("bearer", "Bearer token", e.prometheus_auth)}${opt("basic", t("Username and password"), e.prometheus_auth)}</select>`)}
    ${field(t("Extra selector"), inp("prometheus_extra_selector", e.prometheus_extra_selector, 'placeholder=\'cluster="prod"\''), t("added to every query"))}
    <div data-auth="bearer" class="full">${field("Token", inp("prometheus_token", e.prometheus_token, 'autocomplete="off"'), t("use <code>${VARIABLE}</code> to read it from an environment variable (Secret)"))}</div>
    <div data-auth="basic">${field(t("Username"), inp("prometheus_username", e.prometheus_username, 'autocomplete="off"'))}</div>
    <div data-auth="basic">${field(t("Password"), inp("prometheus_password", e.prometheus_password, 'type="password" autocomplete="new-password"'), t("or <code>${VARIABLE}</code>"))}</div>
    <label class="check full"><input type="checkbox" name="prometheus_verify_tls" ${e.prometheus_verify_tls ? "checked" : ""}> ${t("Verify TLS certificate")}</label>
  </div>`;
  const m = modal(isNew ? t("New environment") : t("Edit {name}", { name: e.name }), body, {
    onSubmit: async (form) => {
      const data = formData(form);
      data.sort_order = data.sort_order ?? 0;
      if (isNew) await api("/api/environments", { method: "POST", body: data });
      else await api(`/api/environments/${e.id}`, { method: "PUT", body: data });
      toast(t("Environment saved"));
      await loadBase(); route();
    },
  });
  const sync = () => { const a = $("[name=prometheus_auth]", m.form).value; $$("[data-auth]", m.form).forEach((el) => { el.style.display = el.dataset.auth === a ? "" : "none"; }); };
  $("[name=prometheus_auth]", m.form).addEventListener("change", sync);
  sync();
}

// -------------------------------------------------------------- webhooks
async function viewWebhooks(view) {
  const hooks = await api("/api/webhooks");
  view.innerHTML = `
    <div class="filters"><span class="muted">${t("The weekly report is sent to every active webhook.")}</span><div style="flex:1"></div><button class="btn primary" id="w-new" type="button">${t("+ New webhook")}</button></div>
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>${t("Name")}</th><th>${t("Type")}</th><th>URL</th><th>${t("Environments")}</th><th>${t("Last delivery")}</th><th></th></tr></thead>
      <tbody>${hooks.map((w) => `<tr><td><b>${esc(w.name)}</b> ${w.enabled ? "" : '<span class="chip">${t("disabled")}</span>'}${w.only_outdated ? ` <span class="chip">${t("only when something is pending")}</span>` : ""}</td>
        <td>${esc(S.meta.webhook_kinds[w.kind] || w.kind)}</td><td class="mono small">${esc(w.url)}</td>
        <td class="small">${w.environment_ids.length ? w.environment_ids.map(envName).map(esc).join(", ") : t("all")}</td>
        <td class="small">${w.last_sent_at ? `${fmtDate(w.last_sent_at, true)}<div class="muted">${esc(w.last_status || "")}</div>` : `<span class="muted">${t("never")}</span>`}</td>
        <td class="nowrap right"><button class="btn sm" data-test="${w.id}" type="button">${t("Test")}</button> <button class="btn sm" data-edit="${w.id}" type="button">${t("Edit")}</button> <button class="btn sm danger" data-del="${w.id}" type="button">${t("Delete")}</button></td></tr>`).join("") || `<tr><td colspan="6" class="empty">${t("No webhook configured.")}</td></tr>`}</tbody>
    </table></div></div>`;
  $("#w-new", view).addEventListener("click", () => hookForm());
  $$("[data-edit]", view).forEach((b) => b.addEventListener("click", () => hookForm(hooks.find((w) => w.id === Number(b.dataset.edit)))));
  $$("[data-del]", view).forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(t("Delete webhook?"))) return;
    await api(`/api/webhooks/${b.dataset.del}`, { method: "DELETE" }); route();
  }));
  $$("[data-test]", view).forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const r = await api(`/api/webhooks/${b.dataset.test}/test`, { method: "POST" });
      toast(r.ok ? t("Sent: {detail}", { detail: r.detail }) : t("Failed: {detail}", { detail: r.detail }), !r.ok);
    } finally { b.disabled = false; route(); }
  }));
}

function hookForm(w = null) {
  const isNew = !w;
  w = w || { kind: "slack", enabled: true, environment_ids: [] };
  const body = `<div class="form-grid">
    ${field(t("Name *"), inp("name", w.name, "required"))}
    ${field(t("Type"), `<select name="kind">${Object.entries(S.meta.webhook_kinds).map(([k, v]) => opt(k, v, w.kind)).join("")}</select>`)}
    ${field("URL *", inp("url", w.url, 'required autocomplete="off"'), t("the URL is hidden after saving; use <code>${VARIABLE}</code> to read it from an environment variable"), "full")}
    <div class="full"><div class="small muted" style="margin-bottom:6px">${t("Included environments (none checked = all)")}</div><div class="pill-row">${S.envs.map((e) => `<label class="check"><input type="checkbox" data-skip="1" data-env-id="${e.id}" ${w.environment_ids.includes(e.id) ? "checked" : ""}> ${esc(e.name)}</label>`).join("")}</div></div>
    <label class="check"><input type="checkbox" name="enabled" ${w.enabled ? "checked" : ""}>${t(" Enabled")}</label>
    <label class="check"><input type="checkbox" name="only_outdated" ${w.only_outdated ? "checked" : ""}>${t(" Send only when there are outdated applications")}</label>
  </div>`;
  modal(isNew ? t("New webhook") : t("Edit {name}", { name: w.name }), body, {
    onSubmit: async (form) => {
      const data = formData(form);
      data.environment_ids = $$("[data-env-id]", form).filter((c) => c.checked).map((c) => Number(c.dataset.envId));
      if (isNew) await api("/api/webhooks", { method: "POST", body: data });
      else await api(`/api/webhooks/${w.id}`, { method: "PUT", body: data });
      toast(t("Webhook saved")); route();
    },
  });
}

// -------------------------------------------------------------- discover
async function viewDiscover(view) {
  const envs = S.envs.filter((e) => e.prometheus_url);
  if (!envs.length) { view.innerHTML = `<div class="card empty">${t("Configure the Prometheus of an <a href=\"#/environments\">environment</a> to discover the applications of the cluster.")}</div>`; return; }
  const st = viewDiscover.state ||= { env: envs[0].id, namespace: "", result: null };
  view.innerHTML = `
    <form class="card filters" id="dc-form" style="margin-bottom:16px">
      ${field(t("Environment"), `<select id="dc-env">${envs.map((e) => opt(e.id, e.name, st.env)).join("")}</select>`)}
      ${field("Namespace", `<input id="dc-ns" value="${esc(st.namespace)}" placeholder="${t("empty = all")}">`)}
      <button class="btn primary" type="submit" style="margin-top:18px">${t("Discover")}</button>
      <button class="btn" type="button" id="dc-all" style="margin-top:18px" hidden>${t("Monitor all")}</button>
      <span class="small muted" style="margin-top:18px">${t("Groups the workloads by Helm release (Secrets <code>sh.helm.release.v1.*</code> and label <code>app.kubernetes.io/instance</code>).")}</span>
    </form>
    <div id="dc-out"></div>`;
  const out = $("#dc-out", view);
  const comps = () => st.result.groups.flatMap((g) => g.components.map((c) => ({ g, c })));
  const trackBtn = (c, key) => c.project
    ? `<a class="btn sm" href="#/apps/${c.project.id}">${t("Monitored: {name}", { name: esc(c.project.name) })}</a>`
    : `<button class="btn sm primary" type="button" data-track="${key}">${t("Monitor")}</button>`;
  const draw = () => {
    const r = st.result;
    if (!r) return;
    if (!r.ok) { out.innerHTML = `<div class="card err">⚠ ${esc(r.error)}</div>`; return; }
    const pending = comps().filter(({ c }) => !c.project);
    const allBtn = $("#dc-all", view);
    allBtn.hidden = !pending.length;
    allBtn.textContent = `${t("Monitor all")} (${pending.length})`;
    out.innerHTML = r.groups.map((g, i) => {
      const multi = g.components.length > 1;
      const tracked = g.components.filter((c) => c.project).length;
      return `<div class="card${g.parent ? " dc-child" : ""}" style="margin-bottom:12px">
      <div class="card-head"><div>
        <div class="row"><h2 style="margin:0">${esc(g.release || g.workloads[0].name)}</h2>
          ${g.release ? `<span class="chip">${g.helm ? `Helm${g.revision ? ` rev ${g.revision}` : ""}` : "release (label)"}</span>` : `<span class="chip">${t("no Helm")}</span>`}
          ${g.chart ? `<span class="chip">${esc(g.chart)}</span>` : ""}
          ${g.parent ? `<span class="chip" title="${t("Pods created by an operator or subchart of the Helm release {name}", { name: esc(g.parent) })}">↳ ${t("part of {name}", { name: esc(g.parent) })}</span>` : ""}
          <span class="small muted">${esc(g.namespace)}</span></div>
        <div class="small muted" style="margin-top:4px">${g.workloads.length} workload(s) · ${multi ? `${t("{n} images", { n: g.components.length })} · ${t("{n} monitored", { n: tracked })}` : `${t("version")} ${ver(g.version)} ${g.image ? `<span class="mono">${esc(g.image)}</span>` : ""}`}</div>
      </div>
      ${multi ? "" : trackBtn(g.components[0], `${i}:0`)}</div>
      ${multi ? `<div class="table-wrap"><table>
        <thead><tr><th>${t("Image")}</th><th>${t("Version")}</th><th>Containers</th><th></th></tr></thead>
        <tbody>${g.components.map((c, j) => `<tr><td class="mono small">${esc(c.repository || "?")}</td><td>${ver(c.version)}</td>
          <td class="small">${c.containers.map((x) => `<div><span class="muted">${esc(x.workload)}</span> ${esc(x.container)}</div>`).join("")}</td>
          <td class="nowrap right">${trackBtn(c, `${i}:${j}`)}</td></tr>`).join("")}</tbody>
      </table></div>` : ""}
      <details><summary class="small">${t("Workloads and images")}</summary><div class="table-wrap"><table><tbody>
        ${g.workloads.map((w) => `<tr><td class="nowrap"><span class="muted small">${esc(w.kind)}</span> <b>${esc(w.name)}</b>${w.chart && w.chart !== g.chart ? ` <span class="chip" title="subchart">${esc(w.chart)}</span>` : ""}</td><td>${w.containers.map((c) => `<div class="small"><span class="muted">${esc(c.container)}</span> <span class="mono">${esc(c.image)}</span></div>`).join("")}</td></tr>`).join("")}
      </tbody></table></div></details>
    </div>`;
    }).join("") || `<div class="card empty">${t("No container found.")}</div>`;
    $$("[data-track]", out).forEach((b) => b.addEventListener("click", () => {
      const [i, j] = b.dataset.track.split(":").map(Number);
      const c = r.groups[i].components[j];
      projectForm(c.suggestion, true, { image: c.image, chart: c.chart });
    }));
  };
  $("#dc-all", view).addEventListener("click", async (e) => {
    const todo = comps().filter(({ c }) => !c.project);
    if (!confirm(t("Monitor {n} image(s)? Each image becomes an application. Without a known repository, the Docker Hub image is used – adjust it later in Applications.", { n: todo.length }))) return;
    e.target.disabled = true;
    let ok = 0; const fails = [];
    for (const { c } of todo) {
      const sg = c.suggestion;
      const image = (c.image || "").split("@")[0].replace(/:[^/:]*$/, "");
      const repository = sg.repository || image;
      if (!repository) { fails.push(`${sg.name}: ${t("no image/repository")}`); continue; }
      try {
        await api("/api/projects?refresh=false", { method: "POST", body: { ...sg, repository, source_type: sg.repository ? "github_releases" : "dockerhub",
          deployments: { [st.env]: { version_source: "prometheus" } } } });
        ok++;
      } catch (err) { fails.push(`${sg.name}: ${err.message}`); }
    }
    e.target.disabled = false;
    toast(t("{n} application(s) added", { n: ok }) + (fails.length ? t(", {n} failure(s)", { n: fails.length }) : ""), fails.length > 0, () => showError(fails.join("\n"), t("Failed to add")));
    await loadBase();
    if (ok) startRefresh();
    $("#dc-form", view).requestSubmit();
  });
  $("#dc-form", view).addEventListener("submit", async (e) => {
    e.preventDefault();
    st.env = Number($("#dc-env", view).value);
    st.namespace = $("#dc-ns", view).value.trim();
    out.innerHTML = `<div class="card muted">${t("Querying Prometheus…")}</div>`;
    try {
      st.result = await api(`/api/environments/${st.env}/discover?namespace=${encodeURIComponent(st.namespace)}`);
    } catch (err) { st.result = { ok: false, error: err.message }; }
    draw();
  });
  draw();
}

// -------------------------------------------------------------- settings
async function viewSettings(view) {
  const c = await api("/api/settings");
  view.innerHTML = `<form class="card" id="st-form" style="max-width:640px">
    <div class="card-head"><h2>${t("Schedule")}</h2></div>
    <div class="form-grid">
      ${field(t("Report schedule (cron)"), inp("report_cron", c.report_cron, "required"), `${t("minute hour day month weekday · time zone <code>{tz}</code> · e.g. <code>0 9 * * mon</code> = Mondays at 9am", { tz: esc(c.timezone) })}${c.next_runs?.weekly_report ? ` · ${t("next delivery {when}", { when: fmtDate(c.next_runs.weekly_report, true) })}` : ""}`, "full")}
      ${field(t("Version refresh (minutes)"), inp("refresh_interval_minutes", c.refresh_interval_minutes, 'type="number" min="0"'), `${t("0 disables the automatic refresh")}${c.next_runs?.refresh ? ` · ${t("next refresh {when}", { when: fmtDate(c.next_runs.refresh, true) })}` : ""}`, "full")}
    </div>
    <div class="card-head" style="margin-top:18px"><h2>${t("Language")}</h2></div>
    <div class="form-grid">
      ${field(t("Interface, reports and webhook language"), `<select name="language">${Object.entries(c.languages).map(([k, v]) => opt(k, v, c.language)).join("")}</select>`, t("default: <code>APP_LANGUAGE</code> (Helm: <code>config.language</code>)"), "full")}
    </div>
    <div class="row" style="margin-top:14px"><button class="btn primary" type="submit">${t("Save")}</button></div>
  </form>`;
  $("#st-form", view).addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/settings", { method: "PUT", body: formData(e.target) });
      toast(t("Settings saved"));
      await loadBase();
      route();
    } catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ logs
async function viewLogs(view) {
  const st = viewLogs.state ||= { level: "", q: "" };
  view.innerHTML = `<form class="filters" id="lg-form">
    <select id="lg-level">${[["", t("All levels")], ["ERROR", t("Error")], ["WARNING", t("Warning")], ["INFO", "Info"]].map(([v, l]) => opt(v, l, st.level)).join("")}</select>
    <input type="search" id="lg-q" placeholder="${t("Search the message")}" value="${esc(st.q)}">
    <button class="btn" type="submit">${t("Filter")}</button>
  </form><div id="lg-out"></div>`;
  const load = async () => {
    const rows = await api(`/api/logs?limit=500&level=${st.level}&q=${encodeURIComponent(st.q)}`);
    $("#lg-out", view).innerHTML = `<div class="card"><div class="table-wrap"><table>
      <thead><tr><th>${t("When")}</th><th>${t("Level")}</th><th>${t("Origin")}</th><th>${t("Message")}</th></tr></thead><tbody>${rows.map((r) => `<tr>
        <td class="nowrap small">${fmtDate(r.created_at, true)}</td><td><span class="lvl-${esc(r.level)}">${esc(r.level)}</span></td>
        <td class="small muted">${esc(r.logger.replace("changelog_watcher.", ""))}</td>
        <td>${esc(r.message)}${r.detail ? `<details><summary class="small">traceback</summary><pre class="log">${esc(r.detail)}</pre></details>` : ""}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">${t("No logs.")}</td></tr>`}</tbody></table></div></div>`;
  };
  $("#lg-form", view).addEventListener("submit", (e) => { e.preventDefault(); st.level = $("#lg-level", view).value; st.q = $("#lg-q", view).value.trim(); load(); });
  await load();
}

// ---------------------------------------------------------------- router
const ROUTES = {  // titles are translated in route()
  dashboard: ["Dashboard", viewDashboard],
  apps: ["Applications", viewApps],
  discover: ["Discover in the cluster", viewDiscover],
  compare: ["Compare environments", viewCompare],
  releases: ["New versions", viewReleases],
  reports: ["Reports", viewReports],
  environments: ["Environments", viewEnvironments],
  webhooks: ["Webhooks", viewWebhooks],
  settings: ["Settings", viewSettings],
  logs: ["Logs", viewLogs],
};

async function route() {
  const [name = "dashboard", id] = location.hash.replace(/^#\/?/, "").split("/");
  const key = ROUTES[name] ? name : "dashboard";
  const [title, fn] = ROUTES[key];
  $$("#nav a, #nav-foot a").forEach((a) => a.classList.toggle("active", a.dataset.route === key));
  $("#page-title").textContent = t(title);
  $("#sidebar").classList.remove("open");
  S.charts = [];
  const view = $("#view");
  try {
    if (key === "apps" && id) await viewAppDetail(view, id);
    else await fn(view, id);
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="err">${t("Failed to load: {error}", { error: esc(e.message) })}</div></div>`;
  }
}

/** Translate the static shell (index.html): [data-i18n] text and [data-i18n-title] / [data-i18n-label] attributes. */
function applyStatic() {
  document.documentElement.lang = S.lang === "pt-BR" ? "pt-BR" : "en";
  $$("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $$("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
  $$("[data-i18n-label]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.i18nLabel)); });
  renderRefreshInfo();
}

function initTheme() {
  const root = document.documentElement, box = $("#theme-toggle");
  box.checked = root.dataset.theme === "dark";
  box.addEventListener("change", () => {
    root.dataset.theme = box.checked ? "dark" : "light";
    try { localStorage.setItem("cw-theme", root.dataset.theme); } catch { /* ignore */ }
    route();
  });
}

async function init() {
  initTheme();
  $("#menu-btn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#refresh-btn").addEventListener("click", () => startRefresh());
  window.addEventListener("hashchange", route);
  try {
    await loadBase();
  } catch (e) {
    $("#view").innerHTML = `<div class="card err">${t("Could not connect to the API: {error}", { error: esc(e.message) })}</div>`;
    return;
  }
  S.refresh = await api("/api/refresh/status");
  renderRefreshInfo();
  if (S.refresh.running) pollRefresh();
  setInterval(renderRefreshInfo, 60000);
  route();
}

init();
