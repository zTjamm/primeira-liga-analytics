"""Прогнозы на будущие матчи.

Обучаем на всей доступной истории (walk-forward не нужен — мы уже за его
пределами, истории «после» матча не существует), сохраняем вероятности 1X2,
тотала 2.5 и индивидуальных тоталов, а также объяснение прогноза в числах.

Запуск:
    python -m models.predict
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

from etl.config import CURRENT_SEASON, PROCESSED, REPORTS
from . import forecast, verdict, weather
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
        # Нет расписания, но сверять старые прогнозы с результатами всё равно
        # полезно: CSV обновился, и вердикты по сыгранным матчам посчитаются.
        # Раньше здесь был выход с ошибкой, из-за чего падал весь пайплайн.
        print("! Нет fixtures.parquet — новые прогнозы не считаем, "
              "но сверяем прошлые с результатами")
        _finish(matches_df)
        return

    fixtures = pl.read_parquet(fixtures_path)
    future = fixtures.filter(pl.col("status") != "FINISHED")
    if future.height == 0:
        print("Будущих матчей нет — обновлять нечего.")
        _finish(matches_df)
        return

    elo, dc = fit_models(matches)
    print(f"DC: {dc.summary()}")

    # Погода — fail-soft: недоступность API не должна ронять предсказания
    wx: dict = {}
    try:
        wx = weather.fetch_window(days_ahead=7)
        weather.save(wx)
        ok = sum(1 for v in wx.values() if v)
        print(f"Погода: {ok}/{len(wx)} стадионов")
    except Exception as exc:  # noqa: BLE001
        print(f"Погода недоступна ({type(exc).__name__}) — прогнозы считаем без неё")

    # Сколько матчей каждая команда сыграла в обучающей выборке DC: нужно,
    # чтобы отличать уверенный прогноз от вывода по почти незнакомому клубу.
    window_start = max(m["match_date"] for m in matches) - timedelta(
        days=int(365.25 * DC_WINDOW_YEARS))
    games_in_window: dict[str, int] = {}
    for m in matches:
        if m["match_date"] < window_start:
            continue
        for side in (m["home_id"], m["away_id"]):
            games_in_window[side] = games_in_window.get(side, 0) + 1

    now = datetime.now(timezone.utc)
    rows = []
    ledger = []
    for f in future.to_dicts():
        p = predict_match(elo, dc, f["home_id"], f["away_id"])
        kickoff = datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
        hours_before = (kickoff - now).total_seconds() / 3600.0
        cond = weather.conditions_at(wx, f["home_id"], f["kickoff_utc"])

        # Отказ от вердикта там, где исходы почти равны. Вероятности при
        # этом остаются в данных: не показывается решение, а не расчёт.
        team_games = min(games_in_window.get(f["home_id"], 0),
                         games_in_window.get(f["away_id"], 0))
        verdict_given, no_reason = verdict.verdict(p["confidence"], team_games)

        row = {
            "fd_match_id": f["fd_match_id"],
            "verdict_given": verdict_given,
            "verdict_reason": no_reason,
            "team_games": team_games,
            "date": str(f["match_date"]),
            "kickoff_utc": f["kickoff_utc"],
            "matchday": f["matchday"],
            "home_id": f["home_id"],
            "away_id": f["away_id"],
            "home_name": f["home_name"],
            "away_name": f["away_name"],
            "hours_before": round(hours_before, 2),
            "stage": forecast.stage_for(hours_before),
            "weather": cond,
            **p,
        }
        rows.append(row)

        ledger.append({
            "key": str(f["fd_match_id"]),
            "date": str(f["match_date"]),
            "kickoff": f["kickoff_utc"],
            "matchday": f["matchday"],
            "home_id": f["home_id"],
            "away_id": f["away_id"],
            "home_name": f["home_name"],
            "away_name": f["away_name"],
            "generated_at": now.isoformat(timespec="seconds"),
            "hours_before": round(hours_before, 2),
            "stage": row["stage"],
            "p_home": p["p_home"], "p_draw": p["p_draw"], "p_away": p["p_away"],
            "extra": {"over25": p["over25"], "xg_home": p["xg_home"],
                      "xg_away": p["xg_away"], "weather": cond,
                      # Отметка в журнале нужна, чтобы честно считать точность
                      # именно по выданным вердиктам: вердикт выдаётся не всем,
                      # и точность без этой метки была бы вводить в заблуждение.
                      "verdict_given": verdict_given,
                      "verdict_reason": no_reason},
        })
    rows.sort(key=lambda r: r["kickoff_utc"])

    out = PROCESSED / "predictions.json"
    out.write_text(json.dumps({
        "generated_for_season": CURRENT_SEASON,
        "history_matches": len(matches),
        "generated_at": now.isoformat(timespec="seconds"),
        "upcoming": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    added = forecast.append(ledger)

    print(f"\nПрогнозов: {len(rows)} → {out}")
    print(f"В журнал добавлено новых записей: {added} (всего этапов: {len(ledger)})")
    print(f"\n{'дата':<11} {'матч':<40} {'П1':>6} {'Х':>6} {'П2':>6} {'этап':>7} {'до матча':>9}")
    print("-" * 95)
    for r in rows[:14]:
        name = f"{r['home_name']} — {r['away_name']}"
        print(f"{r['date']:<11} {name[:39]:<40} {r['p_home']:>6.3f} {r['p_draw']:>6.3f} "
              f"{r['p_away']:>6.3f} {r['stage']:>7} {r['hours_before']:>8.1f}ч")
    if len(rows) > 14:
        print(f"… ещё {len(rows) - 14}")

    # Рейтинг команд — полезно для витрины
    REPORTS.joinpath("elo_table.json").write_text(
        json.dumps(elo.table(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    REPORTS.joinpath("dc_strength.json").write_text(
        json.dumps(dc.strength(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _finish(matches_df)


def _finish(matches_df: pl.DataFrame) -> None:
    """Сводит журнал в отчёт с вердиктами по сыгранным матчам."""
    hist = forecast.save(matches_df, "football")
    s = hist["summary"]
    if s.get("total"):
        o = s["overall"]
        print(f"\nЖурнал: {s['total']} сыгранных прогнозов, точность {o['accuracy']:.1%}, "
              f"log-loss {o['logloss']}")
        if s["multi_stage"]:
            print(f"  с несколькими этапами: {s['multi_stage']}, "
                  f"мнение изменилось: {s['flipped']}")
    else:
        print("\nЖурнал пуст — сыгранных прогнозов пока нет. "
              "Как только матчи будут сыграны, здесь появятся пометки.")
    print("Отчёт: data/processed/football_journal.json")


if __name__ == "__main__":
    main()
