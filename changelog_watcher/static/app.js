/* Changelog Watcher – single page app (no build step). */
"use strict";

const S = { meta: null, envs: [], projects: [], refresh: null, charts: [], envFilter: null };
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const STATUS = {
  up_to_date: { label: "Atualizado", short: "Atualizado" },
  patch: { label: "Bugfix disponível", short: "Bugfix" },
  minor: { label: "Minor disponível", short: "Minor" },
  major: { label: "Major disponível", short: "Major" },
  unknown: { label: "Desconhecido", short: "Desconhecido" },
};
const STATUS_KEYS = ["up_to_date", "patch", "minor", "major", "unknown"];
const STATUS_VAR = { up_to_date: "--st-up", patch: "--st-patch", minor: "--st-minor", major: "--st-major", unknown: "--st-unknown" };
const RELATION = {
  same: "Igual",
  source_ahead: "Origem à frente",
  target_ahead: "Destino à frente",
  only_source: "Só na origem",
  only_target: "Só no destino",
  unknown: "Sem versão",
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
  return withTime ? d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : d.toLocaleDateString("pt-BR");
}
function rel(iso) {
  if (!iso) return "nunca";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "agora";
  if (s < 3600) return `há ${Math.round(s / 60)} min`;
  if (s < 86400) return `há ${Math.round(s / 3600)} h`;
  return `há ${Math.round(s / 86400)} dias`;
}
function pct(r) { return r == null ? "—" : `${Math.round(r * 100)}%`; }
function days(n) {
  if (!n) return "0 dias";
  if (n >= 365) return `${(n / 365).toFixed(1).replace(".", ",")} anos`;
  if (n >= 60) return `${Math.round(n / 30)} meses`;
  return `${Math.round(n)} dias`;
}
function num(n, d = 0) { return Number(n || 0).toLocaleString("pt-BR", { maximumFractionDigits: d, minimumFractionDigits: d }); }
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

function toast(msg, error = false) {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error" : "");
  el.textContent = msg;
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
  out = out.replace(/!\[([^\]]*)\]\((https?:[^)\s]+)\)/g, (_, alt, url) => `<a href="${url}" target="_blank" rel="noopener">[imagem${alt ? ": " + alt : ""}]</a>`);
  out = out.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, (_, t, url) => `<a href="${url}" target="_blank" rel="noopener">${t}</a>`);
  out = out.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (_, pre, url) => `${pre}<a href="${url}" target="_blank" rel="noopener">${url}</a>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|\s)_([^_]+)_(?=\s|$|[.,;:])/g, "$1<em>$2</em>");
  out = out.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
  return out;
}
function md(text) {
  if (!text) return `<p class="muted">Sem notas de versão.</p>`;
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
function stackedBars(el, rows, keys, colorOf, labelOf) {
  registerChart(() => {
    if (!el.isConnected) return;
    const W = Math.max(el.clientWidth, 280);
    const labelW = Math.min(150, Math.max(90, W * 0.3));
    const maxChars = Math.floor((labelW - 10) / 7);
    const short = (t) => (t.length > maxChars ? `${t.slice(0, maxChars - 1)}…` : t);
    const rowH = 28, gap = 14, barW = W - labelW - 50;
    const H = rows.length * (rowH + gap);
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Distribuição de status por ambiente">`;
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
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Gráfico de barras">`;
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
        tip.show(`<div class="t">${esc(it.label)}</div><div class="r"><span>${esc(it.hint || "Aplicações")}</span><b>${it.value}${unit}</b></div>`, ev.clientX - b.left, ev.clientY - b.top);
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
    if (!all.length) { el.innerHTML = `<div class="empty">Sem histórico ainda – os pontos são gravados a cada atualização.</div>`; return; }
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
      tip.show(`<div class="t">${new Date(best).toLocaleDateString("pt-BR")}</div>${rows}`, X(best) / scale, ev.clientY - b.top);
    });
    hit.addEventListener("mouseleave", () => { tip.hide(); xh.style.display = "none"; dots.innerHTML = ""; });
  });
}

function legend(items, line = false) {
  return `<div class="legend">${items.map((i) => `<span><i class="${line ? "line" : ""}" style="background:${i.color}"></i>${esc(i.label)}</span>`).join("")}</div>`;
}

// ------------------------------------------------------------------ modal
function modal(title, body, { onSubmit, submitLabel = "Salvar", wide = false } = {}) {
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<form class="modal" ${wide ? 'style="width:min(1000px,100%)"' : ""} novalidate>
    <header><h2>${esc(title)}</h2><button type="button" class="btn ghost" data-close aria-label="Fechar">✕</button></header>
    <div class="body">${body}</div>
    <footer><button type="button" class="btn" data-close>Cancelar</button>${onSubmit ? `<button class="btn primary" type="submit">${esc(submitLabel)}</button>` : ""}</footer>
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
  const sch = meta.schedule;
  $("#schedule-info").textContent = `Relatório: ${sch.report_cron} (${sch.timezone}) · atualização a cada ${sch.refresh_interval_minutes} min`;
}

// ------------------------------------------------------------- refreshing
async function startRefresh(projectIds) {
  try {
    const st = await api("/api/refresh", { method: "POST", body: { project_ids: projectIds || null } });
    S.refresh = st;
    renderRefreshInfo();
    toast(st.message || "Atualização iniciada");
    pollRefresh();
  } catch (e) { toast(e.message, true); }
}
async function pollRefresh() {
  const st = await api("/api/refresh/status");
  S.refresh = st;
  renderRefreshInfo();
  if (st.running) { setTimeout(pollRefresh, 2000); return; }
  const errs = st.projects_failed + st.detections_failed;
  toast(`Atualização concluída: ${st.new_releases} novas versões${errs ? `, ${errs} erro(s)` : ""}`, errs > 0);
  await loadBase();
  route();
}
function renderRefreshInfo() {
  const st = S.refresh;
  const btn = $("#refresh-btn");
  const icon = $("svg", btn);
  if (st?.running) {
    btn.disabled = true; icon.classList.add("spin"); $("span", btn).textContent = "Atualizando…";
  } else {
    btn.disabled = false; icon.classList.remove("spin"); $("span", btn).textContent = "Buscar atualizações";
  }
  $("#refresh-info").innerHTML = st?.finished_at
    ? `Última atualização ${rel(st.finished_at)}${st.success === false ? ` · <span class="err">${st.errors.length} erro(s)</span>` : ""}`
    : "";
}

// ================================================================ VIEWS
function envFilterBar(onChange, selected) {
  const items = [{ id: "", name: "Todos os ambientes" }, ...S.envs];
  const html = `<div class="seg" role="group" aria-label="Ambiente">${items.map((e) => `<button type="button" data-env="${e.id}" class="${String(selected ?? "") === String(e.id) ? "on" : ""}">${esc(e.name)}</button>`).join("")}</div>`;
  return { html, bind(root) { $$("[data-env]", root).forEach((b) => b.addEventListener("click", () => onChange(b.dataset.env ? Number(b.dataset.env) : null))); } };
}

