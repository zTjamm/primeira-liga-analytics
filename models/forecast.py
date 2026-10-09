"""Журнал прогнозов: что мы предсказали, с какого момента и как сложилось.

Прогнозы, который каждый раз перезаписывается, бесполезны — нельзя ни понять,
насколько модель ошибается, ни оценить, помогает ли обновление ближе к матчу.
Поэтому ведём append-only журнал: одна запись на пару (матч, этап), где этап —
это расстояние до начала игры.

Зачем этапы. Главный вопрос при обновлении «за 4 часа»: а оно вообще что-то
улучшает? Ответ на него измеряемый — сравниваем точность и log-loss одного и
того же матча, предсказанного за 24 часа и за 4 часа. Если разницы нет,
обновление ближе к матчу не нужно, и это тоже полезный результат.

Файл журнала коммитится в git: без этого на каждом прогоне CI история
обнулялась бы.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from etl.config import PROCESSED, ROOT

LEDGER = ROOT / "data" / "forecast_log.jsonl"
HISTORY = PROCESSED / "forecast_history.json"

# Границы этапов в часах до начала матча. Этап определяет, в какой слот
# попадёт запись; повторные прогоны внутри одного слота обновляют её,
# но не плодят дубликаты.
STAGES: list[tuple[str, float]] = [
    ("T-72h+", 72.0),   # от 72 часов и дальше
    ("T-24h", 12.0),    # 12–72 часа
    ("T-6h", 4.0),      # 4–12 часов
    ("T-4h", 0.0),      # меньше 4 часов — финальный срез
]


def stage_for(hours_before: float) -> str:
    for name, upper in STAGES:
        if hours_before >= upper:
            return name
    return STAGES[-1][0]


def _load() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append(records: list[dict]) -> int:
    """Дописывает прогнозы в журнал. Запись с тем же (матч, этап)
    заменяется более свежей — иначе десять прогонов подряд дадут
    десять одинаковых строк."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    existing = _load()
    key = lambda r: (r["fd_match_id"], r["stage"])  # noqa: E731
    merged: dict[tuple, dict] = {key(r): r for r in existing}
    added = 0
    for r in records:
        k = key(r)
        if k not in merged:
            added += 1
        merged[k] = r

    payload = [merged[k] for k in sorted(merged, key=lambda t: (t[0], STAGE_ORDER.get(t[1], 99)))]
    LEDGER.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in payload) + "\n", encoding="utf-8"
    )
    return added


STAGE_ORDER = {name: i for i, (name, _) in enumerate(STAGES)}


def _date_key(d) -> str:
    """Дату приводим к ISO-строке с обеих сторон.

    В parquet match_date лежит типом date, а в журнале он сохранён строкой.
    Если не привести к общему виду, ключи не совпадут никогда: страница
    будет годами показывать «не угадано» вместо реального результата.
    """
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _results_index(matches: pl.DataFrame) -> dict[tuple, dict]:
    """Индекс результатов по (дата, домашний, гостевой)."""
    if matches.height == 0 or "ftr" not in matches.columns:
        return {}
    idx = {}
    for m in matches.filter(pl.col("ftr").is_not_null()).to_dicts():
        idx[(_date_key(m["match_date"]), m["home_id"], m["away_id"])] = m
    return idx


ACTUAL = {"H": 0, "D": 1, "A": 2}


