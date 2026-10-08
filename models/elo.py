"""Elo-рейтинг для футбола.

Классический Elo требует поправок, иначе он заметно уступает пуассоновским
моделям:

  * множитель, зависящий от разницы мячей (одиночный гол — полный вес,
    крупный разгром — почти двойной);
  * явное домашнее преимущество;
  * между сезонами рейтинг не обнуляется, а плавно подтягивается к среднему,
    иначе новички врываются в лигу с несправедливым рейтингом.

Самый важный момент: Elo сам по себе не даёт распределения голов, а без него
нельзя получить вероятности 1X2 (в частности, вероятность ничьей). Раньше здесь
стояла захардкоженная константа «2.5 гола на 400 пунктов», но она неверна для
конкретной лиги и для текущего момента.

Поэтому коэффициент пересчитывается онлайн: на каждом матче накапливается
регрессия «разница рейтингов → фактическая разница мячей». Это обычный МНК,
считается за O(1) и использует только прошлое.

Модель обновляется строго в хронологическом порядке — утечки из будущего нет.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .poisson import poisson_1x2

INIT_RATING = 1500.0
K_BASE = 20.0
HOME_ADV = 60.0          # рейтинговых пунктов
REGRESS_TO_MEAN = 0.25   # доля возврата к среднему между сезонами

# Базовая лига: средние голы хозяев и гостей. Это только стартовая точка,
# дальше коэффициенты пересчитываются по фактическим матчам.
BASE_HOME_GOALS = 1.40
BASE_AWAY_GOALS = 1.15


def goal_diff_multiplier(diff: int) -> float:
    """Вес результата по разнице мячей (подход, близкий к World Football Elo)."""
    d = abs(diff)
    if d <= 1:
        return 1.0
    if d == 2:
        return 1.5
    return (11.0 + d) / 8.0


def actual_score(hg: int, ag: int) -> float:
    return 1.0 if hg > ag else (0.5 if hg == ag else 0.0)


@dataclass
class _Slope:
    """Онлайн-регрессия разницы мячей на разницу рейтингов (МНК без intercept)."""

    sxx: float = 0.0
    sxy: float = 0.0
    n: int = 0

    def add(self, x: float, y: float) -> None:
        self.sxx += x * x
        self.sxy += x * y
        self.n += 1

    @property
    def slope(self) -> float:
        if self.sxx < 1e-9:
            return 2.5 / 400.0        # запасной вариант до накопления данных
        return self.sxy / self.sxx


@dataclass
class EloModel:
    ratings: dict[str, float] = field(default_factory=dict)
    last_season: dict[str, str] = field(default_factory=dict)
    home_adv: float = HOME_ADV
    k_base: float = K_BASE
    reg: _Slope = field(default_factory=_Slope)
    base_home_goals: float = BASE_HOME_GOALS
    base_away_goals: float = BASE_AWAY_GOALS

    def _r(self, team: str) -> float:
        return self.ratings.get(team, INIT_RATING)

    def start_season(self, season: str) -> None:
        """В начале сезона подтягиваем рейтинги к среднему: сила команд меняется,
        но не обнуляется — иначе теряется память о долгосрочной силе."""
        mean = float(np.mean(list(self.ratings.values()))) if self.ratings else INIT_RATING
        for team in list(self.ratings):
            self.ratings[team] = mean + REGRESS_TO_MEAN * (self.ratings[team] - mean)
            self.last_season[team] = season

    def expected_goal_diff(self, home: str, away: str) -> float:
        diff = self._r(home) - self._r(away) + self.home_adv
        return diff * self.reg.slope

    def predict_1x2(self, home: str, away: str) -> np.ndarray:
        """Вероятности 1X2. Общая доля голов берётся из лиговой базы,
        разница — из рейтинга через онлайн-калиброванный коэффициент."""
        ediff = self.expected_goal_diff(home, away)
        total = self.base_home_goals + self.base_away_goals
        home_xg = max(0.10, (total + ediff) / 2.0)
        away_xg = max(0.08, (total - ediff) / 2.0)
        return poisson_1x2(home_xg, away_xg)

    def update(self, home: str, away: str, hg: int, ag: int) -> None:
        diff = self._r(home) - self._r(away) + self.home_adv
        # сначала калибруем коэффициент по наблюдённому матчу (только прошлое),
        # потом пересчитываем рейтинг
        self.reg.add(diff, float(hg - ag))

        exp_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        actual = actual_score(hg, ag)
        k = self.k_base * goal_diff_multiplier(hg - ag)
        delta = k * (actual - exp_home)
        self.ratings[home] = self._r(home) + delta
        self.ratings[away] = self._r(away) - delta

    def table(self) -> list[dict]:
        return sorted(
            ({"team": t, "rating": round(r, 1)} for t, r in self.ratings.items()),
            key=lambda x: -x["rating"],
        )
