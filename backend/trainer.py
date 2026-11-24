"""Сценарии, сессия обучаемого, правила ПТЭ, эталонный бланк и детерминированная оценка."""

from __future__ import annotations

import itertools
import random
import time
import uuid
from typing import Optional

from grid import (CABLES, ELEMENTS, FAULTABLE, LOADS, NODES, GridState, I_ZERO,
                  apply_protection, solve)

# --------------------------------------------------------------------------
# Погода и нагрузки
# --------------------------------------------------------------------------

CLIMATE = {
    "astana_winter": {
        "city": "Астана", "season": "зима", "air_temp_c": -35,
        "weather": "мороз −35 °C, позёмка, ветер 12 м/с",
        # электроотопление/догрев: рост бытовой и больничной нагрузки
        "factors": {"ZHK1": 1.25, "ZHK2": 1.25, "HOSP": 1.15, "TRCA": 1.0, "TRCB": 1.05},
        "k_temp": 1.0,
    },
    "almaty_summer": {
        "city": "Алматы", "season": "лето", "air_temp_c": 38,
        "weather": "жара +38 °C, грунт прогрет до +25 °C",
        # кондиционирование; I_доп КЛ в земле снижается (ПУЭ, поправка на t грунта +25 °C)
        "factors": {"ZHK1": 1.1, "ZHK2": 1.1, "HOSP": 1.1, "TRCA": 1.15, "TRCB": 1.25},
        "k_temp": 0.94,
    },
}

FAULT_TEMPLATES = {
    "medium": [
        {"faulted": "KL12", "cause": "Повреждение КЛ-10 кВ Ф-12 экскаватором при земляных работах сторонней организации (без допуска).",
         "protections": ["МТЗ Ф-12 — отключение Q-12", "АВР резервного ввода больницы (Q-21К) — отказ: неисправность цепей оперативного тока"],
         "time": "07:42"},
        {"faulted": "KL11", "cause": "Пробой изоляции КЛ Ф-11 в соединительной муфте (старение, увлажнение).",
         "protections": ["ТО Ф-11 — отключение Q-11", "ОЗЗ — сигнал «Земля на I СШ» перед отключением"],
         "time": "19:15"},
        {"faulted": "KL22", "cause": "Повреждение КЛ Ф-22 при прокладке коммуникаций методом ГНБ.",
         "protections": ["МТЗ Ф-22 — отключение Q-22"],
         "time": "13:05"},
    ],
    "high": [
        {"faulted": "T1", "cause": "Внутреннее повреждение Т1 (витковое замыкание), действие газовой защиты.",
         "protections": ["Газовая защита Т1 — отключение В-110 Т1 и Q-Т1", "АВР СВ-10 — отказ (заклинивание привода СВ)"],
         "time": "06:58"},
        {"faulted": "KL11", "cause": "Повреждение КЛ Ф-11 при земляных работах; одновременно — пиковая вечерняя нагрузка.",
         "protections": ["МТЗ Ф-11 — отключение Q-11", "Перегрузка Ф-22 по сигналу АСКУЭ (предупреждение)"],
         "time": "18:40"},
    ],
}


def _questions():
    return [
        "Какой участок и какими аппаратами изолировать в первую очередь?",
        "Кого временно ограничить (III категория), чтобы не перегрузить резервный кабель/трансформатор по току?",
        "В какой последовательности составить бланк переключений и куда направить ДГУ / бригаду ОВБ?",
    ]


