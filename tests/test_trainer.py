"""Проверки солвера, правил ПТЭ и оценки (запуск: pytest -q из корня репозитория)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from grid import GridState, solve, apply_protection  # noqa: E402
from trainer import Session, evaluate, offline_scenario, sanitize_scenario  # noqa: E402


def make(fe, city="almaty_summer", diff="medium"):
    sc = offline_scenario(city, diff, 1)
    sc["faulted_element"] = fe
    return Session(sanitize_scenario(sc))


def test_normal_mode_kcl():
    r = solve(GridState())
    total = sum(GridState().loads.values())
    assert abs(r["elements"]["T1"]["current"] + r["elements"]["T2"]["current"] - total) < 0.5
    assert r["unserved"] == {"I": 0, "II": 0, "III": 0}
    assert not r["warnings"]


def test_loop_splits_current():
    st = GridState()
    st.switches["QSV"] = True
    r = solve(st)
    # параллельная работа Т1 и Т2 → токи выравниваются, предупреждение
    assert abs(r["elements"]["T1"]["current"] - r["elements"]["T2"]["current"]) < 5
    assert any(w["code"] == "TR_PARALLEL" for w in r["warnings"])


def test_protection_trips_on_energized_fault():
    st = GridState()
    st.faulted = ["KL12"]
    ev = apply_protection(st)
    assert st.switches["Q12"] is False and ev


def test_reserve_overload_detected():
    s = make("KL11")
    s.toggle("QS11"); s.toggle("QSK11")
    e = s.toggle("QP1")  # без ограничения ТРЦ → перегруз Ф-22
    assert s.result["elements"]["KL22"]["overload"]
    assert not e["violations"]


def test_correct_solution_scores_high():
    s = make("KL11")
    for eid in ("QS11", "QSK11", "QTRCB", "QP1"):
        s.toggle(eid)
    s.check_voltage("KL11")
    s.toggle("ZN11")
    assert not s.violations
    sc = evaluate(s)["scores"]
    assert sc["total"] >= 90, sc


def test_disconnector_under_load_and_earth_rules():
    s = make("KL12")
    s.toggle("QS22")  # разъединитель под нагрузкой ТРЦ
    assert any(v["code"] == "DISCONNECTOR_UNDER_LOAD" for v in s.violations)
    s.toggle("ZN12")  # без проверки УВН (но без напряжения)
    assert any(v["code"] == "EARTH_NO_CHECK" for v in s.violations)
    det = evaluate(s)
    assert det["scores"]["safety"] < 20


def test_energize_fault_from_reserve():
    s = make("KL12")
    s.toggle("QK21")  # больница от Ф-21, но КЛ Ф-12 не отделена со стороны РП → КЗ
    assert any(v["code"] == "ENERGIZE_FAULT" for v in s.violations)
    assert not s.result["loads"]["HOSP"]["energized"]


def test_transformer_cascade():
    s = make("T1", diff="high")
    s.toggle("QSV")
    assert s.result["elements"]["T2"]["overload"]
