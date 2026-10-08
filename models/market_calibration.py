"""Проверка качества снятия маржи.

Снятие маржи — тот этап, где легко незаметно испортить всё сравнение с рынком.
Поэтому методы проверяются не «на глаз», а сравнением средних предсказанных
вероятностей с фактическими частотами исходов и логистикой.

Запуск:
    python -m models.market_calibration
"""
from __future__ import annotations

import json

import numpy as np
import polars as pl

from etl.config import PROCESSED, REPORTS
from . import market
from .backtest import ACTUAL_INDEX, Prediction, score

METHODS = ["proportional", "power", "shin"]


def run() -> dict:
    df = pl.read_parquet(PROCESSED / "matches.parquet")
    sub = df.filter(pl.col("odds_close").list.len() == 3)
    if sub.height == 0:
        raise SystemExit("Нет матчей с закрывающими коэффициентами")

    rows = sub.to_dicts()
    actual = np.zeros(3)
    for r in rows:
        actual[ACTUAL_INDEX[r["ftr"]]] += 1
    actual /= actual.sum()

    report: dict = {
        "n_matches": sub.height,
        "actual_freq": {"H": round(actual[0], 4), "D": round(actual[1], 4), "A": round(actual[2], 4)},
        "methods": {},
    }

    print(f"Матчей: {sub.height}")
    print(f"Фактические частоты: H={actual[0]:.4f}  D={actual[1]:.4f}  A={actual[2]:.4f}\n")
    print(f"{'метод':>14} {'log-loss':>9} {'Brier':>8} {'точн.':>7} {'средн.маржа':>12}   средние вероятности")
    print("-" * 92)

    for method in METHODS:
        preds = []
        margins = []
        for r in rows:
            d = market.devig(r["odds_close"], method)
            if d is None:
                continue
            margins.append(d.overround)
            preds.append(
                Prediction(r["match_id"], r["season"], r["match_date"], r["home_id"], r["away_id"],
                           r["ftr"], d.probs, d.probs, np.asarray(r["odds_close"]), method)
            )
        m = score(preds, method, "market_probs")
        mean_p = np.array([p.model_probs for p in preds]).mean(0)
        gap = float(np.abs(mean_p - actual).sum())
        report["methods"][method] = {
            "logloss": round(m.logloss, 5),
            "brier": round(m.brier, 5),
            "accuracy": round(m.accuracy, 4),
            "mean_probs": {"H": round(float(mean_p[0]), 4), "D": round(float(mean_p[1]), 4),
                           "A": round(float(mean_p[2]), 4)},
            "calibration_gap": round(gap, 5),
            "mean_overround": round(float(np.mean(margins)), 5),
        }
        print(f"{method:>14} {m.logloss:>9.5f} {m.brier:>8.5f} {m.accuracy:>7.4f} "
              f"{np.mean(margins):>12.4f}   H={mean_p[0]:.4f} D={mean_p[1]:.4f} A={mean_p[2]:.4f}")

    best = min(report["methods"].items(), key=lambda kv: kv[1]["logloss"])
    report["chosen"] = best[0]
    print(f"\nЛучший по log-loss: {best[0]} ({best[1]['logloss']:.5f})")
    print("Чем меньше calibration_gap (сумма отклонений средних вероятностей), тем лучше метод.")

    REPORTS.joinpath("market_calibration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    run()