def offline_scenario(city_key: str, difficulty: str, seed: Optional[int] = None) -> dict:
    rnd = random.Random(seed)
    clim = CLIMATE[city_key]
    tpl = rnd.choice(FAULT_TEMPLATES[difficulty])
    factors = {k: round(v * rnd.uniform(0.97, 1.05), 3) for k, v in clim["factors"].items()}
    if difficulty == "high" and tpl["faulted"] == "KL11":
        factors["ZHK1"] = round(factors["ZHK1"] * 1.1, 3)
    fe = tpl["faulted"]
    return {
        "title": f"{'Каскадный отказ' if difficulty == 'high' else 'Отключение'}: {ELEMENTS[fe].label}",
        "substation_name": "ПС 110/10 кВ «Северная»",
        "city": clim["city"], "season": clim["season"], "air_temp_c": clim["air_temp_c"],
        "weather": clim["weather"], "time": tpl["time"], "difficulty": difficulty,
        "faulted_element": fe, "cause": tpl["cause"], "protections": tpl["protections"],
        "avr_failed": True, "extra_tripped": [],
        "load_factors": [{"load_id": k, "factor": v} for k, v in factors.items()],
        "k_temp": clim["k_temp"], "dgu_available": True, "dgu_eta_min": rnd.choice([25, 40, 55]),
        "scheme_description": "",
        "incident_description": f"{tpl['time']} — {tpl['cause']} Сработали: " + "; ".join(tpl["protections"]) + ".",
        "reserves_description": "",
        "questions": _questions(),
        "source": "offline",
    }


def sanitize_scenario(sc: dict) -> dict:
    """Приводит сценарий (в т.ч. от LLM) к допустимым значениям топологии."""
    out = dict(sc)
    if out.get("faulted_element") not in FAULTABLE:
        out["faulted_element"] = "KL12"
    out["extra_tripped"] = [b for b in out.get("extra_tripped", []) or []
                            if b in ELEMENTS and ELEMENTS[b].kind == "breaker"]
    lf = {}
    for item in out.get("load_factors", []) or []:
        if item.get("load_id") in LOADS:
            lf[item["load_id"]] = min(1.6, max(0.6, float(item.get("factor", 1.0))))
    out["load_factors"] = [{"load_id": k, "factor": lf.get(k, 1.0)} for k in LOADS]
    out["k_temp"] = min(1.15, max(0.8, float(out.get("k_temp", 1.0) or 1.0)))
    out["dgu_available"] = bool(out.get("dgu_available", True))
    qs = [q for q in (out.get("questions") or []) if isinstance(q, str) and q.strip()]
    out["questions"] = (qs + _questions())[:3] if len(qs) < 3 else qs[:3]
    return out


def initial_state(sc: dict) -> tuple[GridState, list[dict]]:
    st = GridState()
    st.k_temp = sc["k_temp"]
    st.dgu_available = sc["dgu_available"]
    for item in sc["load_factors"]:
        st.loads[item["load_id"]] = round(NODES[item["load_id"]].base_load * item["factor"], 1)
    st.faulted = [sc["faulted_element"]]
    if sc["faulted_element"] in ("T1", "T2"):
        # газовая защита действует на отключение трансформатора со всех сторон
        k = sc["faulted_element"][1]
        st.switches[f"QT{k}"] = False
        st.switches[f"Q110-T{k}"] = False
    for b in sc.get("extra_tripped", []):
        st.switches[b] = False
    # ДГУ в начале не подключена (на подъезде)
    st.switches["QDGU"] = False
    events = apply_protection(st)
    return st, events


def scheme_summary(sc: dict, st: GridState) -> dict:
    """Фактические цифры для вводной (рассчитаны солвером, а не придуманы LLM)."""
    pre = st.clone()
    pre.faulted = []
    for k, el in ELEMENTS.items():
        if el.kind in ("breaker", "disconnector", "earth"):
            pre.switches[k] = el.normal_closed
    pre.switches["QDGU"] = False
    r = solve(pre)
    cables = []
    for k in CABLES:
        e = r["elements"][k]
        cables.append({"id": k, "label": ELEMENTS[k].label, "i_dop": e["i_dop"], "pre_fault_current": e["current"]})
    return {
        "loads": [{"id": k, "label": NODES[k].label, "category": NODES[k].category, "current": st.loads[k]} for k in LOADS],
        "cables": cables,
        "transformers": [{"id": t, "current": r["elements"][t]["current"], "i_nom": 550} for t in ("T1", "T2")],
    }


# --------------------------------------------------------------------------
# Сессия обучаемого
# --------------------------------------------------------------------------

def _cable_ends(cable_id: str) -> tuple[str, str]:
    el = ELEMENTS[cable_id]
    return el.a, el.b