function kpi(label, value, foot = "", extra = "", cls = "") {
  return `<div class="card kpi ${cls}"><div class="label">${label}</div><div class="value num">${value}</div>${extra}${foot ? `<div class="foot">${foot}</div>` : ""}</div>`;
}

function updatesCell(s) {
  const parts = [];
  if (s.latest_patch) parts.push(`<span><span class="k">bugfix</span><b>${esc(s.latest_patch)}</b></span>`);
  if (s.latest_minor) parts.push(`<span><span class="k">minor</span><b>${esc(s.latest_minor)}</b></span>`);
  if (s.latest_major) parts.push(`<span><span class="k">major</span><b>${esc(s.latest_major)}</b></span>`);
  return parts.length ? `<div class="upd">${parts.join("")}</div>` : `<span class="muted">—</span>`;
}

// ------------------------------------------------------------- dashboard
async function viewDashboard(view) {
  const q = S.envFilter ? `?environment_id=${S.envFilter}` : "";
  const [d, hist] = await Promise.all([api(`/api/dashboard${q}`), api(`/api/history?days=180${S.envFilter ? `&environment_id=${S.envFilter}` : ""}`)]);
  const o = d.overall;
  const filter = envFilterBar((id) => { S.envFilter = id; route(); }, S.envFilter);
  if (!S.envs.length) {
    view.innerHTML = `<div class="card empty">Nenhum ambiente cadastrado. <a href="#/environments">Cadastre um ambiente</a> e depois <a href="#/apps">as aplicações</a>.</div>`;
    return;
  }
  const ratio = o.up_to_date_ratio;
  view.innerHTML = `
    <div class="filters">${filter.html}</div>
    <div class="grid kpis">
      ${kpi("Ambiente atualizado", pct(ratio), `${o.counts.up_to_date} de ${o.known} com versão conhecida`, `<div class="meter"><i style="width:${(ratio || 0) * 100}%"></i></div>`, "hero")}
      ${kpi("Implantações monitoradas", num(o.total), `${d.projects} aplicações · ${o.counts.unknown} sem versão identificada`)}
      ${kpi(`${badge("patch", "Bugfix")} pendente`, num(o.patch_pending), `${o.counts.patch} só com bugfix pendente`)}
      ${kpi(`${badge("minor", "Minor")} pendente`, num(o.minor_pending), `${o.counts.minor} com minor como maior salto`)}
      ${kpi(`${badge("major", "Major")} pendente`, num(o.major_pending), `${o.counts.major} implantações`)}
      ${kpi("Idade acumulada", `${num(o.libyears, 1)} <span class="small muted">libyears</span>`, "soma do atraso entre a versão em uso e a mais recente")}
      ${kpi("Atraso médio", days(o.avg_outdated_days), `maior atraso: ${days(o.max_outdated_days)}`)}
      ${kpi("Diferenças entre ambientes", num(d.drift_count), `<a href="#/compare">comparar ambientes</a>`)}
    </div>
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><div class="card-head"><h2>Status por ambiente</h2><span class="sub">maior atualização pendente de cada implantação</span></div><div class="chart" id="ch-status"></div>${legend(STATUS_KEYS.map((k) => ({ label: STATUS[k].label, color: statusColor(k) })))}</div>
      <div class="card"><div class="card-head"><h2>Há quanto tempo estão desatualizadas</h2><span class="sub">desde a primeira versão mais nova publicada</span></div><div class="chart" id="ch-age"></div></div>
    </div>
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><div class="card-head"><h2>% atualizado ao longo do tempo</h2></div><div class="chart" id="ch-trend"></div><div id="lg-trend"></div></div>
      <div class="card"><div class="card-head"><h2>Envelhecimento do ambiente (libyears)</h2><span class="sub">quanto maior, mais antigo</span></div><div class="chart" id="ch-lib"></div><div id="lg-lib"></div></div>
    </div>
    <div class="card" style="margin-top:16px">
      <div class="card-head"><h2>Mais desatualizadas</h2><a class="small" href="#/apps">ver todas</a></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Aplicação</th><th>Ambiente</th><th>Status</th><th>Atual</th><th>Disponível</th><th>Atrasada há</th><th class="num">Versões atrás</th><th class="num">Libyears</th></tr></thead>
        <tbody>${d.most_outdated.map((s) => {
          const w = o.max_outdated_days ? (s.outdated_days / o.max_outdated_days) * 100 : 0;
          return `<tr class="clickable" data-href="#/apps/${s.project_id}"><td><b>${esc(s.project)}</b></td><td>${esc(s.environment)}</td><td>${badge(s.status)}</td><td>${ver(s.current_version)}</td><td>${updatesCell(s)}</td>
          <td><div class="row" style="flex-wrap:nowrap"><div class="bar-track" style="flex:1"><i style="width:${w}%"></i></div><span class="small nowrap num">${days(s.outdated_days)}</span></div></td>
          <td class="num">${s.versions_behind}</td><td class="num">${s.libyears == null ? "—" : num(s.libyears, 2)}</td></tr>`;
        }).join("") || `<tr><td colspan="8" class="empty">Nada desatualizado 🎉</td></tr>`}</tbody>
      </table></div>
    </div>`;
  filter.bind(view);
  bindRowLinks(view);

  stackedBars($("#ch-status", view), d.environments.map((e) => ({ label: e.environment, values: e.counts })), STATUS_KEYS, statusColor, (k) => STATUS[k].label);
  columnChart($("#ch-age", view), Object.entries(o.age_buckets).map(([label, value]) => ({ label, value, hint: "Implantações desatualizadas" })));

  const byEnv = {};
  hist.forEach((h) => { (byEnv[h.environment_id] ||= []).push(h); });
  const trend = Object.entries(byEnv).map(([id, pts]) => ({ name: envName(id), color: envColor(id), points: pts.map((p) => ({ x: p.day, y: p.up_to_date_ratio == null ? null : p.up_to_date_ratio * 100 })) }));
  const lib = Object.entries(byEnv).map(([id, pts]) => ({ name: envName(id), color: envColor(id), points: pts.map((p) => ({ x: p.day, y: p.libyears })) }));
  lineChart($("#ch-trend", view), trend, { yMax: 100, yFmt: (v) => `${Math.round(v)}%`, label: "Percentual atualizado" });
  lineChart($("#ch-lib", view), lib, { yFmt: (v, tip) => (tip ? `${num(v, 2)} libyears` : num(v, 1)), label: "Libyears" });
  if (trend.length > 1) { $("#lg-trend", view).innerHTML = legend(trend.map((s) => ({ label: s.name, color: s.color })), true); $("#lg-lib", view).innerHTML = legend(lib.map((s) => ({ label: s.name, color: s.color })), true); }
}

function bindRowLinks(root) {
  $$("tr[data-href]", root).forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("a,button,input,select")) return;
    location.hash = tr.dataset.href;
  }));
}

