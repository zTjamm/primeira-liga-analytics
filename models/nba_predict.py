"""Прогнозы NBA на ближайшие матчи.
Использует ту же BasketballModel, но с изолированным обучением на данных NBA.
Запуск:
    python -m models.nba_predict
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from etl.config import PROCESSED
from models import forecast
from models.basketball import BasketballModel


def _load_names(matches: pl.DataFrame) -> dict[str, str]:
    pairs = pl.concat([
        matches.select(["home_id", "home_name"]).rename({"home_id": "id", "home_name": "name"}),
        matches.select(["away_id", "away_name"]).rename({"away_id": "id", "away_name": "name"}),
    ])
    grouped = pairs.group_by("id").agg(pl.col("name").mode().first().alias("name"))
    return {r["id"]: r["name"] for r in grouped.to_dicts()}


def main():
    print("Загрузка данных NBA...")
    matches_path = PROCESSED / "nba_matches.parquet"
    if not matches_path.exists():
        print(f"  Нет {matches_path} — запусти: python -m etl.fetch_nba")
        return

    matches = pl.read_parquet(matches_path)
    played = matches.filter(pl.col("home_score").is_not_null())
    print(f"  Матчей в истории: {matches.height}")
    print(f"  Сыгранных: {played.height}")

    names = _load_names(played)
    teams = sorted({m["home_id"] for m in played.to_dicts()} | {m["away_id"] for m in played.to_dicts()})
    model = BasketballModel().fit(played.to_dicts(), teams)
    print(f"Модель: {model.summary().replace('σ', 'sigma')}")

    out = PROCESSED / "nba_predictions.json"
    out.write_text(json.dumps({
        "league": "NBA",
        "season": "2026-27",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history_matches": played.height,
        "model": model.summary(),
        "draws_possible": False,
        "upcoming": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Файл: {out}")

    hist = forecast.save(played, "nba", out=PROCESSED / "nba_journal.json")
    s = hist["summary"]
    if s.get("total"):
        print(f"Журнал NBA: {s['total']} прогнозов, точность {s['overall']['accuracy']:.1%}, log-loss {s['overall']['logloss']}")
    else:
        print("Журнал NBA пока пуст.")

    public_dir = Path("web/public/data")
    public_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, public_dir / "nba_predictions.json")
    shutil.copy(PROCESSED / "nba_journal.json", public_dir / "nba_journal.json")


if __name__ == "__main__":
    main()