class Session:
    def __init__(self, scenario: dict):
        self.id = uuid.uuid4().hex[:12]
        self.scenario = scenario
        self.state, self.initial_events = initial_state(scenario)
        self.initial = self.state.clone()
        self.log: list[dict] = []
        self.violations: list[dict] = []
        self.verified_dead: set[str] = set()
        self.peak_loading: dict[str, float] = {}
        self.started = time.time()
        self.result = solve(self.state)
        self._track(self.result)

    # ----------------------------------------------------------------
    def _track(self, res):
        for k, e in res["elements"].items():
            if "loading" in e:
                self.peak_loading[k] = max(self.peak_loading.get(k, 0.0), e["loading"])
        self.peak_loading["DGU"] = max(self.peak_loading.get("DGU", 0.0), res["dgu"]["loading"])
        live = set(res["energized_nodes"])
        for c in list(self.verified_dead):
            a, b = _cable_ends(c)
            if a in live or b in live:
                self.verified_dead.discard(c)

    def _violation(self, code, severity, text, step):
        v = {"code": code, "severity": severity, "text": text, "step": step}
        self.violations.append(v)
        return v

    def reset(self):
        self.state = self.initial.clone()
        self.log.clear()
        self.violations.clear()
        self.verified_dead.clear()
        self.peak_loading.clear()
        self.started = time.time()
        self.result = solve(self.state)
        self._track(self.result)

    def check_voltage(self, cable_id: str) -> dict:
        if cable_id not in CABLES:
            raise ValueError("Проверка напряжения выполняется на КЛ")
        live = set(self.result["energized_nodes"])
        a, b = _cable_ends(cable_id)
        present = a in live or b in live
        if not present:
            self.verified_dead.add(cable_id)
        step = len(self.log) + 1
        entry = {
            "n": step, "t": round(time.time() - self.started, 1), "element": cable_id,
            "label": ELEMENTS[cable_id].label, "action": "Проверить отсутствие напряжения (УВН)",
            "result": "Напряжение ЕСТЬ" if present else "Напряжение отсутствует",
            "violations": [], "events": [],
            "unserved": self.result["unserved"], "max_loading": self.result["max_loading"],
        }
        self.log.append(entry)
        return entry

    def toggle(self, eid: str) -> dict:
        if eid not in ELEMENTS or ELEMENTS[eid].kind not in ("breaker", "disconnector", "earth"):
            raise ValueError(f"Элемент {eid} не является коммутационным аппаратом")
        el = ELEMENTS[eid]
        step = len(self.log) + 1
        before = self.result
        closing = not self.state.switches[eid]
        viols = []
        hosp_before = before["loads"]["HOSP"]["energized"]

        if el.kind == "earth" and closing:
            a, b = _cable_ends(el.cable)
            live = set(before["energized_nodes"])
            if a in live or b in live:
                viols.append(self._violation("EARTH_ON_LIVE", "critical",
                                             f"Включение {el.label} на КЛ под напряжением — КЗ, поражение персонала!", step))
            elif el.cable not in self.verified_dead:
                viols.append(self._violation("EARTH_NO_CHECK", "major",
                                             f"{el.label} включены без проверки отсутствия напряжения указателем (ПТЭ/ПТБ).", step))

        i_before = before["elements"][eid]["current"] if not closing else 0.0
        new = self.state.clone()
        new.switches[eid] = closing
        events = apply_protection(new)
        after = solve(new)
        i_after = after["elements"][eid]["current"] if closing and not events else 0.0

        if el.kind == "disconnector":
            i_op = max(i_before, i_after)
            if i_op > I_ZERO:
                viols.append(self._violation("DISCONNECTOR_UNDER_LOAD", "critical",
                                             f"{'Отключение' if not closing else 'Включение'} разъединителя {el.label} "
                                             f"под током {i_op:.0f} А — открытая дуга, КЗ по фазам. Разъединителем "
                                             f"разрешено коммутировать только без нагрузки (ПТЭ).", step))
        if events:
            if el.kind == "earth":
                pass  # уже зафиксировано как EARTH_ON_LIVE
            else:
                viols.append(self._violation("ENERGIZE_FAULT", "critical",
                                             f"Подача напряжения на повреждённый/заземлённый участок аппаратом {el.label} — "
                                             f"повторное КЗ, отключение защитой.", step))
        if any(w["code"] == "DGU_PARALLEL" for w in after["warnings"]) and not any(
                w["code"] == "DGU_PARALLEL" for w in before["warnings"]):
            viols.append(self._violation("DGU_PARALLEL", "critical",
                                         "Несинхронное включение ДГУ параллельно с сетью.", step))
        if hosp_before and not after["loads"]["HOSP"]["energized"]:
            viols.append(self._violation("CAT_I_DROPPED", "major",
                                         "Действием обучаемого погашена больница (I категория).", step))

        self.state = new
        self.result = after
        self._track(after)
        entry = {
            "n": step, "t": round(time.time() - self.started, 1), "element": eid, "label": el.label,
            "action": "Включить" if closing else "Отключить",
            "current_switched": round(max(i_before, i_after), 1),
            "violations": viols, "events": events,
            "unserved": after["unserved"], "max_loading": after["max_loading"],
        }
        self.log.append(entry)
        return entry

    def snapshot(self) -> dict:
        return {
            "session_id": self.id,
            "switches": self.state.switches,
            "faulted": self.state.faulted,
            "loads_demand": self.state.loads,
            "k_temp": self.state.k_temp,
            "result": self.result,
            "log": self.log,
            "violations": self.violations,
            "verified_dead": sorted(self.verified_dead),
        }


