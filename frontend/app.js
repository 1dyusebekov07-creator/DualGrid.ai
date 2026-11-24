"use strict";
/* DualGrid Tutor — клиент: SVG-схема, вызовы API, панели вводной/журнала/разбора. */

const $ = (s) => document.querySelector(s);
const SVGNS = "http://www.w3.org/2000/svg";
const app = { topo: null, nodes: {}, els: {}, snap: null, scenario: null, scheme: null, mode: "switch", sid: null };

const CRIT_NAMES = {
  category: "1. Категория надёжности (I кат.)",
  thermal: "2. Тепловой предел кабелей (I_раб ≤ I_доп)",
  sequence: "3. Логика изоляции и очерёдность",
};

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Ошибка ${r.status}`);
  return data;
}

function busy(on, text) {
  $("#busy").hidden = !on;
  if (text) $("#busy-text").textContent = text;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

function toast(text, critical) {
  const t = document.createElement("div");
  t.textContent = text;
  Object.assign(t.style, {
    position: "fixed", right: "20px", bottom: "20px", maxWidth: "420px", zIndex: 30,
    background: critical ? "#3a1416" : "#1d2a38", border: `1px solid ${critical ? "#ff4d4f" : "#4fb3ff"}`,
    padding: "10px 14px", borderRadius: "8px", boxShadow: "0 6px 24px #0008", lineHeight: 1.4,
  });
  document.body.appendChild(t);
  setTimeout(() => t.remove(), critical ? 6500 : 3500);
}

/* ---------------- геометрия ---------------- */
function polyLen(pts) {
  let L = 0;
  for (let i = 1; i < pts.length; i++) L += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  return L;
}
function pointAt(pts, frac) {
  const total = polyLen(pts) * frac;
  let acc = 0;
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i];
    const seg = Math.hypot(x1 - x0, y1 - y0);
    if (acc + seg >= total || i === pts.length - 1) {
      const t = seg ? Math.min(1, (total - acc) / seg) : 0;
      return { x: x0 + (x1 - x0) * t, y: y0 + (y1 - y0) * t, ang: Math.atan2(y1 - y0, x1 - x0), i };
    }
    acc += seg;
  }
  return { x: pts[0][0], y: pts[0][1], ang: 0, i: 1 };
}
function splitAt(pts, frac) {
  const p = pointAt(pts, frac);
  return [[...pts.slice(0, p.i), [p.x, p.y]], [[p.x, p.y], ...pts.slice(p.i)]];
}
const ptsStr = (pts) => pts.map((p) => p.join(",")).join(" ");

/* ---------------- отрисовка схемы ---------------- */
const SYMBOL_FRAC = { QSP1: 0.17 };
const LABEL_LEFT = new Set(["QTRCA", "QTRCB", "QSK21"]);
const EARTH_ATTACH = { ZNP1: [560, 690] };
const SHORT_NAMES = { TRCA: "ТРЦ-А (эвакуация)", TRCB: "ТРЦ-Б (HVAC, реклама)" };
function liveSet() { return new Set(app.snap ? app.snap.result.energized_nodes : []); }

function wireClass(info, live, hv) {
  if (!live) return "dead";
  if (info && info.faulted) return "fault";
  if (info && info.loading !== undefined) {
    if (info.loading > 100) return "over";
    if (info.loading >= 80) return "warn";
  }
  return hv ? "hv" : "live";
}

function renderScheme() {
  const svg = $("#scheme");
  svg.innerHTML = "";
  svg.classList.toggle("mode-check", app.mode === "check");
  const live = liveSet();
  const res = app.snap ? app.snap.result : null;
  const info = (id) => (res ? res.elements[id] : {});
  const gW = el("g", {}, svg), gS = el("g", {}, svg), gL = el("g", { "pointer-events": "none" }, svg);

  // шины
  for (const [id, x0, x1] of [["B1", 70, 470], ["B2", 530, 930]]) {
    const n = app.nodes[id];
    el("line", { x1: x0, y1: n.y, x2: x1, y2: n.y, class: `wire bus ${live.has(id) ? "live" : "dead"}` }, gW);
    el("text", { x: x0, y: n.y - 10, class: "lbl strong" }, gL).textContent = n.label;
  }
  // РП ТРЦ — короткая шина
  const trc = app.nodes.TRC;
  el("line", { x1: 680, y1: trc.y, x2: 840, y2: trc.y, class: `wire bus ${live.has("TRC") ? "live" : "dead"}`, "stroke-width": 5 }, gW);
  el("text", { x: 845, y: trc.y + 4, class: "lbl strong" }, gL).textContent = "РП ТРЦ";

  for (const e of app.topo.elements) {
    const inf = info(e.id) || {};
    const hv = e.id.startsWith("Q110") || (e.kind === "transformer");
    if (e.kind === "cable") {
      const isLive = live.has(e.a) || live.has(e.b);
      const cls = app.snap && app.snap.faulted.includes(e.id) ? "fault" : wireClass(inf, isLive, false);
      el("polyline", { points: ptsStr(e.pts), class: `wire cable ${cls}` }, gW);
      const hit = el("polyline", { points: ptsStr(e.pts), class: "hit cable-hit", "stroke-width": 16, "data-id": e.id }, gS);
      hit.addEventListener("click", () => onCableClick(e.id));
      bindTip(hit, e.id);
      drawAmpLabel(gL, e, inf, isLive);
      if (app.snap && app.snap.faulted.includes(e.id)) drawFault(gS, pointAt(e.pts, 0.55));
      if (app.snap && app.snap.verified_dead.includes(e.id)) {
        const p = pointAt(e.pts, 0.2);
        el("text", { x: p.x - 22, y: p.y + 4, class: "checked-mark" }, gL).textContent = "✓УВН";
      }
      continue;
    }
    if (e.kind === "transformer") {
      const isLive = live.has(e.a);
      el("polyline", { points: ptsStr(e.pts), class: `wire ${isLive ? "hv" : "dead"}` }, gW);
      const p = pointAt(e.pts, 0.5);
      const faulted = app.snap && app.snap.faulted.includes(e.id);
      el("circle", { cx: p.x, cy: p.y - 8, r: 13, class: "tr-coil", stroke: isLive ? "var(--v110)" : "var(--dead)", fill: "var(--bg)" }, gS);
      el("circle", { cx: p.x, cy: p.y + 8, r: 13, class: "tr-coil", stroke: live.has(e.b) ? "var(--v10)" : "var(--dead)", fill: "none" }, gS);
      if (faulted) drawFault(gS, { x: p.x + 22, y: p.y });
      el("text", { x: p.x + 20, y: p.y - 14, class: "lbl strong" }, gL).textContent = e.label.split(" ")[0];
      const t = el("text", { x: p.x + 20, y: p.y + 2, class: `amp ${ampCls(inf, live.has(e.b))}` }, gL);
      t.textContent = `${Math.round(inf.current || 0)}/${inf.i_dop} А · ${Math.round(inf.loading || 0)}%`;
      const hit = el("circle", { cx: p.x, cy: p.y, r: 22, class: "hit" }, gS);
      bindTip(hit, e.id);
      continue;
    }
    if (e.kind === "earth") { drawEarth(gW, gS, e); continue; }
    // выключатели и разъединители: половинки провода окрашены по своим узлам
    const [h1, h2] = splitAt(e.pts, SYMBOL_FRAC[e.id] || 0.5);
    el("polyline", { points: ptsStr(h1), class: `wire ${live.has(e.a) ? (hv ? "hv" : "live") : "dead"}` }, gW);
    el("polyline", { points: ptsStr(h2), class: `wire ${live.has(e.b) ? (hv ? "hv" : "live") : "dead"}` }, gW);
    const p = pointAt(e.pts, SYMBOL_FRAC[e.id] || 0.5);
    const closed = app.snap ? app.snap.switches[e.id] : e.normal_closed;
    const g = el("g", { class: "sw", "data-id": e.id }, gS);
    if (e.kind === "breaker") {
      el("rect", { x: p.x - 8, y: p.y - 8, width: 16, height: 16, rx: 2, class: `sym brk-sym ${closed ? "on" : "off"}` }, g);
    } else {
      const ang = p.ang + (closed ? 0 : -0.6);
      const L = 13;
      el("line", { x1: p.x - Math.cos(p.ang) * L, y1: p.y - Math.sin(p.ang) * L,
        x2: p.x - Math.cos(p.ang) * L + Math.cos(ang) * 2 * L, y2: p.y - Math.sin(p.ang) * L + Math.sin(ang) * 2 * L,
        class: `sym ds-blade ${closed ? "on" : "off"}` }, g);
      el("rect", { x: p.x - 13, y: p.y - 13, width: 26, height: 26, fill: "var(--bg)", opacity: 0 }, g);
      el("circle", { cx: p.x - Math.cos(p.ang) * L, cy: p.y - Math.sin(p.ang) * L, r: 2.5, fill: closed ? "var(--closed)" : "var(--open)" }, g);
    }
    el("rect", { x: p.x - 15, y: p.y - 15, width: 30, height: 30, class: "hit" }, g);
    g.addEventListener("click", () => onSwitchClick(e.id));
    bindTip(g, e.id);
    const short = e.label.split(" ")[0];
    const vertical = Math.abs(Math.sin(p.ang)) > 0.7;
    const left = LABEL_LEFT.has(e.id);
    el("text", { x: vertical ? p.x + (left ? -13 : 13) : p.x - 14, y: vertical ? p.y + 4 : p.y - 13, class: "lbl",
      "text-anchor": left ? "end" : "start" }, gL).textContent = short;
  }

  // узлы: источники, нагрузки, ДГУ
  for (const n of app.topo.nodes) {
    if (n.kind === "source" && n.id !== "DGU") {
      el("text", { x: n.x + 12, y: n.y + 4, class: "lbl strong" }, gL).textContent = n.label;
      el("circle", { cx: n.x, cy: n.y, r: 5, fill: "var(--v110)" }, gS);
    }
    if (n.id === "DGU") {
      const ok = app.snap ? app.snap.result.dgu.available : true;
      el("circle", { cx: n.x, cy: n.y + 16, r: 18, fill: "var(--bg)", stroke: ok ? "var(--warn)" : "var(--dead)", "stroke-width": 2.5 }, gS);
      el("text", { x: n.x, y: n.y + 21, "text-anchor": "middle", class: "lbl strong" }, gL).textContent = "G~";
      el("text", { x: n.x - 24, y: n.y + 50, class: "lbl" }, gL).textContent = "ДГУ 2500 кВА";
    }
    if (n.kind === "load") drawLoad(gS, gL, n, res);
  }
}

function ampCls(inf, isLive) {
  if (!isLive || !inf || !inf.current) return "dead";
  if (inf.loading > 100) return "over";
  if (inf.loading >= 80) return "warn";
  return "ok";
}

function drawAmpLabel(g, e, inf, isLive) {
  const anchors = { KL11: [128, 430], KL12: [300, 445], KL21: [588, 455], KL22: [650, 395], KL23: [800, 430], KLP1: [380, 708] };
  const [x, y] = anchors[e.id] || [pointAt(e.pts, 0.5).x + 8, pointAt(e.pts, 0.5).y];
  const name = e.label.replace("КЛ ", "").split(" ")[0];
  const l1 = `${name} ${e.cable_type.replace("АСБ-10 ", "").replace("ААШв-10 ", "")}`;
  const l2 = `${Math.round(inf.current || 0)} / ${Math.round(inf.i_dop || e.i_dop)} А`;
  const l3 = `${Math.round(inf.loading || 0)}% I_доп`;
  el("rect", { x: x - 4, y: y - 13, width: 112, height: 46, class: "amp-bg" }, g);
  el("text", { x, y, class: "lbl strong" }, g).textContent = l1;
  el("text", { x, y: y + 14, class: `amp ${ampCls(inf, isLive)}` }, g).textContent = l2;
  el("text", { x, y: y + 28, class: `amp ${ampCls(inf, isLive)}` }, g).textContent = l3;
}

function drawFault(g, p) {
  const { x, y } = p;
  el("polygon", { points: `${x + 2},${y - 16} ${x - 8},${y + 2} ${x - 1},${y + 2} ${x - 4},${y + 16} ${x + 8},${y - 3} ${x + 1},${y - 3}`, class: "fault-mark" }, g);
}

function drawEarth(gW, gS, e) {
  const [p0, p1] = e.pts;
  const closed = app.snap ? app.snap.switches[e.id] : false;
  const [ax, ay] = EARTH_ATTACH[e.id] || [p0[0] - 16, p0[1]]; // точка присоединения к кабелю
  el("line", { x1: ax, y1: ay, x2: p0[0], y2: p0[1], class: "wire dead", "stroke-width": 2 }, gW);
  const g = el("g", { class: "sw", "data-id": e.id }, gS);
  const ang = closed ? 0 : -0.7;
  el("line", { x1: p0[0], y1: p0[1], x2: p0[0] + Math.cos(ang) * 18, y2: p0[1] + Math.sin(ang) * 18,
    class: `sym ds-blade ${closed ? "on" : "off"}` }, g);
  const gx = p1[0] + 2;
  for (const [i, h] of [[0, 9], [1, 6], [2, 3]]) {
    el("line", { x1: gx + i * 4, y1: p0[1] - h, x2: gx + i * 4, y2: p0[1] + h, stroke: closed ? "var(--closed)" : "var(--muted)", "stroke-width": 2 }, g);
  }
  el("rect", { x: p0[0] - 4, y: p0[1] - 14, width: 36, height: 28, class: "hit" }, g);
  g.addEventListener("click", () => onSwitchClick(e.id));
  bindTip(g, e.id);
}

function drawLoad(gS, gL, n, res) {
  const w = n.id === "HOSP" ? 170 : (n.id.startsWith("TRC") ? 140 : 140), h = 40;
  const l = res ? res.loads[n.id] : null;
  const on = l ? l.energized : true;
  const cat = n.category;
  el("rect", { x: n.x - w / 2, y: n.y - 20, width: w, height: h,
    class: `load-box ${on ? "on" : "off"} ${cat === "III" ? "cat3" : ""}` }, gS);
  const title = SHORT_NAMES[n.id] || n.label.replace(/\s*\((I|II|III) кат\.\)/, "");
  el("text", { x: n.x - w / 2 + 8, y: n.y - 4, class: "lbl strong" }, gL).textContent = title;
  const t = el("text", { x: n.x - w / 2 + 8, y: n.y + 12, class: `amp ${on ? "ok" : (cat === "III" ? "warn" : "over")}` }, gL);
  const amps = l ? Math.round(l.demand) : n.base_load;
  t.textContent = `${cat} кат · ${amps} А` + (on ? (n.id === "HOSP" && l ? ` · ${l.voltage_kv.toFixed(2)} кВ` : "") : " ✖");
  if (n.id === "HOSP") {
    el("rect", { x: n.x + w / 2 - 24, y: n.y - 14, width: 16, height: 16, rx: 3, fill: "#fff" }, gS);
    el("path", { d: `M${n.x + w / 2 - 18} ${n.y - 11}h4v4h4v4h-4v4h-4v-4h-4v-4h4z`, fill: "#e5484d" }, gS);
  }
}

/* ---------------- подсказки ---------------- */
function bindTip(node, id) {
  const tip = $("#tooltip");
  node.addEventListener("mousemove", (ev) => {
    const e = app.els[id];
    const inf = app.snap ? app.snap.result.elements[id] : {};
    const lines = [e.label];
    if (inf && "closed" in inf) lines.push(`Положение: ${inf.closed ? "ВКЛЮЧЕН" : "ОТКЛЮЧЕН"}`);
    lines.push(`Напряжение: ${inf && inf.energized ? "есть" : "нет"}`);
    if (inf && inf.current !== undefined) lines.push(`Ток: ${inf.current} А`);
    if (e.i_dop) lines.push(`I_доп: ${inf.i_dop} А${e.kind === "cable" ? ` (норм. ${e.i_dop} А × k_t)` : ""} · загрузка ${inf.loading}%`);
    if (inf && inf.p_mw !== undefined) lines.push(`P ≈ ${inf.p_mw} МВт`);
    if (e.length_km) lines.push(`L = ${e.length_km} км`);
    if (inf && inf.faulted) lines.push("⚡ ПОВРЕЖДЕНИЕ");
    if (e.kind === "disconnector") lines.push("Коммутировать только без нагрузки!");
    tip.textContent = lines.join("\n");
    const r = $(".scheme-wrap").getBoundingClientRect();
    tip.style.left = `${Math.min(ev.clientX - r.left + 14, r.width - 260)}px`;
    tip.style.top = `${ev.clientY - r.top + 14}px`;
    tip.hidden = false;
  });
  node.addEventListener("mouseleave", () => { tip.hidden = true; });
}

/* ---------------- статус ---------------- */
function meter(id, name, cur, lim, loading, extra = "") {
  const cls = loading > 100 ? "over" : loading >= 80 ? "warn" : "";
  $(`#m-${id}`).innerHTML = `<div class="name"><span>${name}</span><span>${extra}</span></div>
    <div class="val">${Math.round(cur)} / ${Math.round(lim)} А · ${Math.round(loading)}%</div>
    <div class="bar"><i class="${cls}" style="width:${Math.min(100, loading)}%"></i></div>`;
}

function renderStatus() {
  if (!app.snap) return;
  const r = app.snap.result;
  meter("T1", "Т1 ТДН-10000", r.elements.T1.current, 550, r.elements.T1.loading);
  meter("T2", "Т2 ТДН-10000", r.elements.T2.current, 550, r.elements.T2.loading);
  meter("DGU", "ДГУ 2500 кВА", r.dgu.current, r.dgu.rating, r.dgu.loading, r.dgu.current > 0 ? "в работе" : "резерв");
  const u = r.unserved;
  $("#unserved").innerHTML = `<div class="name" style="color:var(--muted)">Без питания (ток нагрузки)</div>
    <div class="row c1"><span>I категория</span><b>${Math.round(u.I)} А</b></div>
    <div class="row c2"><span>II категория</span><b>${Math.round(u.II)} А</b></div>
    <div class="row c3"><span>III категория</span><b>${Math.round(u.III)} А</b></div>`;
  $("#warnings").innerHTML = r.warnings.map((w) => `<li class="${w.severity}">${esc(w.text)}</li>`).join("");
}

function renderLog() {
  const log = app.snap ? app.snap.log : [];
  $("#log-count").textContent = log.length;
  $("#log-empty").hidden = log.length > 0;
  $("#oplog").innerHTML = log.map((e) => `
    <li class="${e.violations && e.violations.length ? "bad" : ""}">
      <div class="head"><span><span class="n">№${e.n}</span> <b>${esc(e.label)}</b> — ${esc(e.action)}</span><span class="n">${e.t} с</span></div>
      ${e.result ? `<div>${esc(e.result)}</div>` : ""}
      ${e.current_switched ? `<div class="n">Коммутируемый ток: ${e.current_switched} А</div>` : ""}
      ${(e.events || []).map((v) => `<div class="ev">⚡ ${esc(v.text)}</div>`).join("")}
      ${(e.violations || []).map((v) => `<div class="v">✖ ${esc(v.text)}</div>`).join("")}
      <div class="n">Без питания: I ${Math.round(e.unserved.I)} · II ${Math.round(e.unserved.II)} · III ${Math.round(e.unserved.III)} А · max ${Math.round(e.max_loading)}%</div>
    </li>`).reverse().join("");
}

function renderAll() { renderScheme(); renderStatus(); renderLog(); }

/* ---------------- вводная ---------------- */
function renderBrief(data) {
  const sc = data.scenario, sch = data.scheme;
  $("#brief-empty").hidden = true;
  const b = $("#brief");
  b.hidden = false;
  const diff = sc.difficulty === "high" ? "Высокий — каскадный отказ" : "Средний";
  b.innerHTML = `
    <h2>${esc(sc.title)}</h2>
    <div class="meta">
      <span class="chip">${esc(sc.city)} · ${esc(sc.season)} · ${sc.air_temp_c > 0 ? "+" : ""}${sc.air_temp_c} °C</span>
      <span class="chip">${esc(sc.time)}</span>
      <span class="chip ${sc.difficulty === "high" ? "red" : ""}">${diff}</span>
      <span class="chip">k_t I_доп = ${sc.k_temp}</span>
      <span class="chip">${sc.source === "claude" ? "Экзаменатор: Claude" : "Экзаменатор: офлайн-шаблон"}</span>
    </div>
    <p class="src">${esc(sc.weather)}</p>
    <h3>1. Исходная схема узла</h3>
    <p>${esc(sc.scheme_description)}</p>
    <table>
      <tr><th>Потребитель</th><th>Кат.</th><th>Ток, А</th></tr>
      ${sch.loads.map((l) => `<tr><td>${esc(l.label)}</td><td>${l.category}</td><td class="num">${Math.round(l.current)}</td></tr>`).join("")}
    </table>
    <table style="margin-top:8px">
      <tr><th>КЛ-10 кВ</th><th>I_доп, А</th><th>До аварии, А</th></tr>
      ${sch.cables.map((c) => `<tr><td>${esc(c.label)}</td><td class="num">${Math.round(c.i_dop)}</td><td class="num">${Math.round(c.pre_fault_current)}</td></tr>`).join("")}
      ${sch.transformers.map((t) => `<tr><td>${t.id} ТДН-10000/110</td><td class="num">${t.i_nom}</td><td class="num">${Math.round(t.current)}</td></tr>`).join("")}
    </table>
    <h3>2. Что произошло</h3>
    <p>${esc(sc.incident_description)}</p>
    <ul>${sc.protections.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
    ${data.initial_events.map((e) => `<p class="src">Расчёт РЗА: ${esc(e.text)}</p>`).join("")}
    <h3>3. Доступные резервы</h3>
    <p>${esc(sc.reserves_description)}</p>
    <h3>4. Вопросы обучаемому</h3>
    <ol class="questions">${sc.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ol>
    <p class="hint">Выполните переключения на схеме слева, затем ответьте на вопросы во вкладке «Ответ». Правильный ответ будет показан только после сдачи.</p>
    ${data.ai_error ? `<p class="hint">Claude API недоступен (${esc(data.ai_error)}), использован офлайн-шаблон.</p>` : ""}`;
  $("#answer-form").innerHTML = sc.questions.map((q, i) =>
    `<label for="ans${i}">${i + 1}. ${esc(q)}</label><textarea id="ans${i}" placeholder="Ваш ответ…"></textarea>`).join("");
  $("#btn-submit").disabled = false;
  $("#review").hidden = true;
  $("#review-empty").hidden = false;
}

/* ---------------- разбор ---------------- */
function renderReview(d) {
  const r = d.report, s = d.scores;
  $("#review-empty").hidden = true;
  const v = $("#review");
  v.hidden = false;
  const sbar = (name, val, max) => {
    const pct = (100 * val) / max;
    const cls = pct < 50 ? "over" : pct < 80 ? "warn" : "";
    return `<div class="sbar">${name}: <b>${val}</b> / ${max}<div class="bar"><i class="${cls}" style="width:${pct}%"></i></div></div>`;
  };
  v.innerHTML = `
    <div class="score-card">
      <div class="score-total">${s.total}<small>/100</small></div>
      <div>
        ${sbar("Скорость и точность локализации", s.localization, 30)}
        ${sbar("Токовые лимиты и баланс", s.limits, 35)}
        ${sbar("Безопасность по ПТЭ/ПУЭ", s.safety, 35)}
      </div>
    </div>
    <p class="src">Наставник: ${d.source === "claude" ? "Claude (оценка сверена с расчётом солвера, отклонение ≤ ±5 баллов)" : "офлайн-режим (детерминированная оценка)"}${d.ai_error ? ` · ошибка API: ${esc(d.ai_error)}` : ""}</p>
    <p>${esc(r.summary)}</p>
    <h3>Инженерная проверка</h3>
    ${r.criteria.map((c) => `<div class="verdict ${c.verdict}"><b>${CRIT_NAMES[c.criterion] || c.criterion}</b>${esc(c.details)}</div>`).join("")}
    <h3>Расчёт токов итогового режима</h3>
    <pre class="calc">${esc(r.calculation)}</pre>
    <h3>Эталонный бланк переключений</h3>
    <table>
      <tr><th>№</th><th>Объект</th><th>Команда</th></tr>
      ${r.blank.map((b) => `<tr><td class="num">${b.n}</td><td>${esc(b.object)}</td><td>${esc(b.command)}${b.note ? `<div class="src">${esc(b.note)}</div>` : ""}</td></tr>`).join("")}
    </table>
    <h3>Эталон: потребители после переключений</h3>
    <p>${Object.values(d.deterministic.reference.loads).map((l) => `${esc(l.label)} — ${l.energized ? "запитан" : "ограничен"}`).join("; ")}.</p>
    <h3>Рекомендация</h3>
    <p>${esc(r.recommendation)}</p>
    <div class="meta">${r.topics.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>`;
}

/* ---------------- действия ---------------- */
async function onSwitchClick(id) {
  if (!app.sid) return toast("Сначала получите задачу у Экзаменатора.");
  if (app.mode !== "switch") return toast("Режим УВН: кликните по кабелю для проверки напряжения.");
  try {
    const d = await api("/api/toggle", { session_id: app.sid, element_id: id });
    app.snap = d;
    renderAll();
    for (const ev of d.entry.events) toast("⚡ " + ev.text, true);
    for (const v of d.entry.violations) toast("✖ " + v.text, true);
  } catch (e) { toast(e.message, true); }
}

async function onCableClick(id) {
  if (!app.sid || app.mode !== "check") return;
  try {
    const d = await api("/api/check", { session_id: app.sid, cable_id: id });
    app.snap = d;
    renderAll();
    toast(`${d.entry.label}: ${d.entry.result}`, d.entry.result.includes("ЕСТЬ"));
  } catch (e) { toast(e.message, true); }
}

async function newScenario() {
  busy(true, app.ai ? "Агент-Экзаменатор (Claude) готовит вводную…" : "Генерирую вводную…");
  try {
    const d = await api("/api/scenario", { city_key: $("#city").value, difficulty: $("#difficulty").value, use_ai: true });
    app.sid = d.session_id;
    app.snap = d;
    app.scenario = d.scenario;
    renderBrief(d);
    renderAll();
    switchTab("brief");
  } catch (e) { toast(e.message, true); }
  busy(false);
}

async function submit() {
  if (!app.sid) return;
  const answers = app.scenario.questions.map((_, i) => ($(`#ans${i}`) || {}).value || "");
  busy(true, app.ai ? "Агент-Наставник (Claude) проверяет решение…" : "Проверяю решение…");
  try {
    const d = await api("/api/submit", { session_id: app.sid, answers, use_ai: true });
    renderReview(d);
    switchTab("review");
  } catch (e) { toast(e.message, true); }
  busy(false);
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tab-body").forEach((b) => { b.hidden = b.id !== `tab-${name}`; });
}

async function init() {
  const [st, topo] = await Promise.all([api("/api/status"), api("/api/topology")]);
  app.ai = st.ai;
  app.topo = topo;
  topo.nodes.forEach((n) => (app.nodes[n.id] = n));
  topo.elements.forEach((e) => (app.els[e.id] = e));
  $("#city").innerHTML = Object.entries(st.cities).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  const pill = $("#ai-pill");
  pill.textContent = st.ai ? `AI: ${st.model}` : "AI: офлайн-режим";
  pill.classList.toggle("ai", st.ai);
  pill.title = st.ai ? "Экзаменатор и Наставник работают через Claude API" : "Нет ANTHROPIC_API_KEY — используются детерминированные шаблоны и расчётная оценка";

  $("#btn-new").addEventListener("click", newScenario);
  $("#btn-submit").addEventListener("click", submit);
  $("#btn-reset").addEventListener("click", async () => {
    if (!app.sid) return;
    app.snap = { ...app.snap, ...(await api("/api/reset", { session_id: app.sid })) };
    renderAll();
  });
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
  document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => {
    app.mode = b.dataset.mode;
    document.querySelectorAll(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    renderScheme();
  }));
  renderScheme();
}

init().catch((e) => toast("Ошибка загрузки: " + e.message, true));
