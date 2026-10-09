"""Журнал прогнозов — общий для обоих видов спорта.

Идея журнала: прогноз, который каждый раз перезаписывается, бесполезен.
Нельзя ни понять, насколько модель ошибается, ни оценить, помогает ли
обновление ближе к матчу. Поэтому ведём append-only журнал: одна запись
на пару (матч, этап), где этап — расстояние до начала игры.

Спорта два, и различия между ними только в источнике результатов:
матчи футбола лежат в matches.parquet, баскетбола — в vtb_matches.parquet.
Всё остальное — этапы, разрешение вердиктов, метрики по этапам — общее,
поэтому один модуль на оба. Дублировать эту логику ради двух строк
разницы было бы хуже: правки в одном месте разъедутся.

Проверено тестами: tests/test_forecast.py покрывает football-путь,
поскольку именно он вызывается по умолчанию.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from etl.config import ROOT

# Границы этапов в часах до начала матча. Повторные прогоны внутри одного
# слота обновляют запись, а не плодят дубликаты.
STAGES: list[tuple[str, float]] = [
    ("T-72h+", 72.0),   # от 72 часов и дальше
    ("T-24h", 12.0),    # 12–72 часа
    ("T-6h", 4.0),      # 4–12 часов
    ("T-4h", 0.0),      # меньше 4 часов — финальный срез
]
STAGE_ORDER = {name: i for i, (name, _) in enumerate(STAGES)}

ACTUAL = {"H": 0, "D": 1, "A": 2}


def ledger_path(sport: str) -> Path:
    return ROOT / "data" / f"forecast_log_{sport}.jsonl"


def stage_for(hours_before: float) -> str:
    for name, upper in STAGES:
        if hours_before >= upper:
            return name
    return STAGES[-1][0]


def load(sport: str) -> list[dict]:
    path = ledger_path(sport)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # Строка может оборваться при обрыве связи во время коммита
            # в CI. Пропускаем её, а не роняем весь разбор журнала.
            continue
    return out


def append(records: list[dict], sport: str = "football") -> int:
    """Дописывает прогнозы в журнал. Запись с тем же (матч, этап)
    заменяется более свежей — иначе десять прогонов подряд дадут
    десять одинаковых строк."""
    path = ledger_path(sport)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {(r["key"], r["stage"]): r for r in load(sport)}
    added = 0
    for r in records:
        k = (r["key"], r["stage"])
        if k not in merged:
            added += 1
        merged[k] = r

    payload = [merged[k] for k in sorted(merged, key=lambda t: (t[0], STAGE_ORDER.get(t[1], 99)))]
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in payload) + "\n",
        encoding="utf-8",
    )
    return added


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


def build_history(matches: pl.DataFrame, sport: str = "football") -> dict:
    """Сводит журнал в отчёт: сыгранные прогнозы с вердиктом и незавершённые."""
    log = load(sport)
    results = _results_index(matches)

    grouped: dict[str, list[dict]] = {}
    for r in log:
        grouped.setdefault(r["key"], []).append(r)

    resolved: list[dict] = []
    pending: list[dict] = []

    for key, versions in grouped.items():
        versions.sort(key=lambda r: STAGE_ORDER.get(r["stage"], 99))
        latest = versions[-1]
        res = results.get(
            (_date_key(latest["date"]), latest["home_id"], latest["away_id"])
        )

        base = {
            "key": key,
            "date": latest["date"],
            "kickoff": latest.get("kickoff"),
            "matchday": latest.get("matchday"),
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
            "extra": latest.get("extra"),
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

        first = versions[0]
        shift = round(sum(abs(a - b) for a, b in
                          zip([first["p_home"], first["p_draw"], first["p_away"]], probs)), 4)
        first_top = max(range(3), key=lambda i: [first["p_home"], first["p_draw"], first["p_away"]][i])

        base.update({
            "actual": res["ftr"],
            "score": f"{res['fthg']}-{res['ftag']}",
            "hit": hit,
            "p_actual": round(p_actual, 4) if p_actual is not None else None,
            "logloss": round(-math.log(max(p_actual, 1e-12)), 4) if p_actual is not None else None,
            "n_versions": len(versions),
            "shift": shift,
            "flipped": shift > 0.01 and first_top != max(range(3), key=lambda i: probs[i]),
        })
        resolved.append(base)

    resolved.sort(key=lambda r: (r["date"], r["kickoff"] or ""), reverse=True)
    pending.sort(key=lambda r: (r["kickoff"] or "", r["date"]))

    return {
        "sport": sport,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resolved": resolved,
        "pending": pending,
        "summary": summary(resolved),
    }


def summary(resolved: list[dict]) -> dict:
    """Итоги и разбивка по этапам: помогает ли обновление ближе к матчу."""
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


def save(matches: pl.DataFrame, sport: str = "football", out: Path | None = None) -> dict:
    hist = build_history(matches, sport)
    if out is None:
        from etl.config import PROCESSED
        out = PROCESSED / f"{sport}_journal.json"
    out.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    return hist
