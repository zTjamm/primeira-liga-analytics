"""Прогнозы на будущие матчи.

Обучаем на всей доступной истории (walk-forward не нужен — мы уже за его
пределами, истории «после» матча не существует), сохраняем вероятности 1X2,
тотала 2.5 и индивидуальных тоталов, а также объяснение прогноза в числах.

Запуск:
    python -m models.predict
"""
from __future__ import annotations

import json

import numpy as np
import polars as pl

from etl.config import CURRENT_SEASON, PROCESSED, REPORTS
from .dixon_coles import DixonColesModel
from .elo import EloModel
from .poisson import over_prob

DC_WINDOW_YEARS = 6
BLEND_ELO = 0.5


def fit_models(matches: list[dict], verbose: bool = True) -> tuple[EloModel, DixonColesModel]:
    elo = EloModel()
    season = None
    for m in matches:
        if m["season"] != season:
            if season is not None:
                elo.start_season(m["season"])
            season = m["season"]
        elo.update(m["home_id"], m["away_id"], m["fthg"], m["ftag"])

    teams = sorted({m["home_id"] for m in matches} | {m["away_id"] for m in matches})
    dc = DixonColesModel().fit(matches, teams)
    if verbose:
        print(f"Elo: обучен на {len(matches)} матчах, {len(teams)} команд")
        print(f"DC: rho={dc.rho:+.4f}, gamma={dc.home_adv:+.3f}, сошёлся={dc._converged}")
        top = dc.strength()[:5]
        print("     сильнейшие по (атака + оборона): "
              + ", ".join(f"{t['team']} {t['overall']:+.2f}" for t in top))
    return elo, dc


def predict_match(elo: EloModel, dc: DixonColesModel, home: str, away: str) -> dict:
    p_elo = elo.predict_1x2(home, away)
    p_dc = dc.predict_1x2(home, away)
    probs = BLEND_ELO * p_elo + (1 - BLEND_ELO) * p_dc

    matrix = dc.score_matrix(home, away)
    over, under = over_prob(matrix, 2.5)
    lam_h, lam_a = dc.lambdas(home, away)

    top = int(np.argmax(probs))
    return {
        "p_home": round(float(probs[0]), 4),
        "p_draw": round(float(probs[1]), 4),
        "p_away": round(float(probs[2]), 4),
        "p_home_elo": round(float(p_elo[0]), 4),
        "p_draw_elo": round(float(p_elo[1]), 4),
        "p_home_dc": round(float(p_dc[0]), 4),
        "p_draw_dc": round(float(p_dc[1]), 4),
        "prediction": ["H", "D", "A"][top],
        "confidence": round(float(probs[top]), 4),
        "over25": round(float(over), 4),
        "under25": round(float(under), 4),
        "xg_home": round(float(lam_h), 3),
        "xg_away": round(float(lam_a), 3),
        "elo_home": round(elo.ratings.get(home, 1500.0), 1),
        "elo_away": round(elo.ratings.get(away, 1500.0), 1),
        "elo_diff": round(elo.ratings.get(home, 1500.0) - elo.ratings.get(away, 1500.0), 1),
        "attack_home": round(dc.attack.get(home, 0.0), 3),
        "defence_home": round(dc.defence.get(home, 0.0), 3),
        "attack_away": round(dc.attack.get(away, 0.0), 3),
        "defence_away": round(dc.defence.get(away, 0.0), 3),
    }


def main() -> None:
    matches_df = pl.read_parquet(PROCESSED / "matches.parquet").sort("match_date")
    matches = matches_df.to_dicts()

    fixtures_path = PROCESSED / "fixtures.parquet"
    if not fixtures_path.exists():
        raise SystemExit("Нет fixtures.parquet. Запусти: python -m etl.run")
    fixtures = pl.read_parquet(fixtures_path)
    future = fixtures.filter(pl.col("status") != "FINISHED")
    if future.height == 0:
        print("Будущих матчей нет — обновлять нечего.")
        return

    elo, dc = fit_models(matches)
    print(f"DC: {dc.summary()}")

    rows = []
    for f in future.to_dicts():
        p = predict_match(elo, dc, f["home_id"], f["away_id"])
        rows.append({
            "fd_match_id": f["fd_match_id"],
            "date": str(f["match_date"]),
            "kickoff_utc": f["kickoff_utc"],
            "matchday": f["matchday"],
            "home_id": f["home_id"],
            "away_id": f["away_id"],
            "home_name": f["home_name"],
            "away_name": f["away_name"],
            **p,
        })
    rows.sort(key=lambda r: r["date"])

    out = PROCESSED / "predictions.json"
    out.write_text(json.dumps({
        "generated_for_season": CURRENT_SEASON,
        "history_matches": len(matches),
        "upcoming": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nПрогнозов: {len(rows)} → {out}")
    print(f"\n{'дата':<11} {'матч':<44} {'П1':>6} {'Х':>6} {'П2':>6}  {'тотал':>6}")
    print("-" * 90)
    for r in rows[:20]:
        name = f"{r['home_name']} — {r['away_name']}"
        tot = "ТБ2.5" if r["over25"] >= 0.5 else "ТМ2.5"
        print(f"{r['date']:<11} {name[:43]:<44} {r['p_home']:>6.3f} {r['p_draw']:>6.3f} "
              f"{r['p_away']:>6.3f}  {tot:>6} ({max(r['over25'], r['under25']):.3f})")
    if len(rows) > 20:
        print(f"… ещё {len(rows) - 20}")

    # Рейтинг команд — полезно для витрины
    REPORTS.joinpath("elo_table.json").write_text(
        json.dumps(elo.table(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    REPORTS.joinpath("dc_strength.json").write_text(
        json.dumps(dc.strength(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
