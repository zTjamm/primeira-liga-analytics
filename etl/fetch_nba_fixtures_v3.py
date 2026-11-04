"""Сбор фикстур (расписания) NBA через ScoreboardV3 API.
Запуск:
    python -m etl.fetch_nba_fixtures_v3
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from etl.config import PROCESSED, RAW, REPORTS

API_BASE = "https://stats.nba.com/stats"
NBA_LEAGUE_ID = "00"
SEASONS = ["2024-25", "2025-26", "2026-27"]
CURRENT_SEASON = "2026-27"

RATE_LIMIT_DELAY = 1.0

SESSION_CACHE = RAW / "nba_cache_v3"
SESSION_CACHE.mkdir(parents=True, exist_ok=True)


def _cache_path(season: str) -> Path:
    return SESSION_CACHE / f"nba_{season}_fixtures.parquet"


def _get_season_date_range(season: str) -> tuple[date, date]:
    """Определяет диапазон дат для сезона NBA."""
    if season == "2024-25":
        return date(2024, 10, 4), date(2025, 6, 22)
    elif season == "2025-26":
        return date(2025, 10, 3), date(2026, 6, 13)
    elif season == "2026-27":
        return date(2026, 10, 3), date(2027, 4, 18)
    else:
        raise ValueError(f"Unknown season: {season}")


def _fetch_date(target_date: date, season: str) -> list[dict]:
    """Загружает матчи на одну дату через ScoreboardV3."""
    try:
        from nba_api.stats.endpoints import scoreboardv3
        sb = scoreboardv3.ScoreboardV3(game_date=target_date.isoformat())
        
        game_header_df = sb.game_header.get_data_frame()
        line_score_df = sb.line_score.get_data_frame()
        
        if game_header_df.empty:
            return []
        
        # Get team info from line_score
        line_score_df = sb.line_score.get_data_frame()
        if line_score_df.empty:
            return []
        
        # Group by gameId to get home/away teams
        team_info = line_score_df.groupby('gameId').agg(
            pl.col('teamId').alias('team_ids'),
            pl.col('teamCity').alias('team_cities'),
            pl.col('teamName').alias('team_names'),
            pl.col('teamTricode').alias('team_tricodes'),
            pl.col('score').alias('scores'),
        )
        
        # Merge with game header
        games = game_header_df.join(team_info, on='gameId', how='left')
        
        rows = []
        for row in games.to_dicts():
            # Determine home/away from team info
            # Usually first is away, second is home (need to verify)
            team_cities = row.get('team_cities', [])
            team_names = row.get('team_names', [])
            team_ids = row.get('team_ids', [])
            team_tricodes = row.get('team_tricodes', [])
            
            if len(team_cities) >= 2:
                # NBA: first is away, second is home (based on API convention)
                away_city = team_cities[0]
                away_name = team_names[0]
                away_id = team_ids[0]
                home_city = team_cities[1]
                home_name = team_names[1]
                home_id = team_ids[1]
            else:
                continue
            
            rows.append({
                "match_id": str(row.get("gameId")),
                "match_date": target_date.isoformat(),
                "season": season,
                "home_id": str(home_id),
                "home_name": f"{home_city} {home_name}",
                "away_id": str(away_id),
                "away_name": f"{away_city} {away_name}",
                "status": row.get("gameStatusText", ""),
                "is_future": row.get("gameStatus", 0) == 1,  # 1 = pre-game
            })
        return rows

    except Exception as e:
        print(f"    Ошибка {target_date}: {e}")
        return []


def fetch_season_fixtures(season: str) -> pl.DataFrame:
    """Загружает все матчи сезона, итерируясь по датам."""
    cache_file = RAW / f"nba_{season}_fixtures_v3.parquet"
    if cache_file.exists():
        print(f"  Сезон {season}: из кэша {cache_file.name}")
        return pl.read_parquet(cache_file)

    start_date, end_date = _get_season_date_range(season)
    print(f"  Сезон {season}: {start_date} to {end_date}")

    all_rows: list[dict] = []
    current = start_date
    while current <= end_date:
        rows = _fetch_date(current, season)
        if rows:
            all_rows.extend(rows)
        current += timedelta(days=1)
        time.sleep(0.5)  # вежливость к API

    # Сохраняем кэш
    if all_rows:
        df = pl.DataFrame(all_rows)
        df.write_parquet(RAW / f"nba_{season}_fixtures_v3.parquet")
    else:
        df = pl.DataFrame()

    print(f"  Сезон {season}: {df.height} матчей")
    return df


def fetch_all_fixtures() -> pl.DataFrame:
    frames = [fetch_season_fixtures(s) for s in SEASONS]
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed").sort("match_date")


def _get_season_date_range(season: str) -> tuple[date, date]:
    """Определяет границы сезона NBA."""
    if season == "2024-25":
        return date(2024, 10, 4), date(2025, 6, 22)
    elif season == "2025-26":
        return date(2025, 10, 3), date(2026, 6, 13)
    elif season == "2026-27":
        return date(2026, 10, 3), date(2027, 4, 18)
    else:
        raise ValueError(f"Unknown season: {season}")


def main():
    print("Загрузка фикстур NBA (ScoreboardV3)...")
    df = fetch_all_fixtures()
    if df.is_empty():
        print("Нет данных фикстур NBA")
        return

    out_path = PROCESSED / "nba_fixtures_v3.parquet"
    df.write_parquet(out_path)
    print(f"Сохранено: {out_path}")
    print(f"Всего матчей: {len(df)}")

    stats = {
        "total_fixtures": len(df),
        "seasons": sorted(df['season'].unique().to_list()),
        "date_range": [str(df['match_date'].min()), str(df['match_date'].max())],
    }
    stats_path = REPORTS / "nba_fixtures_v3_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats_path}")


if __name__ == "__main__":
    main()