"""Прогнозы Единой лиги ВТБ на ближайшие матчи.

Отличие от футбольного пайплайна намеренное: матчи баскетбола не
привязаны к турам в привычном смысле. В сезоне 2026/27 формат двухэтапный
(«Регулярный чемпионат. Первый этап», затем «Группа А» и «Группа Б»), и
matchday из API означает разные вещи в разных фазах. Поэтому группируем
по фазе (phase_name) и дате, а не по номеру тура.

Ничьих в этой лиге не бывает: 0 из 509 сыгранных матчей. Модель всё равно
даёт крошечную вероятность ничьей из-за дискретности счёта, и мы её не
скрываем — просто не делаем вид, что это равновероятный исход.

Запуск:
    python -m models.bt_predict
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import polars as pl

from etl.config import CURRENT_SEASON, PROCESSED
from .basketball import BasketballModel

WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"]
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн",
          "июл", "авг", "сен", "окт", "ноя", "дек"]


def _load_names(matches: pl.DataFrame) -> dict[str, str]:
    """Русские названия команд по teamId.

    Имена приходят из API уже на русском, поэтому ничего не хардкодим:
    собираем из данных. Если клуб переименовали, берём один вариант
    устойчиво, чтобы в интерфейсе не прыгало название между матчами.
    """
    pairs = pl.concat([
        matches.select(["home_id", "home_name"])
        .rename({"home_id": "id", "home_name": "name"}),
        matches.select(["away_id", "away_name"])
        .rename({"away_id": "id", "away_name": "name"}),
    ])
    grouped = (
        pairs.group_by("id")
        .agg(pl.col("name").drop_nulls().unique().sort().alias("variants"))
    )
    # Берём последний вариант по алфавиту: у переименованных клубов в
    # истории остаются оба написания, и в интерфейсе название не должно
    # прыгать от матча к матчу.
    return {
        row["id"]: row["variants"][-1]
        for row in grouped.select(["id", "variants"]).to_dicts()
    }


def _russian_name(names: dict[str, str], team_id: str) -> str:
    return names.get(team_id, team_id)


def build() -> dict:
    matches_path = PROCESSED / "vtb_matches.parquet"
    fixtures_path = PROCESSED / "vtb_fixtures.parquet"
    if not matches_path.exists():
        raise SystemExit("Нет vtb_matches.parquet. Запусти: python -m etl.fetch_vtb")
    if not fixtures_path.exists():
        raise SystemExit("Нет vtb_fixtures.parquet — расписание пустое.")

    played = pl.read_parquet(matches_path).sort("match_date")
    fixtures = pl.read_parquet(fixtures_path).sort("match_date")

    names = _load_names(played)

    teams = sorted({m["home_id"] for m in played.to_dicts()}
                   | {m["away_id"] for m in played.to_dicts()})
    model = BasketballModel().fit(played.to_dicts(), teams)
    print(f"Модель: {model.summary()}")
    print(f"Сильнейшие: " + ", ".join(
        f"{_russian_name(names, r["team"])} {r['overall']:+.1f}" for r in model.strength()[:4]))

    now = datetime.now(timezone.utc)
    rows = []
    for f in fixtures.to_dicts():
        for t in (f["home_id"], f["away_id"]):
            model.attack.setdefault(t, 0.0)
            model.defence.setdefault(t, 0.0)
        probs = model.predict_1x2(f["home_id"], f["away_id"])
        eh, eas = model.predicted_scores(f["home_id"], f["away_id"])
        kickoff = f.get("kickoff_msk") or ""
        try:
            dt = datetime.fromisoformat(kickoff)
            hours = (dt.replace(tzinfo=None) - now.replace(tzinfo=None)).total_seconds() / 3600
        except Exception:
            hours = None

        top = int(np.argmax(probs))
        d = f["match_date"]
        rows.append({
            "match_id": f["match_id"],
            "date": str(d),
            "weekday": WEEKDAYS[d.weekday()] if hasattr(d, "weekday") else "",
            "day_month": f"{d.day} {MONTHS[d.month - 1]}" if hasattr(d, "day") else str(d),
            "kickoff_msk": kickoff[:16].replace("T", " ") if kickoff else None,
            "phase_name": f.get("phase_name"),
            "match_number": f.get("match_number"),
            "home_id": f["home_id"],
            "away_id": f["away_id"],
            "home_name": _russian_name(names, f["home_id"]),
            "away_name": _russian_name(names, f["away_id"]),
            "p_home": round(float(probs[0]), 4),
            "p_draw": round(float(probs[1]), 5),
            "p_away": round(float(probs[2]), 4),
            "prediction": ["H", "D", "A"][top],
            "confidence": round(float(probs[top]), 4),
            "exp_home_score": round(float(eh), 1),
            "exp_away_score": round(float(eas), 1),
            "exp_total": round(float(eh + eas), 1),
            "over_155": round(float(model.predict_total_2h(f["home_id"], f["away_id"])), 4),
            "sigma_margin": round(model.sigma_margin, 2),
            "strength_home": round(model.attack.get(f["home_id"], 0)
                                   + model.defence.get(f["home_id"], 0), 2),
            "strength_away": round(model.attack.get(f["away_id"], 0)
                                   + model.defence.get(f["away_id"], 0), 2),
            "hours_before": round(hours, 1) if hours is not None else None,
        })

    out = PROCESSED / "vtb_predictions.json"
    out.write_text(json.dumps({
        "league": "Единая лига ВТБ",
        "season": CURRENT_SEASON,
        "generated_at": now.isoformat(timespec="seconds"),
        "history_matches": played.height,
        "model": model.summary(),
        "draws_possible": False,
        "upcoming": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # сила команд — отдельным файлом, как и по фFootballу
    strength = [
        {**r, "name": _russian_name(names, r["team"])} for r in model.strength()
    ]
    (PROCESSED / "vtb_strength.json").write_text(
        json.dumps(strength, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nПрогнозов: {len(rows)} → {out}")
    print(f"\n{'дата':<12} {'матч':<40} {'П1':>6} {'П2':>6} {'счёт':>12}")
    print("-" * 82)
    for r in rows[:15]:
        name = f"{r['home_name']} — {r['away_name']}"
        sc = f"{r['exp_home_score']}:{r['exp_away_score']}"
        print(f"{r['day_month']:<12} {name[:39]:<40} {r['p_home']:>6.3f} {r['p_away']:>6.3f} {sc:>12}")
    if len(rows) > 15:
        print(f"… ещё {len(rows) - 15}")

    return {"upcoming": len(rows), "teams": len(teams)}


if __name__ == "__main__":
    build()

