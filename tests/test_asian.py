"""Проверка азиатского хэндикапа на случаях, где ответ известен вручную.

Рынок хэндикапа легко описать неправильно: целые линии допускают возврат,
а четверти — нет. Ошибка здесь даёт правдоподобные, но бессмысленные
вероятности, поэтому проверяем на счетах, для которых ответ считается
в уме.

Запуск:
    python -m tests.test_asian
"""
from __future__ import annotations

import numpy as np

from models.asian import QUARTERS, cover_probs, fair_line, has_push, split_line, summarize

PASS = "\u2713"
FAIL = "\u2717"


def _matrix(score_h: int, score_a: int) -> np.ndarray:
    """Матрица счёта, где нужный результат имеет вероятность 1."""
    m = np.zeros((8, 8))
    m[score_h, score_a] = 1.0
    return m


def _check(name: str, got, want) -> bool:
    ok = got == want
    print(f"  [{PASS if ok else FAIL}] {name}" + ("" if ok else f"  получено {got!r}, ждали {want!r}"))
    return ok


def check_split() -> bool:
    print("Разбивка дробных линий:")
    ok = True
    ok &= _check("целая линия остаётся одной", split_line(-1.0), [-1.0])
    ok &= _check("половинная — одна", split_line(-0.5), [-0.5])
    ok &= _check("-0.25 делится на -0.5 и 0.0", split_line(-0.25), [-0.5, 0.0])
    ok &= _check("-0.75 делится на -1.0 и -0.5", split_line(-0.75), [-1.0, -0.5])
    ok &= _check("+1.25 делится на +1.0 и +1.5", split_line(1.25), [1.0, 1.5])
    return ok


def check_push() -> bool:
    print("\nВозвраты (пуш):")
    ok = True
    ok &= _check("на целой линии возврат возможен", has_push(-1.0), True)
    ok &= _check("на 0.0 возврат возможен", has_push(0.0), True)
    ok &= _check("на половинной — нет", has_push(-0.5), False)
    ok &= _check("на четверти — нет", has_push(-0.25), False)
    return ok


def check_cover() -> bool:
    print("\nПокрытие линии на известных счетах:")
    ok = True

    # Sporting 3:0 при линии -1.0: хозяева выиграли в три, линия закрыта.
    r = cover_probs(_matrix(3, 0), -1.0)
    ok &= _check("3:0 при линии -1.0 — хозяева выиграли", round(r["win"], 3), 1.0)

    # Тот же счёт при линии -2.5: разница 3, а нужно больше 2.5 — выиграли,
    # но возврат невозможен, проигрыша нет.
    r = cover_probs(_matrix(3, 0), -2.5)
    ok &= _check("3:0 при линии -2.5 — выиграли, возврата нет",
                 (round(r["win"], 3), r["push"]), (1.0, 0.0))

    # Ровно один мяч при линии -1.0 — возврат, не победа и не поражение.
    r = cover_probs(_matrix(1, 0), -1.0)
    ok &= _check("1:0 при линии -1.0 — возврат", round(r["push"], 3), 1.0)
    ok &= _check("  и выигрыша при этом нет", round(r["win"], 3), 0.0)

    # 2:0 при линии -1.5: разница 2 > 1.5, выиграли.
    r = cover_probs(_matrix(2, 0), -1.5)
    ok &= _check("2:0 при линии -1.5 — хозяева выиграли", round(r["win"], 3), 1.0)

    # 2:0 при линии -2.0: ровно в два мяча — возврат.
    r = cover_probs(_matrix(2, 0), -2.0)
    ok &= _check("2:0 при линии -2.0 — возврат", round(r["push"], 3), 1.0)

    # 1:0 при линии -0.75: делится на -0.5 и -1.0. Против 1.0 — возврат
    # (разница ровно 1), против 0.5 — проигрыш (нужно больше 0.5, а 1 > 0.5,
    # значит выигрыш). Итог: половина выиграла, половина вернулась.
    r = cover_probs(_matrix(1, 0), -0.75)
    ok &= _check("1:0 при линии -0.75 — половина выиграла", round(r["win"], 3), 0.5)
    ok &= _check("  половина вернулась", round(r["push"], 3), 0.5)

    # Гостевая победа 0:2 при линии +1.5: хозяева с форой 1.5 не покрыли.
    r = cover_probs(_matrix(0, 2), 1.5)
    ok &= _check("0:2 при линии +1.5 — хозяева проиграли ставку",
                 round(r["win"], 3), 0.0)

    # 2:2 при линии +1.0: хозяева ПОЛУЧАЮТ гол, 3:2 — линия закрыта ими.
    # Раньше здесь стояло обратное ожидание, и оно было неверным: положительная
    # линия означает фору хозяевам, а не преимущество гостей.
    r = cover_probs(_matrix(2, 2), 1.0)
    ok &= _check("2:2 при линии +1.0 — хозяева закрыли фору", round(r["win"], 3), 1.0)

    # 0:2 при линии +1.0: с форой получается 1:2, не хватило.
    r = cover_probs(_matrix(0, 2), 1.0)
    ok &= _check("0:2 при линии +1.0 — хозяева не покрыли", round(r["win"], 3), 0.0)
    return ok


