"""Сбор фикстур (расписания) WNBA из официального API NBA (nba_api).
Запуск:
    python -m etl.fetch_wnba_fixtures
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import polars as pl
from nba_api.stats.endpoints import leaguegamefinder
from nba_api.stats.static import teams as nba_teams

from etl.config import PROCESSED, RAW, REPORTS

WNBA_LEAGUE_ID = "10"
SEASONS = ["2024", "2025", "2026", "2027"]
CURRENT_SEASON = "2027"
RATE_LIMIT_DELAY = 1.0

SESSION_CACHE = RAW / "wnba_cache"
SESSION_CACHE.mkdir(parents=True, exist_ok=True)


def _cache_path(season: str) -> Path:
    return SESSION_CACHE / f"wnba_{season}_fixtures.parquet"


def _get_wnba_teams() -> dict[int, str]:
    """Справочник команд: team_id -> name."""
    wnba_teams = nba_teams.get_wnba_teams()
    return {t["id"]: t["full_name"] for t in wnba_teams}


def fetch_season_fixtures(season: str) -> pl.DataFrame:
    """Загружает все матчи сезона WNBA, включая будущие."""
    cache_file = _cache_path(season)
    if cache_file.exists():
        print(f"  Сезон {season}: из кэша {cache_file.name}")
        return pl.read_parquet(cache_file)

    time.sleep(RATE_LIMIT_DELAY)

    try:
        gamefinder = leaguegamefinder.LeagueGameFinder(
            league_id_nullable=WNBA_LEAGUE_ID,
            season_nullable=season,
            timeout=30,
        )
        games = gamefinder.get_data_frames()[0]
    except Exception as e:
        print(f"  Ошибка сезона {season}: {e}")
        return pl.DataFrame()

    if games.empty:
        print(f"  Сезон {season}: 0 матчей")
        return pl.DataFrame()

    team_names = _get_wnba_teams()

    records = []
    for game_id in games["GAME_ID"].unique().tolist():
        game_rows = games[games["GAME_ID"] == game_id]
        if len(game_rows) != 2:
            continue

        home_row = game_rows[game_rows["MATCHUP"].str.contains("vs.", na=False)]
        away_row = game_rows[game_rows["MATCHUP"].str.contains("@", na=False)]

        if home_row.empty or away_row.empty:
            continue

        home = home_row.iloc[0]
        away = away_row.iloc[0]

        home_pts = home["PTS"]
        away_pts = away["PTS"]
        is_played = not (pd.isna(home_pts) or pd.isna(away_pts))

        record = {
            "match_id": str(game_id),
            "season": season,
            "match_date": pd.to_datetime(home["GAME_DATE"]).date(),
            "home_id": str(home["TEAM_ID"]),
            "home_name": team_names.get(home["TEAM_ID"], home["TEAM_NAME"]),
            "away_id": str(away["TEAM_ID"]),
            "away_name": team_names.get(away["TEAM_ID"], away["TEAM_NAME"]),
            "home_score": int(home_pts) if is_played else None,
            "away_score": int(away_pts) if is_played else None,
            "is_played": is_played,
            "is_future": not is_played,
            "home_fg_pct": float(home["FG_PCT"]) if pd.notna(home["FG_PCT"]) else None,
            "away_fg_pct": float(away["FG_PCT"]) if pd.notna(away["FG_PCT"]) else None,
            "home_3p_pct": float(home["FG3_PCT"]) if pd.notna(home["FG3_PCT"]) else None,
            "away_3p_pct": float(away["FG3_PCT"]) if pd.notna(away["FG3_PCT"]) else None,
            "home_reb": int(home["REB"]) if pd.notna(home["REB"]) else 0,
            "away_reb": int(away["REB"]) if pd.notna(away["REB"]) else 0,
            "home_ast": int(home["AST"]) if pd.notna(home["AST"]) else 0,
            "away_ast": int(away["AST"]) if pd.notna(away["AST"]) else 0,
        }
        records.append(record)

    if not records:
        print(f"  Сезон {season}: 0 матчей")
        return pl.DataFrame()

    df = pl.DataFrame(records)
    df.write_parquet(SESSION_CACHE / f"wnba_{season}_fixtures.parquet")
    print(f"  Сезон {season}: {len(df)} матчей (фикстуры)")
    return df


def fetch_all_fixtures() -> pl.DataFrame:
    frames = [fetch_season_fixtures(s) for s in SEASONS]
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed").sort("match_date")


def main():
    print("Загрузка фикстур WNBA...")
    df = fetch_all_fixtures()
    if df.is_empty():
        print("Нет данных фикстур WNBA")
        return

    out_path = PROCESSED / "wnba_fixtures.parquet"
    df.write_parquet(out_path)
    print(f"Сохранено: {out_path}")
    print(f"Всего матчей: {len(df)}")
    print(f"Сезоны: {sorted(df['season'].unique().to_list())}")
    print(f"Диапазон: {df['match_date'].min()} … {df['match_date'].max()}")

    future = df.filter(pl.col("is_future"))
    if future.height:
        future_path = PROCESSED / "wnba_future_fixtures.parquet"
        future.write_parquet(future_path)
        print(f"Будущие матчи: {future.height} -> {future_path}")

    stats = {
        "total_fixtures": len(df),
        "future_fixtures": future.height if future.height else 0,
        "seasons": sorted(df['season'].unique().to_list()),
        "date_range": [str(df['match_date'].min()), str(df['match_date'].max())],
    }
    stats_path = REPORTS / "wnba_fixtures_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats_path}")


if __name__ == "__main__":
    main()