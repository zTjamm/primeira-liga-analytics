"""Сбор данных Единой лиги ВТБ из официального API.

Источник — https://api.vtb-league.com, то есть API самой лиги, который
питает её собственный сайт. Ключ не нужен, лимитов не заявлено, ToS не
нарушается: это публичный интерфейс правообладателя.

Эндпоинт найден в JS-бандле сайта:
    GET /v2/leagues/vtb/seasons/{season}/matches?limit=500&fields=...

ЧТО ТУТ ВАЖНО ЗНАТЬ ДО НАЧАЛА РАБОТЫ

1. История короткая. Сезоны до 2024/25 (id 2025) не отдаются вовсе —
   эндпоинт возвращает ноль матчей. Всего доступно около 582 матчей,
   тогда как по Primeira Liga их 7490. В тринадцать раз меньше, и это
   ограничивает качество модели жёстче, чем любые настройки.

2. Матчи делятся на регулярный чемпионат, плей-офф и Матч Всех Звезд.
   В общий список попадает всё, включая звёздный матч, где составы
   сборные и клубов там нет. Отделяем по полю matchType, а не по
   названиям команд — так надёжнее.

3. Идентификатор команды (teamId) стабилен при смене названий:
   MBA-MAI и MBA имеют один id 2747, PARMA и БЕТСИТИ ПАРМА — 3059.
   Поэтому справочник строится по id, а не по тексту имени.

4. Статистики матча нет. Ни владения, ни процента реализации бросков,
   ни подборов. Есть только счёт, счёт по четвертям, посещаемость и
   признак овертайма. Это ключевое ограничение: без владения или темпа
   невозможно разделить силу атаки и силу обороны — они сливаются.
   Модель строится на совместном распределении (разница, тотал).
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import polars as pl
import requests

from etl.config import PROCESSED, RAW, REPORTS

API_BASE = "https://api.vtb-league.com/v2"
LEAGUE = "vtb"

# Сезон в API обозначен последним годом: 2027 = 2026/27.
# Глубже 2025 история не отдаётся — проверено перебором.
SEASONS: list[tuple[str, str]] = [
    ("2025", "2024/25"),
    ("2026", "2025/26"),
    ("2027", "2026/27"),
]

CURRENT_SEASON = "2026/27"

# Что оставляем в обучении по умолчанию.
#
# ALLSTARS выбрасываем всегда: там составы сборные, они создали бы
# фантомные «команды» в реестре.
#
# FINALS — плей-офф — по умолчанию ИСКЛЮЧАЮТСЯ, и это не формальность.
# Во-первых, там серии: phaseName вида «1/2 финала (1)» и «(2)» — это две
# ноги одной серии, и результаты внутри серии коррелированы, что ломает
# независимость наблюдений. Во-вторых, отбор: в плей-офф вышли сильнейшие,
# то есть распределение соперников там другое, чем в регулярке. Смешивать
# их в одной выборке — значит учить модель на двух разных задачах.
KEEP_MATCH_TYPES = {"REGULAR"}
# Оставляем возможность включить плей-офф осознанно, для отдельной проверки.
INCLUDE_PLAYOFFS = False

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "primeira-liga-analytics/1.0 (+research; contact via repo)",
    "Accept": "application/json",
    "Origin": "https://vtb-league.com",
    "Referer": "https://vtb-league.com/ru/statistics/",
})


def _name(node) -> str:
    """teamName приходит объектом {ru, en}. Берём русский — он канонический."""
    if isinstance(node, dict):
        return node.get("ru") or node.get("en") or ""
    return str(node or "")


def fetch_season(season_id: str, season: str) -> pl.DataFrame:
    url = f"{API_BASE}/leagues/{LEAGUE}/seasons/{season_id}/matches?limit=500"
    local = RAW / f"vtb_{season_id}.json"

    if local.exists():
        payload = json.loads(local.read_text(encoding="utf-8"))
    else:
        r = SESSION.get(url, timeout=60)
        r.raise_for_status()
        payload = r.json()
        local.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.5)

    if payload.get("meta", {}).get("code") != 200:
        raise RuntimeError(f"сезон {season}: API вернул {payload.get('meta')}")

    rows = []
    for m in payload.get("data", []):
        mtype = m.get("matchType")
        rows.append({
            "match_id": str(m.get("matchId")),
            "season_id": season_id,
            "season": season,
            "match_type": mtype,
            "phase_name": m.get("phaseName") if isinstance(m.get("phaseName"), str)
                          else json.dumps(m.get("phaseName"), ensure_ascii=False),
            "status": m.get("matchStatus"),
            "match_date": (m.get("matchTimeMSK") or "")[:10] or None,
            "kickoff_msk": (m.get("matchTimeMSK") or "") or None,
            "home_id": str(_team_id(m, home=True) or ""),
            "away_id": str(_team_id(m, home=False) or ""),
            "home_name": _team_name(m, home=True),
            "away_name": _team_name(m, home=False),
            "home_score": _score(m, home=True),
            "away_score": _score(m, home=False),
            "periods": m.get("periods"),
            "extra_period": bool(m.get("extraPeriodsUsed")),
            "attendance": m.get("attendance"),
            "venue": m.get("venue") if isinstance(m.get("venue"), str) else None,
            "quarters": m.get("pointsByQuarters"),
            "is_played": m.get("matchStatus") == "COMPLETE",
        })

    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows)
    df = df.with_columns(
        pl.col("match_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
        pl.col("home_score").cast(pl.Int32, strict=False),
        pl.col("away_score").cast(pl.Int32, strict=False),
        pl.col("attendance").cast(pl.Int32, strict=False),
    )
    return df


def _team_id(m: dict, home: bool) -> str | None:
    for c in m.get("competitors", []):
        if bool(c.get("isHomeCompetitor")) is home:
            return c.get("teamId")
    return None


def _team_name(m: dict, home: bool) -> str:
    for c in m.get("competitors", []):
        if bool(c.get("isHomeCompetitor")) is home:
            return _name(c.get("teamName"))
    return ""


def _score(m: dict, home: bool):
    for c in m.get("competitors", []):
        if bool(c.get("isHomeCompetitor")) is home:
            return c.get("scoreString")
    return None


def fetch_all() -> pl.DataFrame:
    frames = [fetch_season(sid, label) for sid, label in SEASONS]
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed").sort("match_date")


def trainable(df: pl.DataFrame) -> pl.DataFrame:
    """Подмножество, пригодное для обучения: сыгранные, без звёздных,
    со счётом и обеими командами."""
    return df.filter(
        pl.col("is_played")
        & pl.col("match_type").is_in(KEEP_MATCH_TYPES)
        & pl.col("home_score").is_not_null()
        & pl.col("away_score").is_not_null()
        & pl.col("home_id").is_not_null()
        & (pl.col("home_id") != "")
        & pl.col("away_id").is_not_null()
        & (pl.col("away_id") != "")
    )


def teams_registry(df: pl.DataFrame) -> pl.DataFrame:
    """Реестр клубов по teamId с историей названий.

    Строим по обучаемым матчам, поэтому звёздные сборные в реестр не
    попадают. Имена по сезонам склеиваем: один и тот же id может
    называться по-разному, и это нормально — id тут первичен.
    """
    reg = df.filter(pl.col("match_type").is_in(KEEP_MATCH_TYPES)).select([
        pl.struct(["home_id", "home_name", "season"]).alias("h"),
        pl.struct(["away_id", "away_name", "season"]).alias("a"),
    ])
    flat = pl.concat([
        reg.select(["h"]).unnest("h").rename({"home_id": "team_id", "home_name": "name"}),
        reg.select(["a"]).unnest("a").rename({"away_id": "team_id", "away_name": "name"}),
    ]).filter(pl.col("team_id") != "")

    return (
        flat.group_by("team_id")
        .agg(
            pl.col("name").drop_nulls().unique().sort().alias("names"),
            pl.col("season").min().alias("first_season"),
            pl.col("season").max().alias("last_season"),
            pl.len().alias("appearances"),
        )
        .sort("appearances", descending=True)
        .with_columns(pl.col("names").list.head(1).alias("name"))
    )


def build() -> dict:
    raw = fetch_all()
    train = trainable(raw)
    reg = teams_registry(raw)

    train.write_parquet(PROCESSED / "vtb_matches.parquet")
    reg.write_parquet(PROCESSED / "vtb_teams.parquet")

    # Расписание берём ТОЛЬКО из текущего сезона и только со статусом
    # SCHEDULED. Иначе в «предстоящих» попадают отменённые матчи прошлых
    # сезонов: в проверенных данных их оказалось 31 (17 со статусом
    # UNKNOWN/CANCELLED за 2024/25 и 14 CANCELLED за 2025/26), из-за чего
    # счётчик показывал бы 127 матчей вместо 96.
    future = raw.filter(
        (pl.col("season") == CURRENT_SEASON)
        & (~pl.col("is_played"))
        & (pl.col("status") == "SCHEDULED")
        & pl.col("match_type").is_in({"REGULAR", "FINALS"})
        & (pl.col("match_date") >= date.today())
    ).sort("match_date")
    if future.height:
        future.write_parquet(PROCESSED / "vtb_fixtures.parquet")

    played_ids = set(train["match_id"].to_list())
    cancelled = raw.filter(~pl.col("match_id").is_in(list(played_ids)))

    report = {
        "source": f"{API_BASE}/leagues/{LEAGUE}/seasons/<id>/matches",
        "seasons_available": [label for _, label in SEASONS],
        "match_types_used": sorted(KEEP_MATCH_TYPES),
        "include_playoffs": INCLUDE_PLAYOFFS,
        "total_rows": raw.height,
        "trainable": train.height,
        "by_season": {
            label: int(train.filter(pl.col("season") == label).height)
            for _, label in SEASONS
        },
        "dropped_allstars": int(raw.filter(pl.col("match_type") == "ALLSTARS").height),
        "dropped_playoffs": int(
            raw.filter(pl.col("match_type") == "FINALS").height
        ),
        "dropped_not_played": int(cancelled.height),
        "upcoming_current": int(future.height),
        "teams_in_registry": reg.height,
        "teams_thin_history": int(reg.filter(pl.col("appearances") < 20).height),
        "date_range": [str(train["match_date"].min()), str(train["match_date"].max())],
        "avg_points_per_team": round(float(train.select(
            (pl.col("home_score") + pl.col("away_score")) / 2).mean().item()), 2),
        "avg_total_points": round(float(train.select(
            pl.col("home_score") + pl.col("away_score")).mean().item()), 2),
        "home_win_share": round(float(
            train.select((pl.col("home_score") > pl.col("away_score")).mean()).item()), 4),
        "overtime_share": round(float(train.select(pl.col("extra_period").mean()).item()), 4),
        "attendance_median": int(train["attendance"].median() or 0),
    }
    REPORTS.joinpath("vtb_ingest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    rep = build()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
