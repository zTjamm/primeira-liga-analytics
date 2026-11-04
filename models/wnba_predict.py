"""Прогнозы WNBA (женская баскетбольная лига) на ближайшие матчи.

Использует ту же модель, что и ВТБ (BasketballModel), но с изолированным
обучением на данных WNBA. Калибровка температуры НЕ применена:
нужно проверить на данных WNBA, работает ли T=0.1826.

Запуск:
    python -m models.wnba_predict
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

from etl.config import CURRENT_SEASON, PROCESSED
from . import forecast, temperature
from .basketball import BasketballModel

# Фиксированная ставка для отслеживания ROI (можно вынести в .env).
DEFAULT_STAKE = 100.0

WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"]
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн",
          "июл", "авг", "сен", "окт", "ноя", "дек"]

# WNBA-specific settings
WNBA_TOTAL_LINE = 165  # средний тотал WNBA ~163.5, округляем до 165


def _load_names(matches: pl.DataFrame) -> dict[str, str]:
    """Названия команд по teamId."""
    pairs = pl.concat([
        matches.select(["home_id", "home_name"])
        .rename({"home_id": "id", "home_name": "name"}),
        matches.select(["away_id", "away_name"])
        .rename({"away_id": "id", "away_name": "name"}),
    ])
    grouped = (
        pairs.group_by("id")
        .agg(pl.col("name").mode().first().alias("name"))
    )
    return {r["id"]: r["name"] for r in grouped.to_dicts()}


def main():
    print("Загрузка данных WNBA...")
    matches_path = PROCESSED / "wnba_matches.parquet"
    if not matches_path.exists():
        print(f"  Нет {matches_path} — запусти: python -m etl.fetch_wnba")
        return

    matches = pl.read_parquet(matches_path)
    print(f"  Матчей в истории: {matches.height}")
    print(f"  Сезоны: {sorted(matches['season'].unique().to_list())}")

    # Подготовка данных для модели
    played = matches.filter(pl.col("home_score").is_not_null())
    print(f"  Сыгранных матчей: {played.height}")

    names = _load_names(played)

    teams = sorted({m["home_id"] for m in played.to_dicts()}
                   | {m["away_id"] for m in played.to_dicts()})
    model = BasketballModel().fit(played.to_dicts(), teams)
    print(f"Модель: {model.summary().replace('σ', 'sigma')}")
    print(f"Сильнейшие: " + ", ".join(
        f"{names.get(r['team'], r['team'])} {r['overall']:+.1f}" for r in model.strength()[:4]))

    # Калибровка: пока НЕ применяем (нужно проверить на WNBA данных)
    calib_t = None
    print("Калибровка: не применяется (T=0.1826 проверена только на ВТБ)")

    # Убираем ничьи из баскетбола: шанс почти нулевой,
    # перераспределяем вероятность ничьей на П1 и П2 пропорционально.
    def _remove_draw(probs_1x2: list[float]) -> list[float]:
        """Вероятности [P1, Ничья, P2] → [P1, 0.0, P2] с перераспределением."""
        p_home, p_draw, p_away = probs_1x2
        decisive = p_home + p_away
        if decisive > 0:
            return [p_home / decisive, 0.0, p_away / decisive]
        return [0.5, 0.0, 0.5]

    # Загружаем будущие матчи (фикстуры)
    fixtures_path = PROCESSED / "wnba_future_fixtures.parquet"
    upcoming = []
    ledger_records = []
    if fixtures_path.exists():
        fixtures = pl.read_parquet(fixtures_path)
        print(f"  Будущих матчей: {fixtures.height}")
        for f in fixtures.to_dicts():
            probs = model.predict_1x2(f["home_id"], f["away_id"])
            probs = _remove_draw(probs)
            top = int(max(range(2), key=lambda i: probs[i]))
            prediction = ["H", "A"][top]
            verdict_given = bool(max(probs) >= 0.55)
            upcoming.append({
                "match_id": f["match_id"],
                "date": str(f["match_date"]),
                "home_id": f["home_id"],
                "away_id": f["away_id"],
                "home_name": f["home_name"],
                "away_name": f["away_name"],
                "p_home": round(float(probs[0]), 4),
                "p_draw": 0.0,
                "p_away": round(float(probs[1]), 4),
                "prediction": prediction,
                "confidence": round(float(probs[top]), 4),
                "verdict_given": verdict_given,
                "verdict_reason": "" if verdict_given else f"вероятность {max(probs):.1%} ниже порога 0.55",
            })
            ledger_records.append({
                "key": f"wnba_{f['match_id']}",
                "date": str(f["match_date"]),
                "home_id": f["home_id"],
                "away_id": f["away_id"],
                "home_name": f.get("home_name", f["home_id"]),
                "away_name": f.get("away_name", f["away_id"]),
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "hours_before": 24.0,
                "stage": "T-24h",
                "p_home": round(float(probs[0]), 4),
                "p_draw": 0.0,
                "p_away": round(float(probs[1]), 4),
                "prediction": ["H", "A"][top],
                "odds_home": None,
                "odds_draw": None,
                "odds_away": None,
                "stake": None,
                # Метки вердикта обязательны: точность в журнале считается
                # только по выданным вердиктам, иначе отсев был бы незаметен.
                "extra": {
                    "verdict_given": verdict_given,
                    "verdict_reason": "" if verdict_given else f"вероятность {max(probs):.1%} ниже порога 0.55",
                },
            })
    else:
        print("  Будущих матчей нет")

    # Сохраняем результаты
    out = PROCESSED / "wnba_predictions.json"
    out.write_text(json.dumps({
        "league": "WNBA",
        "season": CURRENT_SEASON,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history_matches": played.height,
        "model": model.summary(),
        "draws_possible": False,
        "draw_removed": True,
        "calibration": {
            "applied": False,
            "T": None,
            "method": "температура T не применена (не проверена на WNBA данных)",
            "source": "data/reports/calibration.json",
            "note": "Калибровка T=0.1826 проверена только на ВТБ. Для WNBA требуется отдельная валидация. Ничья удалена из вероятностей.",
        },
        "verdict_threshold": 0.55,
        "upcoming": upcoming,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Файл: {out}")

    # Рейтинги команд — для страницы «Команды».
    strength_path = PROCESSED / "wnba_strength.json"
    strength_rows = []
    for r in model.strength():
        strength_rows.append({
            "team": r["team"],
            "name": names.get(r["team"], r["team"]),
            "attack": r["attack"],
            "defence": r["defence"],
            "overall": r["overall"],
        })
    strength_path.write_text(json.dumps(strength_rows, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"Рейтинги: {strength_path}")

    # --- Запись в журнал прогнозов (с поддержкой ROI) ---------------------
    if ledger_records:
        added = forecast.append(ledger_records, "wnba")
        print(f"В журнал WNBA добавлено новых записей: {added}")

    # Сохраняем историю с метриками (точность, log-loss, ROI) по сыгранным матчам.
    hist = forecast.save(played, "wnba", out=PROCESSED / "wnba_journal.json")
    s = hist["summary"]
    if s.get("total"):
        print(f"Журнал WNBA: {s['total']} сыгранных прогнозов, "
              f"точность {s['overall']['accuracy']:.1%}, "
              f"log-loss {s['overall']['logloss']}")
        if s["overall"].get("roi") is not None:
            print(f"ROI: {s['overall']['roi']:.2%} "
                  f"(ставок: {s['overall']['total_staked']}, прибыль: {s['overall']['total_profit']})")
    else:
        print("Журнал WNBA пока без сыгранных прогнозов — метрики появятся, когда в данных будут результаты.")

    # Копии в витрину: сайт читает файлы из web/public/data.
    public_dir = Path("web/public/data")
    public_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, public_dir / "wnba_predictions.json")
    shutil.copy(PROCESSED / "wnba_journal.json", public_dir / "wnba_journal.json")
    shutil.copy(strength_path, public_dir / "wnba_strength.json")


if __name__ == "__main__":
    main()