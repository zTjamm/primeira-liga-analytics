"""Пуассоновская основа моделей голов.

Общая часть для Elo и Dixon-Coles: построение матрицы распределения голов
и свёртка её в вероятности исходов.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import poisson

MAX_GOALS = 10


def poisson_matrix(home_xg: float, away_xg: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    """P[i][j] = P(хозяева забили i, гости забили j)."""
    i = np.arange(max_goals + 1)
    return np.outer(poisson.pmf(i, home_xg), poisson.pmf(i, away_xg))


def probs_from_scores(matrix: np.ndarray) -> np.ndarray:
    """Сворачивает матрицу голов в вероятности 1X2."""
    home = float(np.tril(matrix, -1).sum())   # хозяева строго больше
    away = float(np.triu(matrix, 1).sum())    # гости строго больше
    draw = float(np.trace(matrix))            # равные счета
    return np.array([home, draw, away]) / (home + draw + away)


def poisson_1x2(home_xg: float, away_xg: float) -> np.ndarray:
    return probs_from_scores(poisson_matrix(home_xg, away_xg))


def over_prob(matrix: np.ndarray, line: float = 2.5) -> tuple[float, float]:
    """Вероятности тотала больше/меньше линии."""
    idx = np.arange(matrix.shape[0])
    totals = idx[:, None] + idx[None, :]
    over = float(matrix[totals > line].sum())
    under = float(matrix[totals < line].sum())
    return over / (over + under), under / (over + under)


def correct_low_scores(matrix: np.ndarray, rho: float) -> np.ndarray:
    """Поправка Dixon-Coles на зависимость исходов при низких счётах.

    Независимый пуассон систематически занижает число ничьих 0:0 и 1:1.
    Параметр rho этим и занимается; функция нормирует результат обратно в 1.
    """
    if rho == 0.0:
        return matrix
    m = matrix.copy()
    tau = {
        (0, 0): 1.0 - home_lambda(m) * away_lambda(m) * rho,
        (0, 1): 1.0 + home_lambda(m) * rho,
        (1, 0): 1.0 + away_lambda(m) * rho,
        (1, 1): 1.0 - rho,
    }
    for (i, j), t in tau.items():
        if i < m.shape[0] and j < m.shape[1]:
            m[i, j] *= t
    total = m.sum()
    return m / total if total > 0 else matrix


def home_lambda(matrix: np.ndarray) -> float:
    """Среднее число голов хозяев, восстановленное из матрицы."""
    idx = np.arange(matrix.shape[0])
    return float((idx[:, None] * matrix).sum() / matrix.sum())


def away_lambda(matrix: np.ndarray) -> float:
    idx = np.arange(matrix.shape[1])
    return float((idx[None, :] * matrix).sum() / matrix.sum())
