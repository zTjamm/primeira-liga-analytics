"""Сбор фикстур (расписания) НХЛ из официального API api-web.nhle.com.
Запуск:
    python -m etl.fetch_nhl_fixtures
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
SEASONS = ["20242025", "20252026", "20262027"]
CURRENT_SEASON = "20262027"

SEASON_DATE_RANGES = {
    "20242025": ("2024-10-04", "2025-04-17"),
    "20252026": ("2025-10-09", "2026-04-18"),
    "20262027": ("2026-10-08", "2027-04-17"),
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "primeira-liga-analytics/1.0"})


def _cache_path(season: str) -> Path:
    return RAW / f"nhl_{season}_fixtures.json"


def _season_for_date(game_date: str, fallback: str) -> str:
    """Сезон по фактической дате матча (см. etl/fetch_nhl.py)."""
    for season, (start, end) in SEASON_DATE_RANGES.items():
        if start <= game_date <= end:
            return season
    return fallback


def _fetch_date(target_date: date, season: str) -> list[dict]:
    """Загружает матчи недели, в которую попадает target_date.

    ВАЖНО: /v1/schedule/{date} отдаёт НЕ один день, а всю неделю целиком
    (в payload семь объектов gameWeek — по одному на день). Если брать из
    ответа все матчи, то при итерации по дням каждый матч попадёт в
    выборку семь раз. Поэтому дата матча берётся из startTimeUTC, а
    строки дедуплицируются по id (см. etl/fetch_nhl.py).
    """
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
            game_state = game.get("gameState", "")
            start = game.get("startTimeUTC")
            game_date = start[:10] if start else target_date.isoformat()
            rows.append({
                "season": _season_for_date(game_date, season),
                "match_id": str(game["id"]),
                "match_date": game_date,
                "kickoff_utc": start,
                "home_id": str(home.get("id")),
                "home_name": home.get("commonName", {}).get("default", ""),
                "away_id": str(away.get("id")),
                "away_name": away.get("commonName", {}).get("default", ""),
                "home_score": home.get("score"),
                "away_score": away.get("score"),
                "status": game_state,
                "is_played": game_state in ("OFF", "FINAL"),
                "is_future": game_state in ("FUT", "PRE", "LIVE", "CRIT"),
            })
    return rows


def fetch_season_fixtures(season: str) -> pl.DataFrame:
    """Загружает все матчи сезона НХЛ, итерируясь по датам."""
    cache_file = RAW / f"nhl_{season}_fixtures.json"
    if cache_file.exists():
        print(f"  Сезон {season}: из кэша {cache_file.name}")
        return pl.read_json(cache_file)

    start_str, end_str = SEASON_DATE_RANGES[season]
    start = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)

    all_rows: list[dict] = []
    seen: dict[str, dict] = {}
    current = start
    while current <= end:
        rows = _fetch_date(current, season)
        for r in rows:
            # Неделя отдаётся целиком: без дедупликации один матч попадает
            # в выборку семь раз.
            seen[r["match_id"]] = r
        current += timedelta(days=1)
        time.sleep(0.15)

    all_rows = list(seen.values())

    # Сохраняем кэш
    if all_rows:
        df = pl.DataFrame(all_rows)
        df.write_json(RAW / f"nhl_{season}_fixtures.json")
    else:
        df = pl.DataFrame()

    print(f"  Сезон {season}: {df.height} матчей (фикстуры)")
    return df


def fetch_all_fixtures() -> pl.DataFrame:
    frames = [fetch_season_fixtures(s) for s in SEASONS]
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed").sort("match_date")


def main():
    print("Загрузка фикстур НХЛ...")
    df = fetch_all_fixtures()
    if df.is_empty():
        print("Нет данных фикстур НХЛ")
        return

    # Сохраняем все фикстуры
    out_path = PROCESSED / "nhl_fixtures.parquet"
    df.write_parquet(out_path)
    print(f"Сохранено: {out_path}")
    print(f"Всего матчей: {len(df)}")
    print(f"Сезоны: {sorted(df['season'].unique().to_list())}")
    print(f"Диапазон: {df['match_date'].min()} … {df['match_date'].max()}")

    # Отдельно сохраняем только будущие матчи.
    #
    # Фильтр по дате, а НЕ по колонке is_future: в официальном API НХЛ
    # есть отложенные матчи прошлых сезонов со статусом FUT, и по
    # статусу они попадали в «будущие». На сайте такие матчи выглядели
    # как матчи будущего сезона, хотя на самом деле это перенесённые
    # игры 2024 года — отсюда «матчи 2024 года в будущем».
    today = date.today().isoformat()
    future = df.filter(pl.col("match_date").cast(pl.Utf8) >= today)
    if future.height:
        future_path = PROCESSED / "nhl_future_fixtures.parquet"
        future.write_parquet(future_path)
        print(f"Будущие матчи: {future.height} -> {future_path}")
    else:
        print(f"Будущих матчей нет (сегодня {today})")

    stats = {
        "total_fixtures": len(df),
        "future_fixtures": future.height if future.height else 0,
        "future_from": today,
        "seasons": sorted(df['season'].unique().to_list()),
        "date_range": [str(df['match_date'].min()), str(df['match_date'].max())],
    }
    stats_path = REPORTS / "nhl_fixtures_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats_path}")


if __name__ == "__main__":
    main()