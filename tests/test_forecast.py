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
         home: str = "porto", away: str = "benfica", extra: dict | None = None) -> dict:
    return {
        "key": str(key), "date": str(d),
        "kickoff": f"{d}T19:00:00Z", "matchday": 8,
        "home_id": home, "away_id": away,
        "home_name": home, "away_name": away,
        "generated_at": f"{d}T{int(hours)}:00:00Z", "hours_before": hours,
        "stage": stage, "p_home": p[0], "p_draw": p[1], "p_away": p[2],
        "extra": extra,
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


def check_vtb_schema() -> bool:
    """Реальная схема официального API ВТБ, а не футбольная.

    Тест check_sports_isolated писал баскетбол теми же колонками ftr/
    fthg/ftag, что и футбол, поэтому проходил. Но API ВТБ отдаёт
    is_played и счёт по очкам — колонки ftr там нет. Индекс результатов
    требовал ftr, из-за чего баскетбольный журнал оставался пустым
    годами: страница писала «сыгранных прогнозов пока нет», хотя
    результаты в API были. Тест ниже повторяет настоящие колонки,
    чтобы этот пробел не вернулся.
    """
    print("\nСхема результатов ВТБ (is_played + счёт, без ftr):")
    d = date(2026, 4, 1)
    vtb = pl.DataFrame([
        {"match_date": d, "home_id": "csk", "away_id": "zenit",
         "home_score": 95, "away_score": 82, "is_played": True, "status": "COMPLETE"},
        {"match_date": d, "home_id": "parma", "away_id": "mba",
         "home_score": 78, "away_score": 81, "is_played": True, "status": "COMPLETE"},
        {"match_date": d, "home_id": "lok", "away_id": "nnov",
         "home_score": None, "away_score": None, "is_played": False, "status": "SCHEDULED"},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        forecast.ledger_path = lambda sport: Path(tmp) / f"log_{sport}.jsonl"
        forecast.append([
            _rec("b1", d, "T-24h", [0.70, 0.02, 0.28], 24.0, home="csk", away="zenit"),
            _rec("b2", d, "T-24h", [0.55, 0.02, 0.45], 24.0, home="parma", away="mba"),
            _rec("b3", d, "T-24h", [0.50, 0.02, 0.50], 24.0, home="lok", away="nnov"),
        ], "basketball")

        hist = forecast.build_history(vtb, "basketball")

        ok = _check("сыгранные матчи резолвятся без колонки ftr",
                    len(hist["resolved"]), 2)
        ok &= _check("несыгранный остаётся в pending", len(hist["pending"]), 1)
        by_key = {r["key"]: r for r in hist["resolved"]}
        ok &= _check("счёт собран из очков", by_key["b1"]["score"], "95-82")
        ok &= _check("победа хозяев распознана", by_key["b1"]["actual"], "H")
        ok &= _check("победа гостей распознана", by_key["b2"]["actual"], "A")
        ok &= _check("исход угадан", by_key["b1"]["hit"], True)
        ok &= _check("гости были названы и ошиблись", by_key["b2"]["hit"], False)
        ok &= _check("названный исход сохранён", by_key["b1"]["picked"], "H")

        tie = pl.DataFrame([
            {"match_date": d, "home_id": "a", "away_id": "b",
             "home_score": 80, "away_score": 80, "is_played": True},
        ])
        forecast.append([_rec("b4", d, "T-24h", [0.10, 0.80, 0.10], 24.0,
                              home="a", away="b")], "basketball")
        h2 = forecast.build_history(tie, "basketball")
        ok &= _check("равный счёт — это ничья", h2["resolved"][0]["actual"], "D")
        ok &= _check("ничью угадали", h2["resolved"][0]["hit"], True)
    return ok


def check_total_lines() -> bool:
    """Линия тотала разная у футбола и баскетбола.

    Код искал ключ over25 у обоих видов спорта, но баскетбол пишет
    over_155 (модель.predict_total_2h). Из-за этого вердикт по тоталу в
    баскетболе был всегда пустым, хотя вероятность в журнале лежала —
    пользователь его просто не видел. Плюс важна граница: модель считает
    «больше 155» с поправкой 155.5, то есть ТБ 155 = тотал от 156.
    """
    print("\nТотал: линия 2.5 у футбола и 155 у баскетбола:")
    d = date(2026, 4, 1)
    fb = pl.DataFrame([
        {"match_date": d, "home_id": "f1", "away_id": "f2",
         "ftr": "H", "fthg": 2, "ftag": 1},   # тотал 3 -> ТБ 2.5
    ])
    bt = pl.DataFrame([
        {"match_date": d, "home_id": "b1", "away_id": "b2",
         "home_score": 80, "away_score": 76, "is_played": True},  # 156 -> ТБ 155
        {"match_date": d, "home_id": "b3", "away_id": "b4",
         "home_score": 78, "away_score": 77, "is_played": True},  # 155 -> ТМ 155
    ])
    with tempfile.TemporaryDirectory() as tmp:
        forecast.ledger_path = lambda sport: Path(tmp) / f"log_{sport}.jsonl"

        forecast.append([_rec("f1", d, "T-24h", [0.5, 0.25, 0.25], 24.0,
                              home="f1", away="f2",
                              extra={"over25": 0.62})], "football")
        forecast.append([_rec("b1", d, "T-24h", [0.55, 0.01, 0.44], 24.0,
                              home="b1", away="b2",
                              extra={"over_155": 0.62})], "basketball")
        forecast.append([_rec("b3", d, "T-24h", [0.45, 0.01, 0.54], 24.0,
                              home="b3", away="b4",
                              extra={"over_155": 0.40})], "basketball")

        f = forecast.build_history(fb, "football")["resolved"][0]
        ok = _check("футбол: назван ТБ 2.5", f["total_label"], "ТБ 2.5")
        ok &= _check("футбол: тотал 3 больше 2.5 — угадан", f["total_hit"], True)
        ok &= _check("футбол: линия 2.5", f["total_line"], 2.5)
        ok &= _check("футбол: уверенность 62%", f["total_p"], 0.62)

        h = forecast.build_history(bt, "basketball")
        by = {r["key"]: r for r in h["resolved"]}

        ok &= _check("баскетбол: тотал вообще посчитан", by["b1"]["total_hit"], True)
        ok &= _check("баскетбол: назван ТБ 155", by["b1"]["total_label"], "ТБ 155")
        ok &= _check("баскетбол: 156 очков = ТБ 155, угадан", by["b1"]["total_hit"], True)
        ok &= _check("баскетбол: назван ТМ 155", by["b3"]["total_label"], "ТМ 155")
        ok &= _check("баскетбол: ровно 155 = ТМ, угадан", by["b3"]["total_hit"], True)
        ok &= _check("баскетбол: линия 155, а не 2.5", by["b1"]["total_line"], 155.0)

        s = h["summary"]["total_market"]
        ok &= _check("баскетбол: сводка по тоталу посчитана", s["n"], 2)
        ok &= _check("баскетбол: оба угаданы", s["hits"], 2)
        ok &= _check("баскетбол: линия в сводке 155", s["line"], 155.0)
        ok &= _check("баскетбол: точность по тоталу 100%", s["accuracy"], 1.0)
    return ok


def check_total_gate() -> bool:
    """Отсев по тоталу должен попадать и в таблицу, и в точность.

    Порог 0.55 применяется к тоталу так же, как к исходу: при тотале
    около линии вердикт не выдаётся. Если забыть про метку, точность
    посчитается по всем матчам и покажет себя лучше, чем отсев даёт на
    самом деле — то есть отсев станет незаметным, а результат
    завышенным. Ровно та ошибка, что была с исходом до введения метки.
    """
    print("\nОтсев по тоталу:")
    from models.verdict import total_verdict

    ok = _check("ТБ 0.45 (уверенность 0.55) — ровно на пороге",
                total_verdict(0.45)[0], True)
    ok &= _check("ТБ 0.46 (уверенность 0.54) — ниже порога",
                 total_verdict(0.46)[0], False)
    ok &= _check("ТБ 0.50 — монетка", total_verdict(0.50)[0], False)
    ok &= _check("ТБ 0.60 — уверенно", total_verdict(0.60)[0], True)
    ok &= _check("ТБ 0.40 — это ТМ, уверенно",
                 total_verdict(0.40)[0], True)
    _, why = total_verdict(0.50)
    ok &= _check("причина отказа названа", "ниже порога" in why, True)

    d = date(2026, 5, 1)
    # Первый матч: тотал уверенный (ТБ) — попадёт в вердикт.
    # Второй: тотал около линии — отсеется, и в вердикте не показывается.
    matches = pl.DataFrame([
        {"match_date": d, "home_id": "t1", "away_id": "t2",
         "ftr": "H", "fthg": 3, "ftag": 1},
        {"match_date": d, "home_id": "t3", "away_id": "t4",
         "ftr": "H", "fthg": 1, "ftag": 1},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        forecast.ledger_path = lambda sport: Path(tmp) / f"log_{sport}.jsonl"
        forecast.append([
            _rec("g1", d, "T-24h", [0.5, 0.25, 0.25], 24.0,
                 home="t1", away="t2",
                 extra={"over25": 0.72, "total_verdict_given": True}),
            _rec("g2", d, "T-24h", [0.45, 0.3, 0.25], 24.0,
                 home="t3", away="t4",
                 extra={"over25": 0.51, "total_verdict_given": False,
                        "total_verdict_reason": "тотал около линии"}),
        ], "football")

        h = forecast.build_history(matches, "football")
        by = {r["key"]: r for r in h["resolved"]}
        s = h["summary"]["total_market"]

        ok &= _check("оба матча попали в resolved", len(h["resolved"]), 2)
        ok &= _check("уверенный тотал отмечен как выданный",
                     by["g1"]["total_given"], True)
        ok &= _check("матч у линии отмечен как отсеянный",
                     by["g2"]["total_given"], False)
        ok &= _check("точность считается только по выданным", s["n"], 1)
        ok &= _check("отсеянных показано рядом", s["withheld"], 1)
        ok &= _check("выданный тотал угадан", s["hits"], 1)
        ok &= _check("точность по тоталу 100% на одном матче",
                     s["accuracy"], 1.0)
    return ok


def check_outcome_gate() -> bool:
    """Сводка по исходу должна считать только ВЫДАННЫЕ вердикты.

    Ровно та же ловушка, что была с тоталом: если делить hit'ы на все
    матчи, отсев становится незаметным — в таблице строка «без вердикта»,
    а в процente она участвует. Проверяем на трёх матчах: два с вердиктом
    (один угадан, один мимо) и один отсеянный, который модель угадала
    бы, но вердикта не выдавала.
    """
    print("\nОтсев по исходу в сводке:")
    d = date(2026, 4, 1)
    matches = pl.DataFrame([
        {"match_date": d, "home_id": "a1", "away_id": "a2", "ftr": "H", "fthg": 2, "ftag": 0},
        {"match_date": d, "home_id": "b1", "away_id": "b2", "ftr": "H", "fthg": 1, "ftag": 0},
        {"match_date": d, "home_id": "c1", "away_id": "c2", "ftr": "H", "fthg": 1, "ftag": 0},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        forecast.ledger_path = lambda sport: Path(tmp) / f"log_{sport}.jsonl"
        forecast.append([
            # Уверенный П1, фактически П1 — угадан
            _rec("v1", d, "T-24h", [0.70, 0.18, 0.12], 24.0, home="a1", away="a2",
                 extra={"verdict_given": True}),
            # П2 назван, но случилось П1 — мимо
            _rec("v2", d, "T-24h", [0.30, 0.22, 0.48], 24.0, home="b1", away="b2",
                 extra={"verdict_given": True}),
            # Отсев (вероятность ниже порога), но исход совпал бы с argmax
            _rec("v3", d, "T-24h", [0.42, 0.33, 0.25], 24.0, home="c1", away="c2",
                 extra={"verdict_given": False,
                        "verdict_reason": "вероятность ниже 0.55"}),
        ], "football")

        h = forecast.build_history(matches, "football")
        s = h["summary"]
        om = s["outcome_market"]

        ok = _check("в resolved все три", len(h["resolved"]), 3)
        ok &= _check("по всем считается 2/3 (старое поле overall)",
                     s["overall"]["accuracy"], round(2 / 3, 4))
        ok &= _check("по выданным только 2 матча", om["n"], 2)
        ok &= _check("из них угадан ровно один", om["hits"], 1)
        ok &= _check("точность по выданным 50%", om["accuracy"], 0.5)
        ok &= _check("отсеяно порогом 1", om["withheld"], 1)
        ok &= _check("записей без флага нет", om["no_flag"], 0)
        ok &= _check("флаг в самом resolved сохранён",
                     {r["key"]: r["verdict_given"] for r in h["resolved"]}["v3"], False)

        # Запись без флага вердикта (сделана до появления отсева) должна
        # считаться как «вердикт выдан», но попадать в no_flag.
        forecast.append([
            _rec("v4", d, "T-4h", [0.51, 0.27, 0.22], 3.0, home="d1", away="d2"),
        ], "football")
        matches2 = pl.concat([matches, pl.DataFrame([
            {"match_date": d, "home_id": "d1", "away_id": "d2", "ftr": "H",
             "fthg": 1, "ftag": 0},
        ])])
        s2 = forecast.build_history(matches2, "football")["summary"]
        ok &= _check("запись без флага посчитана как выданная", s2["outcome_market"]["n"], 3)
        ok &= _check("и показана в no_flag", s2["outcome_market"]["no_flag"], 1)
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
        check_vtb_schema(),
        check_total_lines(),
        check_total_gate(),
        check_outcome_gate(),
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
