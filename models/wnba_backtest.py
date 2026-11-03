"""Генерация демо-журнала WNBA из реальных сыгранных матчей.

Walk-forward по сезонам:
  • Обучаемся на сезоне 2024 → предсказываем 2025
  • Обучаемся на сезонах 2024+2025 → предсказываем 2026

Никаких вымышленных чисел: коэффициенты букмекеров отсутствуют,
поэтому ROI-поля остаются null (заполнятся, когда появятся коэффициенты).

Запуск:
    python -m models.wnba_backtest
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from etl.config import PROCESSED, RAW
from models import forecast
from models.basketball import BasketballModel
from models.wnba_predict import _load_names

RAW_LEDGER = RAW / "forecast_log_wnba.jsonl"
MATCHES_PATH = PROCESSED / "wnba_matches.parquet"
OUT_JOURNAL = PROCESSED / "wnba_journal.json"
PUBLIC_JOURNAL = Path("web/public/data/wnba_journal.json")


def _remove_draw(probs_1x2: list[float]) -> list[float]:
    """Вероятности [P1, Ничья, P2] → [P1, 0.0, P2] с перераспределением."""
    p_home, p_draw, p_away = probs_1x2
    decisive = p_home + p_away
    if decisive > 0:
        return [p_home / decisive, 0.0, p_away / decisive]
    return [0.5, 0.0, 0.5]


def main() -> None:
    matches = pl.read_parquet(MATCHES_PATH).sort("match_date")
    played = matches.filter(pl.col("home_score").is_not_null())

    seasons = sorted(played["season"].unique().to_list())
    print(f"Сезоны в данных: {seasons}")

    names = _load_names(played)
    records: list[dict] = []

    # Walk-forward: для каждого сезона после первого — обучаемся на предыдущих.
    for i in range(1, len(seasons)):
        train_seasons = seasons[:i]
        test_season = seasons[i]

        train_df = played.filter(pl.col("season").is_in(train_seasons))
        test_df = played.filter(pl.col("season") == test_season)

        print(
            f"Обучаю на {train_seasons} ({train_df.height} матчей), "
            f"предсказываю {test_season} ({test_df.height} матчей)"
        )

        teams = sorted(
            {m["home_id"] for m in train_df.to_dicts()}
            | {m["away_id"] for m in train_df.to_dicts()}
        )
        model = BasketballModel().fit(train_df.to_dicts(), teams)

        for m in test_df.to_dicts():
            probs = _remove_draw(list(model.predict_1x2(m["home_id"], m["away_id"])))
            top = int(max(range(3), key=lambda i: probs[i]))
            prediction = ["H", "D", "A"][top]

            record = {
                "key": f"wnba_{m['match_id']}",
                "date": str(m["match_date"]),
                "home_id": m["home_id"],
                "away_id": m["away_id"],
                "home_name": m.get("home_name", m["home_id"]),
                "away_name": m.get("away_name", m["away_id"]),
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "hours_before": 24.0,
                "stage": "T-24h",
                "p_home": round(probs[0], 4),
                "p_draw": 0.0,
                "p_away": round(probs[2], 4),
                "prediction": prediction,
                # Коэффициентов букмекеров для WNBA нет — оставляем null.
                "odds_home": None,
                "odds_draw": None,
                "odds_away": None,
                "stake": None,
            }
            records.append(record)

    # Пишем в журнал прогнозов
    RAW_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    added = forecast.append(records, "wnba")
    print(f"\nВ журнал WNBA добавлено записей: {added}")

    # Строим историю с реальными метриками (точность, log-loss, итоги)
    hist = forecast.save(played, "wnba", out=OUT_JOURNAL)
    s = hist["summary"]

    print(f"\nЖурнал WNBA: {s['total']} сыгранных прогнозов")
    print(f"  Точность: {s['overall']['accuracy']:.1%}")
    print(f"  Log-loss: {s['overall']['logloss']}")
    if s["overall"].get("roi") is not None:
        print(f"  ROI: {s['overall']['roi']:.2%}")
    else:
        print("  ROI: — (нет коэффициентов букмекеров)")

    # Копируем в public/data для веб-интерфейса
    PUBLIC_JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(OUT_JOURNAL, PUBLIC_JOURNAL)
    print(f"Скопировано в {PUBLIC_JOURNAL}")


if __name__ == "__main__":
    main()
