"""Двойной AI-слой: Агент-Экзаменатор и Агент-Наставник (Claude API).

Без ключа API (или при ошибке/отказе модели) оба агента работают в офлайн-режиме:
сценарий берётся из детерминированных шаблонов, оценка — из расчётного модуля.
Все числовые данные (токи, загрузки) агенты получают ОТ солвера и не придумывают.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel

from grid import FAULTABLE, LOADS, topology_brief

log = logging.getLogger("dualgrid.agents")

MODEL = os.environ.get("DUALGRID_MODEL", "claude-opus-5-5")
BETAS = ["server-side-fallback-2026-07-01"]

_client: Optional[anthropic.Anthropic] = None


def ai_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) \
        and os.environ.get("DUALGRID_OFFLINE") != "1"


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# --------------------------------------------------------------------------
# Схемы структурированного вывода
# --------------------------------------------------------------------------

class LoadFactor(BaseModel):
    load_id: Literal[tuple(LOADS)]  # type: ignore[valid-type]
    factor: float


class ExaminerScenario(BaseModel):
    title: str
    substation_name: str
    city: str
    season: str
    air_temp_c: int
    weather: str
    time: str
    difficulty: str
    faulted_element: Literal[tuple(FAULTABLE)]  # type: ignore[valid-type]
    cause: str
    protections: list[str]
    avr_failed: bool
    extra_tripped: list[str]
    load_factors: list[LoadFactor]
    k_temp: float
    dgu_available: bool
    dgu_eta_min: int
    scheme_description: str
    incident_description: str
    reserves_description: str
    questions: list[str]


class CriterionVerdict(BaseModel):
    criterion: Literal["category", "thermal", "sequence"]
    verdict: Literal["ok", "warning", "fail"]
    details: str


class BlankStep(BaseModel):
    n: int
    object: str
    command: str
    note: str


class MentorScores(BaseModel):
    localization: int
    limits: int
    safety: int


class MentorReport(BaseModel):
    summary: str
    criteria: list[CriterionVerdict]
    calculation: str
    scores: MentorScores
    blank: list[BlankStep]
    recommendation: str
    topics: list[str]


def _strict_schema(model: type[BaseModel]) -> dict:
    """JSON Schema для output_config: все поля обязательны, без лишних свойств."""
    schema = model.model_json_schema()

    def fix(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            node.pop("title", None)
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)
    fix(schema)
    return schema


def _call_json(system: str, user: str, model_cls: type[BaseModel], effort: str) -> BaseModel:
    resp = client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=BETAS,
        fallbacks="default",
        system=system,
        output_config={"effort": effort,
                       "format": {"type": "json_schema", "schema": _strict_schema(model_cls)}},
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Модель отклонила запрос")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Ответ модели обрезан по max_tokens")
    text = next(b.text for b in resp.content if b.type == "text")
    return model_cls.model_validate_json(text)


# --------------------------------------------------------------------------
# Агент-Экзаменатор
# --------------------------------------------------------------------------

EXAMINER_SYSTEM = """Ты — старший диспетчер-инструктор учебного центра региональной электросетевой компании Казахстана (уровень 10–110 кВ, нормы ПТЭ и ПУЭ РК).
Твоя задача — сгенерировать одну реалистичную противоаварийную тренировку для студента-энергетика (или стажёра ОДС) на ЗАДАННОЙ учебной схеме.

Правила:
- Используй только элементы из описания схемы; поле faulted_element — один из разрешённых идентификаторов.
- extra_tripped — идентификаторы выключателей, дополнительно отключившихся при аварии (обычно пусто; для каскадного отказа можно, например, QSV не включился — тогда оставь пусто и укажи avr_failed=true).
- load_factors — коэффициенты к базовым токам нагрузок (0.8–1.4), отражающие погоду и время суток (зимой −35 °C — электроотопление, летом +38 °C — кондиционирование).
- k_temp — поправочный коэффициент к I_доп кабелей по температуре грунта по ПУЭ (лето +38 °C ≈ 0.92–0.96, зима ≈ 1.0).
- scheme_description: исходная схема узла (ПС, 2 ввода, СВ-10, фидеры, категории потребителей, тип кабеля и I_доп в амперах — бери из описания).
- incident_description: что произошло — время, сработавшие защиты РЗА/АВР, причина.
- reserves_description: доступные резервы (резервный ввод больницы, перемычка П-1 с её I_доп, ДГУ, время прибытия ДГУ/ОВБ).
- questions — ровно три вопроса обучаемому:
  1) Какой участок и какими аппаратами изолировать в первую очередь?
  2) Кого временно ограничить (III категория), чтобы не перегрузить резервный кабель по току?
  3) В какой последовательности составить бланк переключений и куда направить ДГУ / бригаду ОВБ?
