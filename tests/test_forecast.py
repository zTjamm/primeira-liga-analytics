"""Проверка журнала прогнозов.

Главный риск в этой части — тихо неверный вердикт. Например, если матч
сматчился по дате и командам неправильно, страница годами будет показывать
«не угадано» вместо реального результата. Поэтому сверка проверяется на
синтетических данных, где правильный ответ известен заранее.

Журнал общий для футбола и баскетбола, поэтому проверяем оба ключа
разделения: разные спорт-ключи должны давать независимые журналы.

Запуск:
    python -m tests.test_forecast
"""
from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from etl import config as cfg
from models import forecast

PASS = "\u2713"
FAIL = "\u2717"


def _check(name: str, got, want) -> bool:
    ok = got == want
    print(f"  [{PASS if ok else FAIL}] {name}" + ("" if ok else f"  получено {got!r}, ждали {want!r}"))
    return ok


def check_stage() -> bool:
    print("Разбивка по этапам:")
    ok = True
    ok &= _check("за 100 часов — T-72h+", forecast.stage_for(100.0), "T-72h+")
    ok &= _check("за 30 часов — T-24h", forecast.stage_for(30.0), "T-24h")
    ok &= _check("за 6 часов — T-6h", forecast.stage_for(6.0), "T-6h")
    ok &= _check("за 3.9 часа — T-4h (финальный)", forecast.stage_for(3.9), "T-4h")
    ok &= _check("за 0.5 часа — T-4h", forecast.stage_for(0.5), "T-4h")
    return ok


def _rec(key, d: date, stage: str, p: list[float], hours: float,
         home: str = "porto", away: str = "benfica") -> dict:
    return {
        "key": str(key), "date": str(d),
        "kickoff": f"{d}T19:00:00Z", "matchday": 8,
        "home_id": home, "away_id": away,
        "home_name": home, "away_name": away,
        "generated_at": f"{d}T{int(hours)}:00:00Z", "hours_before": hours,
        "stage": stage, "p_home": p[0], "p_draw": p[1], "p_away": p[2],
        "extra": None,
    }


def check_resolution() -> bool:
    print("\nСверка с результатами:")
    d = date(2026, 3, 14)
    matches = pl.DataFrame([
        {"match_date": d, "home_id": "porto", "away_id": "benfica", "ftr": "H", "fthg": 2, "ftag": 0},
        {"match_date": d, "home_id": "braga", "away_id": "sp Lisbon", "ftr": "A", "fthg": 1, "ftag": 3},
    ])

    with tempfile.TemporaryDirectory() as tmp:
        forecast.ledger_path = lambda sport: Path(tmp) / f"log_{sport}.jsonl"

        # Матч 1: два этапа. Первым срезом модель считала победу хозяев
        # уверенной, к финальному перешла на ничью — фактически победа
        # хозяев. Итоговый прогноз промахнулся, и мнение поменялось.
        forecast.append([
            _rec(1, d, "T-72h+", [0.55, 0.25, 0.20], 72.0),
            _rec(1, d, "T-4h", [0.30, 0.36, 0.34], 3.0),
            _rec(2, d, "T-24h", [0.20, 0.25, 0.55], 24.0, home="braga", away="sp Lisbon"),
        ], "football")

        hist = forecast.build_history(matches, "football")
        r = {x["key"]: x for x in hist["resolved"]}

        ok = True
        ok &= _check("оба матча засчитаны как сыгранные", len(hist["resolved"]), 2)

        m1 = r["1"]
        ok &= _check("факт по матчу 1 определён верно", m1["actual"], "H")
        ok &= _check("счёт по матчу 1", m1["score"], "2-0")
        ok &= _check("финальная версия выбрана (T-4h)", m1["stage"], "T-4h")
        ok &= _check("финальный прогноз ставил на ничью, факт П1 — промах", m1["hit"], False)
        ok &= _check("вероятность на фактический исход = 0.30", round(m1["p_actual"], 2), 0.30)
        ok &= _check("log-loss от фактического исхода", round(m1["logloss"], 3),
                     round(-__import__("math").log(0.30), 3))
        ok &= _check("мнение изменилось (0.55 → 0.36)", m1["flipped"], True)
        ok &= _check("сдвиг = сумма изменений (0.25+0.11+0.14)", round(m1["shift"], 2), 0.50)
        ok &= _check("в матче сохранены оба среза", m1["n_versions"], 2)

        m2 = r["2"]
        ok &= _check("факт по матчу 2 определён верно", m2["actual"], "A")
        ok &= _check("уверенная победа гостей — попадание", m2["hit"], True)
        ok &= _check("вероятность на фактический исход = 0.55", round(m2["p_actual"], 2), 0.55)
        ok &= _check("одна версия — мнение не менялось", m2["n_versions"], 1)

        s = hist["summary"]
        ok &= _check("всего 2 прогноза", s["total"], 2)
        ok &= _check("точность 1 из 2 = 0.5", s["overall"]["accuracy"], 0.5)
        ok &= _check("разбивка по этапам содержит T-4h и T-24h",
                     sorted(s["by_stage"].keys()), ["T-24h", "T-4h"])
        ok &= _check("матчей с несколькими этапами", s["multi_stage"], 1)

        # повторный прогон в том же этапе не должен плодить дубликаты
        forecast.append([_rec(1, d, "T-4h", [0.31, 0.35, 0.34], 2.0)], "football")
        m1b = next(x for x in forecast.build_history(matches, "football")["resolved"]
                   if x["key"] == "1")
        ok &= _check("повторный прогон обновил запись, а не добавил новую",
                     m1b["n_versions"], 2)
        ok &= _check("и значения обновились", round(m1b["p_actual"], 2), 0.31)
        ok &= _check("всего записей в журнале футбола", len(forecast.load("football")), 3)

        ok &= _check("журнал баскетбола пуст и не смешан с футболом",
                     len(forecast.load("basketball")), 0)

    return ok


