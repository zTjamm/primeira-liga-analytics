"""Dixon-Coles: пуассоновская модель голов с поправкой на низкие счёты.

Классика для футбола с историей успешного применения. Идея:

    lambda (дома)   = exp(attack_home - defence_away + gamma)
    lambda (в гостях) = exp(attack_away - defence_home)
    rho             — поправка на зависимость исходов при счётах 0:0, 0:1, 1:0, 1:1

Тонкость, на которой легко испортить модель: сила команды одновременно
выражается через её атаку И через оборону соперников. Если штрафовать только
одну из сторон, оптимизатор просто выберет непштрафованную и вторая
останется нулевой — модель вырождается в «все команды одинаковы».
Поэтому оба вектора центрируются к нулю (идентифицируемость) и штрафуются
симметрично.

Силу команд оцениваем по взвешенному максимуму правдоподобия с экспоненциальным
затуханием: недавние матчи говорят о текущей силе больше, чем матчи
пятилетней давности.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

from .poisson import MAX_GOALS, correct_low_scores, poisson_matrix, probs_from_scores

RIDGE = 0.01        # слабый симметричный штраф: гасит переобучение, но не вырождает модель


@dataclass
class DixonColesModel:
    teams: list[str] = field(default_factory=list)
    attack: dict[str, float] = field(default_factory=dict)
    defence: dict[str, float] = field(default_factory=dict)
    home_adv: float = 0.0
    rho: float = -0.05
    half_life_days: float = 180.0
    mean_home_goals: float = 1.35
    mean_away_goals: float = 1.15
    _converged: bool = False
    _message: str = ""

    # ---------------------------------------------------------------- fit

    def fit(self, matches: list[dict], teams: list[str], half_life_days: float = 180.0) -> "DixonColesModel":
        if not matches:
            raise ValueError("Пустой обучающий набор")
        self.teams = list(teams)
        self.half_life_days = half_life_days
        self.attack = {t: 0.0 for t in self.teams}
        self.defence = {t: 0.0 for t in self.teams}

        idx = {t: i for i, t in enumerate(self.teams)}
        known = [m for m in matches if m["home_id"] in idx and m["away_id"] in idx]
        if len(known) < 50:
            raise ValueError(f"Слишком мало матчей с известными командами: {len(known)}")

        hg = np.array([m["fthg"] for m in known], dtype=float)
        ag = np.array([m["ftag"] for m in known], dtype=float)
        hi = np.array([idx[m["home_id"]] for m in known])
        ai = np.array([idx[m["away_id"]] for m in known])

        # Базовые ставки берём из данных, а не задаём вручную: gamma — это
        # отклонение от лиговой базы, а сама база обязана быть оценена.
        self.mean_home_goals = float(np.mean(hg))
        self.mean_away_goals = float(np.mean(ag))
        self.home_adv = float(np.log(max(self.mean_home_goals, 0.05) / max(self.mean_away_goals, 0.05)))

        last = max(m["match_date"] for m in known)
        ages = np.array([(last - m["match_date"]).days for m in known], dtype=float)
        w = np.power(0.5, ages / half_life_days)
        w_sum = float(w.sum())

        n = len(self.teams)

        def unpack(theta: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            atk = theta[:n]
            dfn = theta[n : 2 * n]
            # центрируем оба вектора: иначе параметры неразделимы
            return atk - atk.mean(), dfn - dfn.mean()

        def neg_ll(theta: np.ndarray) -> float:
            atk, dfn = unpack(theta)
            gamma, rho = theta[2 * n], theta[2 * n + 1]

            lam_h = np.clip(np.exp(atk[hi] - dfn[ai] + gamma), 0.05, 8.0)
            lam_a = np.clip(np.exp(atk[ai] - dfn[hi]), 0.05, 8.0)

            ll = poisson.logpmf(hg, lam_h) + poisson.logpmf(ag, lam_a)
            ll = ll + _rho_log_tau(hg, ag, lam_h, lam_a, rho)
            ll = ll - RIDGE * np.sum(atk ** 2 + dfn ** 2)
            return -float(np.sum(w * ll))

        x0 = np.zeros(2 * n + 2)
        x0[2 * n] = self.home_adv
        x0[2 * n + 1] = -0.05
        bounds = [(-1.5, 1.5)] * (2 * n) + [(-0.5, 0.8), (-0.2, 0.2)]

        res = minimize(neg_ll, x0, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 1000, "maxfun": 20000})

        atk, dfn = unpack(res.x)
        self.attack = {t: float(atk[idx[t]]) for t in self.teams}
        self.defence = {t: float(dfn[idx[t]]) for t in self.teams}
        self.home_adv = float(res.x[2 * n])
        self.rho = float(res.x[2 * n + 1])
        self._converged = bool(res.success)
        self._message = str(res.message)
        return self

    # ---------------------------------------------------------------- predict

    def lambdas(self, home: str, away: str) -> tuple[float, float]:
        atk_h = self.attack.get(home, 0.0) - self.defence.get(away, 0.0) + self.home_adv
        atk_a = self.attack.get(away, 0.0) - self.defence.get(home, 0.0)
        return (float(np.clip(np.exp(atk_h), 0.05, 8.0)),
                float(np.clip(np.exp(atk_a), 0.05, 8.0)))

    def score_matrix(self, home: str, away: str) -> np.ndarray:
        lam_h, lam_a = self.lambdas(home, away)
        return correct_low_scores(poisson_matrix(lam_h, lam_a, MAX_GOALS), self.rho)

    def predict_1x2(self, home: str, away: str) -> np.ndarray:
        return probs_from_scores(self.score_matrix(home, away))

    def strength(self) -> list[dict]:
        """Рейтинг-листа: атака минус оборона (оборона со знаком «лучше — меньше»)."""
        rows = [
            {
                "team": t,
                "attack": round(self.attack[t], 3),
                "defence": round(self.defence[t], 3),
                "overall": round(self.attack[t] - self.defence[t], 3),
            }
            for t in self.teams
        ]
        return sorted(rows, key=lambda r: -r["overall"])

    def summary(self) -> str:
        return (f"матчей в окне: ok, gamma={self.home_adv:+.3f} "
                f"(база {self.mean_home_goals:.2f}/{self.mean_away_goals:.2f}), "
                f"rho={self.rho:+.4f}, сошёлся: {self._converged}")


# ------------------------------------------------------------------ helpers


def _rho_log_tau(hg, ag, lam_h, lam_a, rho: float) -> np.ndarray:
    """Логарифм поправки Dixon-Coles для четырёх «низких» счетов."""
    tau = np.ones_like(hg, dtype=float)
    if rho == 0.0:
        return np.zeros_like(hg, dtype=float)
    m00 = (hg == 0) & (ag == 0)
    m01 = (hg == 0) & (ag == 1)
    m10 = (hg == 1) & (ag == 0)
    m11 = (hg == 1) & (ag == 1)
    tau[m00] = 1.0 - lam_h[m00] * lam_a[m00] * rho
    tau[m01] = 1.0 + lam_h[m01] * rho
    tau[m10] = 1.0 + lam_a[m10] * rho
    tau[m11] = 1.0 - rho
    return np.log(np.clip(tau, 1e-9, None))
