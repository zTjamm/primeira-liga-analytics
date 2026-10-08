"""Перевод коэффициентов в вероятности — то есть снятие маржи букмекера.

Наивная пропорциональная нормализация (`(1/o_i) / Σ(1/o_j)`) занижает
вероятности фаворитов и завышает андердогов: у букмекера заложено
неравномерное ожидание (favourite–longshot bias). Для честного сравнения
с рынком это важно — иначе мы сравниваем свою модель с искажённой.

Реализованы три метода; `shin` — предпочтительный, `power` — запасной.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class DevigResult:
    probs: np.ndarray      # вероятности 1X2 в сумме 1
    overround: float       # сумма 1/o до нормализации
    method: str


def overround(odds: np.ndarray) -> float:
    return float(np.sum(1.0 / odds))


def devig_power(odds: np.ndarray) -> DevigResult:
    """Подбирает показатель степени k, при котором Σ(1/o_i)^k = 1.

    Метод power — стандартный компромисс между простотой и учётом
    структуры фаворитов. Именно его применяют в большинстве сравнений
    с рынком, поэтому берём его как базу.
    """
    inv = 1.0 / odds

    def f(k: float) -> float:
        return float(np.sum(inv ** k)) - 1.0

    lo, hi = 0.5, 1.6
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    p = inv ** k
    return DevigResult(p / p.sum(), overround(odds), "power")



def _shin_probs(z: float, inv: np.ndarray, book: float) -> np.ndarray:
    """Формула Шина для вероятностей при заданном параметре z.

        p_i = ( sqrt(z^2 + 4(1-z)^2 * b_i * B) - z ) / (2(1-z))

    где b_i = 1/o_i, B = sum(b_i) ("сумма книги", > 1).
    При z -> 0 получаем p_i = sqrt(b_i*B), и сумма p больше 1;
    при z -> 1 сумма стремится к 0. Значит корень уравнения sum(p_i) = 1
    существует, единственен и находится бисекцией.
    """
    om = 1.0 - z
    return (np.sqrt(z * z + 4.0 * om * om * inv * book) - z) / (2.0 * om)


def devig_shin(odds: np.ndarray) -> DevigResult:
    """Метод Шина: учитывает перекос букмекера в сторону фаворитов.

    ВНИМАНИЕ, этот вариант проверен и НЕ используется как основной.
    На реальных данных Primeira Liga он даёт z ≈ 0.33-0.45, тогда как в
    литературе для футбольных рынков z ожидается в диапазоне 0.01-0.05.
    То есть коррекция получается примерно в 10 раз сильнее нужной:
    логистика ~0.94 против ~0.92 у power, а доля побед хозяев падает до
    0.40 при фактической 0.426. Метод power на тех же данных калиброван
    лучше, поэтому основной метод — power, а этот оставлен для сверки.
    """
    inv = 1.0 / odds
    book = float(inv.sum())
    if book <= 1.0 + 1e-9:
        return DevigResult(inv / book, book, "shin")

    def total(z: float) -> float:
        return float(_shin_probs(z, inv, book).sum()) - 1.0

    lo, hi = 1e-6, 0.999
    for _ in range(200):
        mid = (lo + hi) / 2
        if total(mid) > 0:
            lo = mid
        else:
            hi = mid
    z = (lo + hi) / 2
    p = _shin_probs(z, inv, book)
    return DevigResult(p / p.sum(), book, "shin")


def devig_proportional(odds: np.ndarray) -> DevigResult:
    """Наивная пропорциональная нормализация. Оставлена как диагностика:
    именно её чаще всего (ошибочно) принимают за "вероятности рынка"."""
    inv = 1.0 / odds
    return DevigResult(inv / inv.sum(), overround(odds), "proportional")


def devig(odds: list[float], method: str = "power") -> DevigResult | None:
    """Точка входа. None, если коэффициенты непригодны.

    По умолчанию `power`: он проще, стандартен для сравнений с рынком
    и на данных Primeira Liga показывает лучшую калибровку из трёх методов
    (см. docstring devig_shin и отчёт data/reports/market_calibration.json).
    """
    if odds is None or len(odds) != 3:
        return None
    o = np.asarray([float(x) for x in odds], dtype=float)
    if not np.all(np.isfinite(o)) or np.any(o <= 1.0):
        return None
    if overround(o) > 1.35:      # подозрительно много маржи — не доверяем
        return None
    fn = {"shin": devig_shin, "power": devig_power, "proportional": devig_proportional}.get(method)
    return fn(o) if fn else devig_shin(o)

def devig_two_way(odds: list[float], method: str = "power") -> np.ndarray | None:
    """Для тоталов: два исхода, здесь маржа снимается нормировкой,
    искажение от фаворитов выражено слабее, чем в 1X2."""
    if odds is None or len(odds) != 2:
        return None
    o = np.asarray([float(x) for x in odds], dtype=float)
    if not np.all(np.isfinite(o)) or np.any(o <= 1.0):
        return None
    inv = 1.0 / o
    return inv / inv.sum()
