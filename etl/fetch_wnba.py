"""Сбор данных WNBA из nba_api.

Источник — официальный API NBA (через библиотеку nba_api).
Ключ не нужен, библиотека обращается напрямую к серверам NBA.com.

Ключевые особенности:
1. История доступна с 1997 года (первый сезон WNBA).
2. Статистика богаче, чем у ВТБ: есть FG%, 3PT%, REB, AST, STL, BLK, TOV и т.д.
3. Игры идут с мая по октябрь (сезон).
4. Нет коэффициентов букмекеров — только результаты.
5. Нет ничьих (как и в ВТБ): 0 из всех сыгранных матчей.

Запуск:
    python -m etl.fetch_wnba
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import polars as pl
from nba_api.stats.endpoints import leaguegamefinder
from nba_api.stats.static import teams as nba_teams

from etl.config import PROCESSED, RAW, REPORTS

# Кеш загруженных сезонов, чтобы не качать с NBA API повторно.
CACHE_DIR = RAW / "wnba_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(season_id: str) -> Path:
    return CACHE_DIR / f"wnba_{season_id}.parquet"


# Сезоны WNBA (год последнего сезона)
SEASONS: list[tuple[str, str]] = [
    ("2024", "2024"),
    ("2025", "2025"),
    ("2026", "2026"),
]

CURRENT_SEASON = "2026"

# WNBA league_id = 10
WNBA_LEAGUE_ID = "10"

# Что оставляем в обучении
KEEP_MATCH_TYPES = {"Regular Season", "Playoffs"}  # все матчи
INCLUDE_PLAYOFFS = True

# Rate limiting: nba_api требует задержки между запросами
RATE_LIMIT_DELAY = 1.0  # секунды между запросами


def _get_wnba_teams() -> dict[int, str]:
    """Справочник команд: team_id -> name."""
    wnba_teams = nba_teams.get_wnba_teams()
    return {t["id"]: t["full_name"] for t in wnba_teams}


def fetch_season(season_id: str, season: str) -> pl.DataFrame:
    """Отдаёт матчи сезона WNBA с кешированием."""
    cache_file = _cache_path(season_id)

    if cache_file.exists():
        print(f"  Сезон {season}: загружаю из кэша {cache_file.name}")
        return pl.read_parquet(cache_file)

    time.sleep(RATE_LIMIT_DELAY)

    try:
        gamefinder = leaguegamefinder.LeagueGameFinder(
            league_id_nullable=WNBA_LEAGUE_ID,
            season_nullable=season_id,
            timeout=30,
        )
        games = gamefinder.get_data_frames()[0]
    except Exception as e:
        print(f"  Ошибка при загрузке сезона {season}: {e}")
        return pl.DataFrame()

    if games.empty:
        print(f"  Сезон {season}: 0 матчей")
        return pl.DataFrame()

    # Преобразуем в нужный формат
    # Каждая игра представлена двумя строками (для каждой команды)
    # Нужно объединить их в один матч

    # Группируем по GAME_ID
    game_ids = games["GAME_ID"].unique().tolist()

    records = []
    for game_id in game_ids:
        game_rows = games[games["GAME_ID"] == game_id]
        if len(game_rows) != 2:
            continue

        home_row = game_rows[game_rows["MATCHUP"].str.contains("vs.", na=False)]
        away_row = game_rows[game_rows["MATCHUP"].str.contains("@", na=False)]

        if home_row.empty or away_row.empty:
            continue

        home = home_row.iloc[0]
        away = away_row.iloc[0]

        # Проверяем, сыгран ли матч (есть счёт)
        home_pts = home["PTS"]
        away_pts = away["PTS"]
        if pd.isna(home_pts) or pd.isna(away_pts):
            continue

        record = {
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
            # Статистика (для будущей модели)
            "home_fg_pct": float(home["FG_PCT"]) if pd.notna(home["FG_PCT"]) else None,
            "away_fg_pct": float(away["FG_PCT"]) if pd.notna(away["FG_PCT"]) else None,
            "home_3p_pct": float(home["FG3_PCT"]) if pd.notna(home["FG3_PCT"]) else None,
            "away_3p_pct": float(away["FG3_PCT"]) if pd.notna(away["FG3_PCT"]) else None,
            "home_reb": int(home["REB"]) if pd.notna(home["REB"]) else 0,
            "away_reb": int(away["REB"]) if pd.notna(away["REB"]) else 0,
            "home_ast": int(home["AST"]) if pd.notna(home["AST"]) else 0,
            "away_ast": int(away["AST"]) if pd.notna(away["AST"]) else 0,
            "home_stl": int(home["STL"]) if pd.notna(home["STL"]) else 0,
            "away_stl": int(away["STL"]) if pd.notna(away["STL"]) else 0,
            "home_blk": int(home["BLK"]) if pd.notna(home["BLK"]) else 0,
            "away_blk": int(away["BLK"]) if pd.notna(away["BLK"]) else 0,
            "home_tov": int(home["TOV"]) if pd.notna(home["TOV"]) else 0,
            "away_tov": int(away["TOV"]) if pd.notna(away["TOV"]) else 0,
        }
        records.append(record)

    if not records:
        print(f"  Сезон {season}: 0 матчей")
        return pl.DataFrame()

    df = pl.DataFrame(records)

    # Сохраняем в кэш, чтобы не качать с NBA API повторно.
    df.write_parquet(cache_file)
    print(f"  Сезон {season}: {len(df)} матчей (сохранено в кэш)")

    return df


def fetch_all() -> pl.DataFrame:
    frames = [fetch_season(sid, label) for sid, label in SEASONS]
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed").sort("match_date")


def main():
    print("Загрузка данных WNBA...")
    df = fetch_all()
    if df.is_empty():
        print("Нет данных WNBA")
        return

    print(f"Всего матчей: {len(df)}")
    print(f"Сезоны: {sorted(df['season'].unique().to_list())}")
    print(f"Диапазон дат: {df['match_date'].min()} … {df['match_date'].max()}")

    # Сохраняем в parquet
    out_path = PROCESSED / "wnba_matches.parquet"
    df.write_parquet(out_path)
    print(f"Сохранено: {out_path}")

    # Статистика
    stats = {
        "total_matches": len(df),
        "seasons": sorted(df['season'].unique().to_list()),
        "date_range": [str(df['match_date'].min()), str(df['match_date'].max())],
        "teams": df['home_id'].n_unique(),
        "avg_points_per_game": round(df['total_points'].mean(), 1),
        "home_win_share": round(df['home_win'].mean(), 4),
    }
    stats_path = REPORTS / "wnba_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats_path}")


if __name__ == "__main__":
    main()