# --------------------------------------------------------------------------
# Эталонный бланк переключений (детерминированный поиск)
# --------------------------------------------------------------------------

def _isolation_devices(fe: str) -> list[str]:
    """Аппараты, отделяющие повреждённый элемент: сначала выключатели, затем разъединители."""
    if fe in ("T1", "T2"):
        k = fe[1]
        return [f"QT{k}", f"Q110-T{k}"]
    code = fe[2:]
    if code == "P1":
        return ["QP1", "QSP1"]
    devs = [f"Q{code}"]
    if f"QK{code}" in ELEMENTS:
        devs.append(f"QK{code}")
    devs.append(f"QS{code}")
    if f"QSK{code}" in ELEMENTS:
        devs.append(f"QSK{code}")
    return devs


RESTORE_CANDIDATES = ["QTRCB", "QTRCA", "QK21", "QSV", "QP1", "QDGU"]
CLOSE_PRIORITY = ["QK21", "QSV", "QP1", "QDGU", "QTRCA", "QTRCB"]


def reference_plan(scenario: dict, init: GridState) -> dict:
    fe = scenario["faulted_element"]
    st = init.clone()
    steps = []

    def add(obj, cmd, note=""):
        steps.append({"n": len(steps) + 1, "object": obj, "command": cmd, "note": note})

    add("Диспетчер ОДС", "Принять сообщение о срабатывании РЗА, записать в оперативный журнал, сообщить руководству",
        "Время, сработавшие защиты, положение аппаратов")
    devs = _isolation_devices(fe)
    for d in devs:
        el = ELEMENTS[d]
        if el.kind == "breaker":
            if st.switches[d]:
                st.switches[d] = False
                add(el.label, "Отключить", "Снятие нагрузки выключателем")
            else:
                add(el.label, "Проверить отключённое положение", "Отключён защитой; снять оперативный ток с привода")
    for d in devs:
        el = ELEMENTS[d]
        if el.kind == "disconnector":
            st.switches[d] = False
            add(el.label, "Отключить", "Разъединитель — только после отключения выключателя (ток = 0)")
    base = st.clone()
    cands = [c for c in RESTORE_CANDIDATES if c not in devs]
    best = None
    for combo in itertools.product([False, True], repeat=len(cands)):
        t = base.clone()
        for c, v in zip(cands, combo):
            t.switches[c] = v
        if apply_protection(t.clone()):
            continue
        r = solve(t)
        if any(w["code"] in ("OVERLOAD", "DGU_OVERLOAD", "DGU_PARALLEL") for w in r["warnings"]):
            continue
        ops = sum(1 for c, v in zip(cands, combo) if v != base.switches[c])
        par = any(w["code"] == "TR_PARALLEL" for w in r["warnings"])
        u = r["unserved"]
        score = 1000 * u["I"] + 10 * u["II"] + 1 * u["III"] + (60 if par else 0) + 2 * ops \
            + (5 if r["loads"]["HOSP"]["fed_by_dgu_only"] else 0) \
            + (0.5 * r["loads"]["TRCA"]["demand"] if not r["loads"]["TRCA"]["energized"] else 0)
        if best is None or score < best[0]:
            best = (score, combo, r)
    restore_ops = []
    if best:
        _, combo, r = best
        target = dict(zip(cands, combo))
        opens = [c for c in cands if base.switches[c] and not target[c]]
        closes = sorted([c for c in cands if not base.switches[c] and target[c]], key=CLOSE_PRIORITY.index)
        for c in opens:
            add(ELEMENTS[c].label, "Отключить", "Временное ограничение потребителя III категории до ввода резерва")
            restore_ops.append((c, False))
        for c in closes:
            note = {"QK21": "Питание больницы (I кат.) по резервному вводу от II СШ",
                    "QSV": "Перевод нагрузки I СШ на Т2 через СВ",
                    "QP1": "Резервирование через перемычку П-1",
                    "QDGU": "ДГУ — только на обесточенную и отделённую от сети секцию РП"}.get(c, "")
            add(ELEMENTS[c].label, "Включить", note)
            restore_ops.append((c, True))
        final = base.clone()
        for c, v in restore_ops:
            final.switches[c] = v
        final_res = solve(final)
    else:
        final_res = solve(base)
    # Подготовка рабочего места для бригады — после восстановления питания потребителей
    if fe in CABLES:
        add(ELEMENTS[fe].label, "Проверить отсутствие напряжения указателем УВН-10 на всех фазах", "С обеих сторон КЛ")
        zn = f"ZN{fe[2:]}"
        add(ELEMENTS[zn].label, "Включить", "Заземлить КЛ со стороны ПС; в РП — переносное заземление")
        add(f"Приводы {ELEMENTS[devs[0]].label}, ЛР", "Вывесить плакаты «Не включать! Работа на линии»", "")
    else:
        add(f"Т{fe[1]}", "Вывесить плакаты «Не включать! Работают люди», проверить отсутствие напряжения, наложить заземления", "")
    add("Бригада ОВБ / ЛЭЛ", "Направить на трассу для определения места повреждения (прожиг, импульсный метод), "
        "ДГУ — в РП больницы в горячий резерв", "")
    add("Диспетчер ОДС", "Записать в журнал, проконтролировать токи нагрузки по телеизмерениям", "")
    return {
        "steps": steps,
        "final": final_res,
        "isolation_devices": devs,
        "restore_ops": restore_ops,
        "switch_ops": len(devs) + len(restore_ops) + (1 if fe in CABLES else 0),
    }


