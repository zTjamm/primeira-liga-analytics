"""Сбор данных НХЛ из официального API api-web.nhle.com.
Ключ не нужен, лимитов не заявлено.
Источник: https://api-web.nhle.com/v1

Запуск:
    python -m etl.fetch_nhl
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import requests

from etl.config import PROCESSED, RAW, REPORTS

API_BASE = "https://api-web.nhle.com/v1"
# Формат сезонов NHL: 20242025 = сезон 2024/25
SEASONS = ["20242025", "20252026", "20262027"]
CURRENT_SEASON = "20262027"

# Примерные границы регулярных сезонов (месяц-день начала и конца)
SEASON_DATE_RANGES = {
    "20242025": ("2024-10-04", "2025-04-17"),
    "20252026": ("2025-10-09", "2026-04-18"),
    "20262027": ("2026-10-08", "2027-04-17"),
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "primeira-liga-analytics/1.0"})


def _cache_path(season: str) -> Path:
    return RAW / f"nhl_{season}.json"


def _fetch_date(target_date: date, season: str) -> list[dict]:
    """Загружает матчи на одну дату."""
    url = f"{API_BASE}/schedule/{target_date.isoformat()}"
    try:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        print(f"    Ошибка {target_date}: {e}")
        return []

    rows = []
    for week in payload.get("gameWeek", []):
        for game in week.get("games", []):
            home = game.get("homeTeam", {})
            away = game.get("awayTeam", {})
            rows.append({
                "season": season,
                "match_id": str(game["id"]),
                "match_date": target_date.isoformat(),
                "home_id": str(home.get("id")),
                "home_name": home.get("commonName", {}).get("default", ""),
                "away_id": str(away.get("id")),
                "away_name": away.get("commonName", {}).get("default", ""),
                "home_score": home.get("score"),
                "away_score": away.get("score"),
                "status": game.get("gameState", ""),
                "is_played": game.get("gameState", "") in ("OFF", "FINAL"),
            })
    return rows


def fetch_season(season: str) -> pl.DataFrame:
    """Загружает все матчи сезона НХЛ, итерируясь по датам."""
    cache_file = _cache_path(season)
    if cache_file.exists():
        print(f"  Сезон {season}: из кэша {cache_file.name}")
        return pl.read_json(cache_file)

    start_str, end_str = SEASON_DATE_RANGES[season]
    start = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)

    all_rows: list[dict] = []
    current = start
    while current <= end:
        rows = _fetch_date(current, season)
        if rows:
            all_rows.extend(rows)
        current += timedelta(days=1)
        time.sleep(0.15)  # вежливость к API

    # Сохраняем кэш
    if all_rows:
        df = pl.DataFrame(all_rows)
        df.write_json(cache_file)
    else:
        df = pl.DataFrame()

    print(f"  Сезон {season}: {df.height} матчей")
    return df


def fetch_all() -> pl.DataFrame:
    frames = [fetch_season(s) for s in SEASONS]
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame()
    df = pl.concat(frames, how="diagonal_relaxed").sort("match_date")
    return df


def main():
    print("Загрузка данных NHL...")
    df = fetch_all()
    if df.is_empty():
        print("Нет данных NHL")
        return

    df = df.with_columns(
        pl.col("match_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
        pl.col("home_score").cast(pl.Int32, strict=False),
        pl.col("away_score").cast(pl.Int32, strict=False),
        pl.col("season").cast(pl.Utf8),
    )

    out_path = PROCESSED / "nhl_matches.parquet"
    df.write_parquet(out_path)
    print(f"Сохранено: {out_path}")
    print(f"Всего матчей: {len(df)}")
    print(f"Сезоны: {sorted(df['season'].unique().to_list())}")
    print(f"Диапазон: {df['match_date'].min()} … {df['match_date'].max()}")

    played = df.filter(pl.col("is_played"))
    stats = {
        "total_matches": len(df),
        "seasons": sorted(df['season'].unique().to_list()),
        "date_range": [str(df['match_date'].min()), str(df['match_date'].max())],
        "teams": df['home_id'].n_unique(),
        "home_win_share": round(played["home_score"].gt(played["away_score"]).mean(), 4),
    }
    stats_path = REPORTS / "nhl_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats_path}")


if __name__ == "__main__":
    main()