// ---------------------------------------------------------- applications
async function viewApps(view) {
  const statuses = await api("/api/status");
  const byKey = {};
  statuses.forEach((s) => { byKey[`${s.project_id}:${s.environment_id}`] = s; });
  const allTags = [...new Set(S.projects.flatMap((p) => p.tags || []))].sort();
  const st = viewApps.state ||= { q: "", status: "", tag: "" };
  view.innerHTML = `
    <div class="filters">
      <input type="search" id="f-q" placeholder="Buscar aplicação…" value="${esc(st.q)}">
      <select id="f-status"><option value="">Todos os status</option>${STATUS_KEYS.map((k) => opt(k, STATUS[k].label, st.status)).join("")}<option value="outdated" ${st.status === "outdated" ? "selected" : ""}>Qualquer atualização</option></select>
      ${allTags.length ? `<select id="f-tag"><option value="">Todas as tags</option>${allTags.map((t) => opt(t, t, st.tag)).join("")}</select>` : ""}
      <div class="spacer" style="flex:1"></div>
      <button class="btn primary" id="new-app" type="button">+ Nova aplicação</button>
    </div>
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>Aplicação</th>${S.envs.map((e) => `<th>${esc(e.name)}</th>`).join("")}<th>Mais recente</th><th></th></tr></thead>
      <tbody id="apps-body"></tbody>
    </table></div></div>`;
  const body = $("#apps-body", view);
  const draw = () => {
    const rows = S.projects.filter((p) => {
      if (st.q && !`${p.name} ${p.repository} ${(p.tags || []).join(" ")}`.toLowerCase().includes(st.q.toLowerCase())) return false;
      if (st.tag && !(p.tags || []).includes(st.tag)) return false;
      if (st.status) {
        const ss = S.envs.map((e) => byKey[`${p.id}:${e.id}`]).filter(Boolean);
        if (st.status === "outdated") return ss.some((s) => ["patch", "minor", "major"].includes(s.status));
        return ss.some((s) => s.status === st.status);
      }
      return true;
    });
    body.innerHTML = rows.map((p) => {
      const any = S.envs.map((e) => byKey[`${p.id}:${e.id}`]).find(Boolean);
      return `<tr class="clickable" data-href="#/apps/${p.id}">
        <td><div><b>${esc(p.name)}</b> ${p.enabled ? "" : '<span class="chip">desativada</span>'}</div>
          <div class="small muted">${esc(S.meta.source_types[p.source_type] || p.source_type)} · ${esc(p.repository)}</div>
          ${p.last_error ? `<div class="err" title="${esc(p.last_error)}">⚠ ${esc(p.last_error.slice(0, 80))}</div>` : ""}
          ${(p.tags || []).length ? `<div class="pill-row" style="margin-top:4px">${p.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>` : ""}</td>
        ${S.envs.map((e) => {
          const s = byKey[`${p.id}:${e.id}`];
          if (!s) return `<td class="muted small">não implantado</td>`;
          return `<td><div class="cell-ver">${ver(s.current_version)} ${badge(s.status)} ${updatesCell(s)}
            ${s.detection_error ? `<span class="err" title="${esc(s.detection_error)}">⚠ detecção falhou</span>` : ""}</div></td>`;
        }).join("")}
        <td>${ver(any?.latest_version)}<div class="small muted">${any?.latest_version_date ? fmtDate(any.latest_version_date) : ""}</div></td>
        <td class="nowrap right"><button class="btn sm" data-refresh="${p.id}" type="button" title="Buscar releases">↻</button> <button class="btn sm" data-edit="${p.id}" type="button">Editar</button></td>
      </tr>`;
    }).join("") || `<tr><td colspan="${S.envs.length + 3}" class="empty">${S.projects.length ? "Nenhuma aplicação com esses filtros." : "Nenhuma aplicação cadastrada ainda."}</td></tr>`;
    bindRowLinks(body);
    $$("[data-edit]", body).forEach((b) => b.addEventListener("click", () => projectForm(S.projects.find((p) => p.id === Number(b.dataset.edit)))));
    $$("[data-refresh]", body).forEach((b) => b.addEventListener("click", () => startRefresh([Number(b.dataset.refresh)])));
  };
  draw();
  $("#f-q", view).addEventListener("input", debounce((e) => { st.q = e.target.value; draw(); }, 150));
  $("#f-status", view).addEventListener("change", (e) => { st.status = e.target.value; draw(); });
  $("#f-tag", view)?.addEventListener("change", (e) => { st.tag = e.target.value; draw(); });
  $("#new-app", view).addEventListener("click", () => projectForm());
}

/** ``p`` is an existing project, or (with isNew) a prefill such as a discovery suggestion. */
function projectForm(p = null, isNew = !p) {
  if (isNew) p = { source_type: "github_releases", version_scheme: "semver", detection_mode: "none", workload_kind: "deployment", enabled: true, tags: [], deployments: [], ...p };
  const deps = Object.fromEntries((p.deployments || []).map((d) => [d.environment_id, d]));
  const st = S.meta.source_types;
  const body = `
    <fieldset><legend>Aplicação</legend><div class="form-grid">
      ${field("Nome *", inp("name", p.name, "required"))}
      ${field("Tags", inp("tags", (p.tags || []).join(", "), 'placeholder="infra, observabilidade"'), "separadas por vírgula")}
      ${field("Descrição", inp("description", p.description), "", "full")}
      <label class="check full"><input type="checkbox" name="enabled" ${p.enabled ? "checked" : ""}> Monitoramento ativo</label>
    </div></fieldset>
    <fieldset><legend>Fonte do changelog / releases</legend><div class="form-grid">
      ${field("Tipo de fonte", `<select name="source_type">${Object.entries(st).map(([k, v]) => opt(k, v, p.source_type)).join("")}</select>`)}
      ${field("Repositório / URL *", inp("repository", p.repository, 'required placeholder="argoproj/argo-cd"'), '<span id="repo-hint"></span>')}
      <div data-show="helm_index">${field("Nome do chart", inp("chart_name", p.chart_name))}</div>
      <div data-show="helm_index"><label class="check" style="margin-top:22px"><input type="checkbox" name="use_app_version" ${p.use_app_version ? "checked" : ""}> Comparar pelo appVersion</label></div>
      <div data-show="github_releases github_tags gitlab_releases gitlab_tags">${field("URL da API (opcional)", inp("source_url", p.source_url, 'placeholder="https://github.empresa.com/api/v3"'), "GitHub Enterprise ou GitLab self-hosted")}</div>
      ${field("Padrão de tag (regex)", inp("tag_pattern", p.tag_pattern, 'placeholder="^v?(\\d+\\.\\d+\\.\\d+)$"'), "opcional – filtra tags de monorepos, ex: <code>^chart-(.*)$</code>")}
      ${field("URL do CHANGELOG", inp("changelog_url", p.changelog_url, 'placeholder="https://github.com/org/repo/blob/main/CHANGELOG.md"'), "opcional – completa as notas quando a fonte não tem", "full")}
      ${field("Esquema de versão", `<select name="version_scheme">${opt("semver", "SemVer – major.minor.patch", p.version_scheme)}${opt("major_patch", "major.bugfix (ex: PostgreSQL 16.2)", p.version_scheme)}</select>`)}
      <label class="check"><input type="checkbox" name="include_prerelease" ${p.include_prerelease ? "checked" : ""}> Considerar pre-releases (rc, beta…)</label>
    </div></fieldset>
    <fieldset><legend>Descobrir versão em uso via Prometheus</legend><div class="form-grid">
      ${field("Modo de detecção", `<select name="detection_mode">${Object.entries(S.meta.detection_modes).map(([k, v]) => opt(k, v, p.detection_mode)).join("")}</select>`, "usa <code>kube_pod_container_info</code> do kube-state-metrics", "full")}
      <div data-mode="workload">${field("Tipo de workload", `<select name="workload_kind">${["deployment", "statefulset", "daemonset", "cronjob"].map((k) => opt(k, k, p.workload_kind)).join("")}</select>`)}</div>
      <div data-mode="workload helm image query">${field("Namespace", inp("workload_namespace", p.workload_namespace))}</div>
      <div data-mode="workload helm query">${field("Nome do workload / release Helm", inp("workload_name", p.workload_name), "no modo Helm, todos os workloads do release são considerados")}</div>
      <div data-mode="workload helm image">${field("Container", inp("container_name", p.container_name), "opcional")}</div>
      <div data-mode="helm image" class="full">${field("Imagem", inp("image_pattern", p.image_pattern, 'placeholder="quay.io/argoproj/argocd"'), "repositório da imagem ou regex – no modo Helm (opcional) escolhe qual imagem do release define a versão")}</div>
      <div data-mode="query" class="full">${field("Consulta PromQL", `<textarea name="promql_query" placeholder='kube_pod_container_info{namespace="{namespace}", container="app"}'>${esc(p.promql_query || "")}</textarea>`, "as séries devem ter o label <code>image</code> (ou <code>version</code>); {namespace} e {name} são substituídos")}</div>
      <div data-mode="workload helm image query" class="full">${field("Padrão de versão na tag da imagem (regex)", inp("image_version_pattern", p.image_version_pattern), "opcional, ex: <code>^v?(\\d+\\.\\d+\\.\\d+)</code>")}</div>
    </div></fieldset>
    <fieldset><legend>Ambientes</legend>
      ${S.envs.length ? `<div class="table-wrap"><table><thead><tr><th>Ambiente</th><th>Implantado</th><th>Origem da versão</th><th>Versão manual</th><th>Namespace (sobrescrever)</th><th>Workload (sobrescrever)</th></tr></thead><tbody>
      ${S.envs.map((e) => {
        const d = deps[e.id];
        const src = d?.version_source || (p.detection_mode !== "none" && e.prometheus_url ? "prometheus" : "manual");
        return `<tr data-dep="${e.id}"><td><b>${esc(e.name)}</b>${e.prometheus_url ? "" : '<div class="small muted">sem Prometheus</div>'}</td>
          <td><input type="checkbox" data-skip="1" data-f="on" ${d || isNew ? "checked" : ""}></td>
          <td><select data-skip="1" data-f="version_source">${opt("manual", "Manual", src)}${opt("prometheus", "Prometheus", src)}</select></td>
          <td><input data-skip="1" data-f="manual_version" value="${esc(d?.manual_version || "")}" placeholder="1.2.3" style="width:110px"></td>
          <td><input data-skip="1" data-f="namespace_override" value="${esc(d?.namespace_override || "")}" style="width:140px"></td>
          <td><input data-skip="1" data-f="workload_name_override" value="${esc(d?.workload_name_override || "")}" style="width:140px"></td></tr>`;
      }).join("")}</tbody></table></div>` : `<p class="muted">Cadastre ambientes para informar as versões em uso.</p>`}
    </fieldset>`;
  const m = modal(isNew ? "Nova aplicação" : `Editar ${p.name}`, body, {
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
      toast(isNew ? "Aplicação criada – buscando releases…" : "Aplicação atualizada");
      await loadBase();
      setTimeout(() => pollRefresh(), 1500);
      if (isNew) location.hash = `#/apps/${saved.id}`; else route();
    },
  });
  const form = m.form;
  const hints = {
    github_releases: "owner/repo", github_tags: "owner/repo", gitlab_releases: "grupo/projeto", gitlab_tags: "grupo/projeto",
    helm_index: "URL do repositório Helm, ex: https://argoproj.github.io/argo-helm", dockerhub: "imagem, ex: library/nginx ou bitnami/redis",
    changelog: "URL do CHANGELOG.md (raw ou github.com/.../blob/...)",
    webpage: "URL da página que lista as versões – use o padrão de tag para ignorar outras versões citadas",
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
        <div class="small muted" style="margin-top:4px">${esc(S.meta.source_types[p.source_type] || p.source_type)} · ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(p.repository)}</a>` : esc(p.repository)} · ${p.release_count} versões conhecidas · verificado ${rel(p.last_checked_at)}</div>
        ${p.description ? `<p style="margin:8px 0 0">${esc(p.description)}</p>` : ""}
        ${p.last_error ? `<div class="err" style="margin-top:6px">⚠ ${esc(p.last_error)}</div>` : ""}
      </div>
      <div class="row"><button class="btn" id="d-refresh" type="button">↻ Buscar releases</button><button class="btn" id="d-edit" type="button">Editar</button><button class="btn danger" id="d-del" type="button">Excluir</button></div></div>
    </div>
    <div class="grid cols-${Math.min(Math.max(mine.length, 1), 3)}" style="margin-top:16px">
      ${mine.map((s) => `<div class="card">
        <div class="card-head"><h2>${esc(s.environment)}</h2>${badge(s.status, STATUS[s.status].label)}</div>
        <div class="row" style="align-items:baseline"><span class="small muted">Em uso</span><span class="ver" style="font-size:20px;font-weight:600">${esc(s.current_version || "—")}</span><span class="small muted">${s.current_release_date ? `publicada em ${fmtDate(s.current_release_date)}` : ""}</span></div>
        <div class="small muted" style="margin:6px 0 10px">Origem: ${s.version_source === "prometheus" ? `Prometheus${s.detected_at ? ` (${rel(s.detected_at)})` : ""}` : "manual"}${s.detected_image ? `<br><span class="mono">${esc(s.detected_image)}</span>` : ""}</div>
        ${s.detection_error ? `<div class="err" style="margin-bottom:8px">⚠ ${esc(s.detection_error)}</div>` : ""}
        <table><tbody>
          <tr><td class="muted">Bugfix</td><td>${s.latest_patch ? `${s.latest_patch_url ? `<a href="${esc(s.latest_patch_url)}" target="_blank" rel="noopener">${esc(s.latest_patch)}</a>` : esc(s.latest_patch)} <span class="small muted">(${s.patch_behind} atrás)</span>` : "—"}</td></tr>
          <tr><td class="muted">Minor</td><td>${s.latest_minor ? `${s.latest_minor_url ? `<a href="${esc(s.latest_minor_url)}" target="_blank" rel="noopener">${esc(s.latest_minor)}</a>` : esc(s.latest_minor)} <span class="small muted">(${s.minor_behind} atrás)</span>` : "—"}</td></tr>
          <tr><td class="muted">Major</td><td>${s.latest_major ? `${s.latest_major_url ? `<a href="${esc(s.latest_major_url)}" target="_blank" rel="noopener">${esc(s.latest_major)}</a>` : esc(s.latest_major)} <span class="small muted">(${s.major_behind} atrás)</span>` : "—"}</td></tr>
          <tr><td class="muted">Atrasada há</td><td>${s.outdated_since ? `${days(s.outdated_days)} <span class="small muted">(desde ${fmtDate(s.outdated_since)})</span>` : "—"}</td></tr>
          <tr><td class="muted">Libyears</td><td class="num">${s.libyears == null ? "—" : num(s.libyears, 2)}</td></tr>
        </tbody></table>
        <div class="row" style="margin-top:10px">
          ${s.versions_behind ? `<button class="btn sm" data-notes="${s.environment_id}" type="button">Ver changelog pendente (${s.versions_behind})</button>` : ""}
          ${p.detection_mode !== "none" ? `<button class="btn sm" data-detect="${s.environment_id}" type="button">Testar detecção</button>` : ""}
        </div>
        <div data-detect-out="${s.environment_id}" class="small" style="margin-top:8px"></div>
      </div>`).join("") || `<div class="card empty">Esta aplicação não está implantada em nenhum ambiente. Edite-a para informar onde ela roda.</div>`}
    </div>
    <div class="card" style="margin-top:16px">
      <div class="card-head"><h2 id="notes-title">Versões publicadas</h2><span class="sub" id="notes-sub">${releases.length} versões</span></div>
      <div id="notes"></div>
    </div>`;
  const renderReleases = (list, current) => {
    $("#notes", view).innerHTML = list.map((r, i) => `<div class="release">
      <div class="head"><b class="ver">${esc(r.tag)}</b>${r.title && r.title !== r.tag ? `<span>${esc(r.title)}</span>` : ""}${r.prerelease ? '<span class="chip">pre-release</span>' : ""}
      ${current && r.version === current ? '<span class="chip">em uso</span>' : ""}<span class="small muted">${fmtDate(r.published_at)}</span>${r.url ? `<a class="small" href="${esc(r.url)}" target="_blank" rel="noopener">abrir ↗</a>` : ""}</div>
      ${r.notes ? `<details ${i < 3 ? "open" : ""}><summary>Notas da versão</summary><div class="notes">${md(r.notes)}</div></details>` : ""}
    </div>`).join("") || `<div class="empty">Nenhuma versão encontrada ainda. Clique em “Buscar releases”.</div>`;
  };
  renderReleases(releases.slice(0, 50));
  $("#d-refresh", view).addEventListener("click", () => startRefresh([p.id]));
  $("#d-edit", view).addEventListener("click", () => projectForm(p));
  $("#d-del", view).addEventListener("click", async () => {
    if (!confirm(`Excluir ${p.name}? O histórico de releases dessa aplicação também será apagado.`)) return;
    await api(`/api/projects/${p.id}`, { method: "DELETE" });
    toast("Aplicação excluída");
    await loadBase();
    location.hash = "#/apps";
  });
  $$("[data-notes]", view).forEach((b) => b.addEventListener("click", async () => {
    const s = mine.find((x) => x.environment_id === Number(b.dataset.notes));
    const list = await api(`/api/projects/${p.id}/changelog?from=${encodeURIComponent(s.current_version)}`);
    $("#notes-title", view).textContent = `Changelog pendente em ${s.environment}`;
    $("#notes-sub", view).innerHTML = `${esc(s.current_version)} → ${esc(s.latest_version)} · ${list.length} versões · <a href="#" id="show-all">ver todas</a>`;
    renderReleases(list, s.current_version);
    $("#show-all", view).addEventListener("click", (e) => { e.preventDefault(); $("#notes-title", view).textContent = "Versões publicadas"; $("#notes-sub", view).textContent = `${releases.length} versões`; renderReleases(releases.slice(0, 50)); });
    $("#notes", view).scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  $$("[data-detect]", view).forEach((b) => b.addEventListener("click", async () => {
    const out = $(`[data-detect-out="${b.dataset.detect}"]`, view);
    out.textContent = "Consultando Prometheus…";
    const r = await api(`/api/projects/${p.id}/detect?environment_id=${b.dataset.detect}`, { method: "POST" });
    out.innerHTML = r.ok
      ? `✅ Versão <b class="ver">${esc(r.version)}</b> (${esc(r.image)})${Object.keys(r.images).length > 1 ? `<div>${Object.entries(r.images).map(([img, n]) => `<div class="mono">${n}× ${esc(img)}</div>`).join("")}</div>` : "<br>"}<span class="mono muted">${esc(r.query)}</span>`
      : `<span class="err">⚠ ${esc(r.error)}</span>${r.query ? `<br><span class="mono muted">${esc(r.query)}</span>` : ""}`;
  }));
}

// --------------------------------------------------------------- compare
async function viewCompare(view) {
  if (S.envs.length < 2) { view.innerHTML = `<div class="card empty">Cadastre pelo menos dois ambientes para comparar.</div>`; return; }
  const st = viewCompare.state ||= { source: S.envs[S.envs.length - 1].id, target: S.envs[0].id, only: true };
  const [cmp, mat] = await Promise.all([
    api(`/api/compare?source=${st.source}&target=${st.target}&only_differences=${st.only}`),
    api("/api/matrix"),
  ]);
  const sName = envName(st.source), tName = envName(st.target);
  const relText = (r) => {
    if (r.relation === "source_ahead") return `<span class="diff-ahead">${esc(sName)} à frente</span> por ${r.releases_between} versão(ões)`;
    if (r.relation === "target_ahead") return `<span class="diff-behind">${esc(sName)} atrás</span> por ${r.releases_between} versão(ões)`;
    if (r.relation === "only_source") return `<span class="muted">só em ${esc(sName)}</span>`;
    if (r.relation === "only_target") return `<span class="muted">só em ${esc(tName)}</span>`;
    return esc(RELATION[r.relation]);
  };
  const drift = mat.rows.filter((r) => r.drift);
  view.innerHTML = `
    <div class="card">
      <div class="filters" style="margin:0">
        ${field("Origem", `<select id="c-src">${S.envs.map((e) => opt(e.id, e.name, st.source)).join("")}</select>`)}
        <button class="btn ghost" id="c-swap" type="button" title="Inverter" style="margin-top:18px">⇄</button>
        ${field("Destino", `<select id="c-tgt">${S.envs.map((e) => opt(e.id, e.name, st.target)).join("")}</select>`)}
        <label class="check" style="margin-top:18px"><input type="checkbox" id="c-only" ${st.only ? "checked" : ""}> Só diferenças</label>
        <div style="flex:1"></div>
        <div class="pill-row" style="margin-top:18px">${Object.entries(cmp.counts).map(([k, v]) => `<span class="chip">${esc(RELATION[k])}: ${v}</span>`).join("")}</div>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h2>${esc(sName)} × ${esc(tName)}</h2></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Aplicação</th><th>${esc(sName)}</th><th>${esc(tName)}</th><th>Diferença</th><th>Mais recente</th><th>Versões entre os ambientes</th></tr></thead>
        <tbody>${cmp.rows.map((r) => `<tr><td><a href="#/apps/${r.project_id}"><b>${esc(r.project)}</b></a></td><td>${ver(r.source_version)}</td><td>${ver(r.target_version)}</td><td>${relText(r)}</td><td>${ver(r.latest_version)}</td>
          <td>${r.releases.length ? `<details><summary>${r.releases.length} versão(ões)</summary><div class="small">${r.releases.map((x) => `<div>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.tag)}</a>` : esc(x.tag)} <span class="muted">${fmtDate(x.published_at)}</span></div>`).join("")}</div></details>` : '<span class="muted">—</span>'}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">Os ambientes estão com as mesmas versões 🎉</td></tr>`}</tbody>
      </table></div>
    </div>
    <div class="card">
      <div class="card-head"><h2>Matriz de versões</h2><span class="sub">${drift.length} aplicações com versões diferentes entre ambientes</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Aplicação</th>${mat.environments.map((e) => `<th>${esc(e.name)}</th>`).join("")}<th>Mais recente</th></tr></thead>
        <tbody>${mat.rows.map((r) => `<tr><td><a href="#/apps/${r.project_id}">${esc(r.project)}</a> ${r.drift ? '<span class="chip">divergente</span>' : ""}</td>
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
  const list = await api(`/api/releases/recent?days=${st.days}`);
  view.innerHTML = `
    <div class="filters"><div class="seg">${[7, 14, 30, 90].map((d) => `<button type="button" data-days="${d}" class="${st.days === d ? "on" : ""}">${d} dias</button>`).join("")}</div>
      <span class="muted">${list.length} versões publicadas</span></div>
    <div class="card">${list.map((r) => `<div class="release">
      <div class="head"><a href="#/apps/${r.project_id}"><b>${esc(r.project)}</b></a><span class="ver">${esc(r.tag)}</span>${r.level === "older" ? badge("unknown", "Linha anterior") : badge(r.level, r.level === "up_to_date" ? "Já em uso" : r.level === "unknown" ? "Sem versão em uso" : `${STATUS[r.level].short} p/ o ambiente`)}
      <span class="small muted">${fmtDate(r.published_at)}</span>${r.url ? `<a class="small" href="${esc(r.url)}" target="_blank" rel="noopener">abrir ↗</a>` : ""}</div>
      <div class="small muted">${Object.entries(r.deployed).map(([env, v]) => `${esc(env)}: <span class="ver">${esc(v || "?")}</span>`).join(" · ") || "não implantado"}</div>
      ${r.notes ? `<details><summary>Notas da versão</summary><div class="notes">${md(r.notes)}</div></details>` : ""}
    </div>`).join("") || `<div class="empty">Nenhuma versão nova no período.</div>`}</div>`;
  $$("[data-days]", view).forEach((b) => b.addEventListener("click", () => { st.days = Number(b.dataset.days); route(); }));
}

// --------------------------------------------------------------- reports
async function viewReports(view, id) {
  const reports = await api("/api/reports");
  const current = id ? await api(`/api/reports/${id}`) : reports[0] ? await api(`/api/reports/${reports[0].id}`) : null;
  view.innerHTML = `
    <div class="filters">
      <button class="btn" id="r-gen" type="button">Gerar prévia</button>
      <button class="btn primary" id="r-send" type="button">Gerar e enviar aos webhooks</button>
      <span class="muted small">Envio automático: <code>${esc(S.meta.schedule.report_cron)}</code> (${esc(S.meta.schedule.timezone)})${S.meta.schedule.next_runs?.weekly_report ? ` · próximo ${fmtDate(S.meta.schedule.next_runs.weekly_report, true)}` : ""}</span>
    </div>
    <div class="grid" style="grid-template-columns:minmax(220px,300px) minmax(0,1fr)" id="r-grid">
      <div class="card"><h3>Histórico</h3>${reports.map((r) => `<a href="#/reports/${r.id}" style="display:block;padding:8px;border-radius:8px;${current && current.id === r.id ? "background:var(--accent-wash)" : ""}">
        <b>${fmtDate(r.created_at, true)}</b> <span class="chip">${r.trigger === "schedule" ? "agendado" : "manual"}</span>
        <div class="small muted">${r.new_releases} novas versões · ${r.outdated} desatualizadas · ${r.delivery.length ? `${r.delivery.filter((d) => d.ok).length}/${r.delivery.length} envios ok` : "não enviado"}</div></a>`).join("") || `<div class="muted">Nenhum relatório gerado.</div>`}</div>
      <div class="card">${current ? `
        <div class="card-head"><h2>Relatório #${current.id}</h2><div class="row">
          <a class="btn sm" href="/api/reports/${current.id}/markdown">Baixar .md</a>
          <button class="btn sm" id="r-resend" type="button">Enviar</button>
          <button class="btn sm danger" id="r-del" type="button">Excluir</button></div></div>
        ${current.delivery.length ? `<div class="pill-row" style="margin-bottom:10px">${current.delivery.map((d) => `<span class="chip" title="${esc(d.detail || "")}">${d.ok ? "✅" : "❌"} ${esc(d.webhook)}${d.skipped ? " (ignorado)" : ""}</span>`).join("")}</div>` : ""}
        ${md(current.markdown)}` : `<div class="empty">Gere o primeiro relatório.</div>`}</div>
    </div>`;
  if (window.innerWidth < 860) $("#r-grid", view).style.gridTemplateColumns = "1fr";
  const gen = async (send) => {
    const r = await api("/api/reports", { method: "POST", body: { send } });
    if (send) {
      const ok = r.delivery.filter((d) => d.ok).length;
      toast(r.delivery.length ? `Enviado para ${ok}/${r.delivery.length} webhook(s)` : "Nenhum webhook ativo configurado", ok < r.delivery.length || !r.delivery.length);
    }
    location.hash = `#/reports/${r.id}`;
  };
  $("#r-gen", view).addEventListener("click", () => gen(false).catch((e) => toast(e.message, true)));
  $("#r-send", view).addEventListener("click", () => gen(true).catch((e) => toast(e.message, true)));
  $("#r-resend", view)?.addEventListener("click", async () => {
    const r = await api(`/api/reports/${current.id}/send`, { method: "POST" });
    toast(r.delivery.length ? `Enviado: ${r.delivery.filter((d) => d.ok).length}/${r.delivery.length}` : "Nenhum webhook ativo", !r.delivery.length);
    route();
  });
  $("#r-del", view)?.addEventListener("click", async () => {
    if (!confirm("Excluir este relatório?")) return;
    await api(`/api/reports/${current.id}`, { method: "DELETE" });
    location.hash = "#/reports";
  });
}

