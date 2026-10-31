"""Проверки калибровки температурой и подбора порогов РФПЛ.

Эти два блока — единственные, где решение «применять или нет» принимает
сам код по записанному заранее правилу (APPLY_RULE в models/calibrate.py),
а не человек по взгляду на цифры. Значит, проверять здесь нужно ровно то,
из-за чего отказ от подгонки вообще возможен:

1. температура меняет только шкалу: сумма вероятностей равна единице,
   argmax и точность не меняются — иначе под видом калибровки скрывалась
   бы другая модель;
2. подбор T отказывается работать на слишком малой выборке и находит
   минимум log-loss там, где минимум есть;
3. правило внедрения срабатывает в обе стороны: помогает — применяется,
   вредит или не переносится на следующий сезон — нет;
4. пороги РФПЛ читаются из отчёта, а при отсутствии отчёта — из констант,
   и «тотал не подобрался» передаётся как None, а не как тихий 0.55.

Запуск:
    python -m tests.test_calibrate
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

PASS = "\u2713"
FAIL = "\u2717"


def _check(name: str, got, want) -> bool:
    ok = got == want
    print(f"  [{PASS if ok else FAIL}] {name}"
          + ("" if ok else f"  (получено {got!r}, ждали {want!r})"))
    return ok


def _check_bool(name: str, cond: bool, note: str = "") -> bool:
    print(f"  [{PASS if cond else FAIL}] {name}"
          + (f"  ({note})" if note and not cond else ""))
    return cond


# ---------------------------------------------------------------- apply

def test_apply() -> list[bool]:
    from models import temperature

    print("Температура: свойства преобразования")
    ok = []
    p = [0.55, 0.25, 0.20]

    ok.append(_check("T=None оставляет вектор как есть",
                     temperature.apply(p, None), p))
    ok.append(_check("T=1 оставляет вектор как есть",
                     [round(x, 6) for x in temperature.apply(p, 1.0)],
                     [round(x, 6) for x in p]))
    ok.append(_check("исходный вектор не мутируется", p, [0.55, 0.25, 0.20]))

    for t in (0.2, 0.5, 1.0, 2.0, 3.0):
        q = temperature.apply(p, t)
        ok.append(_check_bool(f"T={t}: сумма вероятностей = 1",
                              abs(sum(q) - 1.0) < 1e-9, f"сумма {sum(q)}"))
        ok.append(_check_bool(f"T={t}: argmax не изменился",
                              q.index(max(q)) == 0,
                              f"argmax {q.index(max(q))}"))
        ok.append(_check_bool(f"T={t}: порядок исходов сохранён",
                              sorted(range(len(q)), key=q.__getitem__, reverse=True)
                              == sorted(range(len(p)), key=p.__getitem__, reverse=True),
                              f"порядок {sorted(range(len(q)), key=q.__getitem__, reverse=True)}"))

    sharp = max(temperature.apply(p, 0.5))
    flat = max(temperature.apply(p, 2.0))
    ok.append(_check_bool("T<1 заостряет (max растёт)", sharp > max(p),
                          f"{sharp} vs {max(p)}"))
    ok.append(_check_bool("T>1 сглаживает (max падает)", flat < max(p),
                          f"{flat} vs {max(p)}"))
    # Нулевые вероятности не должны ронять преобразование в 0/0
    ok.append(_check_bool("нулевая вероятность не ломает сумму",
                          abs(sum(temperature.apply([1.0, 0.0, 0.0], 0.3)) - 1.0) < 1e-9))
    return ok


# ----------------------------------------------------------------- fit

def _rows(p: list[float], n0: int, n1: int) -> list[tuple[list[float], int]]:
    """n0 строк с фактом 0, n1 строк с фактом 1 — без случайности,
    чтобы проверка не могла упасть на неудачном random_state."""
    return [(p, 0)] * n0 + [(p, 1)] * n1


def test_fit() -> list[bool]:
    from models import temperature

    print("\nПодбор температуры:")
    ok = []

    try:
        temperature.fit(_rows([0.6, 0.2, 0.2], 20, 5))
        ok.append(_check("на 25 строках подбор отклонён", False, True))
    except ValueError:
        ok.append(_check("на 25 строках подбор отклонён", True, True))

    # Модель систематически занижает уверенность: 90% фактов — исход 0,
    # а модель говорит 0.6. Калибровка обязана заострить и уменьшить loss.
    under = _rows([0.6, 0.2, 0.2], 90, 10)
    t_under = temperature.fit(under)
    ok.append(_check_bool("заниженная уверенность: T < 1", t_under < 1.0,
                          f"T={t_under:.4f}"))
    ok.append(_check_bool("T уменьшает log-loss на тех же данных",
                          temperature.mean_nll(under, t_under)
                          < temperature.mean_nll(under) - 1e-4,
                          f"{temperature.mean_nll(under, t_under):.5f} "
                          f"vs {temperature.mean_nll(under):.5f}"))

    # Модель переоценивает: 10% фактов — исход 0 при заявленных 0.95.
    over = _rows([0.95, 0.03, 0.02], 10, 90)
    t_over = temperature.fit(over)
    ok.append(_check_bool("завышенная уверенность: T > 1", t_over > 1.0,
                          f"T={t_over:.4f}"))
    ok.append(_check_bool("T уменьшает log-loss на переоценённых данных",
                          temperature.mean_nll(over, t_over)
                          < temperature.mean_nll(over) - 1e-4))

    # Почти правдоподобные вероятности: минимум около 1, и loss почти не
    # меняется — подгоняться тут нечему.
    good = _rows([0.7, 0.15, 0.15], 70, 30)
    t_good = temperature.fit(good)
    ok.append(_check_bool("хорошо откалиброванные вероятности: T около 1",
                          0.8 < t_good < 1.3, f"T={t_good:.4f}"))
    return ok


# -------------------------------------------------------- load_temperature

def test_load_temperature() -> list[bool]:
    from models import temperature

    print("\nЧтение температуры из отчёта:")
    ok = []
    real = temperature.REPORTS
    try:
        with tempfile.TemporaryDirectory() as td:
            temperature.REPORTS = Path(td)

            ok.append(_check("нет отчёта → None (прогноз считается сырым)",
                             temperature.load_temperature("basketball"), None))

            path = Path(td) / "calibration.json"
            path.write_text(json.dumps({
                "basketball": {"applied": False, "T": 0.18},
                "football": {"applied": True, "T": 0.85},
            }), encoding="utf-8")
            ok.append(_check("applied=false → None, даже если T записан",
                             temperature.load_temperature("basketball"), None))
            ok.append(_check("applied=true → T читается",
                             temperature.load_temperature("football"), 0.85))
            ok.append(_check("вид спорта, которого нет в отчёте → None",
                             temperature.load_temperature("rpl"), None))

            # Повреждённый файл не должен ронять пайплайн
            path.write_text("{сломанный json", encoding="utf-8")
            ok.append(_check("битый JSON → None, а не исключение",
                             temperature.load_temperature("basketball"), None))

            # T вне допустимого диапазона — признак порчи, не результата
            path.write_text(json.dumps(
                {"basketball": {"applied": True, "T": 99.0}}), encoding="utf-8")
            ok.append(_check("T вне [0.1, 3.0] → None",
                             temperature.load_temperature("basketball"), None))
    finally:
        temperature.REPORTS = real
    return ok


# ------------------------------------------------------------ check_sport

def _season(p: list[float], n: int, win_share: float) -> list[tuple[list[float], int]]:
    """Сезон из n строк: доля win_share даёт исход 0, остальное — исход 1."""
    n0 = round(n * win_share)
    return [(p, 0)] * n0 + [(p, 1)] * (n - n0)


def test_apply_rule() -> list[bool]:
    from models import calibrate

    print("\nПравило внедрения (APPLY_RULE):")
    ok = []

    # Калибровка помогает: распределение фактов одинаково от сезона к
    # сезону, а модель стабильно занижает уверенность.
    under = _season([0.6, 0.2, 0.2], 120, 0.9)
    helps = {"s1": under, "s2": list(under), "s3": list(under)}
    b = calibrate.check_sport("synthetic", helps, ["s1", "s2", "s3"])
    ok.append(_check("помогает стабильно → applied=true", b["applied"], True))
    ok.append(_check("внедряемость объяснена словами", bool(b["applied_reason"]), True))
    ok.append(_check("пар проверено: две", b["pairs"], 2))
    ok.append(_check("обе пары улучшились", b["improved_pairs"], 2))
    ok.append(_check_bool("T меньше единицы (заострение)",
                          b["T"] is not None and b["T"] < 1.0, f"T={b['T']}"))

    # Обучение и проверка смотрят в разные стороны: на сезоне-ученике
    # 90% побед хозяев, на следующем — 50%. Заострение здесь вредит, и
    # правило обязано отказать, даже если средняя по парам выглядела бы
    # неплохо.
    shift = {
        "s1": _season([0.6, 0.2, 0.2], 120, 0.9),
        "s2": _season([0.6, 0.2, 0.2], 120, 0.5),
        "s3": _season([0.6, 0.2, 0.2], 120, 0.5),
    }
    b2 = calibrate.check_sport("synthetic", shift, ["s1", "s2", "s3"])
    ok.append(_check("распределение сменилось → applied=false", b2["applied"], False))
    ok.append(_check_bool("причина отказа названа",
                          "средняя дельта" in b2["applied_reason"]
                          or "держак" in b2["applied_reason"],
                          b2["applied_reason"]))

    # Мало данных — нет ни одной пары, и это отказ, а не «внедрено»
    tiny = {"s1": _season([0.6, 0.2, 0.2], 20, 0.9),
            "s2": _season([0.6, 0.2, 0.2], 20, 0.9)}
    b3 = calibrate.check_sport("synthetic", tiny, ["s1", "s2"])
    ok.append(_check("мало данных → applied=false", b3["applied"], False))
    ok.append(_check("пар с достаточным объёмом нет", b3["pairs"], 0))

    # Точность не должна меняться: температура монотонна. Изменение
    # точности здесь — баг в проверке, а не «успех».
    rows = under
    raw_acc = sum(1 for p, y in rows if p.index(max(p)) == y) / len(rows)
    cal_acc = calibrate._accuracy(rows, b["T"])
    ok.append(_check("точность до и после калибровки совпадает",
                     round(raw_acc, 6), round(cal_acc, 6)))
    return ok


# --------------------------------------------------- подбор порогов РФПЛ

def _rpl_row(conf: float, y: int, p_over: float, over: int, season: str = "2023/24") -> dict:
    return {
        "season": season, "conf": conf, "top": 0 if conf >= 0.5 else 2,
        "y": y, "p_over": p_over, "over": over, "min_team_games": 30,
    }


def test_thresholds() -> list[bool]:
    from models import rpl_selective

    print("\nПодбор порогов РФПЛ:")
    ok = []

    rows = [
        _rpl_row(0.80, 0, 0.70, 1),   # исход угадан, ТБ угадан
        _rpl_row(0.70, 0, 0.70, 0),   # исход угадан, ТБ промах
        _rpl_row(0.50, 2, 0.60, 1),   # исход мимо (прогноз П1, факт П2)
        _rpl_row(0.45, 0, 0.40, 0),   # вне порога, ТМ угадан
    ]

    m = rpl_selective.evaluate_outcome(rows, 0.60)
    ok.append(_check("при пороге 0.60 отобрано 2 матча", m["n"], 2))
    ok.append(_check("точность на отобранном 2 из 2", m["accuracy"], 1.0))
    ok.append(_check("покрытие 2 из 4", m["coverage"], 0.5))
    ok.append(_check("правило «всегда на хозяев»: 2 из 2",
                     m["accuracy_always_home"], 1.0))
    ok.append(_check("преимущество над примитивом", m["edge"], 0.0))

    low = rpl_selective.evaluate_outcome(rows, 0.90)
    ok.append(_check("порог выше всех вероятностей → пусто", low.get("n"), 0))
    ok.append(_check("пустая выборка не выдаёт точность", "accuracy" not in low, True))

    base = rpl_selective.evaluate_outcome(rows, 0.0)
    ok.append(_check("без отсева — все 4", base["n"], 4))

    # Тотал: conf = max(ТБ, ТМ), как в models/verdict.py
    t = rpl_selective.evaluate_total(rows, 0.65)
    ok.append(_check("тотал при 0.65: отобраны два ТБ", t["n"], 2))
    ok.append(_check("тотал точность 1 из 2", t["accuracy"], 0.5))
    ok.append(_check("база «всегда ТБ» на отобранном 1 из 2",
                     t["accuracy_always_over"], 0.5))

    # Выбор порога: только при достаточном объёме
    cand = [(0.55, {"n": 10, "edge": 0.9}),      # отличное преимущество, но 10 матчей
            (0.60, {"n": 50, "edge": 0.1}),
            (0.65, {"n": 60, "edge": 0.3})]
    ok.append(_check("мало матчей — порог не выбирается",
                     rpl_selective._pick_threshold(cand, "edge"), 0.65))
    ok.append(_check("все кандидаты малы → None",
                     rpl_selective._pick_threshold(
                         [(0.55, {"n": 5, "edge": 0.9})], "edge"), None))
    return ok


def test_load_thresholds() -> list[bool]:
    from models import rpl_selective

    print("\nЧтение порогов из отчёта подбора:")
    ok = []
    real = rpl_selective.REPORTS
    try:
        with tempfile.TemporaryDirectory() as td:
            rpl_selective.REPORTS = Path(td)

            ok.append(_check("нет отчёта → константы (0.55, 0.65)",
                             rpl_selective.load_thresholds(),
                             (rpl_selective.RPL_VERDICT_THRESHOLD,
                              rpl_selective.RPL_TOTAL_THRESHOLD)))

            path = Path(td) / "rpl_selective.json"
            path.write_text(json.dumps({
                "outcome": {"chosen": 0.60},
                "total": {"chosen": 0.70},
            }), encoding="utf-8")
            ok.append(_check("пороги берутся из отчёта",
                             rpl_selective.load_thresholds(), (0.60, 0.70)))

            # chosen=None по тоталу — законный итог: отсев не помог,
            # вердикт по тоталу не выдаётся вообще.
            path.write_text(json.dumps({
                "outcome": {"chosen": 0.55},
                "total": {"chosen": None},
            }), encoding="utf-8")
            ok.append(_check("тотал не подобрался → None, а не 0.55",
                             rpl_selective.load_thresholds(), (0.55, None)))

            # Пустой/битый отчёт не должен ронять пайплайн прогнозов
            path.write_text("{так не читается", encoding="utf-8")
            ok.append(_check("битый отчёт → константы",
                             rpl_selective.load_thresholds(),
                             (rpl_selective.RPL_VERDICT_THRESHOLD,
                              rpl_selective.RPL_TOTAL_THRESHOLD)))
    finally:
        rpl_selective.REPORTS = real
    return ok


def main() -> int:
    results: list[bool] = []
    results += test_apply()
    results += test_fit()
    results += test_load_temperature()
    results += test_apply_rule()
    results += test_thresholds()
    results += test_load_thresholds()
    print("-" * 46)
    if all(results):
        print("Все проверки пройдены.")
        return 0
    print(f"Провалено {results.count(False)} из {len(results)} проверок.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
