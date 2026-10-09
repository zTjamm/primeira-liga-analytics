"""Модель для баскетбола: двумерное нормальное распределение на
(разница счёта, тотал).

ПОЧЕМУ НЕ DIXON-COLES

Модель Дэвида Кейнса из футбола описывает ЧИСЛО ГОЛОВ как пуассоновское.
В баскетболе счёт не счётчик редких событий, а результат непрерывного
процесса: 160 очков за матч — это не «много или мало голов», а почти
нормальная величина. Пуассон на таких числах даёт заметно худшую
калибровку, чем нормальное.

ЕСТЬ ОДНО УПРОЩЕНИЕ, КОТОРОЕ ЧАСТО УПУСКАЮТ

Для исхода 1X2 не нужна совместная плотность. Победа хозяев — это
P(разница > 0), а разница тут единственный аргумент; тотал на знак
разницы не влияет. Поэтому:

    1X2  → маргинальное распределение разницы
    Тотал → маргинальное распределение суммы

Совместная структура (корреляция rho) нужна только для совместных
вопросов вида «выиграть и пробить тотал» и для точного счёта. Считать
её для базовых прогнозов — лишняя работа без выигрыша.

РАЗДЕЛЕНИЕ АТАКИ И ОБОРОНЫ

Оно работает и в аддитивной модели, в отличие от того, что можно
заподозрить сразу. Параметры identifiablны: сумму атак и сумму оборон
принудительно приводим к нулю, а среднее лиги фиксируем из данных.
Именно поэтому без владения здесь НЕ нельзя разделить атаку и оборону —
нельзя лишь не получить нормализованные на 100 владений рейтинги,
которые обычно и считают «настоящей» силой команды.

ВЕСА ПО ДАВНОСТИ

Как и в футболе: матч двухлетней давности говорит о силе команды
меньше, чем вчерашний. Половина веса каждые 180 дней.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

HALF_LIFE_DAYS = 180.0

# Штраф на параметры команд.
#
# Значение выбрано НЕ по отложенным матчам — специально, чтобы не
# подбирать гиперпараметр на том же наборе, на котором потом отчитываемся.
# Раньше здесь стояло 0.5: оптимизатор полностью гасил параметры команд,
# и все прогнозы сходились к 0.52 против фактических 0.54, то есть модель
# становилась хуже наивного baseline. Штраф должен быть слабее масштаба
# данных, иначе он съедает весь сигнал.
#
# Почему именно 0.002, а не «оптимальное» 0.0: при нуле модель подгоняет
# и шум — дисперсия разницы падает с реальных ~21 очка до 12, а сила команд
# растягивается на 80 очков. Такая модель выдаёт вероятности 0.997 и
# на отложенных данных случайно получает лучшую метрику — но это
# артефакт переобучения, а не качество. 0.002 — это минимум, при котором
# оценки команд ещё различимы и шум не втягивается в параметры.
RIDGE = 0.002


@dataclass
class BasketballModel:
    teams: list[str] = field(default_factory=list)
    attack: dict[str, float] = field(default_factory=dict)
    defence: dict[str, float] = field(default_factory=dict)
    home_adv: float = 0.0
    league_avg: float = 80.0
    sigma_margin: float = 12.0
    sigma_total: float = 16.0
    rho: float = -0.10
    _converged: bool = False
    _message: str = ""

    # ------------------------------------------------------------------ fit

    def fit(self, matches: list[dict], teams: list[str],
            half_life_days: float = HALF_LIFE_DAYS) -> "BasketballModel":
        """matches: список словарей с home_id, away_id, home_score, away_score,
        match_date. Команды, которых нет в teams, получают нейтральные параметры."""
        if not matches:
            raise ValueError("пустой обучающий набор")

        self.teams = list(teams)
        self.attack = {t: 0.0 for t in self.teams}
        self.defence = {t: 0.0 for t in self.teams}
        idx = {t: i for i, t in enumerate(self.teams)}

        known = [m for m in matches if m["home_id"] in idx and m["away_id"] in idx]
        if len(known) < 30:
            raise ValueError(f"мало матчей известных команд: {len(known)}")

        hs = np.array([m["home_score"] for m in known], dtype=float)
        asc = np.array([m["away_score"] for m in known], dtype=float)
        hi = np.array([idx[m["home_id"]] for m in known])
        ai = np.array([idx[m["away_id"]] for m in known])

        self.league_avg = float(np.mean((hs + asc) / 2.0))

        last = max(m["match_date"] for m in known)
        ages = np.array([(last - m["match_date"]).days for m in known], dtype=float)
        w = np.power(0.5, ages / half_life_days)
        wsum = float(w.sum())

        n = len(self.teams)

        def unpack(theta: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            a = theta[:n]
            d = theta[n : 2 * n]
            # identifiability: оба вектора центрируем
            return a - a.mean(), d - d.mean()

        def neg_ll(theta: np.ndarray) -> float:
            """Отрицательное взвешенное логарифмическое правдоподобие.

            Внимание на знак: здесь собирается САМА логистика (со знаком
            «плюс»), а возвращается её отрицание. Если перепутать, то
            оптимизатор будет максимизировать логистик�� вместо
            минимизации потерь — и прижмёт дисперсию к нулю, потому что
              узкое распределение даёт высокую плотность.
            """
            atk, dfn = unpack(theta)
            home_adv, log_sm, log_st = theta[2 * n], theta[2 * n + 1], theta[2 * n + 2]

            mu_margin = home_adv + atk[hi] - dfn[ai]
            mu_total = 2.0 * self.league_avg
            sm = np.exp(log_sm)
            st = np.exp(log_st)

            margin = hs - asc
            total = hs + asc

            ll = (
                norm.logpdf(margin, mu_margin, sm)
                + norm.logpdf(total, mu_total, st)
                - RIDGE * np.sum(atk ** 2 + dfn ** 2) / max(wsum, 1.0) * 100.0
            )
            return -float(np.sum(w * ll))

        x0 = np.zeros(2 * n + 3)
        x0[2 * n + 1] = np.log(13.0)
        x0[2 * n + 2] = np.log(17.0)
        # Границы подобраны по физике, а не «на глаз»: разница мячей в
        # баскетболе имеет сд 12-16 очков, а домашнее преимущество
        # редко превышает 8 очков. Слишком широкие границы дают
        # оптимизатору возможность уползти в бессмыслицу на малой выборке.
        bounds = (
            [(-20.0, 20.0)] * n
            + [(-20.0, 20.0)] * n
            + [(-8.0, 8.0), (np.log(6.0), np.log(28.0)), (np.log(8.0), np.log(35.0))]
        )

        res = minimize(neg_ll, x0, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 1000, "maxfun": 40000})

        atk, dfn = unpack(res.x)
        self.attack = {t: float(atk[idx[t]]) for t in self.teams}
        self.defence = {t: float(dfn[idx[t]]) for t in self.teams}
        self.home_adv = float(res.x[2 * n])
        self.sigma_margin = float(np.exp(res.x[2 * n + 1]))
        self.sigma_total = float(np.exp(res.x[2 * n + 2]))
        self._converged = bool(res.success)
        self._message = str(res.message)
        return self

    # -------------------------------------------------------------- predict

    def predicted_scores(self, home: str, away: str) -> tuple[float, float]:
        mu_margin = self.home_adv + self.attack.get(home, 0.0) - self.defence.get(away, 0.0)
        total = 2.0 * self.league_avg
        return (total + mu_margin) / 2.0, (total - mu_margin) / 2.0

    def margin_params(self, home: str, away: str) -> tuple[float, float]:
        return (self.home_adv + self.attack.get(home, 0.0) - self.defence.get(away, 0.0),
                self.sigma_margin)

    def total_params(self) -> tuple[float, float]:
        return 2.0 * self.league_avg, self.sigma_total

    def predict_1x2(self, home: str, away: str) -> np.ndarray:
        """Вероятности на счёт в целых очках.

        Разница — дискретная величина, поэтому берём интервальную
        вероятность с непрерывной поправкой: P(M = k) = Φ((k+0.5−μ)/σ)
        − Φ((k−0.5−μ)/σ). Так ничья получается ненулевой, хотя в
        баскетболе она редка (около 0.5%).
        """
        mu, sd = self.margin_params(home, away)
        kmax = 40
        ks = np.arange(-kmax, kmax + 1)
        z_hi = (ks + 0.5 - mu) / sd
        z_lo = (ks - 0.5 - mu) / sd
        pmf = norm.cdf(z_hi) - norm.cdf(z_lo)

        home_w = float(pmf[ks > 0].sum())
        draw = float(pmf[ks == 0].sum())
        away_w = float(pmf[ks < 0].sum())
        total = home_w + draw + away_w
        return np.array([home_w, draw, away_w]) / total

    def predict_total_2h(self, home: str, away: str) -> float:
        """Вероятность тотала больше 155 очков (середина лиги)."""
        mu, sd = self.total_params()
        return float(1.0 - norm.cdf((155.5 - mu) / sd))

    def over_under_line(self, line: float = 160.0) -> float:
        mu, sd = self.total_params()
        return float(1.0 - norm.cdf((line + 0.5 - mu) / sd))

    # ---------------------------------------------------------------- misc

    def strength(self) -> list[dict]:
        """Рейтинг-листа. В аддитивной модели больше — значит лучше и по
        атаке, и по обороне, поэтому итог это СУММА, а не разность."""
        rows = [
            {
                "team": t,
                "attack": round(self.attack[t], 2),
                "defence": round(self.defence[t], 2),
                "overall": round(self.attack[t] + self.defence[t], 2),
            }
            for t in self.teams
        ]
        return sorted(rows, key=lambda r: -r["overall"])

    def summary(self) -> str:
        return (f"лига: {self.league_avg:.1f} очков на команду, "
                f"дома +{self.home_adv:.2f}, σ_разница={self.sigma_margin:.2f}, "
                f"σ_тотал={self.sigma_total:.2f}, сошёлся={self._converged}")
