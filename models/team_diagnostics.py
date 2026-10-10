"""Диагностика по командам: где модель ошибается заметнее всего.

Средние цифры скрывают важную вещь: ошибка может быть размазана по лиге
ровномерно, а может концентрироваться на handful команд. Второе ценнее —
по такой команде обычно видно, чего модели не хватает.

Сравниваем не «угадал или нет», а log-loss по каждой команде: модель против
закрывающей линии. Разница показывает, где букмекер знает то, чего нет в
наших данных. Обычно это травмы и составы, и они бьют по конкретным
клубам, а не по всей лиге сразу.

Осторожность с выводами: на одну команду приходится 30-40 матчей за три
сезона, и разница в 0.02 log-loss легко укладывается в шум. Поэтому рядом
печатается и число матчей — читать выводы без него нельзя.

Запуск:
    python -m models.team_diagnostics
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta

import numpy as np
import polars as pl

from etl.config import PROCESSED, REPORTS
from models.dixon_coles import DixonColesModel
from models.market import devig

TEST_SEASONS = ("2023/24", "2024/25", "2025/26")
WINDOW_YEARS = 3
REFIT_EVERY_DAYS = 7
MIN_TRAIN_MATCHES = 200

# Ниже этого числа матчей разницу нельзя отличить от шума. Порог
# отмечен в отчёте, чтобы выводы не выглядели точнее, чем они есть.
MIN_GAMES = 60


def _at(seq, i: int) -> float | None:
    try:
        v = seq[i]
    except Exception:
        return None
    return float(v) if v is not None and np.isfinite(v) and 1.01 < v < 1000 else None


def collect() -> list[dict]:
    allm = (pl.read_parquet(PROCESSED / "matches.parquet")
            .sort("match_date").to_dicts())
    rows: list[dict] = []
    model: DixonColesModel | None = None
    last_fit = None

    for mt in allm:
        if mt.get("fthg") is None or mt.get("ftag") is None:
            continue
        if mt["season"] not in TEST_SEASONS:
            continue
        cutoff = mt["match_date"]
        ws = cutoff - timedelta(days=int(365.25 * WINDOW_YEARS))
        train = [x for x in allm
                 if ws <= x["match_date"] < cutoff and x.get("fthg") is not None]
        ready = model is not None
        if (model is None or last_fit is None
                or (cutoff - last_fit).days >= REFIT_EVERY_DAYS):
            if len(train) >= MIN_TRAIN_MATCHES:
                teams = sorted({x["home_id"] for x in train} | {x["away_id"] for x in train})
                model = DixonColesModel().fit(train, teams)
                last_fit = cutoff
                ready = True
        if not ready:
            continue

        h, a = mt["home_id"], mt["away_id"]
        for t in (h, a):
            model.attack.setdefault(t, 0.0)
            model.defence.setdefault(t, 0.0)
        p = [float(x) for x in model.predict_1x2(h, a)]
        oh, od, oa = (_at(mt.get("odds_close"), 0), _at(mt.get("odds_close"), 1),
                      _at(mt.get("odds_close"), 2))
        d = devig([oh, od, oa]) if (oh and od and oa) else None
        if not d:
            continue
        y = 0 if mt["fthg"] > mt["ftag"] else (1 if mt["fthg"] == mt["ftag"] else 2)
        rows.append({
            "home": mt["home_name"], "away": mt["away_name"],
            "p": p, "q": [float(x) for x in d.probs], "y": y,
        })
    return rows


def main() -> int:
    print("Считаю walk-forward по командам…")
    rows = collect()
    print(f"матчей с линией: {len(rows)}\n")

    acc: dict[str, dict] = defaultdict(lambda: {"m": [], "l": [], "hit": 0})
    for r in rows:
        lm = -np.log(max(r["p"][r["y"]], 1e-12))
        ll = -np.log(max(r["q"][r["y"]], 1e-12))
        for team in (r["home"], r["away"]):
            acc[team]["m"].append(lm)
            acc[team]["l"].append(ll)
            acc[team]["hit"] += int(int(np.argmax(r["p"])) == r["y"])

    table = []
    for team, d in acc.items():
        n = len(d["m"])
        if n < 10:
            continue
        lm = float(np.mean(d["m"]))
        ll = float(np.mean(d["l"]))
        table.append({
            "team": team, "n": n,
            "logloss_model": round(lm, 4),
            "logloss_line": round(ll, 4),
            # Положительное число означает, что модель хуже линии.
            "gap": round(lm - ll, 4),
            "hit_rate": round(d["hit"] / n, 4),
            "thin": n < MIN_GAMES,
        })
    table.sort(key=lambda r: -r["gap"])

    out = {"test_seasons": list(TEST_SEASONS), "min_games": MIN_GAMES,
           "teams": table}

    print("=" * 76)
    print("ГДЕ МОДЕЛЬ ХУЖЕ ЛИНИИ (разница log-loss, больше = хуже)")
    print("=" * 76)
    print("%-26s%7s%11s%11s%10s%10s" % (
        "команда", "матчей", "модель", "линия", "разница", "точность"))
    print("-" * 76)
    for r in table[:10]:
        mark = " *" if r["thin"] else ""
        print("%-26s%7d%11.4f%11.4f%+10.4f%9.1f%%%s" % (
            r["team"][:26], r["n"], r["logloss_model"], r["logloss_line"],
            r["gap"], 100 * r["hit_rate"], mark))

    print()
    print("=" * 76)
    print("ГДЕ МОДЕЛЬ ЛУЧШЕ ЛИНИИ")
    print("=" * 76)
    print("%-26s%7s%11s%11s%10s%10s" % (
        "команда", "матчей", "модель", "линия", "разница", "точность"))
    print("-" * 76)
    for r in table[-10:][::-1]:
        mark = " *" if r["thin"] else ""
        print("%-26s%7d%11.4f%11.4f%+10.4f%9.1f%%%s" % (
            r["team"][:26], r["n"], r["logloss_model"], r["logloss_line"],
            r["gap"], 100 * r["hit_rate"], mark))

    solid = [r for r in table if not r["thin"]]
    gaps = np.array([r["gap"] for r in solid])
    print()
    print(f"Звёздочкой отмечены команды с числом матчей меньше {MIN_GAMES}: "
          "разницу на них нельзя отличить от шума.")
    print()
    print(f"Надёжных команд ({len(solid)} из {len(table)}): "
          f"разброс разницы от {gaps.min():+.4f} до {gaps.max():+.4f},")
    print(f"медиана {np.median(gaps):+.4f}, среднее {gaps.mean():+.4f}.")
    worse = sum(1 for g in gaps if g > 0)
    print(f"модель хуже линии у {worse} команд из {len(solid)}.")
    print()
    if gaps.std() > 0.05:
        print("Разброс заметный: ошибка не размазана по лиге, а сидит на")
        print("конкретных клубах — обычно это травмы и составы.")
    else:
        print("Разброс небольшой: модель ошибается более-менее равномерно,")
        print("и локальных проблемных команд нет.")
    out["spread"] = {
        "solid": len(solid), "min": round(float(gaps.min()), 4),
        "max": round(float(gaps.max()), 4), "median": round(float(np.median(gaps)), 4),
        "worse_than_line": worse,
    }

    dst = REPORTS / "team_diagnostics.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())