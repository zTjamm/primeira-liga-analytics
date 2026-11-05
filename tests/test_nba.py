"""Проверка пайплайна NBA.

Здесь собрано то, что в NBA уже ломалось и что легко сломать снова.

1. Единый источник данных. Для будущих матчей был отдельный сборщик
   (etl/fetch_nba_fixtures.py, а до него ещё два — v3 и v3_fast) со
   своими соглашениями и вечным кэшем. Ни один из них не запускался ни
   в цикле сервера, ни в CI, из-за чего в журнале NBA не было матчей за
   последний день. Теперь источник один — nba_matches.parquet.

2. Кэш сезона не должен быть «навсегда». Старый сборщик возвращал кэш
   и больше не ходил в сеть: сезон записывался один раз, и новые матчи
   в него уже не попадали.

3. Число матчей в завершённом сезоне известно точно: 1 401 (1 230 регулярки
   плюс 171 матч плей-офф). Расхождение означает потерянный запрос.

4. Кто хозяева — по календарю, а не по строке MATCHUP. У части матчей
   статистика отдаёт одинаковый MATCHUP в обеих строках, а у матчей NBA
   Global Games строк три (очки гостей разбиты на две части). Старый
   разбор по строке терял такие матчи молча: девять игр за сезон.

5. Неудачный запрос не должен терять сезон молча.

6. Будущие матчи — только те, что ещё не начались, и у них должно быть
   время начала: без него они не попадают в раздел «ближайшие два дня».

Запуск:
    python -m tests.test_nba
"""
from __future__ import annotations

import ast
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import polars as pl

from etl import fetch_nba as fn
from models import ball_sport, nba_predict

ROOT = Path(__file__).resolve().parent.parent

PASS = "\u2713"
FAIL = "\u2717"


def _check(name: str, got, want) -> bool:
    ok = got == want
    print(f"  [{PASS if ok else FAIL}] {name}"
          + ("" if ok else f"  (получено {got!r}, ждали {want!r})"))
    return ok


def _check_bool(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{PASS if ok else FAIL}] {name}"
          + ("" if ok else f"  ({detail})"))
    return ok


# --------------------------------------------------------------------------
# 1. Единый источник данных