def check_sports_isolated() -> bool:
    """Два спорта должны вести независимые журналы: иначе прогноз по
    баскетболу попал бы в футбольную историю и вердикты перепутались бы
    (у обоих по три исхода с теми же буквами, но разными смыслом)."""
    print("\nРазделение по видам спорта:")
    d = date(2026, 4, 1)
    matches = pl.DataFrame([
        {"match_date": d, "home_id": "cskа", "away_id": "zenit", "ftr": "H", "fthg": 95, "ftag": 82},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        forecast.ledger_path = lambda sport: Path(tmp) / f"log_{sport}.jsonl"
        forecast.append([_rec("b1", d, "T-24h", [0.70, 0.02, 0.28], 24.0,
                              home="cskа", away="zenit")], "basketball")
        forecast.append([_rec("f1", d, "T-24h", [0.60, 0.25, 0.15], 24.0)], "football")

        fb = forecast.build_history(matches, "basketball")
        ff = forecast.build_history(matches, "football")

        ok = _check("в баскетболе резолвится только свой матч", len(fb["resolved"]), 1)
        ok &= _check("и это нужный матч", fb["resolved"][0]["key"], "b1")
        ok &= _check("счёт баскетбольный 95:82", fb["resolved"][0]["score"], "95-82")
        ok &= _check("футбольный матч в баскетбольном журнале не резолвится",
                     len(ff["resolved"]), 0)
        ok &= _check("футбольный матч остаётся в pending", len(ff["pending"]), 1)
    return ok


def check_no_overlap() -> bool:
    """Прогнозы должны относиться к матчам будущим, а не к уже сыгранным:
    иначе в resolved попадёт мусор."""
    print("\nГраница «будущее / прошлое»:")
    today = date.today()
    future = pl.DataFrame([
        {"match_date": today + timedelta(days=1), "home_id": "porto", "away_id": "braga",
         "ftr": None, "fthg": None, "ftag": None},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        forecast.ledger_path = lambda sport: Path(tmp) / f"log_{sport}.jsonl"
        forecast.append([_rec("x1", today + timedelta(days=1), "T-24h", [0.4, 0.3, 0.3], 24.0)],
                        "basketball")
        hist = forecast.build_history(future, "basketball")
        ok = _check("матч без результата уходит в pending", len(hist["pending"]), 1)
        ok &= _check("в resolved пусто", len(hist["resolved"]), 0)
    return ok


def check_empty() -> bool:
    print("\nПустое состояние:")
    with tempfile.TemporaryDirectory() as tmp:
        forecast.ledger_path = lambda sport: Path(tmp) / f"log_{sport}.jsonl"
        hist = forecast.build_history(pl.DataFrame(), "football")
        ok = _check("пустой журнал не падает", hist["summary"]["total"], 0)
        ok &= _check("resolved и pending пусты",
                     (len(hist["resolved"]), len(hist["pending"])), (0, 0))
    return ok


def check_ledger_roundtrip() -> bool:
    """Битый JSON не должен ронять загрузку: строка может оборваться
    при обрыве соединения во время коммита в CI."""
    print("\nУстойчивость чтения:")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "log.jsonl"
        p.write_text(
            json.dumps(_rec(1, date(2026, 3, 14), "T-24h", [0.4, 0.3, 0.3], 24.0),
                       ensure_ascii=False) + "\n" + "{битая строка\n\n",
            encoding="utf-8",
        )
        forecast.ledger_path = lambda sport: p
        return _check("битая строка пропущена, остальное прочитано",
                      len(forecast.load("football")), 1)


def main() -> int:
    print("Проверка журнала прогнозов\n" + "-" * 46)
    results = [
        check_stage(),
        check_resolution(),
        check_sports_isolated(),
        check_no_overlap(),
        check_empty(),
        check_ledger_roundtrip(),
    ]
    print("-" * 46)
    if all(results):
        print("Все проверки пройдены.")
        return 0
    print("Есть неудачные проверки.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