def check_sum() -> bool:
    print("\nСумма вероятностей всегда единица:")
    ok = True
    m = np.zeros((8, 8))
    rng = np.random.default_rng(7)
    m = rng.random((8, 8))
    m /= m.sum()
    for line in QUARTERS + (-1.0, -2.0, 1.5, 2.0):
        r = cover_probs(m, line)
        total = r["win"] + r["push"] + r["lose"]
        if abs(total - 1.0) > 1e-9:
            print(f"  [{FAIL}] линия {line}: сумма {total:.12f}")
            ok = False
    if ok:
        print(f"  [{PASS}] проверено {len(QUARTERS) + 4} линий, расхождений нет")
    return ok


def check_fair_line() -> bool:
    print("\nСправедливая линия (медиана разницы):")
    ok = True
    ok &= _check("3:0 -> хозяева настолько сильны, что линия отрицательная",
                 fair_line(_matrix(3, 0)) <= -1.0, True)
    ok &= _check("2:2 -> линия около нуля", fair_line(_matrix(2, 2)), 0.0)
    # Равномерная матрица: медиана примерно посередине диапазона разниц.
    m = np.ones((8, 8))
    fl = fair_line(m)
    ok &= _check("равномерная -> где-то около 0", abs(fl) <= 1.0, True)
    return ok


def check_summarize() -> bool:
    print("\nРазбивка по разнице:")
    s = summarize(_matrix(3, 0))
    ok = True
    ok &= _check("3:0 -> хозяева в два и больше", round(s["home_2plus"], 3), 1.0)
    ok &= _check("  ровно в один мяч — нет", round(s["home_exactly_1"], 3), 0.0)
    ok &= _check("  гости не выиграли", round(s["away_2plus"], 3), 0.0)
    s = summarize(_matrix(1, 0))
    ok &= _check("1:0 -> ровно в один мяч", round(s["home_exactly_1"], 3), 1.0)
    ok &= _check("  хозяева в два+ — нет", round(s["home_2plus"], 3), 0.0)
    s = summarize(_matrix(1, 1))
    ok &= _check("1:1 -> ничья попала в «не выиграл»",
                 round(s["home_0_or_away"], 3), 1.0)
    return ok


def main() -> int:
    print("Проверка азиатского хэндикапа\n" + "-" * 46)
    results = [
        check_split(),
        check_push(),
        check_cover(),
        check_sum(),
        check_fair_line(),
        check_summarize(),
    ]
    print("-" * 46)
    if all(results):
        print("Все проверки пройдены.")
        return 0
    print("Есть неудачные проверки.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())