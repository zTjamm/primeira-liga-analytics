"""Скачивание расписания и результатов с football-data.org.

Из этого источника берём только то, чего нет в CSV:
  * будущие матчи (для прогнозов)
  * официальные названия клубов и их ID
  * текущую таблицу

История, статистика матча и коэффициенты — из football-data.co.uk.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import polars as pl
import requests

from .config import COMP_CODE, FD_ORG_BASE, fixture_horizon_days, fd_token

SESSION = requests.Session()


def _headers() -> dict[str, str]:
    tok = fd_token()
    if not tok:
        raise RuntimeError(
            "Нет FD_ORG_TOKEN. Положи токен в .env (шаблон — .env.example). "
            "Без него недоступно расписание будущих матчей."
        )
    return {"X-Auth-Token": tok}


def _get(path: str, **params) -> dict:
    resp = SESSION.get(f"{FD_ORG_BASE}{path}", headers=_headers(), params=params, timeout=60)
    resp.raise_for_status()
    time.sleep(0.2)
    return resp.json()


def fetch_fixtures(days_ahead: int | None = None) -> pl.DataFrame:
    """Будущие и незавершённые матчи."""
    days = days_ahead or fixture_horizon_days()
    today = date.today()
    payload = _get(
        f"/competitions/{COMP_CODE}/matches",
        dateFrom=today.isoformat(),
        dateTo=(today + timedelta(days=days)).isoformat(),
    )
    rows = []
    for m in payload.get("matches", []):
        ft = m.get("score", {}).get("fullTime", {})
        rows.append(
            {
                "fd_match_id": m["id"],
                "match_date": date.fromisoformat(m["utcDate"][:10]),
                "kickoff_utc": m["utcDate"],
                "matchday": m.get("matchday"),
                "status": m.get("status"),
                "home_name": m["homeTeam"]["name"],
                "away_name": m["awayTeam"]["name"],
                "home_id_api": m["homeTeam"]["id"],
                "away_id_api": m["awayTeam"]["id"],
                "fthg": ft.get("home"),
                "ftag": ft.get("away"),
                "ftr": _ftr(ft.get("home"), ft.get("away")),
            }
        )
    if not rows:
        return pl.DataFrame(
            schema={
                "fd_match_id": pl.Int64, "match_date": pl.Date, "kickoff_utc": pl.String,
                "matchday": pl.Int64, "status": pl.String, "home_name": pl.String,
                "away_name": pl.String, "home_id_api": pl.Int64, "away_id_api": pl.Int64,
                "fthg": pl.Int64, "ftag": pl.Int64, "ftr": pl.String,
            }
        )
    return pl.DataFrame(rows).sort("match_date")


def _ftr(h: int | None, a: int | None) -> str | None:
    if h is None or a is None:
        return None
    return "H" if h > a else ("A" if a > h else "D")


def fetch_results_since(start: date) -> pl.DataFrame:
    """Сыгранные матчи начиная с даты — чтобы догружать результаты быстрее,
    чем обновляется CSV (он отстаёт примерно на две недели)."""
    today = date.today()
    payload = _get(
        f"/competitions/{COMP_CODE}/matches",
        dateFrom=start.isoformat(),
        dateTo=today.isoformat(),
        status="FINISHED",
    )
    rows = []
    for m in payload.get("matches", []):
        ft = m.get("score", {}).get("fullTime", {})
        rows.append(
            {
                "fd_match_id": m["id"],
                "match_date": date.fromisoformat(m["utcDate"][:10]),
                "kickoff_utc": m["utcDate"],
                "matchday": m.get("matchday"),
                "status": m.get("status"),
                "home_name": m["homeTeam"]["name"],
                "away_name": m["awayTeam"]["name"],
                "home_id_api": m["homeTeam"]["id"],
                "away_id_api": m["awayTeam"]["id"],
                "fthg": ft.get("home"),
                "ftag": ft.get("away"),
                "ftr": _ftr(ft.get("home"), ft.get("away")),
            }
        )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def fetch_standings() -> pl.DataFrame:
    payload = _get(f"/competitions/{COMP_CODE}/standings")
    blocks = payload.get("standings", [])
    if not blocks:
        return pl.DataFrame()
    table = blocks[0].get("table", [])
    rows = []
    for e in table:
        rows.append(
            {
                "position": e.get("position"),
                "team_name": e["team"]["name"],
                "team_id_api": e["team"]["id"],
                "played": e.get("playedGames"),
                "won": e.get("won"),
                "drawn": e.get("draw"),
                "lost": e.get("lost"),
                "goals_for": e.get("goalsFor"),
                "goals_against": e.get("goalsAgainst"),
                "goal_difference": e.get("goalDifference"),
                "points": e.get("points"),
            }
        )
    return pl.DataFrame(rows)


def fetch_teams() -> pl.DataFrame:
    payload = _get(f"/competitions/{COMP_CODE}/teams")
    return pl.DataFrame(
        [
            {
                "team_id_api": t["id"],
                "team_name": t["name"],
                "short_name": t.get("shortName"),
                "founded": t.get("founded"),
            }
            for t in payload.get("teams", [])
        ]
    )
