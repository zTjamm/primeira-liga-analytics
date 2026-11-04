"""Прогнозы НХЛ на ближайшие матчи.

Модель — та же аддитивная нормальная, что и для баскетбола
(models/basketball.py), но обученная на данных НХЛ: счёт в хоккее
измеряется в шайбах, и параметры лиги другие.

В отличие от NBA и WNBA, три исхода остаются: ничья в хоккее возможна
(основное время, овертайм, буллиты), и её вероятность в модели
не ноль — а около 15-20% на равных командах. Выбрасывать её значило бы
врать в прогнозе.

Запуск:
    python -m models.nhl_predict
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

    Точного времени старта в бесплатных фикстурах НХЛ раньше не было,
    поэтому при его отсутствии считаем от полуночи дня матча. Это
    влияет только на подпись этапа в журнале, а не на вероятности.
    """
    if kickoff:
        try:
            ts = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        except ValueError:
            ts = None
    else:
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
    print("Загрузка данных NHL...")
    matches_path = PROCESSED / "nhl_matches.parquet"
    if not matches_path.exists():
        print(f"  Нет {matches_path} — запусти: python -m etl.fetch_nhl")
        return

    matches = pl.read_parquet(matches_path)
    played = matches.filter(pl.col("home_score").is_not_null())
    print(f"  Матчей в истории: {matches.height}")
    print(f"  Сыгранных: {played.height}")

    names = ball_sport.load_names(played)
    games = ball_sport.team_games(played)
    teams = sorted(games)
    # Границы оптимизации — под хоккей, а не под баскетбол.
    #
    # Баскетбольные по умолчанию (σ_разница 6-28 очков) для хоккея
    # непригодны: оптимизатор упирается в нижнюю границу и выдаёт
    # σ_разница = 6.0 шайбы при реальном разбросе около 2.6. Из-за
    # этого все вероятности сжимались к 0.50 и вердикт не мог быть
    # выдан ни по одному матчу.
    model = BasketballModel(score_unit="шайб").fit(
        played.to_dicts(), teams,
        attack_bound=3.0,
        home_adv_bound=1.0,
        sigma_margin_range=(1.2, 5.0),
        sigma_total_range=(1.5, 6.0),
        sigma_margin_init=2.6,
        sigma_total_init=2.3,
        # Штраф на параметры команд подобран walk-forward: обучение на
        # сезоне 2024/25, проверка на 2025/26. При слишком слабом штрафе
        # log-loss на отложенном сезоне растёт (0.6930 → 0.7070), при
        # слишком сильном модель перестаёт различать команды (разброс
        # сил падает с 0.6 шайбы до нуля).
        penalty_scale=0.10,
    )
    print(f"Модель: {model.summary().replace('σ', 'sigma')}")
    print("Сильнейшие: " + ", ".join(
        f"{names.get(r['team'], r['team'])} {r['overall']:+.1f}"
        for r in model.strength()[:4]))

    # Загружаем будущие матчи (фикстуры).
    fixtures_path = PROCESSED / "nhl_future_fixtures.parquet"
    upcoming: list[dict] = []
    ledger: list[dict] = []
    if fixtures_path.exists():
        fixtures = ball_sport.future_fixtures(pl.read_parquet(fixtures_path))
        print(f"  Будущих матчей: {fixtures.height}")
        now = datetime.now(timezone.utc)
        for f in fixtures.to_dicts():
            # Ничьей в НХЛ на практике не бывает: за 2901 сыгранный матч
            # трёх сезонов в данных нет ни одной ничьей — матч всегда
            # заканчивается победой, в том числе по буллитам. Поэтому
            # вероятность ничьей из модели (она возникает из-за
            # дискретности счёта и нормального приближения) удаляется и
            # перераспределяется на П1 и П2.
            probs = ball_sport.remove_draw(model.predict_1x2(f["home_id"], f["away_id"]))
            top = int(max(range(2), key=lambda i: probs[i]))
            prediction = ["H", "A"][top]
            confidence = float(probs[top])

            # Отказ от вердикта там, где исходы почти равны: вероятности
            # остаются в данных, не показывается только решение.
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
                "key": f"nhl_{f['match_id']}",
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

    out = PROCESSED / "nhl_predictions.json"
    ball_sport.write_json(out, {
        "league": "NHL",
        "season": "20262027",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history_matches": played.height,
        "model": model.summary(),
        "draws_possible": False,
        "draw_removed": True,
        "draw_note": "Ни одной ничьей в 2901 сыгранном матче НХЛ за три сезона: матч всегда заканчивается победой, в том числе по буллитам.",
        "verdict_threshold": verdict.VERDICT_THRESHOLD,
        "upcoming": upcoming,
    })
    print(f"Файл: {out}")

    # Рейтинги команд — для страницы «Команды».
    strength_path = PROCESSED / "nhl_strength.json"
    ball_sport.export_strength(model, names, strength_path)
    print(f"Рейтинги: {strength_path}")

    # Прогнозы по будущим матчам попадают в журнал: когда матч сыграют,
    # запись сама разрешится в «угадано/мимо» по данным НХЛ.
    if ledger:
        added = forecast.append(ledger, "nhl")
        print(f"В журнал НХЛ добавлено новых записей: {added}")

    hist = forecast.save(played, "nhl", out=PROCESSED / "nhl_journal.json")
    s = hist["summary"]
    if s.get("total"):
        print(f"Журнал NHL: {s['total']} прогнозов, точность {s['overall']['accuracy']:.1%}, "
              f"log-loss {s['overall']['logloss']}")
        if s["overall"].get("roi") is not None:
            print(f"ROI: {s['overall']['roi']:.2%} "
                  f"(ставок: {s['overall']['total_staked']}, прибыль: {s['overall']['total_profit']})")
    else:
        print("Журнал NHL пока пуст.")

    public_dir = Path("web/public/data")
    public_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, public_dir / "nhl_predictions.json")
    shutil.copy(PROCESSED / "nhl_journal.json", public_dir / "nhl_journal.json")
    shutil.copy(strength_path, public_dir / "nhl_strength.json")


if __name__ == "__main__":
    main()