- ВАЖНО: не пиши правильный ответ, не подсказывай решение. Только вводная.
- Пиши по-русски, профессиональным диспетчерским языком."""


def examiner_generate(city: str, season: str, difficulty: str, seed_hint: str) -> dict:
    user = (f"Схема учебного узла:\n{topology_brief()}\n\n"
            f"Разрешённые faulted_element: {', '.join(FAULTABLE)}\n"
            f"Идентификаторы нагрузок: {', '.join(LOADS)}\n\n"
            f"Входные параметры сценария:\n- Город и сезон: {city}, {season}\n"
            f"- Уровень сложности: {'Высокий — каскадный отказ' if difficulty == 'high' else 'Средний'}\n"
            f"- Вариант: {seed_hint}\n\n"
            "Сгенерируй вводную. Поле difficulty заполни значением "
            f"\"{difficulty}\".")
    out = _call_json(EXAMINER_SYSTEM, user, ExaminerScenario, effort="low")
    d = out.model_dump()
    d["load_factors"] = [lf for lf in d["load_factors"]]
    d["source"] = "claude"
    return d


# --------------------------------------------------------------------------
# Агент-Наставник
# --------------------------------------------------------------------------

MENTOR_SYSTEM = """Ты — ИИ-наставник и эксперт по оперативным переключениям и РЗА в электросетях 10–110 кВ (по ПТЭ и ПУЭ РК).
Проанализируй ответ обучаемого на выданную аварийную вводную.

Тебе переданы: вводная, журнал фактических операций обучаемого на тренажёре (с токами, рассчитанными узловым солвером),
итоговый режим сети, детерминированная предварительная оценка и эталонный бланк, построенный расчётным модулем,
а также текстовые ответы обучаемого на три вопроса.

Проведи жёсткую инженерную проверку:
1. category — Безопасность и категория надёжности: не оставлены ли без питания потребители I категории (больница) без ввода резерва или ДГУ?
2. thermal — Тепловой предел кабелей (I_раб ≤ I_доп): проверь арифметически суммарный ток на резервной линии/трансформаторе после переключения.
   Если студент перегрузил кабель или шину — укажи точный расчёт в амперах и МВт (P = √3·U·I·cosφ, U = 10,5 кВ, cosφ = 0,92) и к чему это приведёт (отключение МТЗ или пробой изоляции).
3. sequence — Логика изоляции и очерёдность: разделены ли операции выключателями (под нагрузкой) и разъединителями (без нагрузки), проверено ли отсутствие напряжения перед заземлением.
4. Оценка по 100-балльной шкале: localization 0–30, limits 0–35, safety 0–35.
   Опирайся на детерминированную оценку — она посчитана по фактическим токам. Ты можешь скорректировать каждую составляющую
   не более чем на ±5 баллов с учётом качества текстовых ответов (понимание, обоснование). Не превышай пределы шкал.
5. Эталонный разбор: blank — пошаговый правильный бланк переключений (№ -> Объект -> Команда -> Примечание).
   Бери за основу переданный эталонный бланк (его операции проверены расчётом), можешь уточнить формулировки и примечания, но не меняй состав операций с аппаратами.
   recommendation — короткая рекомендация; topics — 1–3 темы для повторения из списка: «РЗА», «Токи КЗ», «Пропускная способность КЛ-10 кВ», «Порядок оперативных переключений», «Категории надёжности электроснабжения».
calculation — арифметика токов по итоговому режиму (числа бери ТОЛЬКО из переданных данных солвера).
Никогда не выдумывай токи. Пиши по-русски, кратко и по делу, как инструктор."""


def mentor_review(payload: dict) -> dict:
    user = "Данные для проверки (JSON):\n" + json.dumps(payload, ensure_ascii=False, indent=1)
    out = _call_json(MENTOR_SYSTEM, user, MentorReport, effort="medium")
    return out.model_dump()


def clamp_scores(ai: dict, det: dict) -> dict:
    lim = {"localization": 30, "limits": 35, "safety": 35}
    res = {}
    for k, mx in lim.items():
        v = int(ai.get(k, det[k]))
        v = max(det[k] - 5, min(det[k] + 5, v))
        res[k] = max(0, min(mx, v))
    res["total"] = sum(res.values())
    return res
