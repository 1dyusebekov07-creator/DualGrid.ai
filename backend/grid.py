"""Топология учебного узла ПС 110/10 кВ и детерминированный узловой солвер.

Модель: сеть 10 кВ приведена к одной ступени напряжения. Источники (вводы 110 кВ
через трансформаторы, передвижная ДГУ) — узлы с фиксированным напряжением,
нагрузки — источники тока (ток задан в амперах на стороне 10 кВ). Кабели и
трансформаторы — ветви с модулем полного сопротивления |Z|, замкнутые
коммутационные аппараты — ветви с пренебрежимо малым сопротивлением.

Решается система узловых уравнений  Y·U = J  (метод узловых потенциалов),
откуда ток каждой ветви I = (U_a − U_b) / Z. Петли (например, замкнутая
перемычка при включённом СВ) обрабатываются корректно — ток распределится
по сопротивлениям ветвей.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np

U_NOM = 10_500.0          # В, линейное напряжение шин 10 кВ
SQRT3 = 3 ** 0.5
Z_SWITCH = 1e-3           # Ом, сопротивление замкнутого аппарата
I_ZERO = 1.0              # А, ток ниже которого аппарат считается «без нагрузки»


@dataclass
class Node:
    id: str
    label: str
    x: float
    y: float
    kind: str = "node"                 # source | bus | node | load
    category: Optional[str] = None     # I | II | III для нагрузок
    base_load: float = 0.0             # А, базовый ток нагрузки на 10 кВ
    rating: Optional[float] = None     # А, номинальный ток источника (ДГУ)


@dataclass
class Element:
    id: str
    kind: str                          # breaker | disconnector | cable | transformer | earth
    a: str
    b: str
    label: str
    pts: list
    normal_closed: bool = True
    i_dop: Optional[float] = None      # А, допустимый длительный ток (кабель/трансформатор)
    cable_type: Optional[str] = None
    length_km: float = 0.0
    z_ohm: float = Z_SWITCH
    cable: Optional[str] = None        # для ЗН — к какой КЛ относится
    feeder: Optional[str] = None


def _cable_z(r_km: float, x_km: float, length: float) -> float:
    return (r_km ** 2 + x_km ** 2) ** 0.5 * length


# --------------------------------------------------------------------------
# Топология
# --------------------------------------------------------------------------

def build_topology() -> tuple[dict[str, Node], dict[str, Element]]:
    nodes: dict[str, Node] = {}
    els: dict[str, Element] = {}

    def n(*args, **kw):
        node = Node(*args, **kw)
        nodes[node.id] = node
        return node

    def e(*args, **kw):
        el = Element(*args, **kw)
        els[el.id] = el
        return el

    # Источники и трансформаторы ТДН-10000/110 (I_ном,НН = 10000/(√3·10.5) ≈ 550 А)
    z_tr = 0.105 * 10.5 ** 2 / 10.0  # uk·U²/S, Ом приведённые к 10.5 кВ
    for k, x, bus in (("1", 260, "B1"), ("2", 740, "B2")):
        n(f"SRC{k}", f"ВЛ-110 кВ №{k}", x, 28, kind="source")
        n(f"T{k}HV", f"Т{k} ВН", x, 78)
        n(f"T{k}LV", f"Т{k} НН", x, 152)
        e(f"Q110-T{k}", "breaker", f"SRC{k}", f"T{k}HV", f"В-110 Т{k}",
          [[x, 28], [x, 78]])
        e(f"T{k}", "transformer", f"T{k}HV", f"T{k}LV", f"Т{k} ТДН-10000/110",
          [[x, 78], [x, 152]], i_dop=550.0, z_ohm=z_tr)
        e(f"QT{k}", "breaker", f"T{k}LV", bus, f"Q-Т{k} ввод 10 кВ",
          [[x, 152], [x, 220]])

    n("B1", "I СШ 10 кВ", 270, 220, kind="bus")
    n("B2", "II СШ 10 кВ", 730, 220, kind="bus")
    e("QSV", "breaker", "B1", "B2", "СВ-10 (секционный)",
      [[470, 220], [530, 220]], normal_closed=False)

    # Нагрузки
    n("ZHK1", "ЖК «Нурлы» (II кат.)", 120, 560, kind="load", category="II", base_load=170.0)
    n("HOSP", "Больница (I кат.)", 500, 560, kind="load", category="I", base_load=110.0)
    n("TRC", "РП ТРЦ", 760, 520, kind="rp")
    n("TRCA", "ТРЦ-А: эвакуация, лифты (III)", 680, 625, kind="load", category="III", base_load=110.0)
    n("TRCB", "ТРЦ-Б: HVAC, реклама (III)", 840, 625, kind="load", category="III", base_load=100.0)
    n("ZHK2", "ЖК «Береке» (II кат.)", 920, 560, kind="load", category="II", base_load=130.0)

    # Фидеры: (код, шина, x на шине, кабель, I_доп, r, x, L, конечный узел, путь кабеля, конечный аппарат)
    feeders = [
        ("11", "B1", 120, "АСБ-10 3×150", 275.0, 0.206, 0.079, 1.6, "ZHK1",
         [[120, 350], [120, 500]], ("disconnector", True)),
        ("12", "B1", 420, "АСБ-10 3×120", 240.0, 0.253, 0.081, 2.1, "HOSP",
         [[420, 350], [420, 470], [470, 470], [470, 515]], ("disconnector", True)),
        ("21", "B2", 580, "АСБ-10 3×120", 240.0, 0.253, 0.081, 2.4, "HOSP",
         [[580, 350], [580, 470], [530, 470], [530, 515]], ("breaker", False)),
        ("22", "B2", 760, "АСБ-10 3×240", 355.0, 0.129, 0.075, 1.2, "TRC",
         [[760, 350], [760, 470]], ("disconnector", True)),
        ("23", "B2", 920, "ААШв-10 3×95", 205.0, 0.320, 0.083, 1.9, "ZHK2",
         [[920, 350], [920, 500]], ("disconnector", True)),
    ]
    for code, bus, x, ctype, idop, r, xx, L, end, path, (far_kind, far_closed) in feeders:
        f = f"Ф-{code}"
        n(f"F{code}a", f"{f} за выключателем", x, 285)
        n(f"F{code}b", f"{f} начало КЛ", x, 350)
        n(f"F{code}c", f"{f} конец КЛ", path[-1][0], path[-1][1])
        e(f"Q{code}", "breaker", bus, f"F{code}a", f"Q-{code} ({f})",
          [[x, 220], [x, 285]], feeder=f)
        e(f"QS{code}", "disconnector", f"F{code}a", f"F{code}b", f"ЛР-{code}",
          [[x, 285], [x, 350]], feeder=f)
        e(f"KL{code}", "cable", f"F{code}b", f"F{code}c", f"КЛ {f} {ctype}",
          path, i_dop=idop, cable_type=ctype, length_km=L, z_ohm=_cable_z(r, xx, L), feeder=f)
        end_node = nodes[end]
        far_id = f"QSK{code}" if far_kind == "disconnector" else f"QK{code}"
        far_label = f"ЛР-{code}К (в РП)" if far_kind == "disconnector" else f"Q-{code}К резервный ввод (АВР)"
        e(far_id, far_kind, f"F{code}c", end, far_label,
          [path[-1], [path[-1][0], end_node.y - (20 if end_node.kind == "load" else 0)]], normal_closed=far_closed, feeder=f)
        e(f"ZN{code}", "earth", f"F{code}b", f"F{code}b", f"ЗН КЛ {f}",
          [[x + 16, 395], [x + 34, 395]], normal_closed=False, cable=f"KL{code}", feeder=f)

    # Перемычка П-1 ЖК «Нурлы» ↔ РП ТРЦ
    n("P1a", "П-1 у ЖК", 120, 690)
    n("P1b", "П-1 у ТРЦ", 760, 690)
    e("QP1", "breaker", "ZHK1", "P1a", "Q-П1 (перемычка, норм. откл.)",
      [[120, 580], [120, 690]], normal_closed=False, feeder="П-1")
    e("KLP1", "cable", "P1a", "P1b", "КЛ П-1 АСБ-10 3×120",
      [[120, 690], [760, 690]], i_dop=240.0, cable_type="АСБ-10 3×120", length_km=0.9,
      z_ohm=_cable_z(0.253, 0.081, 0.9), feeder="П-1")
    e("QSP1", "disconnector", "P1b", "TRC", "ЛР-П1 (в РП ТРЦ)",
      [[760, 690], [760, 600], [760, 520]], feeder="П-1")
    e("ZNP1", "earth", "P1a", "P1a", "ЗН КЛ П-1", [[560, 712], [578, 712]],
      normal_closed=False, cable="KLP1", feeder="П-1")

    # Нагрузки ТРЦ через выключатели (ступени ограничения)
    e("QTRCA", "breaker", "TRC", "TRCA", "Q-ТРЦ-А", [[740, 520], [680, 520], [680, 605]])
    e("QTRCB", "breaker", "TRC", "TRCB", "Q-ТРЦ-Б", [[780, 520], [840, 520], [840, 605]])

    # Передвижная ДГУ 2500 кВА → I_ном ≈ 137 А на 10.5 кВ
    n("DGU", "ДГУ 2500 кВА (передвижная)", 330, 630, kind="source", rating=137.0)
    e("QDGU", "breaker", "DGU", "HOSP", "Q-ДГУ (ввод ДГУ в РП больницы)",
      [[330, 630], [330, 600], [470, 600], [470, 580]], normal_closed=False)

    return nodes, els


NODES, ELEMENTS = build_topology()
CABLES = [k for k, v in ELEMENTS.items() if v.kind == "cable"]
FAULTABLE = CABLES + ["T1", "T2"]
LOADS = [k for k, v in NODES.items() if v.kind == "load"]
SWITCHABLE = [k for k, v in ELEMENTS.items() if v.kind in ("breaker", "disconnector", "earth")]


def default_switches() -> dict[str, bool]:
    return {k: ELEMENTS[k].normal_closed for k in SWITCHABLE}


# --------------------------------------------------------------------------
# Состояние и расчёт
# --------------------------------------------------------------------------

@dataclass
class GridState:
    switches: dict[str, bool] = field(default_factory=default_switches)
    faulted: list[str] = field(default_factory=list)
    loads: dict[str, float] = field(default_factory=lambda: {k: NODES[k].base_load for k in LOADS})
    k_temp: float = 1.0                 # поправочный коэффициент I_доп по температуре грунта (ПУЭ)
    dgu_available: bool = True

    def clone(self) -> "GridState":
        return copy.deepcopy(self)

    def to_dict(self):
        return asdict(self)


def _conducting(state: GridState, el: Element) -> bool:
    if el.kind in ("cable", "transformer"):
        return True
    if el.kind == "earth":
        return False
    return bool(state.switches.get(el.id, False))


def _sources(state: GridState) -> list[str]:
    src = ["SRC1", "SRC2"]
    if state.dgu_available:
        src.append("DGU")
    return src


def _adjacency(state: GridState):
    adj: dict[str, list[tuple[str, str]]] = {k: [] for k in NODES}
    for el in ELEMENTS.values():
        if _conducting(state, el):
            adj[el.a].append((el.b, el.id))
            adj[el.b].append((el.a, el.id))
    return adj


def _components(state: GridState):
    adj = _adjacency(state)
    comp: dict[str, int] = {}
    cid = 0
    for start in NODES:
        if start in comp:
            continue
        stack = [start]
        comp[start] = cid
        while stack:
            u = stack.pop()
            for v, _ in adj[u]:
                if v not in comp:
                    comp[v] = cid
                    stack.append(v)
        cid += 1
    return comp


def energized_nodes(state: GridState) -> set[str]:
    comp = _components(state)
    live = {comp[s] for s in _sources(state)}
    return {k for k, c in comp.items() if c in live}


def _fault_nodes(state: GridState) -> dict[str, str]:
    """Узлы, в которых есть КЗ: повреждённые элементы и включённые ЗН."""
    out = {}
    for f in state.faulted:
        el = ELEMENTS[f]
        out[el.a] = f
        out[el.b] = f
    for k, v in state.switches.items():
        if v and ELEMENTS[k].kind == "earth":
            out[ELEMENTS[k].a] = k
    return out


def apply_protection(state: GridState) -> list[dict]:
    """Если КЗ оказалось под напряжением — отключаются ближайшие к месту
    повреждения выключатели (селективная работа МТЗ/ТО). Возвращает события."""
    events = []
    for _ in range(10):
        live = energized_nodes(state)
        faults = {nd: src for nd, src in _fault_nodes(state).items() if nd in live}
        if not faults:
            break
        adj = _adjacency(state)
        # Зона КЗ: узлы, связанные с местом повреждения без выключателей
        zone = set(faults)
        stack = list(faults)
        frontier = []
        while stack:
            u = stack.pop()
            for v, eid in adj[u]:
                if ELEMENTS[eid].kind == "breaker":
                    frontier.append((eid, v))
                    continue
                if v not in zone:
                    zone.add(v)
                    stack.append(v)
        # Отключаются только выключатели, через которые к КЗ притекает ток от источника
        srcs = set(_sources(state))
        tripped = set()
        for eid, v in frontier:
            if v in zone:
                continue
            seen = {v} | zone
            st2 = [v]
            feeds = v in srcs
            while st2 and not feeds:
                u = st2.pop()
                for w, e2 in adj[u]:
                    if e2 == eid or w in seen:
                        continue
                    if w in srcs:
                        feeds = True
                        break
                    seen.add(w)
                    st2.append(w)
            if feeds:
                tripped.add(eid)
        if not tripped:  # КЗ непосредственно на источнике без выключателя — не бывает в модели
            break
        for t in sorted(tripped):
            state.switches[t] = False
        events.append({
            "type": "protection_trip",
            "fault": sorted(set(faults.values())),
            "tripped": sorted(tripped),
            "text": "Срабатывание РЗА (МТЗ/ТО): КЗ на "
                    + ", ".join(ELEMENTS[f].label for f in sorted(set(faults.values())))
                    + " — отключены " + ", ".join(ELEMENTS[t].label for t in sorted(tripped)),
        })
    return events


def solve(state: GridState) -> dict:
    """Узловой расчёт установившегося режима. Ничего не меняет в state."""
    live = energized_nodes(state)
    comp = _components(state)
    sources = [s for s in _sources(state) if s in live]

    branches = [el for el in ELEMENTS.values() if _conducting(state, el) and el.a in live]
    unknown = [k for k in NODES if k in live and k not in sources]
    idx = {k: i for i, k in enumerate(unknown)}
    nU = len(unknown)
    U = {s: U_NOM for s in sources}

    if nU:
        Y = np.zeros((nU, nU))
        J = np.zeros(nU)
        for el in branches:
            g = 1.0 / max(el.z_ohm, Z_SWITCH)
            a, b = el.a, el.b
            ia, ib = idx.get(a), idx.get(b)
            if ia is not None:
                Y[ia, ia] += g
            if ib is not None:
                Y[ib, ib] += g
            if ia is not None and ib is not None:
                Y[ia, ib] -= g
                Y[ib, ia] -= g
            if ia is not None and ib is None:
                J[ia] += g * U[b]
            if ib is not None and ia is None:
                J[ib] += g * U[a]
        for k in unknown:
            if NODES[k].kind == "load":
                J[idx[k]] -= state.loads.get(k, 0.0)
        sol = np.linalg.solve(Y, J)
        for k, i in idx.items():
            U[k] = float(sol[i])

    # Напряжение «в фазных величинах»: потери в модели I·Z — линейные, это
    # учебное упрощение (cosφ≈1), достаточно для оценки ΔU.
    elements = {}
    for el in ELEMENTS.values():
        info = {"id": el.id, "kind": el.kind, "energized": el.a in live or el.b in live, "current": 0.0}
        if el.kind in ("breaker", "disconnector", "earth"):
            info["closed"] = bool(state.switches.get(el.id, False))
        if _conducting(state, el) and el.a in live:
            info["current"] = abs(U[el.a] - U[el.b]) / max(el.z_ohm, Z_SWITCH)
            info["current"] = round(info["current"], 1)
        if el.i_dop:
            limit = el.i_dop * (state.k_temp if el.kind == "cable" else 1.0)
            info["i_dop"] = round(limit, 1)
            info["i_dop_nominal"] = el.i_dop
            info["loading"] = round(100.0 * info["current"] / limit, 1)
            info["overload"] = info["loading"] > 100.0
            info["p_mw"] = round(SQRT3 * U_NOM * info["current"] * 0.92 / 1e6, 2)
        if el.id in state.faulted:
            info["faulted"] = True
        elements[el.id] = info

    # Источник ДГУ
    dgu = {"available": state.dgu_available, "current": 0.0, "rating": NODES["DGU"].rating}
    if "DGU" in live and state.dgu_available:
        dgu["current"] = round(sum(elements[e]["current"] for e in ("QDGU",)), 1)
    dgu["loading"] = round(100 * dgu["current"] / dgu["rating"], 1)
    dgu["overload"] = dgu["loading"] > 100

    # Параллельная работа источников
    warnings = []
    by_comp: dict[int, list[str]] = {}
    for s in sources:
        by_comp.setdefault(comp[s], []).append(s)
    parallel = [v for v in by_comp.values() if len(v) > 1]
    for grp in parallel:
        if "DGU" in grp:
            warnings.append({"code": "DGU_PARALLEL", "severity": "critical",
                             "text": "ДГУ включена параллельно с сетью без синхронизации — несинхронное включение!"})
        else:
            warnings.append({"code": "TR_PARALLEL", "severity": "warning",
                             "text": "Трансформаторы Т1 и Т2 работают параллельно через СВ/перемычку — "
                                     "рост токов КЗ на шинах 10 кВ, режим не предусмотрен нормальной схемой."})

    loads = {}
    for k in LOADS:
        nd = NODES[k]
        loads[k] = {
            "id": k, "label": nd.label, "category": nd.category,
            "demand": round(state.loads.get(k, 0.0), 1),
            "energized": k in live,
            "voltage_kv": round(U.get(k, 0.0) / 1000, 2) if k in live else 0.0,
            "fed_by_dgu_only": k in live and by_comp.get(comp[k]) == ["DGU"],
        }

    overloads = [e for e in elements.values() if e.get("overload") and e["current"] > 0]
    for o in overloads:
        el = ELEMENTS[o["id"]]
        warnings.append({"code": "OVERLOAD", "severity": "critical" if o["loading"] > 130 else "warning",
                         "element": o["id"],
                         "text": f"Перегруз {el.label}: {o['current']:.0f} А при I_доп {o['i_dop']:.0f} А "
                                 f"({o['loading']:.0f}%)" + (" — сработает МТЗ / перегрев изоляции"
                                                             if o["loading"] > 130 else
                                                             " — недопустимо длительно, ускоренное старение изоляции")})
    if dgu["overload"]:
        warnings.append({"code": "DGU_OVERLOAD", "severity": "critical",
                         "text": f"ДГУ перегружена: {dgu['current']:.0f} А при I_ном {dgu['rating']:.0f} А"})
    for k, l in loads.items():
        if not l["energized"] and l["category"] == "I":
            warnings.append({"code": "CAT_I_LOST", "severity": "critical",
                             "text": f"{l['label']} без питания — потребитель I категории!"})

    unserved = {c: round(sum(l["demand"] for l in loads.values() if l["category"] == c and not l["energized"]), 1)
                for c in ("I", "II", "III")}

    return {
        "elements": elements,
        "loads": loads,
        "energized_nodes": sorted(live),
        "node_voltage_kv": {k: round(v / 1000, 3) for k, v in U.items()},
        "dgu": dgu,
        "warnings": warnings,
        "unserved": unserved,
        "max_loading": max([e.get("loading", 0) for e in elements.values()] + [dgu["loading"]]),
    }


def current_through(state: GridState, eid: str) -> float:
    res = solve(state)
    return res["elements"][eid]["current"]


def topology_json() -> dict:
    return {
        "nodes": [asdict(n) for n in NODES.values()],
        "elements": [asdict(e) for e in ELEMENTS.values()],
    }


def topology_brief() -> str:
    """Текстовое описание схемы для промптов агентов."""
    lines = ["Узел: ПС 110/10 кВ «Северная», 2×ТДН-10000/110 (I_ном НН 550 А), "
             "I и II СШ 10 кВ, СВ-10 нормально отключён (АВР на СВ)."]
    for k in CABLES:
        el = ELEMENTS[k]
        lines.append(f"- {k}: {el.label}, I_доп={el.i_dop:.0f} А, L={el.length_km} км")
    for k in LOADS:
        nd = NODES[k]
        lines.append(f"- нагрузка {k}: {nd.label}, базовый ток {nd.base_load:.0f} А")
    lines.append("- DGU: передвижная ДГУ 2500 кВА (I_ном 137 А), подключается Q-ДГУ к РП больницы")
    lines.append("- Больница: основной ввод Ф-12 (от I СШ), резервный Ф-21 (от II СШ) через Q-21К, нормально отключён.")
    lines.append("- Перемычка П-1 между ЖК «Нурлы» и РП ТРЦ, Q-П1 нормально отключён.")
    return "\n".join(lines)