// ---------------------------------------------------------- environments
async function viewEnvironments(view) {
  view.innerHTML = `
    <div class="filters"><div style="flex:1"></div><button class="btn primary" id="e-new" type="button">+ Novo ambiente</button></div>
    <div class="grid cols-3">${S.envs.map((e, i) => `<div class="card">
      <div class="card-head"><div class="row"><i style="width:10px;height:10px;border-radius:3px;background:${envColor(e.id)};display:inline-block"></i><h2 style="margin:0">${esc(e.name)}</h2></div><span class="sub">ordem ${e.sort_order}</span></div>
      ${e.description ? `<p class="muted" style="margin-top:0">${esc(e.description)}</p>` : ""}
      <div class="small"><span class="muted">Prometheus:</span> ${e.prometheus_url ? `<span class="mono">${esc(e.prometheus_url)}</span>` : '<span class="muted">não configurado (só versões manuais)</span>'}</div>
      ${e.prometheus_url ? `<div class="small"><span class="muted">Autenticação:</span> ${esc(e.prometheus_auth)}${e.prometheus_extra_selector ? ` · <span class="muted">seletor:</span> <span class="mono">${esc(e.prometheus_extra_selector)}</span>` : ""}</div>` : ""}
      <div class="row" style="margin-top:12px">
        ${e.prometheus_url ? `<button class="btn sm" data-test="${e.id}" type="button">Testar conexão</button>` : ""}
        <button class="btn sm" data-edit="${e.id}" type="button">Editar</button>
        <button class="btn sm danger" data-del="${e.id}" type="button">Excluir</button>
      </div>
      <div class="small" data-out="${e.id}" style="margin-top:8px"></div>
    </div>`).join("") || `<div class="card empty">Nenhum ambiente. Crie, por exemplo, “desenvolvimento” e “produção”.</div>`}</div>`;
  $("#e-new", view).addEventListener("click", () => envForm());
  $$("[data-edit]", view).forEach((b) => b.addEventListener("click", () => envForm(S.envs.find((e) => e.id === Number(b.dataset.edit)))));
  $$("[data-del]", view).forEach((b) => b.addEventListener("click", async () => {
    const e = S.envs.find((x) => x.id === Number(b.dataset.del));
    if (!confirm(`Excluir o ambiente ${e.name}? As versões registradas nele serão apagadas.`)) return;
    await api(`/api/environments/${e.id}`, { method: "DELETE" });
    await loadBase(); route();
  }));
  $$("[data-test]", view).forEach((b) => b.addEventListener("click", async () => {
    const out = $(`[data-out="${b.dataset.test}"]`, view);
    out.textContent = "Testando…";
    const r = await api(`/api/environments/${b.dataset.test}/test`, { method: "POST" });
    out.innerHTML = r.ok ? `✅ Conectado – ${r.containers} containers visíveis em <code>kube_pod_container_info</code>` : `<span class="err">⚠ ${esc(r.error)}</span>`;
  }));
}

