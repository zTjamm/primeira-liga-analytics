"""Проверка загрузки данных ВТБ.

Главный риск здесь — не ошибка, а тихая устаревость. Раньше код брал
сырой JSON с диска, если файл уже есть, и больше в сеть не ходил: файл
сканировался один раз и оставался неизменным навсегда. API тем временем
отдавал новые результаты, а сайт показывал старые. Признаков поломки не
было — цикл отрабатывал штатно, ошибок не возвращал. Так пропал матч
«Зенит — Автодор» (11 октября, 91:71).

Поэтому проверяем именно поведение кэша: текущий сезон обязан
обновляться из сети, а падение сети не должно молча отдавать старое.

Запуск:
    python -m tests.test_fetch_vtb
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from etl import fetch_vtb as fv

PASS = "\u2713"
FAIL = "\u2717"


def _check(name: str, got, want) -> bool:
    ok = got == want
    print(f"  [{PASS if ok else FAIL}] {name}"
          + ("" if ok else f"  (получено {got!r}, ждали {want!r})"))
    return ok


def _payload(matches: list[dict]) -> dict:
    return {"meta": {"code": 200, "count": len(matches)}, "data": matches}


def _match(mid: str, home: str, away: str, status: str = "COMPLETE",
           hs: int = 0, as_: int = 0) -> dict:
    """Заготовка в НАСТОЯЩЕЙ схеме API: competitors[], а не homeTeam/awayTeam.

    Первая версия теста строила выдуманные homeTeam/awayTeam, из-за чего
    проверка на имя команды проходила бы вхолостую: разбор всё равно
    возвращал бы пустую строку. Схема взята из etl/fetch_vtb.py
    (_team_id/_team_name/_score читают именно competitors).
    """
    return {
        "matchId": mid,
        "matchStatus": status,
        # Регулярный чемпионат: только он попадает в обучение по
        # умолчанию (KEEP_MATCH_TYPES), плей-офф исключён намеренно.
        "matchType": "REGULAR",
        "matchTimeMSK": "2026-10-11T15:00:00+03:00",
        "competitors": [
            {"isHomeCompetitor": True, "teamId": "1",
             "teamName": {"ru": home}, "scoreString": str(hs)},
            {"isHomeCompetitor": False, "teamId": "2",
             "teamName": {"ru": away}, "scoreString": str(as_)},
        ],
    }


class _FakeSession:
    """Подмена SESSION: отдаёт заготовку и считает обращения к сети."""

    def __init__(self, payload: dict | Exception):
        self.payload = payload
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return _Resp(self.payload)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


def check_current_season_refetched() -> bool:
    """Текущий сезон обязан читаться из сети, даже если кэш уже есть.

    Именно на этом строилась поломка: файл от 10 октября закрывал собой
    сеть, и результат матча 11 октября до нас просто не доходил.
    """
    print("\nТекущий сезон обновляется из сети:")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        fv.RAW = raw
        stale = _payload([_match("1", "Зенит", "Уралмаш", hs=90, as_=68)])
        (raw / f"vtb_2027.json").write_text(json.dumps(stale, ensure_ascii=False),
                                            encoding="utf-8")

        fresh = _payload([_match("2", "Зенит", "Автодор", hs=91, as_=71),
                          _match("1", "Зенит", "Уралмаш", hs=90, as_=68)])
        fake = _FakeSession(fresh)
        old_session, fv.SESSION = fv.SESSION, fake

        try:
            df = fv.fetch_season("2027", fv.CURRENT_SEASON)
        finally:
            fv.SESSION = old_session

        ok = _check("сеть была вызвана несмотря на кэш", fake.calls, 1)
        ok &= _check("новый матч попал в данные", df.height, 2)
        names = {r["home_name"] for r in df.to_dicts()}
        ok &= _check("Зенит — Автодор получен", "Зенит" in names, True)
        written = json.loads((raw / "vtb_2027.json").read_text(encoding="utf-8"))
        ok &= _check("кэш на диске перезаписан свежим", len(written["data"]), 2)
    return ok


def check_history_cached() -> bool:
    """Исторический сезон не меняется — сеть зря не дёргаем."""
    print("\nИстория берётся из кэша:")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        fv.RAW = raw
        cached = _payload([_match("9", "ЦСКА", "Зенит", hs=83, as_=74)])
        (raw / "vtb_2025.json").write_text(json.dumps(cached, ensure_ascii=False),
                                           encoding="utf-8")

        fake = _FakeSession(_payload([_match("9", "ЦСКА", "Зенит", hs=1, as_=1)]))
        old_session, fv.SESSION = fv.SESSION, fake
        try:
            df = fv.fetch_season("2025", "2024/25")
        finally:
            fv.SESSION = old_session

        ok = _check("историю не перекачиваем", fake.calls, 0)
        ok &= _check("данные из кэша на месте", df.height, 1)
    return ok


def check_history_fetched_without_cache() -> bool:
    """Кэша нет (свежий репозиторий, CI) — история качается из сети.

    До исправления исторический сезон ветка сети не трогала вовсе, и в CI
    fetch_vtb падал на первом же историческом сезоне, унося с собой шаг
    публикации целиком.
    """
    print("\nИстория без кэша загружается из сети:")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        fv.RAW = raw

        payload = _payload([_match("9", "ЦСКА", "Зенит", hs=83, as_=74)])
        fake = _FakeSession(payload)
        old_session, fv.SESSION = fv.SESSION, fake
        try:
            df = fv.fetch_season("2025", "2024/25")
        finally:
            fv.SESSION = old_session

        ok = _check("без кэша сеть дёргается", fake.calls, 1)
        ok &= _check("сезон получен", df.height, 1)
        ok &= _check("кэш создан для следующих запусков",
                     (raw / "vtb_2025.json").exists(), True)

        # Второй вызов — уже из кэша: история не перекачивается зря.
        fake2 = _FakeSession(_payload([]))
        fv.SESSION = fake2
        try:
            again = fv.fetch_season("2025", "2024/25")
        finally:
            fv.SESSION = old_session
        ok &= _check("повторно сеть не трогается", fake2.calls, 0)
        ok &= _check("второй вызов дал те же данные", again.height, 1)
    return ok


def check_network_failure_is_loud() -> bool:
    """Падение сети нельзя проглатывать: кэш покажется как свежие данные."""
    print("\nПадение сети не скрывается:")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        fv.RAW = raw
        cached = _payload([_match("2", "Зенит", "Автодор", hs=91, as_=71)])
        (raw / "vtb_2027.json").write_text(json.dumps(cached, ensure_ascii=False),
                                            encoding="utf-8")

        fake = _FakeSession(RuntimeError("соединение сброшено"))
        old_session, fv.SESSION = fv.SESSION, fake
        try:
            df = fv.fetch_season("2027", fv.CURRENT_SEASON)
        finally:
            fv.SESSION = old_session

        # Данные отдаются (лучше stale, чем пусто), но в stdout обязан
        # попасть явный предупреждающий текст — тест ловит его подменой
        # stdout, чтобы «тихое устаревание» было невозможно пропустить.
        import io
        import contextlib
        buf = io.StringIO()
        fake2 = _FakeSession(RuntimeError("соединение сброшено"))
        fv.SESSION = fake2
        try:
            with contextlib.redirect_stdout(buf):
                fv.fetch_season("2027", fv.CURRENT_SEASON)
        finally:
            fv.SESSION = old_session
        text = buf.getvalue()

        ok = _check("кэш всё же использован", df.height, 1)
        ok &= _check("о сбое сказано вслух", "ВНИМАНИЕ" in text, True)
        ok &= _check("указано время устаревшего кэша", "кэш от" in text, True)
    return ok


def check_cancelled_not_played() -> bool:
    """Отменённый матч не должен попадать в обучение и в вердикты.

    В выдаче API встречается МБА — Енисей со статусом CANCELLED и счётом
    0:0. Если принять его за сыгранный, в модель попадёт матч 0:0.
    """
    print("\nОтменённые матчи не считаются сыгранными:")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp)
        fv.RAW = raw
        payload = _payload([
            _match("1", "МБА", "Енисей", status="CANCELLED", hs=0, as_=0),
            _match("2", "Зенит", "Автодор", status="COMPLETE", hs=91, as_=71),
            _match("3", "МБА", "ПАРМА", status="SCHEDULED"),
        ])
        (raw / "vtb_2027.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        fake = _FakeSession(payload)
        old_session, fv.SESSION = fv.SESSION, fake
        try:
            df = fv.fetch_season("2027", fv.CURRENT_SEASON)
        finally:
            fv.SESSION = old_session

        rows = {r["match_id"]: r for r in df.to_dicts()}
        ok = _check("все три статуса разобраны", df.height, 3)
        ok &= _check("COMPLETE помечен сыгранным", rows["2"]["is_played"], True)
        ok &= _check("CANCELLED помечен НЕ сыгранным", rows["1"]["is_played"], False)
        ok &= _check("SCHEDULED помечен НЕ сыгранным", rows["3"]["is_played"], False)

        tr = fv.trainable(df)
        ok &= _check("в обучение отменённый не попал",
                     "1" in set(tr["match_id"].to_list()), False)
        ok &= _check("в обучение попал завершённый",
                     "2" in set(tr["match_id"].to_list()), True)
    return ok


def main() -> int:
    print("Проверка загрузки данных ВТБ\n" + "-" * 46)
    old_raw = fv.RAW
    results = [
        check_current_season_refetched(),
        check_history_cached(),
        check_history_fetched_without_cache(),
        check_network_failure_is_loud(),
        check_cancelled_not_played(),
    ]
    fv.RAW = old_raw
    print("-" * 46)
    if all(results):
        print("Все проверки пройдены.")
        return 0
    print("Есть непройденные проверки.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
