"""Walk-forward журнал НХЛ: предсказания на истории и их разбор.

История — девять сезонов регулярного чемпионата НХЛ (2018/19 —
2026/27), 10 019 сыгранных матчей. Для каждого сезона модель
обучается на всех предыдущих и предсказывает текущий: проверка идёт
на данных, которых модель не видела.

Модель — пуассоновская, отдельная для хоккея (models/hockey.py):
скорости голов, овертайм, буллиты. Общая с баскетболом нормальная
модель для хоккея не годится — она завышает вероятность ничьей в
восемь раз (около 15% из нормального приближения при реальных нулях).

Что здесь важно не потерять: вердикт (решение «П1 или П2») выдаётся
не по всем матчам, а только там, где вероятность исхода выше
порога 0.55. Точность в журнале считается по выданным вердиктам, а
отсев показывается отдельным числом — иначе отсев был бы незаметен.

Запуск:
    python -m models.nhl_backtest
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from etl.config import PROCESSED
from models import forecast, verdict
from models.hockey import (HockeyModel, load_periods, production_config,
                           production_tie_boost)

# Сколько последних отложенных матчей показывать в веб-журнале.
#
# Полная walk-forward история — это больше десяти тысяч матчей, и
# выгружать их все в JSON для страницы бессмысленно: таблица не
# читается, а файл весит мегабайты одиннадцать. Итоги (точность,
# log-loss) считаются по всей истории, а таблица показывает свежие
# матчи. Полная проверка моделей — в models/nhl_compare.py и
# data/reports/nhl_model_compare.json.
WEB_JOURNAL_ROWS = 600


def load_names(matches: pl.DataFrame) -> dict[str, str]:
    pairs = pl.concat([
        matches.select(["home_id", "home_name"]).rename({"home_id": "id", "home_name": "name"}),
        matches.select(["away_id", "away_name"]).rename({"away_id": "id", "away_name": "name"}),
    ])
    grouped = pairs.group_by("id").agg(pl.col("name").mode().first().alias("name"))
    return {r["id"]: r["name"] for r in grouped.to_dicts()}


def team_games(rows: list[dict]) -> dict[str, int]:
    """Сколько матчей у каждой команды было в обучающем окне.

    У новичка параметры выведены из данных, которых у нас нет, поэтому
    его прогноз формально есть, а по сути нет (models/verdict.py).
    """
    counts: dict[str, int] = {}
    for m in rows:
        for side in ("home_id", "away_id"):
            counts[m[side]] = counts.get(m[side], 0) + 1
    return counts


def walk_forward_rows(train_rows: list[dict], test_rows: list[dict],
                      scale: float, half_life: float,
                      periods: dict[str, dict] | None = None,
                      tie_boost: bool = False) -> list[dict]:
    """Прогнозы модели по отложенному fold'у.

    Одна и та же функция используется и backtest'ом, и проверкой
    калибровки (models/calibrate.py) — иначе журнал и калибровка
    считали бы разные числа.
    """
    teams = sorted({r["home_id"] for r in train_rows}
                   | {r["away_id"] for r in train_rows})
    model = HockeyModel().fit(train_rows, teams,
                               strength_scale=scale,
                               half_life_days=half_life,
                               periods=periods,
                               use_tie_boost=tie_boost)
    games = team_games(train_rows)

    rows: list[dict] = []
    for m in test_rows:
        p_home, p_away = model.predict_outcome(m["home_id"], m["away_id"])
        top = 0 if p_home >= p_away else 1
        prediction = ["H", "A"][top]
        confidence = float(max(p_home, p_away))
        team_games_min = min(games.get(m["home_id"], 0),
                             games.get(m["away_id"], 0))
        ok, reason = verdict.verdict(confidence, team_games_min)
        rows.append({
            "key": f"nhl_{m['match_id']}",
            "date": str(m["match_date"]),
            "home_id": m["home_id"],
            "away_id": m["away_id"],
            "home_name": m.get("home_name", m["home_id"]),
            "away_name": m.get("away_name", m["away_id"]),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hours_before": 24.0,
            "stage": "T-24h",
            "p_home": round(float(p_home), 4),
            "p_draw": 0.0,
            "p_away": round(float(p_away), 4),
            "prediction": prediction,
            # Фактический исход: нужен проверке калибровки
            # (models/calibrate.py), которая считает log-loss по
            # факту, а не по предсказанию.
            "actual": "H" if m["home_score"] > m["away_score"] else "A",
            "odds_home": None,
            "odds_draw": None,
            "odds_away": None,
            "stake": None,
            # Метки вердикта: точность в журнале считается только по
            # выданным вердиктам, иначе отсев был бы незаметен.
            "extra": {
                "verdict_given": ok,
                "verdict_reason": reason,
            },
        })
    return rows


def write_web_journal(hist: dict, path: Path, rows: int = WEB_JOURNAL_ROWS) -> None:
    """Веб-копия журнала: полные итоги, но только свежие строки в таблице.

    Общая функция для backtest'а и прогнозов: иначе прогнозы, копируя
    полный журнал в web/public/data, затирали бы урезанную версию —
    и страница грузила бы четырнадцать мегабайт вместо полутора.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    trimmed = dict(hist)
    trimmed["resolved"] = hist["resolved"][:rows]
    trimmed["resolved_total"] = len(hist["resolved"])
    trimmed["resolved_note"] = (
        f"в таблице показаны последние {rows} матчей из "
        f"{len(hist['resolved'])}; итоги посчитаны по всем"
    )
    path.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def main() -> None:
    matches = pl.read_parquet(PROCESSED / "nhl_matches.parquet").sort("match_date")
    played = matches.filter(pl.col("home_score").is_not_null())

    seasons = sorted(played["season"].unique().to_list())
    print(f"Сезоны в данных: {seasons}")

    scale, half_life = production_config()
    print(f"Гиперпараметры: априор силы {scale}, затухание {half_life:.0f} дн.")

    periods = load_periods()
    print(f"Разбивка по периодам: {len(periods)} матчей "
          "(нужна, чтобы параметр овертайма был идентифицируем)")

    tie_boost = production_tie_boost()
    print(f"Поправка на избыток ничьих (κ): "
          f"{'применяется' if tie_boost else 'не применяется'} "
          "(решение — models/nhl_compare.py)")

    records: list[dict] = []
    for i in range(1, len(seasons)):
        train_seasons = seasons[:i]
        test_season = seasons[i]
        train_df = played.filter(pl.col("season").is_in(train_seasons))
        test_df = played.filter(pl.col("season") == test_season)
        print(
            f"Обучаю на {train_seasons[0]}…{train_seasons[-1]} "
            f"({train_df.height} матчей), предсказываю {test_season} "
            f"({test_df.height} матчей)"
        )
        records.extend(walk_forward_rows(
            train_df.to_dicts(), test_df.to_dicts(), scale, half_life,
            periods, tie_boost))

    added = forecast.append(records, "nhl")
    print(f"\nВ журнал НХЛ добавлено записей: {added}")

    hist = forecast.save(played, "nhl", out=PROCESSED / "nhl_journal.json")
    s = hist["summary"]
    print(f"\nЖурнал НХЛ: {s['total']} сыгранных прогнозов")
    print(f"  Точность (все матчи): {s['overall']['accuracy']:.1%}")
    print(f"  Log-loss: {s['overall']['logloss']}")
    if s.get("outcome_market"):
        om = s["outcome_market"]
        print(f"  Среди выданных вердиктов: {om['n']} матчей, "
              f"точность {om['accuracy']:.1%}, отсеяно {om['withheld']}")

    # Веб-копия: полные итоги, но только свежие строки в таблице.
    public = Path("web/public/data/nhl_journal.json")
    write_web_journal(hist, public)
    print(f"Скопировано в {public}")


if __name__ == "__main__":
    main()
