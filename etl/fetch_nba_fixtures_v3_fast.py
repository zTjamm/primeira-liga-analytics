"""Сбор будущих матчей NBA через ScoreboardV3 API.

Источник — официальные серверы NBA (nba_api), ключ не нужен.
ScoreboardV3 отдаёт все матчи за дату: и сыгранные, и запланированные.

Запуск:
    python -m etl.fetch_nba_fixtures_v3_fast
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import polars as pl

from etl.config import PROCESSED, RAW, REPORTS

SEASONS = ["2024-25", "2025-26", "2026-27"]

SESSION_CACHE = RAW / "nba_cache_v3_fast"
SESSION_CACHE.mkdir(parents=True, exist_ok=True)

# Конец регулярного чемпионата и плей-офф по сезонам. Дальше матчей нет.
SEASON_END_DATES = {
    "2024-25": date(2025, 6, 22),
    "2025-26": date(2026, 6, 13),
    "2026-27": date(2027, 4, 18),
}


def _fetch_date(target_date: date, season: str) -> list[dict]:
    """Загружает матчи на одну дату через NBA API."""
    try:
        from nba_api.stats.endpoints import scoreboardv3
        sb = scoreboardv3.ScoreboardV3(game_date=target_date.isoformat())

        game_header_df = sb.game_header.get_data_frame()
        line_score_df = sb.line_score.get_data_frame()

        if len(game_header_df) == 0:
            return []
        if len(line_score_df) == 0:
            return []

        # nba_api отдаёт pandas DataFrame: to_dict() даёт словарь
        # «колонка -> список значений», а to_dicts() у pandas нет.
        gh = game_header_df.to_dict()
        ls = line_score_df.to_dict()

        # Создаём словарь game_id -> список команд.
        teams_by_game: dict[str, list[dict]] = {}
        for i in range(len(ls["gameId"])):
            gid = str(ls["gameId"][i])
            teams_by_game.setdefault(gid, []).append({
                "team_id": str(ls["teamId"][i]),
                "city": str(ls["teamCity"][i]),
                "name": str(ls["teamName"][i]),
            })

        rows: list[dict] = []
        for i in range(len(gh["gameId"])):
            game_id = str(gh["gameId"][i])
            teams = teams_by_game.get(game_id, [])
            if len(teams) < 2:
                continue

            # В line_score порядок команд: первый = гости, второй = хозяева.
            away = teams[0]
            home = teams[1]

            kickoff = None
            for col in ("gameDateTimeUTC", "gameDateTime"):
                if col in gh and gh[col][i]:
                    kickoff = gh[col][i]
                    break

            rows.append({
                "season": season,
                "match_id": game_id,
                "match_date": target_date.isoformat(),
                "kickoff_utc": kickoff,
                "home_id": home["team_id"],
                "home_name": f"{home['city']} {home['name']}",
                "away_id": away["team_id"],
                "away_name": f"{away['city']} {away['name']}",
                "status": gh.get("gameStatusText", [""] * len(gh["gameId"]))[i],
                "is_played": False,
                "is_future": True,
            })
        return rows
    except Exception as e:
        print(f"    Ошибка {target_date}: {e}")
        return []


def fetch_season_fixtures(season: str) -> pl.DataFrame:
    """Загружает будущие матчи сезона NBA, начиная с сегодняшней даты."""
    cache_file = RAW / f"nba_{season}_fixtures_v3.parquet"
    if cache_file.exists():
        print(f"  Сезон {season}: из кэша {cache_file.name}")
        df = pl.read_parquet(cache_file)
        # Старый кэш мог быть сделан без колонки season — добавляем,
        # иначе сводка по сезонам падает с ColumnNotFoundError.
        if df.height and "season" not in df.columns:
            df = df.with_columns(pl.lit(season).alias("season"))
        return df

    end_date = SEASON_END_DATES.get(season, date.today())
    today = date.today()

    if today > end_date:
        print(f"  Сезон {season}: уже завершён")
        return pl.DataFrame()

    print(f"  Сезон {season}: {today} to {end_date}")

    all_rows: list[dict] = []
    current = today
    while current <= end_date:
        rows = _fetch_date(current, season)
        if rows:
            all_rows.extend(rows)
        current += timedelta(days=1)
        time.sleep(0.5)  # вежливость к API

    if all_rows:
        df = pl.DataFrame(all_rows)
        df.write_parquet(SESSION_CACHE / f"nba_{season}_future_fixtures_v3.parquet")
    else:
        df = pl.DataFrame()

    print(f"  Сезон {season}: {df.height} будущих матчей")
    return df


def fetch_all_fixtures() -> pl.DataFrame:
    frames = [fetch_season_fixtures(s) for s in SEASONS]
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed").sort("match_date")


def main():
    print("Загрузка будущих матчей NBA (ScoreboardV3)...")
    df = fetch_all_fixtures()
    if df.is_empty():
        print("Нет данных фикстур NBA")
        return

    out_path = PROCESSED / "nba_future_fixtures_v3.parquet"
    df.write_parquet(out_path)
    print(f"Сохранено: {out_path}")
    print(f"Всего будущих матчей: {len(df)}")
    print(f"Сезоны: {sorted(df['season'].unique().to_list())}")
    print(f"Диапазон: {df['match_date'].min()} … {df['match_date'].max()}")

    stats = {
        "total_future_fixtures": len(df),
        "seasons": sorted(df["season"].unique().to_list()),
        "date_range": [str(df["match_date"].min()), str(df["match_date"].max())],
    }
    stats_path = REPORTS / "nba_future_fixtures_v3_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats_path}")


if __name__ == "__main__":
    main()