# --------------------------------------------------------------------------
# Детерминированная оценка
# --------------------------------------------------------------------------

def evaluate(session: Session) -> dict:
    sc = session.scenario
    fe = sc["faulted_element"]
    ref = reference_plan(sc, session.initial)
    res = session.result
    sw = session.state.switches
    vcodes = [v["code"] for v in session.violations]
    findings = []

    # --- Локализация (0–30)
    loc = 0
    devs = ref["isolation_devices"]
    breakers_open = all(not sw[d] for d in devs if ELEMENTS[d].kind == "breaker")
    discon_open = all(not sw[d] for d in devs if ELEMENTS[d].kind == "disconnector")
    if breakers_open and discon_open:
        loc += 12
        findings.append(("loc", "ok", "Повреждённый участок отделён с обеих сторон видимым разрывом."))
    elif breakers_open:
        loc += 6
        findings.append(("loc", "warning", "Участок отключён выключателями, но нет видимого разрыва разъединителями."))
    else:
        findings.append(("loc", "fail", "Повреждённый элемент не отделён от сети."))
    if fe in CABLES:
        zn = f"ZN{fe[2:]}"
        if sw[zn] and "EARTH_NO_CHECK" not in vcodes and "EARTH_ON_LIVE" not in vcodes:
            loc += 6
        elif sw[zn]:
            loc += 2
        else:
            findings.append(("loc", "warning", "КЛ не заземлена — бригаду допускать нельзя."))
    else:
        loc += 6 if breakers_open else 0
    if "ENERGIZE_FAULT" not in vcodes:
        loc += 6
    n_ops = sum(1 for e in session.log if e["action"] in ("Включить", "Отключить"))
    extra = max(0, n_ops - ref["switch_ops"] - 2)
    loc += max(0, 6 - extra)
    loc = min(30, loc)

    # --- Токовые лимиты (0–35)
    lim = 0
    over = [w for w in res["warnings"] if w["code"] in ("OVERLOAD", "DGU_OVERLOAD")]
    if not over:
        lim += 15
    else:
        worst = max([res["elements"][w["element"]]["loading"] for w in over if "element" in w] + [res["dgu"]["loading"]])
        lim += max(0, int(15 - (worst - 100)))
        for w in over:
            findings.append(("lim", "fail", w["text"]))
    peak = max(session.peak_loading.values() or [0])
    lim += 5 if peak <= 100 else (2 if peak <= 110 else 0)
    if peak > 100:
        findings.append(("lim", "warning", f"В процессе переключений максимальная загрузка достигала {peak:.0f}% I_доп."))
    ref_u = ref["final"]["unserved"]
    total = {c: sum(session.state.loads[k] for k in LOADS if NODES[k].category == c) for c in ("II", "III")}

    def served_ratio(c):
        ref_served = total[c] - ref_u[c]
        mine = total[c] - res["unserved"][c]
        if ref_served <= 0:
            return 1.0
        return max(0.0, min(1.0, mine / ref_served))
    restore = 10 * served_ratio("II") + 5 * served_ratio("III")
    if over:  # восстановление ценой перегрузки не засчитывается полностью
        restore *= 0.3
        findings.append(("lim", "fail", "Питание восстановлено ценой перегрузки — режим будет отключён защитой."))
    lim += round(restore)
    lim = min(35, lim)

    # --- Безопасность (0–35)
    pen = {"DISCONNECTOR_UNDER_LOAD": 12, "ENERGIZE_FAULT": 12, "EARTH_ON_LIVE": 20,
           "EARTH_NO_CHECK": 8, "DGU_PARALLEL": 12, "CAT_I_DROPPED": 8}
    saf = 35 - sum(pen.get(c, 0) for c in vcodes)
    if not res["loads"]["HOSP"]["energized"]:
        saf -= 15
        findings.append(("cat", "fail", "Больница (I категория) осталась без питания: не введён резерв (Ф-21) и не подключена ДГУ."))
    else:
        findings.append(("cat", "ok", "Больница (I категория) запитана" + (" от ДГУ." if res["loads"]["HOSP"]["fed_by_dgu_only"] else " от сети.")))
    if any(w["code"] == "TR_PARALLEL" for w in res["warnings"]):
        saf -= 3
        findings.append(("saf", "warning", "Оставлена параллельная работа Т1 и Т2 — нарушение нормальной схемы, рост токов КЗ."))
    for v in session.violations:
        findings.append(("saf", "fail", f"Операция №{v['step']}: {v['text']}"))
    saf = max(0, saf)

    return {
        "scores": {"localization": loc, "limits": lim, "safety": saf, "total": loc + lim + saf},
        "findings": [{"criterion": c, "verdict": v, "text": t} for c, v, t in findings],
        "reference": {"steps": ref["steps"], "unserved": ref_u,
                      "loads": ref["final"]["loads"],
                      "key_currents": {k: ref["final"]["elements"][k]["current"]
                                       for k in ("T1", "T2", *CABLES)}},
        "final_unserved": res["unserved"],
        "ops": n_ops,
    }