def build_history(matches: pl.DataFrame) -> dict:
    """Сводит журнал в отчёт: сыгранные прогнозы с вердиктом и незавершённые."""
    import math

    log = _load()
    results = _results_index(matches)

    grouped: dict[int, list[dict]] = {}
    for r in log:
        grouped.setdefault(r["fd_match_id"], []).append(r)

    resolved: list[dict] = []
    pending: list[dict] = []

    for fd_id, versions in grouped.items():
        versions.sort(key=lambda r: STAGE_ORDER.get(r["stage"], 99))
        latest = versions[-1]
        res = results.get(
            (_date_key(latest["match_date"]), latest["home_id"], latest["away_id"])
        )

        base = {
            "fd_match_id": fd_id,
            "date": latest["match_date"],
            "kickoff_utc": latest["kickoff_utc"],
            "matchday": latest["matchday"],
            "home_id": latest["home_id"],
            "away_id": latest["away_id"],
            "home_name": latest.get("home_name"),
            "away_name": latest.get("away_name"),
            "generated_at": latest["generated_at"],
            "hours_before": latest["hours_before"],
            "stage": latest["stage"],
            "p_home": latest["p_home"],
            "p_draw": latest["p_draw"],
            "p_away": latest["p_away"],
            "over25": latest.get("over25"),
            "xg_home": latest.get("xg_home"),
            "xg_away": latest.get("xg_away"),
            "weather": latest.get("weather"),
            "versions": [
                {
                    "stage": v["stage"], "p_home": v["p_home"], "p_draw": v["p_draw"],
                    "p_away": v["p_away"], "hours_before": v["hours_before"],
                    "generated_at": v["generated_at"],
                }
                for v in versions
            ],
        }

        if res is None:
            pending.append(base)
            continue

        probs = [latest["p_home"], latest["p_draw"], latest["p_away"]]
        ai = ACTUAL.get(res["ftr"])
        total = sum(probs) or 1.0
        probs = [p / total for p in probs]

        hit = ai is not None and max(range(3), key=lambda i: probs[i]) == ai
        p_actual = probs[ai] if ai is not None else None

        # Движение модели: менялся ли прогноз от первого среза к последнему
        first = versions[0]
        shift = (
            round(abs(first["p_home"] - latest["p_home"]) +
                  abs(first["p_draw"] - latest["p_draw"]) +
                  abs(first["p_away"] - latest["p_away"]), 4)
        )
        first_top = max(range(3), key=lambda i: [first["p_home"], first["p_draw"], first["p_away"]][i])

        base.update({
            "actual": res["ftr"],
            "score": f"{res['fthg']}-{res['ftag']}",
            "hit": hit,
            "p_actual": round(p_actual, 4) if p_actual is not None else None,
            "logloss": round(-math.log(max(p_actual, 1e-12)), 4) if p_actual is not None else None,
            "n_versions": len(versions),
            "shift": shift,
            "flipped": shift > 0.01 and first_top != (max(range(3), key=lambda i: probs[i])),
        })
        resolved.append(base)

    resolved.sort(key=lambda r: (r["date"], r["kickoff_utc"]), reverse=True)
    pending.sort(key=lambda r: (r["kickoff_utc"] or ""))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resolved": resolved,
        "pending": pending,
        "summary": summary(resolved),
    }


def summary(resolved: list[dict]) -> dict:
    """Итоги и, главное, разбивка по этапам: помогает ли обновление
    ближе к матчу. Это прямой ответ на вопрос «стоит ли обновлять за 4 часа»."""
    if not resolved:
        return {"total": 0}

    def agg(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0}
        losses = [r["logloss"] for r in rows if r["logloss"] is not None]
        return {
            "n": len(rows),
            "accuracy": round(sum(1 for r in rows if r["hit"]) / len(rows), 4),
            "logloss": round(sum(losses) / len(losses), 5) if losses else None,
            "avg_p_actual": round(
                sum(r["p_actual"] for r in rows if r["p_actual"] is not None) / len(rows), 4
            ),
        }

    by_stage: dict[str, dict] = {}
    for stage, _ in STAGES:
        rows = [r for r in resolved if r["stage"] == stage]
        if rows:
            by_stage[stage] = agg(rows)

    multi = [r for r in resolved if r["n_versions"] > 1]
    return {
        "total": len(resolved),
        "overall": agg(resolved),
        "by_stage": by_stage,
        "multi_stage": len(multi),
        "flipped": sum(1 for r in multi if r.get("flipped")),
        "avg_shift": round(sum(r["shift"] for r in multi) / len(multi), 4) if multi else None,
    }


def save(matches: pl.DataFrame) -> dict:
    hist = build_history(matches)
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    return hist
