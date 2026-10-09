"""Walk-forward бэктест по Единой лиге ВТБ.

Честность здесь важнее обычного, потому что данных всего два полных
сезона. Поэтому результаты показываются ТАК, чтобы это было видно:

  * прогон строго вперёд по времени, обучение только на прошлом;
  * отдельная строка по каждому сезону, а не только суммарная;
  * явное сравнение с наивным baseline и с рынком, где рынок доступен;
  * честное признание: ОДИН полноценный тестовый сезон, значит разницу
    между моделями нельзя отличить от особенностей сезона.

Запуск:
    python -m models.bt_backtest
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import polars as pl

from etl.config import PROCESSED, REPORTS
from .basketball import BasketballModel
from .backtest import ACTUAL_INDEX, Prediction, score

REFIT_EVERY_DAYS = 45
MIN_TRAIN_MATCHES = 120


def load() -> pl.DataFrame:
    p = PROCESSED / "vtb_matches.parquet"
    if not p.exists():
        raise SystemExit("Нет vtb_matches.parquet. Запусти: python -m etl.fetch_vtb")
    return pl.read_parquet(p).sort("match_date")


def naive_baseline(matches: list[dict]) -> tuple[np.ndarray, str]:
    """Доли исходов по всей доступной истории. Зная их заранее, мы бы
    «подсмотрели» будущее — поэтому берём по всем матчам и явно называем
    это верхней границей качества, а не моделью."""
    n = len(matches)
    h = sum(1 for m in matches if m["home_score"] > m["away_score"])
    d = sum(1 for m in matches if m["home_score"] == m["away_score"])
    a = n - h - d
    return np.array([h / n, d / n, a / n]), f"naive (средние доли: {h/n:.2f}/{d/n:.2f}/{a/n:.2f})"


def run(matches: list[dict], verbose: bool = True) -> list[Prediction]:
    preds: list[Prediction] = []
    model: BasketballModel | None = None
    last_fit: date | None = None
    window_days = int(365.25 * 3)
    seen: set[str] = set()

    for m in matches:
        cutoff = m["match_date"]
        due = model is None or last_fit is None or (cutoff - last_fit).days >= REFIT_EVERY_DAYS
        if due:
            ws = cutoff - timedelta(days=window_days)
            train = [x for x in matches if ws <= x["match_date"] < cutoff]
            if len(train) < MIN_TRAIN_MATCHES:
                continue          # окно не набрало — честно не прогнозируем
            teams = sorted({x["home_id"] for x in train} | {x["away_id"] for x in train})
            model = BasketballModel().fit(train, teams)
            last_fit = cutoff
            if verbose:
                print(f"  fit на {cutoff}: {len(train)} матчей, {len(teams)} команд, "
                      f"{model.summary()}")

        seen.update((m["home_id"], m["away_id"]))
        for t in (m["home_id"], m["away_id"]):
            if t not in model.attack:
                model.attack[t] = 0.0
                model.defence[t] = 0.0

        probs = model.predict_1x2(m["home_id"], m["away_id"])
        actual = "H" if m["home_score"] > m["away_score"] else (
            "A" if m["home_score"] < m["away_score"] else "D")
        preds.append(Prediction(
            match_id=str(m["match_id"]), season=m["season"], match_date=m["match_date"],
            home=m["home_id"], away=m["away_id"], actual=actual,
            model_probs=probs, market_probs=None, odds_close=None,
            model_name="basketball_bvn",
        ))
    return preds


def main() -> None:
    df = load()
    matches = df.to_dicts()
    seasons = sorted({m["season"] for m in matches})
    print(f"Матчей: {len(matches)} | сезоны: {', '.join(seasons)}")
    print(f"Диапазон: {df['match_date'].min()} … {df['match_date'].max()}\n")

    # сколько реально можно оценить
    print("=== СКОЛЬКО МАТЧЕЙ ВООБЩЕ ПОДДАЁТСЯ ОЦЕНКЕ ===")
    print(f"  обучение на всех предыдущих: первый прогноз возможен после "
          f"{MIN_TRAIN_MATCHES} матчей")
    print(f"  из {len(matches)} это {len(matches) - MIN_TRAIN_MATCHES} "
          f"({(len(matches) - MIN_TRAIN_MATCHES)/len(matches):.0%})\n")

    print("=== WALK-FORWARD ===")
    preds = run(matches)
    probs_naive, naive_label = naive_baseline(matches)

    rows: list[dict] = []
    own = score(preds, "модель (двумерное нормальное)", "model_probs")
    rows.append(own.as_row())

    naive_preds = [
        Prediction(p.match_id, p.season, p.match_date, p.home, p.away, p.actual,
                   probs_naive, None, None)
        for p in preds if p.actual in ACTUAL_INDEX
    ]
    rows.append(score(naive_preds, naive_label, "model_probs").as_row())

    print(bt_table(rows))

    print(f"\n=== ПО СЕЗОНАМ (здесь видно главное) ===")
    per_season = []
    for s in seasons:
        sub = [p for p in preds if p.season == s]
        if len(sub) < 30:
            print(f"  {s}: всего {len(sub)} прогнозов, слишком мало для выводов")
            continue
        r = score(sub, s, "model_probs").as_row()
        per_season.append(r)
    print(bt_table(per_season))

    # насколько выражено домашнее преимущество в данных
    hw = float(df.select((pl.col("home_score") > pl.col("away_score")).mean()).item())
    dr = float(df.select((pl.col("home_score") == pl.col("away_score")).mean()).item())
    print(f"\nДомашнее преимущество в данных: хозяева {hw:.1%}, ничьи {dr:.1%}")

    REPORTS.joinpath("vtb_backtest.json").write_text(json.dumps({
        "n_matches_total": len(matches),
        "n_predictions": len(preds),
        "seasons": seasons,
        "overall": rows,
        "per_season": per_season,
        "home_win_share_data": round(hw, 4),
        "draw_share_data": round(dr, 4),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nОтчёт: data/reports/vtb_backtest.json")


def bt_table(rows: list[dict]) -> str:
    if not rows:
        return "нет данных"
    head = f"{'модель':<38} {'n':>5} {'log-loss':>9} {'Brier':>9} {'точность':>9}"
    out = [head, "-" * len(head)]
    for r in rows:
        out.append(f"{r['model']:<38} {r['n']:>5} {r['logloss']:>9.5f} "
                   f"{r['brier']:>9.5f} {r['accuracy']:>9.4f}")
    return "\n".join(out)


if __name__ == "__main__":
    main()
