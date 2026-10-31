"""Температурная калибровка вероятностей.

Зачем она нужна. Модель может угадывать исход чаще соперника, но при этом
занижать уверенность: говорить «55%» там, где по факту побеждает 75%.
Такая модель честна по направлению, но врёт по шкале — а по шкале
принимаются решения: порог отсева 0.55 отмерен именно в вероятностях, и
если они раздвинуты неверно, то и отсев отмерен неверно.

Как устроена температура. Единственный параметр T меняет резкость, не
меняя порядок исходов:

    q_i = p_i ** (1/T)  /  сумма_j p_j ** (1/T)

T > 1 — сглаживает (модель уверена слишком сильно), T < 1 — заостряет.
Потому что преобразование монотонное, argmax не меняется: точность
прогноза остаётся ровно той же, меняется только шкала вероятностей.
Один параметр — значит, что подогнать на 300 матчах, а не на 30.

ГЛАВНОЕ ПРАВИЛО: калибровка применяется в проде только если она
улучшила log-loss на данных, на которых НЕ подбиралась. Подбор T на
валидации и проверка на валидации — самообман: подгонка всегда выигрывает
на своих же данных. Поэтому процедура строго скользящая:

    учил на сезоне N -> проверял на N+1, для каждой пары подряд;

и только если в большинстве пар стало лучше, калибровка считается
работающей. Иначе — отчёт честно пишет applied=false, и прогнозы
остаются сырыми.

Запуск проверки:
    python -m models.calibrate

Отчёт: data/reports/calibration.json. Прод читает его через
load_temperature() — если файла нет или там applied=false, температура
не применяется.
"""
from __future__ import annotations

import json
import math

from etl.config import REPORTS

# Границы поиска T. Нижняя не 0: при T -> 0 вероятности вырождаются в
# one-hot, и отчёт получает «идеальный» log-loss на обучающей выборке,
# который на новой выборке превращается в катастрофу. Потолок по той же
# причине — сверхрезкое сглаживание бессмысленно.
T_MIN, T_MAX = 0.1, 3.0


def apply(p: list[float], t: float | None) -> list[float]:
    """Применяет температуру t к вектору вероятностей. t None -> как есть."""
    if not t or abs(t - 1.0) < 1e-9:
        return list(p)
    q = [max(x, 1e-12) ** (1.0 / t) for x in p]
    s = sum(q)
    return [x / s for x in q]


def nll(p: list[float], y: int, t: float | None = None) -> float:
    """Средний log-loss вектора p при факте y."""
    q = apply(p, t)
    return -math.log(max(q[y], 1e-12))


def mean_nll(rows: list[tuple[list[float], int]], t: float | None = None) -> float:
    if not rows:
        return float("nan")
    return sum(nll(p, y, t) for p, y in rows) / len(rows)


def fit(rows: list[tuple[list[float], int]]) -> float:
    """Подбирает T методом золотого сечения по log-loss.

    rows — [(вероятности, фактический индекс), …]. Поиск по одному
    параметру, поэтому никакой оптимизации с градиентами не нужно:
    функция выпукла по T, золотое сечение находит минимум надёжно.
    """
    if len(rows) < 30:
        # На нескольких десятках матчей подбор T — это подгонка под шум.
        # Честнее не калибровать, чем калибровать на удачу.
        raise ValueError(f"для подбора температуры мало данных: {len(rows)}")
    lo, hi = T_MIN, T_MAX
    gr = (math.sqrt(5) - 1) / 2
    c, d = hi - gr * (hi - lo), lo + gr * (hi - lo)
    fc, fd = mean_nll(rows, c), mean_nll(rows, d)
    for _ in range(100):
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - gr * (hi - lo)
            fc = mean_nll(rows, c)
        else:
            lo, c, fc = c, d, fd
            d = lo + gr * (hi - lo)
            fd = mean_nll(rows, d)
    return (lo + hi) / 2


def load_temperature(sport: str) -> float | None:
    """T для вида спорта из отчёта калибровки. None — если калибровка
    не подтверждена: файла нет, вид спорта не проверялся или проверка
    показала, что калибровка не помогает.

    Прод обязан уметь работать без отчёта: сервер поднимается с нуля и
    не должен падать от отсутствующего JSON.
    """
    path = REPORTS / "calibration.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    block = data.get(sport) or {}
    if not block.get("applied"):
        return None
    t = block.get("T")
    if not isinstance(t, (int, float)) or not (T_MIN <= float(t) <= T_MAX):
        return None
    return float(t)
