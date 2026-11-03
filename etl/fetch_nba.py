"""Сбор данных NBA из официального API NBA (через библиотеку nba_api).
Ключ не нужен.

Запуск:
    python -m etl.fetch_nba
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import polars as pl
from nba_api.stats.endpoints import leaguegamefinder

from etl.config import PROCESSED, RAW, REPORTS

SEASONS = ["2024-25", "2025-26", "2026-27"]
CURRENT_SEASON = "2026-27"
NBA_LEAGUE_ID = "00"
RATE_LIMIT_DELAY = 1.0

SESSION_CACHE = RAW / "nba_cache"
SESSION_CACHE.mkdir(parents=True, exist_ok=True)


def _cache_path(season: str) -> Path:
    return SESSION_CACHE / f"nba_{season}.parquet"


def fetch_season(season: str) -> pl.DataFrame:
    cache_file = _cache_path(season)
    if cache_file.exists():
        print(f"  Сезон {season}: из кэша {cache_file.name}")
        return pl.read_parquet(cache_file)

    time.sleep(RATE_LIMIT_DELAY)

    try:
        gamefinder = leaguegamefinder.LeagueGameFinder(
            league_id_nullable=NBA_LEAGUE_ID,
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
        if pd.isna(home_pts) or pd.isna(away_pts):
            continue

        records.append({
            "match_id": str(game_id),
            "season": season,
            "match_date": pd.to_datetime(home["GAME_DATE"]).date(),
            "home_id": str(home["TEAM_ID"]),
            "home_name": home["TEAM_NAME"],
            "away_id": str(away["TEAM_ID"]),
            "away_name": away["TEAM_NAME"],
            "home_score": int(home_pts),
            "away_score": int(away_pts),
            "home_win": int(home_pts > away_pts),
            "total_points": int(home_pts + away_pts),
            "home_fg_pct": float(home["FG_PCT"]) if pd.notna(home["FG_PCT"]) else None,
            "away_fg_pct": float(away["FG_PCT"]) if pd.notna(away["FG_PCT"]) else None,
            "home_3p_pct": float(home["FG3_PCT"]) if pd.notna(home["FG3_PCT"]) else None,
            "away_3p_pct": float(away["FG3_PCT"]) if pd.notna(away["FG3_PCT"]) else None,
            "home_reb": int(home["REB"]) if pd.notna(home["REB"]) else 0,
            "away_reb": int(away["REB"]) if pd.notna(away["REB"]) else 0,
            "home_ast": int(home["AST"]) if pd.notna(home["AST"]) else 0,
            "away_ast": int(away["AST"]) if pd.notna(away["AST"]) else 0,
        })

    if not records:
        print(f"  Сезон {season}: 0 матчей")
        return pl.DataFrame()

    df = pl.DataFrame(records)
    df.write_parquet(cache_file)
    print(f"  Сезон {season}: {len(df)} матчей (кэш)")
    return df


def fetch_all() -> pl.DataFrame:
    frames = [fetch_season(s) for s in SEASONS]
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed").sort("match_date")


def main():
    print("Загрузка данных NBA...")
    df = fetch_all()
    if df.is_empty():
        print("Нет данных NBA")
        return

    out_path = PROCESSED / "nba_matches.parquet"
    df.write_parquet(out_path)
    print(f"Сохранено: {out_path}")
    print(f"Всего матчей: {len(df)}")

    stats = {
        "total_matches": len(df),
        "seasons": sorted(df['season'].unique().to_list()),
        "date_range": [str(df['match_date'].min()), str(df['match_date'].max())],
        "teams": df['home_id'].n_unique(),
        "avg_points_per_game": round(df['total_points'].mean(), 1),
        "home_win_share": round(df['home_win'].mean(), 4),
    }
    stats_path = REPORTS / "nba_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats_path}")


if __name__ == "__main__":
    main()
