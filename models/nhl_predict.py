"""Прогнозы НХЛ на ближайшие матчи.

Модель — отдельная для хоккея (models/hockey.py), а не общая
баскетбольная: счёт в хоккее измеряется в шайбах, он дискретный, а
матч состоит из основного времени, овертайма и буллитов.

Почему это важно. Общая аддитивная нормальная модель даёт для хоккея
около 15% вероятности ничьей, потому что разность двух пуассонов
(«Скеллам») имеет ненулевой пик в нуле. В реальности ничьих в
ИТОГОВОМ счёте НХЛ не бывает вообще: за девять сезонов регулярного
чемпионата их ровно ноль (счёт — в отчёте data/reports/nhl_ingest.json,
поле draws). Такую ничью приходилось выбрасывать и
перераспределять, а это ломает калибровку.

Хоккейная модель устроена честнее: счёт — пуассоновский счётчик,
овертайм играется только при ничьей в основное время, а оставшаяся
после овертайма ничья уходит в буллитную серию, победитель которой
определяется жребием. Поэтому ничьи в прогнозе нет by construction, а
не потому, что её выбросили, и доля буллитных серий получается
правдоподобной (единицы процентов), а не 15%.

Гиперпараметры модели (априор силы команды и горизонт затухания
весов) выбраны walk-forward в models/nhl_compare.py; отчёт —
data/reports/nhl_model_compare.json.

Запуск:
    python -m models.nhl_predict
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from etl.config import PROCESSED
from models import ball_sport, forecast, temperature, verdict
from models.hockey import (HockeyModel, load_periods, production_config,
                           production_tie_boost)


def _hours_before(kickoff: str | None, match_date: str, now: datetime) -> float:
    """Сколько часов осталось до матча.

    Время старта берётся из API (оно в UTC), и матчи без него — редкий
    случай у архивных сезонов — считаем от полуночи дня матча. На
    подпись этапа в журнале это влияет, на вероятности — нет.
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


def _upcoming_matches(matches: pl.DataFrame, now: datetime) -> list[dict]:
    """Матчи, которые ещё не начались.

    Логика общая для НХЛ, NBA и WNBA и лежит в models/ball_sport.py —
    здесь осталась тонкая обёртка, чтобы старые вызовы и тесты работали.
    """
    return ball_sport.upcoming_matches(matches, now)


def _season_label(matches: pl.DataFrame, fixtures: list[dict]) -> str:
    """Сезон, к которому относятся будущие матчи."""
    return ball_sport.season_label(matches, fixtures)


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

    scale, half_life = production_config()
    periods = load_periods()
    tie_boost = production_tie_boost()
    print(f"Разбивка по периодам: {len(periods)} матчей")
    print(f"Поправка на избыток ничьих (κ): "
          f"{'применяется' if tie_boost else 'не применяется'}")
    model = HockeyModel().fit(played.to_dicts(), teams,
                               strength_scale=scale,
                               half_life_days=half_life,
                               periods=periods,
                               use_tie_boost=tie_boost)
    print(f"Модель: {model.summary()}")
    print("Сильнейшие: " + ", ".join(
        f"{names.get(r['team'], r['team'])} {r['overall']:+.2f}"
        for r in model.strength()[:4]))

    # Температура из отчёта калибровки. None — законная ситуация: отчёта
    # ещё нет либо проверка показала, что калибровка не помогает. Тогда
    # работают сырые вероятности, и в файле это подписывается явно.
    calib_t = temperature.load_temperature("nhl")
    if calib_t is not None:
        print(f"Калибровка: температура T={calib_t:.4f} "
              "(data/reports/calibration.json, applied=true)")
    else:
        print("Калибровка: не применяется (отчёта нет или проверка "
              "калибровку не подтвердила) — вероятности сырые")

    # Будущие матчи — из того же датасета, что и история.
    #
    # Раньше для них был отдельный сборщик (etl/fetch_nhl_fixtures.py)
    # со своими соглашениями: даты в UTC вместо местных, обход по дням
    # (в семь раз больше запросов, чем нужно), кэш «навсегда» и свой
    # список сезонов. Он не запускался ни в цикле сервера, ни в CI, и
    # из-за этого расписание на сайте зависело от того, когда его
    # запускали руками: CI публиковал прогнозы вовсе без будущих
    # матчей, а в кэше оставались уже сыгранные матчи с прошедшей
    # датой (четыре матча вечера 4 ноября — их файл считал ещё
    # несыгранными).
    #
    # Теперь источник один: data/processed/nhl_matches.parquet, его
    # обновляет etl.fetch_nhl в каждом цикле обновления.
    now = datetime.now(timezone.utc)
    fixtures = _upcoming_matches(matches, now)
    print(f"  Будущих матчей: {len(fixtures)}")
    upcoming: list[dict] = []
    ledger: list[dict] = []
    for f in fixtures:
        p_home, p_away = model.predict_outcome(f["home_id"], f["away_id"])
        probs = temperature.apply([float(p_home), float(p_away)], calib_t)
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
            "shootout_prob": round(float(model.shootout_prob(
                f["home_id"], f["away_id"])), 4),
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
                # Живой прогноз: метка нужна, чтобы по записи было видно,
                # что она не из walk-forward журнала.
                "source": forecast.SOURCE_LIVE,
            },
        })

    out = PROCESSED / "nhl_predictions.json"
    ball_sport.write_json(out, {
        "league": "NHL",
        "season": _season_label(matches, fixtures),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history_matches": played.height,
        "model": model.summary(),
        "draws_possible": False,
        "draw_removed": False,
        "draw_note": (
            f"Ничьей в итоговом счёте НХЛ не бывает: за {played.height} "
            "матчей девяти сезонов regular season их ровно ноль. В модели "
            "ничьи нет by construction — ничья после основного времени "
            "уходит в овертайм, а оставшаяся после овертайма — в буллитную "
            "серию, победитель которой определяется жребием (масса делится "
            "поровну)."
        ),
        "model_family": "hockey_ot (пуассоновские скорости голов + овертайм + буллиты)",
        "model_report": "data/reports/nhl_model_compare.json",
        "calibration": {
            "applied": calib_t is not None,
            "T": calib_t,
            "method": "температура T: q_i = p_i^(1/T) / сумма, "
                      "порядок исходов не меняется, argmax тот же",
            "source": "data/reports/calibration.json (models/calibrate.py)",
            "note": "калибровка применяется только если подтверждена на "
                    "отложенном сезоне; иначе вероятности сырые",
        },
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
    # Журнал в вебе — урезанный: полная история (8748 матчей) весит
    # четырнадцать мегабайт и грузится страницей целиком, а в таблице
    # показываются последние 600 строк.
    ball_sport.write_web_journal(hist, public_dir / "nhl_journal.json")
    shutil.copy(strength_path, public_dir / "nhl_strength.json")


if __name__ == "__main__":
    main()
