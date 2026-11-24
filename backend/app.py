"""DualGrid Tutor — веб-сервер тренажёра (FastAPI)."""

from __future__ import annotations

import logging
import random
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agents
from grid import CABLES, ELEMENTS, SQRT3, U_NOM, topology_json
from trainer import (CLIMATE, Session, evaluate, offline_scenario, sanitize_scenario,
                     scheme_summary)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dualgrid")

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "frontend"

app = FastAPI(title="DualGrid Tutor")
SESSIONS: dict[str, Session] = {}


class ScenarioReq(BaseModel):
    city_key: str = "astana_winter"
    difficulty: str = "medium"
    use_ai: bool = True


class ToggleReq(BaseModel):
    session_id: str
    element_id: str


class CheckReq(BaseModel):
    session_id: str
    cable_id: str


class SubmitReq(BaseModel):
    session_id: str
    answers: list[str] = []
    use_ai: bool = True


def _sess(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if not s:
        raise HTTPException(404, "Сессия не найдена — сгенерируйте новую задачу")
    return s


def _fill_texts(sc: dict, summ: dict):
    if not sc.get("scheme_description"):
        parts = [f"{sc['substation_name']}: 2×ТДН-10000/110 (I_ном НН 550 А), I и II СШ 10 кВ, СВ-10 нормально отключён."]
        for c in summ["cables"]:
            parts.append(f"{c['label']} — I_доп {c['i_dop']:.0f} А (с учётом t грунта), до аварии {c['pre_fault_current']:.0f} А.")
        parts.append("Потребители: " + "; ".join(f"{l['label']} — {l['current']:.0f} А" for l in summ["loads"]) + ".")
        sc["scheme_description"] = " ".join(parts)
    if not sc.get("reserves_description"):
        sc["reserves_description"] = (
            "Резервный ввод больницы Ф-21 от II СШ (Q-21К, АВР). Перемычка П-1 АСБ-10 3×120 между ЖК «Нурлы» и РП ТРЦ "
            f"(I_доп {240 * sc['k_temp']:.0f} А), Q-П1 нормально отключён. Т2 — I_ном 550 А. "
            f"Передвижная ДГУ 2500 кВА (I_ном 137 А) — прибытие к РП больницы через {sc.get('dgu_eta_min', 40)} мин. "
            "Бригада ОВБ — на ПС «Северная».")


@app.get("/api/status")
def status():
    return {"ai": agents.ai_enabled(), "model": agents.MODEL if agents.ai_enabled() else None,
            "cities": {k: f"{v['city']}, {v['season']} {v['air_temp_c']:+d} °C" for k, v in CLIMATE.items()}}


@app.get("/api/topology")
def topology():
    return topology_json()


@app.post("/api/scenario")
def new_scenario(req: ScenarioReq):
    if req.city_key not in CLIMATE or req.difficulty not in ("medium", "high"):
        raise HTTPException(400, "Неизвестные параметры сценария")
    clim = CLIMATE[req.city_key]
    sc, ai_error = None, None
    if req.use_ai and agents.ai_enabled():
        try:
            sc = agents.examiner_generate(f"{clim['city']}", f"{clim['season']} {clim['air_temp_c']:+d} °C",
                                          req.difficulty, f"#{random.randint(1, 9999)}")
        except Exception as e:  # сеть, отказ модели, невалидный JSON — уходим в офлайн
            log.exception("Экзаменатор: ошибка Claude API")
            ai_error = str(e)[:300]
    if sc is None:
        sc = offline_scenario(req.city_key, req.difficulty, random.randint(0, 10 ** 6))
    sc = sanitize_scenario(sc)
    s = Session(sc)
    SESSIONS[s.id] = s
    summ = scheme_summary(sc, s.state)
    _fill_texts(sc, summ)
    return {"scenario": sc, "scheme": summ, "initial_events": s.initial_events,
            "ai_error": ai_error, **s.snapshot()}


@app.post("/api/toggle")
def toggle(req: ToggleReq):
    s = _sess(req.session_id)
    try:
        entry = s.toggle(req.element_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"entry": entry, **s.snapshot()}


@app.post("/api/check")
def check(req: CheckReq):
    s = _sess(req.session_id)
    try:
        entry = s.check_voltage(req.cable_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"entry": entry, **s.snapshot()}


class ResetReq(BaseModel):
    session_id: str


@app.post("/api/reset")
def reset(req: ResetReq):
    s = _sess(req.session_id)
    s.reset()
    return s.snapshot()


def calc_lines(res: dict) -> list[str]:
    out = []
    for k in ("T1", "T2", *CABLES):
        e = res["elements"][k]
        if e["current"] <= 0.5:
            continue
        p = SQRT3 * U_NOM * e["current"] * 0.92 / 1e6
        mark = "ПЕРЕГРУЗ" if e["overload"] else "норма"
        out.append(f"{ELEMENTS[k].label}: I = {e['current']:.0f} А, I_доп = {e['i_dop']:.0f} А → {e['loading']:.0f}% "
                   f"({mark}); P = √3·10,5 кВ·{e['current']:.0f} А·0,92 = {p:.2f} МВт")
    d = res["dgu"]
    if d["current"] > 0:
        out.append(f"ДГУ: I = {d['current']:.0f} А при I_ном {d['rating']:.0f} А → {d['loading']:.0f}%")
    return out


def offline_report(s: Session, det: dict, answers: list[str]) -> dict:
    res = s.result
    crit = {"category": [], "thermal": [], "sequence": []}
    for f in det["findings"]:
        key = {"cat": "category", "lim": "thermal", "loc": "sequence", "saf": "sequence"}[f["criterion"]]
        crit[key].append(f)
    criteria = []
    for k, items in crit.items():
        verdict = "ok"
        if any(i["verdict"] == "fail" for i in items):
            verdict = "fail"
        elif any(i["verdict"] == "warning" for i in items):
            verdict = "warning"
        if k == "thermal" and not items:
            items = [{"text": "Все кабели и трансформаторы в пределах I_доп."}]
        if k == "sequence" and not items:
            items = [{"text": "Нарушений порядка операций не выявлено."}]
        criteria.append({"criterion": k, "verdict": verdict, "details": " ".join(i["text"] for i in items)})
    topics = []
    codes = {v["code"] for v in s.violations}
    if codes & {"DISCONNECTOR_UNDER_LOAD", "EARTH_NO_CHECK", "EARTH_ON_LIVE", "DGU_PARALLEL"}:
        topics.append("Порядок оперативных переключений")
    if any(w["code"] == "OVERLOAD" for w in res["warnings"]) or max(s.peak_loading.values() or [0]) > 100:
        topics.append("Пропускная способность КЛ-10 кВ")
    if "ENERGIZE_FAULT" in codes:
        topics.append("РЗА")
    if not res["loads"]["HOSP"]["energized"]:
        topics.append("Категории надёжности электроснабжения")
    if not topics:
        topics.append("Токи КЗ")
    sc = det["scores"]
    return {
        "summary": f"Итог {sc['total']}/100. Операций: {det['ops']}. Нарушений: {len(s.violations)}. "
                   f"Без питания: I кат. {det['final_unserved']['I']:.0f} А, II кат. {det['final_unserved']['II']:.0f} А, "
                   f"III кат. {det['final_unserved']['III']:.0f} А (эталон: III кат. {det['reference']['unserved']['III']:.0f} А).",
        "criteria": criteria,
        "calculation": "\n".join(calc_lines(res)) or "Сеть обесточена.",
        "scores": {k: sc[k] for k in ("localization", "limits", "safety")},
        "blank": det["reference"]["steps"],
        "recommendation": "Повторите: " + ", ".join(topics) + ". Сравните свой журнал операций с эталонным бланком: "
                          "сначала выключатель, затем разъединители, ограничение III категории — ДО замыкания резерва.",
        "topics": topics,
    }


@app.post("/api/submit")
def submit(req: SubmitReq):
    s = _sess(req.session_id)
    det = evaluate(s)
    report, source, ai_error = None, "offline", None
    if req.use_ai and agents.ai_enabled():
        payload = {
            "vvodnaya": {k: s.scenario.get(k) for k in ("title", "city", "season", "air_temp_c", "weather", "time",
                                                       "faulted_element", "cause", "protections", "questions")},
            "faulted_label": ELEMENTS[s.scenario["faulted_element"]].label,
            "operations_log": [{k: e.get(k) for k in ("n", "t", "label", "action", "result", "current_switched",
                                                      "violations", "events", "unserved", "max_loading")}
                               for e in s.log],
            "final_regime": {
                "calculation": calc_lines(s.result),
                "loads": list(s.result["loads"].values()),
                "warnings": s.result["warnings"],
                "peak_loading_percent": s.peak_loading,
            },
            "deterministic_scores": det["scores"],
            "deterministic_findings": det["findings"],
            "reference_blank": det["reference"]["steps"],
            "reference_unserved": det["reference"]["unserved"],
            "student_answers": [{"question": q, "answer": a} for q, a in
                                zip(s.scenario["questions"], req.answers + [""] * 3)],
        }
        try:
            report = agents.mentor_review(payload)
            source = "claude"
        except Exception as e:
            log.exception("Наставник: ошибка Claude API")
            ai_error = str(e)[:300]
    if report is None:
        report = offline_report(s, det, req.answers)
    scores = agents.clamp_scores(report["scores"], det["scores"])
    if not report.get("blank"):
        report["blank"] = det["reference"]["steps"]
    return {"report": report, "scores": scores, "deterministic": det, "source": source, "ai_error": ai_error,
            "log": s.log, "violations": s.violations}


app.mount("/static", StaticFiles(directory=FRONT), name="static")


@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")
