"""Проверка пайплайна НХЛ.

Здесь собирано то, что в НХЛ уже ломалось и что легко сломать снова.

1. Единый источник данных. Для будущих матчей был отдельный сборщик
   (etl/fetch_nhl_fixtures.py) со своими соглашениями: даты в UTC вместо
   местных, обход по дням вместо недель, кэш «навсегда». Он не
   запускался ни в цикле сервера, ни в CI, из-за чего CI публиковал
   прогнозы вовсе без будущих матчей, а в кэше оставались уже сыгранные
   матчи с прошедшей датой. Теперь источник один — nhl_matches.parquet.

2. Плей-офф не должен попадать в регулярный чемпионат. На границе
   сезона API отдаёт первые игры плей-офф с gameType=2, поэтому фильтр
   смотрит в id матча, а не в поле ответа.

3. Число матчей в полном сезоне известно точно. Расхождение означает
   потерянную неделю обхода: сезон 2020/21 молча давал 867 матчей вместо
   868, и один матч просто не доходил до обучения.

4. Неудачный запрос не должен терять неделю молча.

5. Начало сезона должно быть в кэше, иначе точечная докачка его не
   вылечит: матчи начала сезона уже никогда не попадут в окно обновления.

Запуск:
    python -m tests.test_nhl
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from etl import fetch_nhl as fn
from models import nhl_predict

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


def check_single_source() -> bool:
    """Будущие матчи берутся из того же датасета, что и история."""
    print("\nЕдиный источник данных:")
    ok = True
    predict_text = (ROOT / "models" / "nhl_predict.py").read_text(encoding="utf-8")
    ok &= _check_bool(
        "прогнозы не читают отдельный файл фикстур",
        "nhl_future_fixtures" not in predict_text,
        "файл читался из etl/fetch_nhl_fixtures.py — с датами в UTC "
        "и вечным кэшем")
    ok &= _check_bool(
        "прогнозы берут матчи из nhl_matches.parquet",
        "nhl_matches.parquet" in predict_text)
    ok &= _check_bool(
        "дублирующего сборщика фикстур больше нет",
        not (ROOT / "etl" / "fetch_nhl_fixtures.py").exists(),
        "файл etl/fetch_nhl_fixtures.py всё ещё в репозитории")
    return ok


def _row(match_id: str, match_date: str, kickoff: str | None,
         hs: int | None = None, as_: int | None = None,
         season: str = "20262027") -> dict:
    return {
        "season": season,
        "match_id": match_id,
        "match_date": match_date,
        "kickoff_utc": kickoff,
        "home_id": "10",
        "home_name": "Sabres",
        "away_id": "8",
        "away_name": "Bruins",
        "home_score": hs,
        "away_score": as_,
        "status": "OFF" if hs is not None else "FUT",
        "is_played": hs is not None,
    }


def check_upcoming_selection() -> bool:
    """Отбор будущих матчей: только те, что ещё не начались."""
    print("\nОтбор будущих матчей:")
    now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    rows = [
        # сыгран — в будущие не попадает
        _row("2026020001", "2026-11-04", "2026-11-05T00:30:00Z", 1, 2),
        # начался, но счёта ещё нет — тоже не будущий
        _row("2026020002", "2026-11-05", "2026-11-05T10:00:00Z"),
        # ещё не начался — будущий
        _row("2026020003", "2026-11-05", "2026-11-05T23:00:00Z"),
        _row("2026020004", "2026-11-06", "2026-11-06T01:00:00Z"),
        # без времени старта, но дата прошла — не будущий
        _row("2026020005", "2026-11-01", None),
        # без времени старта, дата впереди — будущий
        _row("2026020006", "2026-11-07", None),
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("match_date").str.strptime(pl.Date, "%Y-%m-%d"),
        pl.col("home_score").cast(pl.Int32),
        pl.col("away_score").cast(pl.Int32),
    )
    got = nhl_predict._upcoming_matches(df, now)
    ids = [m["match_id"] for m in got]
    ok = True
    ok &= _check("взяты только будущие матчи", sorted(ids),
                 ["2026020003", "2026020004", "2026020006"])
    ok &= _check("дата возвращена строкой",
                 all(isinstance(m["match_date"], str) for m in got), True)
    ok &= _check("порядок — по дате, затем по времени старта",
                 [m["match_id"] for m in got],
                 ["2026020003", "2026020004", "2026020006"])
    return ok


def check_playoff_filter() -> bool:
    """Плей-офф не должен попадать в регулярный чемпионат."""
    print("\nФильтр плей-офф:")
    ok = True
    ok &= _check("регулярный матч 2024021307", fn._is_regular_game("2024021307"), True)
    ok &= _check("плей-офф 2024030163 отсечён", fn._is_regular_game("2024030163"), False)
    ok &= _check("пресезон 2024010001 отсечён", fn._is_regular_game("2024010001"), False)
    ok &= _check("короткий id не считается регулярным",
                 fn._is_regular_game("2024"), False)
    ok &= _check("сезон по id не зависит от даты",
                 fn._season_for_id("2020020634"), "20202021")
    return ok


def check_season_counts() -> bool:
    """Число матчей в полном сезоне известно — и это проверяется."""
    print("\nСамопроверка числа матчей:")
    ok = True
    ok &= _check("сезонов с известным числом матчей",
                 len(fn.FULL_SEASON_MATCHES), 8)
    ok &= _check("полный сезон — 1 312 матчей",
                 fn.FULL_SEASON_MATCHES["20242025"], 1312)
    ok &= _check("урзанный пандемией 2020/21 — 868",
                 fn.FULL_SEASON_MATCHES["20202021"], 868)
    ok &= _check("в границах сезона есть 2020/21",
                 "20202021" in fn.SEASON_DATE_RANGES, True)
    ok &= _check("конец сезона 2020/21 — 19 мая 2021",
                 fn.SEASON_DATE_RANGES["20202021"][1], "2021-05-19")
    ok &= _check("текущий сезон не в таблице полных (он неполон по определению)",
                 fn._current_season() in fn.FULL_SEASON_MATCHES, False)
    return ok


def check_refresh_window() -> bool:
    """Окно обновления и самопроверка начала сезона."""
    print("\nОкно обновления текущего сезона:")
    today = date(2026, 11, 5)
    start = date(2026, 10, 1)
    end = date(2027, 4, 17)
    ok = True

    ok &= _check("кэш без начала сезона не считается полным",
                 fn._covers_season_open(
                     [_row("x", "2026-11-01", None)], start), False)
    ok &= _check("кэш с началом сезона полон",
                 fn._covers_season_open(
                     [_row("x", "2026-10-05", None)], start), True)

    window = fn._refresh_window(
        [_row("x", "2026-10-05", None)], start, end, today)
    ok &= _check("окно вокруг сегодняшнего дня", window,
                 (today - timedelta(days=fn.REFRESH_PAST_DAYS),
                  today + timedelta(days=fn.REFRESH_FUTURE_DAYS)))
    ok &= _check("окно не выходит за сезон",
                 fn._refresh_window([_row("x", "2026-10-05", None)],
                                    start, end, today)[1] <= end, True)

    full = fn._refresh_window([_row("x", "2026-11-01", None)],
                              start, end, today)
    ok &= _check("без начала сезона — обход сезона целиком", full,
                 (start, end))
    return ok


class _FakeSession:
    """Сессия, которая N раз подряд падает и потом отдаёт ответ."""

    def __init__(self, failures: int, payload: dict):
        self.failures = failures
        self.payload = payload
        self.calls = 0
        self.headers: dict = {}

    def get(self, url, timeout=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("сеть отвалилась")
        return _Resp(self.payload)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


def check_fetch_retries() -> bool:
    """Потерянная неделя — это десятки матчей, их надо повторить."""
    print("\nПовторы запроса:")
    ok = True
    ok &= _check("попыток на запрос", fn.FETCH_ATTEMPTS, 3)

    payload = {"gameWeek": [{
        "date": "2026-11-05",
        "games": [{
            "id": 2026020003, "gameType": 2, "gameState": "FUT",
            "startTimeUTC": "2026-11-05T23:00:00Z",
            "homeTeam": {"id": 10, "commonName": {"default": "Sabres"}},
            "awayTeam": {"id": 8, "commonName": {"default": "Bruins"}},
        }],
    }]}
    old = fn.SESSION
    try:
        fn.SESSION = _FakeSession(2, payload)
        rows = fn._fetch_date(date(2026, 11, 5), "20262027")
        ok &= _check("после двух неудач запрос всё-таки сделан",
                     fn.SESSION.calls, 3)
        ok &= _check("матч получен", len(rows), 1)
        ok &= _check("дата матча взята из ответа, а не из даты запроса",
                     rows[0]["match_date"] if rows else None, "2026-11-05")

        fn.SESSION = _FakeSession(99, payload)
        rows = fn._fetch_date(date(2026, 11, 5), "20262027")
        ok &= _check("после трёх неудач неделя считается потерянной",
                     len(rows), 0)
    finally:
        fn.SESSION = old
    return ok


def check_workflow() -> bool:
    """CI-workflow НХЛ: шаги в правильном порядке."""
    print("\nCI-workflow НХЛ:")
    path = ROOT / ".github" / "workflows" / "nhl.yml"
    if not path.exists():
        return _check_bool("файл workflow есть", False, str(path))
    text = path.read_text(encoding="utf-8")
    ok = True
    pos_fetch = text.find("python -m etl.fetch_nhl")
    pos_periods = text.find("python -m etl.fetch_nhl_periods")
    pos_predict = text.find("python -m models.nhl_predict")
    ok &= _check_bool("сбор матчей, потом периоды, потом прогнозы",
                      0 <= pos_fetch < pos_periods < pos_predict,
                      f"матчи={pos_fetch}, периоды={pos_periods}, "
                      f"прогнозы={pos_predict}")
    ok &= _check_bool("публикуются прогнозы",
                      "nhl_predictions.json" in text)
    ok &= _check_bool("публикуется разбивка по периодам (seed для CI)",
                      "nhl_periods.parquet" in text)
    return ok


def main() -> int:
    print("Проверка пайплайна НХЛ\n" + "-" * 46)
    results = [
        check_single_source(),
        check_upcoming_selection(),
        check_playoff_filter(),
        check_season_counts(),
        check_refresh_window(),
        check_fetch_retries(),
        check_workflow(),
    ]
    print("-" * 46)
    if all(results):
        print("Все проверки пройдены.")
        return 0
    print("Есть неудачные проверки.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
