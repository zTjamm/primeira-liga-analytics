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

# Формат сезонов NHL: 20242025 = сезон 2024/25.
#
# Сколько сезонов тянуть. Изначально было три (2024/25, 2025/26 и
# текущий 2026/27), но этого мало: на команду выходит около 80 матчей
# за сезон, и сила команды, оценённая по одному сезону, почти целиком
# состоит из шума. Изучение на данных показало: перенос силы команд
# между сезонами держится на уровне r ≈ 0.3-0.5, и на одном сезоне
# подгонки это шум, а не сигнал. Чем больше истории, тем увереннее
# оценка силы — поэтому тянем восемь полных сезонов плюс текущий.
SEASONS = [
    "20182019", "20192020", "20202021", "20212022", "20222023",
    "20232024", "20242025", "20252026", "20262027",
]

# Тип игры, который берём: 2 = регулярный чемпионат.
#
# В API есть и другие типы, и все они попадали в данные:
#   1 — предсезон (17 матчей в 2024/25),
#   3 — плей-офф (16-24 матча за сезон),
#   9 — Олимпиада-2026 (30 матчей со сборными: Франция, Италия, …),
#   19/20 — «4 Nations Face-Off».
#
# Плей-офф — это другой турнир: другая тактика, больше овертаймов,
# другие команды. Сборные — вообще не клубы НХЛ, и в модели они
# выглядят как команды с одной игрой, у которых параметры выводятся
# из шума. Оставим только регулярный сезон.
GAME_TYPE_REGULAR = 2

# Примерные границы регулярных сезонов (месяц-день начала и конца).
# Нужны только для обхода API по датам; season матча определяется по
# префиксу id, а не по дате (см. _season_for_id).
SEASON_DATE_RANGES = {
    "20182019": ("2018-10-03", "2019-04-06"),
    "20192020": ("2019-10-02", "2020-03-11"),
    "20202021": ("2021-01-13", "2021-05-10"),
    "20212022": ("2021-10-12", "2022-04-29"),
    "20222023": ("2022-10-07", "2023-04-14"),
    "20232024": ("2023-10-10", "2024-04-18"),
    "20242025": ("2024-10-04", "2025-04-18"),
    "20252026": ("2025-10-09", "2026-04-17"),
    "20262027": ("2026-10-08", "2027-04-17"),
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "primeira-liga-analytics/1.0"})


def _cache_path(season: str) -> Path:
    return RAW / f"nhl_{season}.json"


def _season_for_id(match_id: str) -> str | None:
    """Сезон по id матча.

    id устроен так: YYYY02NNNN, где YYYY — год начала сезона, а 02 —
    тип игры (регулярный чемпионат). То есть сезон определяется
    однозначно и не зависит от даты: матч, «зависший» на границе
    календаря (например, перенесённый на январь), попадёт в свой
    сезон, а не в чужой.

    Раньше сезон определялся по дате, и матчи плей-офф (апрель-май)
    и Олимпиады (февраль) приписывались к регулярному сезону.
    """
    if not match_id or len(match_id) < 6 or not match_id[:4].isdigit():
        return None
    year = int(match_id[:4])
    return f"{year}{year + 1}"


def _fetch_date(target_date: date, season: str) -> list[dict]:
    """Загружает матчи недели, в которую попадает target_date.

    ВАЖНО: /v1/schedule/{date} отдаёт НЕ один день, а всю неделю целиком
    (в payload семь объектов gameWeek — по одному на день). Если брать из
    ответа все матчи, то при итерации по дням каждый матч попадет в
    выборку семь раз: по разу на каждый день своей недели. Отсюда были
    27 405 «матчей» вместо примерно 4 100 и даты, не совпадающие с
    реальными.

    Поэтому дата матча берётся из startTimeUTC (у API поле называется
    именно так, gameDateTime в ответе нет), а перед сохранением строки
    дедуплицируются по id.
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
            if game.get("gameType") != GAME_TYPE_REGULAR:
                continue
            home = game.get("homeTeam", {})
            away = game.get("awayTeam", {})
            start = game.get("startTimeUTC")
            game_date = start[:10] if start else target_date.isoformat()
            rows.append({
                "season": _season_for_id(str(game.get("id"))) or season,
                "match_id": str(game["id"]),
                "match_date": game_date,
                "kickoff_utc": start,
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
    start = date.fromisoformat(start_str) - timedelta(days=7)
    end = date.fromisoformat(end_str) + timedelta(days=7)

    seen: dict[str, dict] = {}
    current = start
    while current <= end:
        for r in _fetch_date(current, season):
            # Дедупликация по id: неделя отдаётся целиком, поэтому без неё
            # один матч попадает в выборку семь раз.
            seen[r["match_id"]] = r
        current += timedelta(days=1)
        time.sleep(0.12)  # вежливость к API

    all_rows = list(seen.values())

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
        "played_matches": len(played),
        "seasons": sorted(df['season'].unique().to_list()),
        "date_range": [str(df['match_date'].min()), str(df['match_date'].max())],
        "teams": df['home_id'].n_unique(),
        "home_win_share": round(played["home_score"].gt(played["away_score"]).mean(), 4),
        "draws": int(played["home_score"].eq(played["away_score"]).sum()),
    }
    stats_path = REPORTS / "nhl_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats_path}")


if __name__ == "__main__":
    main()
