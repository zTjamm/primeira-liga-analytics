"""Walk-forward бэктест с честным разделением на подбор и оценку.

Порядок работы:
  1. Прогоняем Elo и Dixon-Coles по всей истории строго вперёд по времени.
  2. Вес бленда подбираем на валидационных сезонах (все, кроме последних трёх).
  3. Итоговые цифры считаем на тестовых сезонах — их модель не видела ни при
     подборе веса, ни при настройке.

Почему так: подобрать вес на тесте и потом сообщить метрику того же теста —
это утечка, и она даёт красивые, неверные числа. Разница обычно около
0.01-0.02 log-loss, то есть как раз порядок самого эффекта.

Запуск:
    python -m models.backtest_runner
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import polars as pl

from etl.config import PROCESSED, REPORTS
from . import backtest as bt
from . import market
from .dixon_coles import DixonColesModel
from .elo import EloModel

REFIT_EVERY_DAYS = 30
MIN_TRAIN_MATCHES = 800
DC_WINDOW_YEARS = 6
N_TEST_SEASONS = 3
WEIGHT_GRID = [round(x, 2) for x in np.arange(0.0, 1.01, 0.05)]


def load() -> pl.DataFrame:
    path = PROCESSED / "matches.parquet"
    if not path.exists():
        raise SystemExit("Нет matches.parquet. Сначала: python -m etl.run")
    return pl.read_parquet(path).sort("match_date")


def _prediction(m: dict, probs: np.ndarray, name: str) -> bt.Prediction:
    odds = m.get("odds_close")
    mk = market.devig(odds) if odds else None
    return bt.Prediction(
        match_id=m["match_id"], season=m["season"], match_date=m["match_date"],
        home=m["home_id"], away=m["away_id"], actual=m["ftr"],
        model_probs=probs,
        market_probs=mk.probs if mk else None,
        odds_close=np.asarray(odds, dtype=float) if odds else None,
        model_name=name,
    )


def run_elo(matches: list[dict]) -> list[bt.Prediction]:
    model = EloModel()
    preds: list[bt.Prediction] = []
    season = None
    for m in matches:
        if m["season"] != season:
            if season is not None:
                model.start_season(m["season"])
            season = m["season"]
        preds.append(_prediction(m, model.predict_1x2(m["home_id"], m["away_id"]), "elo"))
        model.update(m["home_id"], m["away_id"], m["fthg"], m["ftag"])
    return preds


def run_dixon_coles(matches: list[dict], verbose: bool = True) -> list[bt.Prediction]:
    preds: list[bt.Prediction] = []
    last_fit: date | None = None
    model: DixonColesModel | None = None
    window_days = int(365.25 * DC_WINDOW_YEARS)

    for m in matches:
        cutoff = m["match_date"]
        if model is None or last_fit is None or (cutoff - last_fit).days >= REFIT_EVERY_DAYS:
            window_start = cutoff - timedelta(days=window_days)
            train = [x for x in matches if window_start <= x["match_date"] < cutoff]
            if len(train) < MIN_TRAIN_MATCHES:
                continue  # окно обучения ещё не набрало массу — честно не прогнозируем
            teams = sorted({x["home_id"] for x in train} | {x["away_id"] for x in train})
            model = DixonColesModel().fit(train, teams)
            last_fit = cutoff
            if verbose:
                print(f"  DC: обучение на {cutoff} — {len(train)} матчей, {len(teams)} команд, "
                      f"gamma={model.home_adv:+.3f}, rho={model.rho:+.4f}, "
                      f"база={model.mean_home_goals:.2f}/{model.mean_away_goals:.2f}")

        for t in (m["home_id"], m["away_id"]):
            # команда, впервые появившаяся после обучения, получает нейтральные параметры
            model.attack.setdefault(t, 0.0)
            model.defence.setdefault(t, 0.0)
        preds.append(_prediction(m, model.predict_1x2(m["home_id"], m["away_id"]), "dixon_coles"))

    return preds


def blend(elo_preds: list[bt.Prediction], dc_preds: list[bt.Prediction],
          w_dc: float) -> list[bt.Prediction]:
    """Бленд с весом w_dc на Dixon-Coles. Совпадение по match_id."""
    index = {p.match_id: p for p in dc_preds}
    out: list[bt.Prediction] = []
    for p in elo_preds:
        q = index.get(p.match_id)
        if q is None:
            continue
        out.append(
            bt.Prediction(
                match_id=p.match_id, season=p.season, match_date=p.match_date,
                home=p.home, away=p.away, actual=p.actual,
                model_probs=w_dc * q.model_probs + (1 - w_dc) * p.model_probs,
                market_probs=p.market_probs, odds_close=p.odds_close,
                model_name=f"blend(dc={w_dc:.2f})",
            )
        )
    return out


def main() -> None:
    df = load()
    matches = df.to_dicts()
    print(f"Матчей: {len(matches)}  ({df['match_date'].min()} … {df['match_date'].max()})")

    seasons_with_odds = [
        s for s in sorted(df["season"].unique().to_list())
        if df.filter(pl.col("season") == s)["has_closing"].sum() > 100
    ]
    if len(seasons_with_odds) < N_TEST_SEASONS + 2:
        raise SystemExit("Мало сезонов с коэффициентами для честного разделения")
    test_seasons = seasons_with_odds[-N_TEST_SEASONS:]
    val_seasons = seasons_with_odds[:-N_TEST_SEASONS]

    print(f"Валидация (подбор веса): {', '.join(val_seasons)}")
    print(f"Тест     (итоговые цифры): {', '.join(test_seasons)}\n")

    print("Elo …")
    elo_preds = run_elo(matches)
    print("Dixon-Coles …")
    dc_preds = run_dixon_coles(matches)

    # --- подбор веса бленда на валидации --------------------------------
    print("\n=== Подбор веса бленда (только валидация) ===")
    print(f"{'w(dc)':>6} {'log-loss':>9}")
    grid: list[tuple[float, float]] = []
    for w in WEIGHT_GRID:
        preds = blend(elo_preds, dc_preds, w)
        sub = [p for p in preds if p.season in val_seasons]
        m = bt.score(sub, "b", "model_probs")
        grid.append((w, m.logloss))
        print(f"{w:>6.2f} {m.logloss:>9.5f}")
    best_w = min(grid, key=lambda t: t[1])[0]
    print(f"→ выбран вес на Dixon-Coles: {best_w:.2f}")

    # --- итоговые метрики на тесте --------------------------------------
    test_blend = blend(elo_preds, dc_preds, best_w)
    rows: list[dict] = []

    def add(preds: list[bt.Prediction], name: str, seasons: list[str]) -> None:
        sub = [p for p in preds if p.season in seasons]
        rows.append(bt.score(sub, name, "model_probs").as_row())

    add(elo_preds, "elo", test_seasons)
    add(dc_preds, "dixon_coles", test_seasons)
    add(test_blend, f"blend (w_dc={best_w:.2f}, подобрано на валидации)", test_seasons)
    rows.append(bt.naive_baseline([p for p in elo_preds if p.season in test_seasons]).as_row())
    rows.append(bt.empirical_baseline([p for p in elo_preds if p.season in test_seasons]).as_row())
    # рынок меряем ровно на тех же матчах, иначе сравнение нечестное
    rows.append(
        bt.score([p for p in test_blend if p.season in test_seasons and p.market_probs is not None],
                 "market (no-vig, closing)", "market_probs").as_row()
    )

    print(f"\n=== Тест: {', '.join(test_seasons)} ({len([p for p in test_blend if p.season in test_seasons])} матчей) ===")
    print(bt.format_table(rows))

    best_model = min((r for r in rows if r["model"].startswith(("elo", "dixon", "blend"))),
                     key=lambda r: r["logloss"])
    market_row = next(r for r in rows if r["model"].startswith("market"))
    gap = best_model["logloss"] - market_row["logloss"]
    print(f"\nЛучшая своя модель: {best_model['model']} — {best_model['logloss']:.5f}")
    print(f"Рынок:                                {market_row['logloss']:.5f}")
    print(f"Разрыв: {gap:+.5f} " + ("(мы хуже рынка)" if gap > 0 else "(мы лучше рынка)"))

    # --- поSeasonно, для понимания стабильности -----------------------
    print(f"\n=== По сезонам (тест) ===")
    per_season: list[dict] = []
    for s in test_seasons:
        for name, preds in [("blend", test_blend), ("market", test_blend)]:
            sub = [p for p in preds if p.season == s]
            if name == "market":
                r = bt.score([p for p in sub if p.market_probs is not None], "market", "market_probs").as_row()
            else:
                r = bt.score(sub, f"blend {s}", "model_probs").as_row()
            r["season"] = s
            per_season.append(r)
    print(bt.format_table(per_season, key="season"))

    REPORTS.joinpath("backtest.json").write_text(
        json.dumps({
            "validation_seasons": val_seasons,
            "test_seasons": test_seasons,
            "chosen_weight_dc": best_w,
            "weight_grid": [{"w": w, "logloss": v} for w, v in grid],
            "test": rows,
            "test_per_season": per_season,
        }, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8",
    )
    print("\nОтчёт: data/reports/backtest.json")


if __name__ == "__main__":
    main()
