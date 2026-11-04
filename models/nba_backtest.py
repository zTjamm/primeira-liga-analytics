"""Генерация демо-журнала NBA из реальных сыгранных матчей.

Walk-forward по сезонам:
  • Обучаемся на сезоне 2024-25 → предсказываем 2025-26
  • Обучаемся на сезонах 2024-25+2025-26 → предсказываем 2026-27

Запуск:
    python -m models.nba_backtest
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from etl.config import PROCESSED, RAW
from models import ball_sport, forecast, verdict
from models.basketball import BasketballModel


def _load_names(matches: pl.DataFrame) -> dict[str, str]:
    pairs = pl.concat([
        matches.select(["home_id", "home_name"]).rename({"home_id": "id", "home_name": "name"}),
        matches.select(["away_id", "away_name"]).rename({"away_id": "id", "away_name": "name"}),
    ])
    grouped = pairs.group_by("id").agg(pl.col("name").mode().first().alias("name"))
    return {r["id"]: r["name"] for r in grouped.to_dicts()}


def main() -> None:
    matches = pl.read_parquet(PROCESSED / "nba_matches.parquet").sort("match_date")
    played = matches.filter(pl.col("home_score").is_not_null())

    seasons = sorted(played["season"].unique().to_list())
    print(f"Сезоны в данных: {seasons}")

    records: list[dict] = []
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

        games: dict[str, int] = {}
        for m in train_df.to_dicts():
            for side in ("home_id", "away_id"):
                games[m[side]] = games.get(m[side], 0) + 1

        for m in test_df.to_dicts():
            # Ничьей в NBA не бывает — вероятность ничьей удаляется,
            # как и в scripts/nba_predict.py.
            probs = ball_sport.remove_draw(model.predict_1x2(m["home_id"], m["away_id"]))
            top = int(max(range(2), key=lambda i: probs[i]))
            prediction = ["H", "A"][top]
            confidence = float(probs[top])
            team_games_min = min(games.get(m["home_id"], 0),
                                 games.get(m["away_id"], 0))
            ok, reason = verdict.verdict(confidence, team_games_min)

            records.append({
                "key": f"nba_{m['match_id']}",
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
                "p_away": round(probs[1], 4),
                "prediction": prediction,
                "odds_home": None,
                "odds_draw": None,
                "odds_away": None,
                "stake": None,
                "extra": {
                    "verdict_given": ok,
                    "verdict_reason": reason,
                },
            })

    RAW_LEDGER = RAW / "forecast_log_nba.jsonl"
    RAW_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    added = forecast.append(records, "nba")
    print(f"\nВ журнал NBA добавлено записей: {added}")

    hist = forecast.save(played, "nba", out=PROCESSED / "nba_journal.json")
    s = hist["summary"]
    print(f"\nЖурнал NBA: {s['total']} сыгранных прогнозов")
    print(f"  Точность: {s['overall']['accuracy']:.1%}")
    print(f"  Log-loss: {s['overall']['logloss']}")

    PUBLIC_JOURNAL = Path("web/public/data/nba_journal.json")
    PUBLIC_JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(PROCESSED / "nba_journal.json", PUBLIC_JOURNAL)
    print(f"Скопировано в {PUBLIC_JOURNAL}")


if __name__ == "__main__":
    main()