function envForm(e = null) {
  const isNew = !e;
  e = e || { prometheus_auth: "none", prometheus_verify_tls: true, sort_order: S.envs.length };
  const body = `<div class="form-grid">
    ${field("Nome *", inp("name", e.name, "required"))}
    ${field("Ordem de exibição", inp("sort_order", e.sort_order, 'type="number"'))}
    ${field("Descrição", inp("description", e.description), "", "full")}
    ${field("URL do Prometheus", inp("prometheus_url", e.prometheus_url, 'placeholder="http://prometheus-operated.monitoring:9090"'), "aceita Thanos/Mimir/VictoriaMetrics (API compatível)", "full")}
    ${field("Autenticação", `<select name="prometheus_auth">${opt("none", "Nenhuma", e.prometheus_auth)}${opt("bearer", "Bearer token", e.prometheus_auth)}${opt("basic", "Usuário e senha", e.prometheus_auth)}</select>`)}
    ${field("Seletor extra", inp("prometheus_extra_selector", e.prometheus_extra_selector, 'placeholder=\'cluster="prod"\''), "adicionado a todas as consultas")}
    <div data-auth="bearer" class="full">${field("Token", inp("prometheus_token", e.prometheus_token, 'autocomplete="off"'), "use <code>${VARIAVEL}</code> para ler de uma variável de ambiente (Secret)")}</div>
    <div data-auth="basic">${field("Usuário", inp("prometheus_username", e.prometheus_username, 'autocomplete="off"'))}</div>
    <div data-auth="basic">${field("Senha", inp("prometheus_password", e.prometheus_password, 'type="password" autocomplete="new-password"'), "ou <code>${VARIAVEL}</code>")}</div>
    <label class="check full"><input type="checkbox" name="prometheus_verify_tls" ${e.prometheus_verify_tls ? "checked" : ""}> Verificar certificado TLS</label>
  </div>`;
  const m = modal(isNew ? "Novo ambiente" : `Editar ${e.name}`, body, {
    onSubmit: async (form) => {
      const data = formData(form);
      data.sort_order = data.sort_order ?? 0;
      if (isNew) await api("/api/environments", { method: "POST", body: data });
      else await api(`/api/environments/${e.id}`, { method: "PUT", body: data });
      toast("Ambiente salvo");
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
    <div class="filters"><span class="muted">O relatório semanal é enviado para todos os webhooks ativos.</span><div style="flex:1"></div><button class="btn primary" id="w-new" type="button">+ Novo webhook</button></div>
    <div class="card"><div class="table-wrap"><table>
      <thead><tr><th>Nome</th><th>Tipo</th><th>URL</th><th>Ambientes</th><th>Último envio</th><th></th></tr></thead>
      <tbody>${hooks.map((w) => `<tr><td><b>${esc(w.name)}</b> ${w.enabled ? "" : '<span class="chip">desativado</span>'}${w.only_outdated ? ' <span class="chip">só se houver pendências</span>' : ""}</td>
        <td>${esc(S.meta.webhook_kinds[w.kind] || w.kind)}</td><td class="mono small">${esc(w.url)}</td>
        <td class="small">${w.environment_ids.length ? w.environment_ids.map(envName).map(esc).join(", ") : "todos"}</td>
        <td class="small">${w.last_sent_at ? `${fmtDate(w.last_sent_at, true)}<div class="muted">${esc(w.last_status || "")}</div>` : '<span class="muted">nunca</span>'}</td>
        <td class="nowrap right"><button class="btn sm" data-test="${w.id}" type="button">Testar</button> <button class="btn sm" data-edit="${w.id}" type="button">Editar</button> <button class="btn sm danger" data-del="${w.id}" type="button">Excluir</button></td></tr>`).join("") || `<tr><td colspan="6" class="empty">Nenhum webhook configurado.</td></tr>`}</tbody>
    </table></div></div>`;
  $("#w-new", view).addEventListener("click", () => hookForm());
  $$("[data-edit]", view).forEach((b) => b.addEventListener("click", () => hookForm(hooks.find((w) => w.id === Number(b.dataset.edit)))));
  $$("[data-del]", view).forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Excluir webhook?")) return;
    await api(`/api/webhooks/${b.dataset.del}`, { method: "DELETE" }); route();
  }));
  $$("[data-test]", view).forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const r = await api(`/api/webhooks/${b.dataset.test}/test`, { method: "POST" });
      toast(r.ok ? `Enviado: ${r.detail}` : `Falhou: ${r.detail}`, !r.ok);
    } finally { b.disabled = false; route(); }
  }));
}

function hookForm(w = null) {
  const isNew = !w;
  w = w || { kind: "slack", enabled: true, environment_ids: [] };
  const body = `<div class="form-grid">
    ${field("Nome *", inp("name", w.name, "required"))}
    ${field("Tipo", `<select name="kind">${Object.entries(S.meta.webhook_kinds).map(([k, v]) => opt(k, v, w.kind)).join("")}</select>`)}
    ${field("URL *", inp("url", w.url, 'required autocomplete="off"'), "a URL fica oculta após salvar; use <code>${VARIAVEL}</code> para ler de uma variável de ambiente", "full")}
    <div class="full"><div class="small muted" style="margin-bottom:6px">Ambientes incluídos (nenhum marcado = todos)</div><div class="pill-row">${S.envs.map((e) => `<label class="check"><input type="checkbox" data-skip="1" data-env-id="${e.id}" ${w.environment_ids.includes(e.id) ? "checked" : ""}> ${esc(e.name)}</label>`).join("")}</div></div>
    <label class="check"><input type="checkbox" name="enabled" ${w.enabled ? "checked" : ""}> Ativo</label>
    <label class="check"><input type="checkbox" name="only_outdated" ${w.only_outdated ? "checked" : ""}> Enviar só quando houver aplicações desatualizadas</label>
  </div>`;
  modal(isNew ? "Novo webhook" : `Editar ${w.name}`, body, {
    onSubmit: async (form) => {
      const data = formData(form);
      data.environment_ids = $$("[data-env-id]", form).filter((c) => c.checked).map((c) => Number(c.dataset.envId));
      if (isNew) await api("/api/webhooks", { method: "POST", body: data });
      else await api(`/api/webhooks/${w.id}`, { method: "PUT", body: data });
      toast("Webhook salvo"); route();
    },
  });
}

// -------------------------------------------------------------- discover
async function viewDiscover(view) {
  const envs = S.envs.filter((e) => e.prometheus_url);
  if (!envs.length) { view.innerHTML = `<div class="card empty">Configure o Prometheus de um <a href="#/environments">ambiente</a> para descobrir as aplicações do cluster.</div>`; return; }
  const st = viewDiscover.state ||= { env: envs[0].id, namespace: "", result: null };
  view.innerHTML = `
    <form class="card filters" id="dc-form" style="margin-bottom:16px">
      ${field("Ambiente", `<select id="dc-env">${envs.map((e) => opt(e.id, e.name, st.env)).join("")}</select>`)}
      ${field("Namespace", `<input id="dc-ns" value="${esc(st.namespace)}" placeholder="vazio = todos">`)}
      <button class="btn primary" type="submit" style="margin-top:18px">Descobrir</button>
      <span class="small muted" style="margin-top:18px">Agrupa os workloads por release Helm (Secrets <code>sh.helm.release.v1.*</code> e label <code>app.kubernetes.io/instance</code>).</span>
    </form>
    <div id="dc-out"></div>`;
  const out = $("#dc-out", view);
  const draw = () => {
    const r = st.result;
    if (!r) return;
    if (!r.ok) { out.innerHTML = `<div class="card err">⚠ ${esc(r.error)}</div>`; return; }
    out.innerHTML = r.groups.map((g, i) => `<div class="card" style="margin-bottom:12px">
      <div class="card-head"><div>
        <div class="row"><h2 style="margin:0">${esc(g.release || g.workloads[0].name)}</h2>
          ${g.release ? `<span class="chip">${g.helm ? `Helm${g.revision ? ` rev ${g.revision}` : ""}` : "release (label)"}</span>` : '<span class="chip">sem Helm</span>'}
          ${g.chart ? `<span class="chip">${esc(g.chart)}</span>` : ""}<span class="small muted">${esc(g.namespace)}</span></div>
        <div class="small muted" style="margin-top:4px">${g.workloads.length} workload(s) · versão ${ver(g.version)} ${g.image ? `<span class="mono">${esc(g.image)}</span>` : ""}</div>
      </div>
      ${g.project ? `<a class="btn sm" href="#/apps/${g.project.id}">Monitorada: ${esc(g.project.name)}</a>` : `<button class="btn sm primary" type="button" data-track="${i}">Monitorar</button>`}</div>
      <details><summary class="small">Workloads e imagens</summary><div class="table-wrap"><table><tbody>
        ${g.workloads.map((w) => `<tr><td class="nowrap"><span class="muted small">${esc(w.kind)}</span> <b>${esc(w.name)}</b></td><td>${w.containers.map((c) => `<div class="small"><span class="muted">${esc(c.container)}</span> <span class="mono">${esc(c.image)}</span></div>`).join("")}</td></tr>`).join("")}
      </tbody></table></div></details>
    </div>`).join("") || `<div class="card empty">Nenhum container encontrado.</div>`;
    $$("[data-track]", out).forEach((b) => b.addEventListener("click", () => projectForm(r.groups[Number(b.dataset.track)].suggestion, true)));
  };
  $("#dc-form", view).addEventListener("submit", async (e) => {
    e.preventDefault();
    st.env = Number($("#dc-env", view).value);
    st.namespace = $("#dc-ns", view).value.trim();
    out.innerHTML = `<div class="card muted">Consultando Prometheus…</div>`;
    try {
      st.result = await api(`/api/environments/${st.env}/discover?namespace=${encodeURIComponent(st.namespace)}`);
    } catch (err) { st.result = { ok: false, error: err.message }; }
    draw();
  });
  draw();
}

// ---------------------------------------------------------------- router
const ROUTES = {
  dashboard: ["Dashboard", viewDashboard],
  apps: ["Aplicações", viewApps],
  discover: ["Descobrir no cluster", viewDiscover],
  compare: ["Comparar ambientes", viewCompare],
  releases: ["Novas versões", viewReleases],
  reports: ["Relatórios", viewReports],
  environments: ["Ambientes", viewEnvironments],
  webhooks: ["Webhooks", viewWebhooks],
};

async function route() {
  const [name = "dashboard", id] = location.hash.replace(/^#\/?/, "").split("/");
  const key = ROUTES[name] ? name : "dashboard";
  const [title, fn] = ROUTES[key];
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === key));
  $("#page-title").textContent = title;
  $("#sidebar").classList.remove("open");
  S.charts = [];
  const view = $("#view");
  try {
    if (key === "apps" && id) await viewAppDetail(view, id);
    else await fn(view, id);
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="err">Erro ao carregar: ${esc(e.message)}</div></div>`;
  }
}

function initTheme() {
  const saved = (() => { try { return localStorage.getItem("cw-theme"); } catch { return null; } })();
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme-toggle").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("cw-theme", next); } catch { /* ignore */ }
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
    $("#view").innerHTML = `<div class="card err">Não foi possível conectar à API: ${esc(e.message)}</div>`;
    return;
  }
  S.refresh = await api("/api/refresh/status");
  renderRefreshInfo();
  if (S.refresh.running) pollRefresh();
  setInterval(renderRefreshInfo, 60000);
  route();
}

init();
