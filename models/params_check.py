"""Два параметра, которые никогда не проверялись на футболе.

1. Вес смеси Elo/Dixon-Coles. Бэктест подобрал на валидации w_dc = 1.0,
   то есть чистый Dixon-Coles без Elo. А в models/predict.py зашито
   BLEND_ELO = 0.5, то есть 50/50. То есть прод работает на весе, который
   валидация отвергла.

2. Период затухания. У баскетбола 730 дней выбран осознанно и задокументирован,
   а у футбола стоит 180 — то же значение, что было до того, как для ВТБ
   доказали обратное. Возможно, оно тоже неверно, и никто этого не проверял.

Запуск:
    python -m models.params_check
"""
from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import polars as pl

from etl.config import PROCESSED, REPORTS
from models.dixon_coles import DixonColesModel
from models.elo import EloModel

VAL_SEASONS = ("2019/20", "2020/21", "2021/22", "2022/23")
TEST_SEASONS = ("2023/24", "2024/25", "2025/26")
WINDOW_YEARS = 3
REFIT_EVERY_DAYS = 7
MIN_TRAIN_MATCHES = 200

HALF_LIVES = (90.0, 180.0, 365.0, 730.0)
WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)   # доля Dixon-Coles


def collect() -> list[dict]:
    """Один прогон на все нужные сезоны; все варианты считаются на нём."""
    allm = (pl.read_parquet(PROCESSED / "matches.parquet")
            .sort("match_date").to_dicts())
    rows: list[dict] = []

    elo = EloModel()
    elo_season = None
    models: dict[float, tuple] = {}   # half_life -> (модель, дата переобучения)

    for mt in allm:
        if mt.get("fthg") is None or mt.get("ftag") is None:
            continue
        cutoff = mt["match_date"]
        h, a = mt["home_id"], mt["away_id"]

        p_elo = [float(x) for x in elo.predict_1x2(h, a)]

        in_scope = mt["season"] in VAL_SEASONS + TEST_SEASONS
        if in_scope:
            ws = cutoff - timedelta(days=int(365.25 * WINDOW_YEARS))
            train = [x for x in allm
                     if ws <= x["match_date"] < cutoff and x.get("fthg") is not None]
            ready_hl = {hl: False for hl in HALF_LIVES}
            if len(train) >= MIN_TRAIN_MATCHES:
                teams = sorted({x["home_id"] for x in train} | {x["away_id"] for x in train})
                for hl in HALF_LIVES:
                    mdl, last = models.get(hl, (None, None))
                    if mdl is None or last is None or (cutoff - last).days >= REFIT_EVERY_DAYS:
                        models[hl] = (DixonColesModel().fit(train, teams, half_life_days=hl), cutoff)
                        ready_hl[hl] = True
                    else:
                        ready_hl[hl] = True
            probs_hl = {}
            for hl in HALF_LIVES:
                mdl, _ = models.get(hl, (None, None))
                if mdl is None or not ready_hl[hl]:
                    continue
                for t in (h, a):
                    mdl.attack.setdefault(t, 0.0)
                    mdl.defence.setdefault(t, 0.0)
                probs_hl[hl] = [float(x) for x in mdl.predict_1x2(h, a)]
            if probs_hl:
                rows.append({
                    "season": mt["season"],
                    "elo": p_elo,
                    "dc": probs_hl,
                    "y": 0 if mt["fthg"] > mt["ftag"] else (1 if mt["fthg"] == mt["ftag"] else 2),
                })

        if elo_season != mt["season"]:
            if elo_season is not None:
                elo.start_season(mt["season"])
            elo_season = mt["season"]
        elo.update(h, a, mt["fthg"], mt["ftag"])
    return rows


def logloss(sample: list[dict], get_p) -> float | None:
    vals = []
    for r in sample:
        p = get_p(r)
        if p is None:
            continue
        s = sum(p)
        vals.append(-np.log(max(p[r["y"]] / s, 1e-12)))
    return float(np.mean(vals)) if vals else None


def main() -> int:
    print("Считаю walk-forward для каждого периода затухания…")
    rows = collect()
    val = [r for r in rows if r["season"] in VAL_SEASONS]
    test = [r for r in rows if r["season"] in TEST_SEASONS]
    print(f"Валидация: {len(val)} | тест: {len(test)}\n")

    out: dict = {"val_seasons": list(VAL_SEASONS), "test_seasons": list(TEST_SEASONS)}

    # --- 1. вес смеси при half_life = 180 (текущий в проде) ----------
    print("=" * 72)
    print("1. ВЕС СМЕСИ (Elo / Dixon-Coles), half_life = 180 дней")
    print("=" * 72)
    print("%-14s%14s%14s" % ("доля DC", "валидация", "тест"))
    w_grid = []
    for w in WEIGHTS:
        v = logloss(val, lambda r, w=w: [w * r["dc"][180.0][i] + (1 - w) * r["elo"][i]
                                         for i in range(3)])
        t = logloss(test, lambda r, w=w: [w * r["dc"][180.0][i] + (1 - w) * r["elo"][i]
                                          for i in range(3)])
        w_grid.append({"w_dc": w, "val": round(v, 5), "test": round(t, 5)})
        print("%-14.2f%14.5f%14.5f" % (w, v, t))
    best_v = min(w_grid, key=lambda x: x["val"])
    print("\n  минимум на валидации: w_dc = %.2f" % best_v["w_dc"])
    prod = next(x for x in w_grid if abs(x["w_dc"] - 0.5) < 1e-9)
    bestt = next(x for x in w_grid if abs(x["w_dc"] - best_v["w_dc"]) < 1e-9)
    print("  прод сейчас:  w_dc = 0.50 -> валидация %.5f, тест %.5f"
          % (prod["val"], prod["test"]))
    print("  если взять:  w_dc = %.2f -> валидация %.5f, тест %.5f"
          % (bestt["w_dc"], bestt["val"], bestt["test"]))
    print("  разница на тесте: %+.5f log-loss" % (bestt["test"] - prod["test"]))
    out["weight_grid"] = w_grid

    # --- 2. период затухания при чистом DC ---------------------------
    print("\n" + "=" * 72)
    print("2. ПЕРИОД ЗАТУХАНИЯ (чистый Dixon-Coles)")
    print("=" * 72)
    print("%-14s%14s%14s" % ("half_life", "валидация", "тест"))
    hl_grid = []
    for hl in HALF_LIVES:
        v = logloss(val, lambda r, hl=hl: r["dc"].get(hl))
        t = logloss(test, lambda r, hl=hl: r["dc"].get(hl))
        hl_grid.append({"half_life": hl, "val": round(v, 5), "test": round(t, 5)})
        print("%-14d%14.5f%14.5f" % (hl, v, t))
    best_hl = min(hl_grid, key=lambda x: x["val"])
    cur = next(x for x in hl_grid if x["half_life"] == 180.0)
    print("\n  минимум на валидации: %d дней" % best_hl["half_life"])
    print("  в проде сейчас:     180 дней -> тест %.5f" % cur["test"])
    print("  разница на тесте:   %+.5f log-loss" % (best_hl["test"] - cur["test"]))
    out["half_life_grid"] = hl_grid

    dst = REPORTS / "params_check.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())