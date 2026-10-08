"""Walk-forward бэктест.

Единственный честный способ оценить модель на футболе: обучаться только на
том, что было известно до матча, и проверять на будущем. Ниже этого порога
не опускаемся — ни перемешивания, ни обучения на всех данных сразу, ни
подбора гиперпараметров на тестовом отрезке.

Метрики:
  log-loss   — основная. Штрафует за уверенные неверные прогнозы.
  Brier      — усреднённая квадратичная ошибка по трём исходам.
  accuracy   — доля угаданных исходов (самая неинформативная, но понятная).
  ROI        — доходность ставок по value (коэффициент × вероятность > 1).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Prediction:
    match_id: str
    season: str
    match_date: object
    home: str
    away: str
    actual: str                      # H / D / A
    model_probs: np.ndarray          # [H, D, A]
    market_probs: np.ndarray | None  # [H, D, A] после снятия маржи
    odds_close: np.ndarray | None    # [H, D, A]
    model_name: str = ""


@dataclass
class Metrics:
    name: str
    n: int = 0
    logloss: float = float("nan")
    brier: float = float("nan")
    accuracy: float = float("nan")
    roi: float = float("nan")
    n_bets: int = 0

    def as_row(self) -> dict:
        return {
            "model": self.name,
            "n": self.n,
            "logloss": round(self.logloss, 5),
            "brier": round(self.brier, 5),
            "accuracy": round(self.accuracy, 5),
            "roi": round(self.roi, 5) if self.roi == self.roi else None,
            "n_bets": self.n_bets,
        }


ACTUAL_INDEX = {"H": 0, "D": 1, "A": 2}


def score(preds: list[Prediction], model_name: str, prob_source: str = "model_probs",
          min_edge: float = 0.02) -> Metrics:
    """Считает метрики по одному набору прогнозов.

    `prob_source` — какие вероятности оцениваем: свои или рыночные.
    Так одним и тем же кодом меряем и модель, и baseline-рынок.
    """
    m = Metrics(name=model_name)
    if not preds:
        return m

    losses: list[float] = []
    briers: list[float] = []
    correct = 0
    profits: list[float] = []

    for p in preds:
        idx = ACTUAL_INDEX.get(p.actual)
        if idx is None:
            continue
        probs = p.model_probs if prob_source == "model_probs" else p.market_probs
        if probs is None:
            continue
        probs = np.asarray(probs, dtype=float)
        probs = probs / probs.sum()

        m.n += 1
        losses.append(-np.log(max(probs[idx], 1e-12)))
        onehot = np.zeros(3)
        onehot[idx] = 1.0
        briers.append(float(np.sum((probs - onehot) ** 2)))
        correct += int(np.argmax(probs) == idx)

        # ROI: ставим, только когда своя вероятность выше рыночной не меньше min_edge
        if prob_source == "model_probs" and p.odds_close is not None:
            odds = np.asarray(p.odds_close, dtype=float)
            if np.all(np.isfinite(odds)):
                value = probs * odds - 1.0
                picks = np.where(value > min_edge)[0]
                if picks.size:
                    # ставим на самый «ценный» исход
                    pick = int(picks[np.argmax(value[picks])])
                    m.n_bets += 1
                    won = int(idx == pick)
                    profits.append(float(odds[pick] - 1.0) if won else -1.0)

    if m.n == 0:
        return m
    m.logloss = float(np.mean(losses))
    m.brier = float(np.mean(briers))
    m.accuracy = correct / m.n
    if profits:
        m.roi = float(np.sum(profits) / len(profits))
    return m


def naive_baseline(season_preds: list[Prediction], home_share: float = 0.45,
                   draw_share: float = 0.25) -> Metrics:
    """Наивный baseline: одинаковые вероятности для всех матчей лиги.

    Модель, которая не обходит его, не имеет смысла. Доли взяты типовыми
    для европейского футбола; здесь они служат именно порогом.
    """
    probs = np.array([home_share, draw_share, 1 - home_share - draw_share])
    preds = [
        Prediction(
            match_id=p.match_id, season=p.season, match_date=p.match_date,
            home=p.home, away=p.away, actual=p.actual,
            model_probs=probs, market_probs=None, odds_close=None,
        )
        for p in season_preds
        if p.actual in ACTUAL_INDEX
    ]
    return score(preds, "naive (H/D/A = 45/25/30)")


def empirical_baseline(season_preds: list[Prediction]) -> Metrics:
    """Baseline посильнее: доли исходов, посчитанные по этому же сезону,
    но только по матчам до тестируемого (progressive)."""
    preds = []
    for p in season_preds:
        if p.actual not in ACTUAL_INDEX:
            continue
        prior = [q for q in season_preds if q.match_date < p.match_date and q.actual in ACTUAL_INDEX]
        if len(prior) < 50:
            continue
        counts = np.array([sum(1 for q in prior if q.actual == k) for k in "HDA"], dtype=float)
        preds.append(
            Prediction(p.match_id, p.season, p.match_date, p.home, p.away, p.actual,
                       counts / counts.sum(), None, None)
        )
    return score(preds, "empirical (доли по сезону до матча)")


def evaluate(preds: list[Prediction], model_name: str) -> Metrics:
    """Модель + рынок одним и тем же кодом — иначе сравнение некорректно."""
    own = score(preds, model_name, "model_probs")
    mkt = score([p for p in preds if p.market_probs is not None], "market (no-vig, closing)", "market_probs")
    return own, mkt  # type: ignore[return-value]


def format_table(rows: list[dict], key: str = "model") -> str:
    if not rows:
        return "нет данных"
    first = f"{'сезон' if key == 'season' else 'модель':<34}"
    head = f"{first} {'n':>6} {'log-loss':>9} {'Brier':>9} {'точность':>9} {'ROI':>8} {'ставок':>7}"
    lines = [head, "-" * len(head)]
    for r in rows:
        roi = f"{r['roi']:.4f}" if r.get("roi") is not None else "—"
        label = str(r.get(key, "—"))
        lines.append(
            f"{label:<34} {r['n']:>6} {r['logloss']:>9.5f} {r['brier']:>9.5f} "
            f"{r['accuracy']:>9.4f} {roi:>8} {r['n_bets']:>7}"
        )
    return "\n".join(lines)
