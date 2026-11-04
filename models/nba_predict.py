"""Прогнозы NBA на ближайшие матчи.

Модель — та же аддитивная нормальная, что и для баскетбола
(models/basketball.py), но обученная на данных NBA: своя средняя
длительность матча, своё домашнее преимущество, свои параметры команд.

Ничьей в NBA не бывает, поэтому вероятность ничьей из модели (около
0.5%, она возникает из-за дискретности счёта) удаляется и
перераспределяется на П1 и П2 пропорционально.

Запуск:
    python -m models.nba_predict
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from etl.config import PROCESSED
from models import ball_sport, forecast, verdict
from models.basketball import BasketballModel


def _hours_before(kickoff: str | None, match_date: str, now: datetime) -> float:
    """Сколько часов осталось до матча.

    Если точного времени старта в фикстурах нет, считаем от полуночи
    дня матча: на подпись этапа в журнале это влияет, на вероятности —
    нет.
    """
    ts = None
    if kickoff:
        try:
            ts = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        except ValueError:
            ts = None
    if ts is None:
        try:
            ts = datetime.fromisoformat(f"{match_date}T00:00:00+00:00")
        except ValueError:
            return 72.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max((ts - now).total_seconds() / 3600.0, 0.0)


def main() -> None:
    print("Загрузка данных NBA...")
    matches_path = PROCESSED / "nba_matches.parquet"
    if not matches_path.exists():
        print(f"  Нет {matches_path} — запусти: python -m etl.fetch_nba")
        return

    matches = pl.read_parquet(matches_path)
    played = matches.filter(pl.col("home_score").is_not_null())
    print(f"  Матчей в истории: {matches.height}")
    print(f"  Сыгранных: {played.height}")

    names = ball_sport.load_names(played)
    games = ball_sport.team_games(played)
    teams = sorted(games)
    model = BasketballModel().fit(played.to_dicts(), teams)
    print(f"Модель: {model.summary().replace('σ', 'sigma')}")
    print("Сильнейшие: " + ", ".join(
        f"{names.get(r['team'], r['team'])} {r['overall']:+.1f}"
        for r in model.strength()[:4]))

    fixtures_path = PROCESSED / "nba_future_fixtures.parquet"
    upcoming: list[dict] = []
    ledger: list[dict] = []
    if fixtures_path.exists():
        fixtures = ball_sport.future_fixtures(pl.read_parquet(fixtures_path))
        print(f"  Будущих матчей: {fixtures.height}")
        now = datetime.now(timezone.utc)
        for f in fixtures.to_dicts():
            probs = ball_sport.remove_draw(model.predict_1x2(f["home_id"], f["away_id"]))
            top = int(max(range(2), key=lambda i: probs[i]))
            prediction = ["H", "A"][top]
            confidence = float(probs[top])

            team_games_min = min(games.get(f["home_id"], 0),
                                 games.get(f["away_id"], 0))
            ok, reason = verdict.verdict(confidence, team_games_min)

            match_date = str(f["match_date"])
            kickoff = f.get("kickoff_utc")
            upcoming.append({
                "match_id": f["match_id"],
                "date": match_date,
                "kickoff_utc": kickoff,
                "home_id": f["home_id"],
                "away_id": f["away_id"],
                "home_name": f["home_name"],
                "away_name": f["away_name"],
                "p_home": round(float(probs[0]), 4),
                "p_draw": 0.0,
                "p_away": round(float(probs[1]), 4),
                "prediction": prediction,
                "confidence": round(confidence, 4),
                "verdict_given": ok,
                "verdict_reason": reason,
            })

            hours = _hours_before(kickoff, match_date, now)
            ledger.append({
                "key": f"nba_{f['match_id']}",
                "date": match_date,
                "kickoff": kickoff,
                "matchday": None,
                "home_id": f["home_id"],
                "away_id": f["away_id"],
                "home_name": f["home_name"],
                "away_name": f["away_name"],
                "generated_at": now.isoformat(timespec="seconds"),
                "hours_before": round(hours, 2),
                "stage": forecast.stage_for(hours),
                "p_home": round(float(probs[0]), 4),
                "p_draw": 0.0,
                "p_away": round(float(probs[1]), 4),
                "prediction": prediction,
                "odds_home": None,
                "odds_draw": None,
                "odds_away": None,
                "stake": None,
                "extra": {
                    "verdict_given": ok,
                    "verdict_reason": reason,
                },
            })
    else:
        print("  Будущих матчей нет")

    out = PROCESSED / "nba_predictions.json"
    ball_sport.write_json(out, {
        "league": "NBA",
        "season": "2026-27",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history_matches": played.height,
        "model": model.summary(),
        "draws_possible": False,
        "draw_removed": True,
        "verdict_threshold": verdict.VERDICT_THRESHOLD,
        "upcoming": upcoming,
    })
    print(f"Файл: {out}")

    strength_path = PROCESSED / "nba_strength.json"
    ball_sport.export_strength(model, names, strength_path)
    print(f"Рейтинги: {strength_path}")

    if ledger:
        added = forecast.append(ledger, "nba")
        print(f"В журнал NBA добавлено новых записей: {added}")

    hist = forecast.save(played, "nba", out=PROCESSED / "nba_journal.json")
    s = hist["summary"]
    if s.get("total"):
        print(f"Журнал NBA: {s['total']} прогнозов, точность {s['overall']['accuracy']:.1%}, "
              f"log-loss {s['overall']['logloss']}")
        if s["overall"].get("roi") is not None:
            print(f"ROI: {s['overall']['roi']:.2%} "
                  f"(ставок: {s['overall']['total_staked']}, прибыль: {s['overall']['total_profit']})")
    else:
        print("Журнал NBA пока пуст.")

    public_dir = Path("web/public/data")
    public_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, public_dir / "nba_predictions.json")
    shutil.copy(PROCESSED / "nba_journal.json", public_dir / "nba_journal.json")
    shutil.copy(strength_path, public_dir / "nba_strength.json")


if __name__ == "__main__":
    main()