def _code_strings(path: Path) -> list[str]:
    """Строковые литералы в коде — без текста документации.

    Иначе проверка «в коде нет имени старого файла» ловила бы
    упоминание этого файла в пояснении к модулю.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value not in docstrings]


def check_single_source() -> bool:
    print("\nЕдиный источник данных:")
    ok = True
    predict_path = ROOT / "models" / "nba_predict.py"
    predict_text = predict_path.read_text(encoding="utf-8")
    code = _code_strings(predict_path)
    ok &= _check_bool(
        "прогнозы не читают отдельный файл фикстур",
        not any("nba_future_fixtures" in s for s in code),
        "файл читался из etl/fetch_nba_fixtures.py — без времени начала "
        "и с вечным кэшем")
    ok &= _check_bool(
        "прогнозы берут матчи из nba_matches.parquet",
        "nba_matches.parquet" in predict_text)
    for name in ("fetch_nba_fixtures.py", "fetch_nba_fixtures_v3.py",
                 "fetch_nba_fixtures_v3_fast.py"):
        ok &= _check_bool(
            f"дублирующего сборщика {name} больше нет",
            not (ROOT / "etl" / name).exists(),
            f"etl/{name} всё ещё в репозитории")
    ok &= _check_bool(
        "в веб-журнал NBA кладётся урезанная история",
        "write_web_journal" in predict_text,
        "полный журнал — три с половиной мегабайта, страница грузит "
        "его целиком")
    return ok


# --------------------------------------------------------------------------
# 2. Кэш не «навсегда»


def check_refresh() -> bool:
    print("\nОбновление текущего сезона:")
    ok = True
    today = date(2026, 11, 5)
    ok &= _check("метка сезона в формате NBA",
                 fn._season_label(2026), "2026-27")
    ok &= _check("сезоны в данных: текущий и два предыдущих",
                 fn._seasons(today), ["2026-27", "2025-26", "2024-25"])
    ok &= _check("обновляется текущий сезон",
                 "2026-27" in fn._refresh_seasons(today), True)
    ok &= _check("завершённый сезон не обновляется",
                 "2025-26" in fn._refresh_seasons(today), False)
    ok &= _check("следующий сезон не обновляется, пока он не близко",
                 "2027-28" in fn._refresh_seasons(today), False)
    ok &= _check("за месяц до старта следующего сезона он уже обновляется",
                 "2026-27" in fn._refresh_seasons(date(2026, 9, 1)), True)

    # Кэш без времени начала — старый формат: он пересобирается, а не
    # используется молча.
    ok &= _check_bool(
        "старый кэш без времени начала считается негодным",
        fn.REQUIRED_COLUMNS and "kickoff_utc" in fn.REQUIRED_COLUMNS
        and "is_played" in fn.REQUIRED_COLUMNS,
        "иначе расписание осталось бы без времени начала навсегда")
    return ok


# --------------------------------------------------------------------------
# 3. Число матчей в сезоне


def check_season_counts() -> bool:
    print("\nСамопроверка числа матчей:")
    ok = True
    ok &= _check("полный сезон — 1 401 матч",
                 fn.FULL_SEASON_MATCHES.get("2024-25"), 1401)
    ok &= _check("и 2025-26 столько же",
                 fn.FULL_SEASON_MATCHES.get("2025-26"), 1401)
    ok &= _check("текущий сезон в таблице проверки не значится",
                 "2026-27" in fn.FULL_SEASON_MATCHES, False)
    return ok


# --------------------------------------------------------------------------
# 4. Кто дома, кто в гостях


def _stats_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for col in ("GAME_ID", "GAME_DATE", "MATCHUP", "TEAM_ID", "TEAM_NAME",
                "PTS", "WL", "FG_PCT", "FG3_PCT", "REB", "AST"):
        if col not in frame.columns:
            frame[col] = None
    return frame


def check_home_away() -> bool:
    print("\nОпределение дома и гостей:")
    ok = True

    # Обычный матч: в обеих строках MATCHUP разный.
    normal = _stats_frame([
        {"GAME_ID": "1", "GAME_DATE": "2026-11-04", "MATCHUP": "PHI @ DET",
         "TEAM_ID": "1610612755", "TEAM_NAME": "Philadelphia 76ers",
         "PTS": 112, "FG_PCT": 0.5, "FG3_PCT": 0.4, "REB": 40, "AST": 25},
        {"GAME_ID": "1", "GAME_DATE": "2026-11-04", "MATCHUP": "DET vs. PHI",
         "TEAM_ID": "1610612765", "TEAM_NAME": "Detroit Pistons",
         "PTS": 106, "FG_PCT": 0.45, "FG3_PCT": 0.35, "REB": 42, "AST": 22},
    ])
    home, away = fn._split_teams(normal, ("1610612765", "1610612755"))
    ok &= _check("хозяева по календарю", str(home.iloc[0]["TEAM_ID"]),
                 "1610612765")
    ok &= _check("гости по календарю", str(away.iloc[0]["TEAM_ID"]),
                 "1610612755")

    # Битый MATCHUP: обе строки одинаковые. Разбор по строке такой матч
    # терял, а календарь знает правильный ответ.
    broken = _stats_frame([
        {"GAME_ID": "2", "GAME_DATE": "2026-10-11", "MATCHUP": "DAL @ HOU",
         "TEAM_ID": "1610612745", "TEAM_NAME": "Houston Rockets",
         "PTS": 115, "FG_PCT": 0.5, "FG3_PCT": 0.4, "REB": 40, "AST": 25},
        {"GAME_ID": "2", "GAME_DATE": "2026-10-11", "MATCHUP": "DAL @ HOU",
         "TEAM_ID": "1610612742", "TEAM_NAME": "Dallas Mavericks",
         "PTS": 132, "FG_PCT": 0.52, "FG3_PCT": 0.42, "REB": 38, "AST": 27},
    ])
    home, away = fn._split_teams(broken, ("1610612745", "1610612742"))
    ok &= _check_bool("битый MATCHUP не ломает разбор", home is not None
                      and away is not None,
                      "обе строки одинаковые — разбор по строке даёт пусто")
    ok &= _check("хозяева из календаря", str(home.iloc[0]["TEAM_ID"]),
                 "1610612745")

    # NBA Global Games: три строки, очки гостей разбиты на две части.
    global_game = _stats_frame([
        {"GAME_ID": "3", "GAME_DATE": "2025-10-04", "MATCHUP": "HAP @ BKN",
         "TEAM_ID": "50014", "TEAM_NAME": "Hapoel Jerusalem B.C.",
         "PTS": 16, "FG_PCT": 0.3, "FG3_PCT": 0.2, "REB": 10, "AST": 4},
        {"GAME_ID": "3", "GAME_DATE": "2025-10-04", "MATCHUP": "BKN vs. HAP",
         "TEAM_ID": "1610612751", "TEAM_NAME": "Brooklyn Nets",
         "PTS": 123, "FG_PCT": 0.5, "FG3_PCT": 0.4, "REB": 40, "AST": 25},
        {"GAME_ID": "3", "GAME_DATE": "2025-10-04", "MATCHUP": "HAP @ BKN",
         "TEAM_ID": "50014", "TEAM_NAME": "Hapoel Jerusalem B.C.",
         "PTS": 72, "FG_PCT": 0.35, "FG3_PCT": 0.25, "REB": 20, "AST": 9},
    ])
    home_rows, away_rows = fn._split_teams(global_game,
                                           ("1610612751", "50014"))
    ok &= _check("очки гостей Global Games сложены",
                 fn._team_points(away_rows), 88)
    ok &= _check("очки хозяев на месте", fn._team_points(home_rows), 123)
    return ok


# --------------------------------------------------------------------------
# 5. Повторы запроса


def check_fetch_retries() -> bool:
    print("\nПовторы запроса:")
    ok = True
    ok &= _check("попыток на запрос", fn.FETCH_ATTEMPTS, 3)

    calls = {"n": 0}

    def _fail():
        calls["n"] += 1
        raise RuntimeError("сеть отвалилась")

    result = fn._fetch_with_retry("тест", _fail)
    ok &= _check("после трёх неудач запрос считается потерянным",
                 calls["n"], 3)
    ok &= _check("результат — None", result, None)

    calls2 = {"n": 0}

    def _ok_after_two():
        calls2["n"] += 1
        if calls2["n"] <= 2:
            raise RuntimeError("сеть отвалилась")
        return "данные"

    ok &= _check("после двух неудач запрос всё-таки сделан",
                 fn._fetch_with_retry("тест", _ok_after_two), "данные")
    ok &= _check("попыток было три", calls2["n"], 3)
    return ok


# --------------------------------------------------------------------------
# 6. Будущие матчи и время начала


def _match(match_id: str, match_date: str, kickoff: str | None,
           hs: int | None = None, as_: int | None = None,
           season: str = "2026-27") -> dict:
    return {
        "match_id": match_id,
        "season": season,
        "match_date": match_date,
        "kickoff_utc": kickoff,
        "home_id": "10",
        "home_name": "Lakers",
        "away_id": "8",
        "away_name": "Celtics",
        "home_score": hs,
        "away_score": as_,
        "is_played": hs is not None,
    }


def check_upcoming_selection() -> bool:
    print("\nОтбор будущих матчей:")
    now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    rows = [
        _match("1", "2026-11-04", "2026-11-05T00:30:00Z", 110, 105),
        _match("2", "2026-11-05", "2026-11-05T10:00:00Z"),
        _match("3", "2026-11-05", "2026-11-05T23:00:00Z"),
        _match("4", "2026-11-06", "2026-11-06T01:00:00Z"),
        _match("5", "2026-11-01", None),
        _match("6", "2026-11-07", None),
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("match_date").str.strptime(pl.Date, "%Y-%m-%d"),
        pl.col("home_score").cast(pl.Int32),
        pl.col("away_score").cast(pl.Int32),
    )
    got = ball_sport.upcoming_matches(df, now)
    ids = [m["match_id"] for m in got]
    ok = True
    ok &= _check("взяты только будущие матчи", sorted(ids), ["3", "4", "6"])
    ok &= _check("дата возвращена строкой",
                 all(isinstance(m["match_date"], str) for m in got), True)
    ok &= _check("порядок — по дате, затем по времени старта", ids,
                 ["3", "4", "6"])

    print("\nВремя начала матча:")
    ok &= _check("заглушка 1900 — это не время начала",
                 fn._kickoff("1900-01-01T02:00:00Z"), None)
    ok &= _check("пустое значение — не время начала", fn._kickoff(""), None)
    ok &= _check("нормальное время сохраняется",
                 fn._kickoff("2026-11-06T02:00:00Z"), "2026-11-06T02:00:00Z")
    return ok


# --------------------------------------------------------------------------
# 7. Цикл сервера и витрина


def check_deploy_and_web() -> bool:
    print("\nЦикл сервера и витрина:")
    text = (ROOT / "deploy" / "update_server.sh").read_text(encoding="utf-8")
    ok = True
    pos_fetch = text.find('run_step "etl.fetch_nba"')
    pos_predict = text.find('run_step "models.nba_predict"')
    ok &= _check_bool("в цикле есть сбор и прогнозы NBA",
                      -1 not in (pos_fetch, pos_predict))
    ok &= _check_bool("сбор идёт раньше прогнозов",
                      0 <= pos_fetch < pos_predict,
                      f"сбор={pos_fetch}, прогнозы={pos_predict}")
    ok &= _check_bool("прогнозы и рейтинги NBA публикуются",
                      "data/processed/nba_predictions.json" in text
                      and "data/processed/nba_strength.json" in text)
    ok &= _check_bool("полный журнал NBA не публикуется в витрину",
                      "data/processed/nba_journal.json" not in text,
                      "иначе страница снова начнёт грузить три с половиной "
                      "мегабайта")

    for page in ("nba", "nhl"):
        source = (ROOT / "web" / "app" / page / "journal" / "page.tsx")
        body = source.read_text(encoding="utf-8")
        ok &= _check_bool(f"на вкладке журнала {page.upper()} есть ближайшие матчи",
                          "Ближайшие 48 часов" in body)
        ok &= _check_bool(f"на вкладке журнала {page.upper()} отбор по времени начала",
                          "p.kickoff" in body and "48 * 3600 * 1000" in body)
    return ok


def check_web_journal_trimmed() -> bool:
    print("\nУрезанный веб-журнал:")
    hist = {
        "sport": "nba",
        "resolved": [{"key": str(i), "date": "2026-11-01"} for i in range(900)],
        "pending": [],
        "summary": {"total": 900},
    }
    out = ROOT / "data" / "processed" / "_test_nba_journal.json"
    try:
        ball_sport.write_web_journal(hist, out)
        import json
        data = json.loads(out.read_text(encoding="utf-8"))
        ok = _check("в веб-журнале 600 строк", len(data["resolved"]), 600)
        ok &= _check("полное число сохранено", data["resolved_total"], 900)
        return ok
    finally:
        if out.exists():
            out.unlink()


def main() -> int:
    print("Проверка пайплайна NBA\n" + "-" * 46)
    results = [
        check_single_source(),
        check_refresh(),
        check_season_counts(),
        check_home_away(),
        check_fetch_retries(),
        check_upcoming_selection(),
        check_deploy_and_web(),
        check_web_journal_trimmed(),
    ]
    print("-" * 46)
    if all(results):
        print("Все проверки пройдены.")
        return 0
    print("Есть неудачные проверки.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
