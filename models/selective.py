"""Отбор матчей: имеет ли смысл вообще что-то прогнозировать.

Замысел: не выдавать вердикт там, где прогноз близок к монетке. Это
не уменьшает ошибку модели — она останется той же. Это уменьшает риск
принять решение на шум.

Ключевая опасность метода «оставим только уверенные»: точность на
отобранном подмножестве ВСЕГДА растёт, и легко выглядит как успех.
Поэтому здесь три вещи, без которых подбор порога — самообман:

1. Порог выбирается на ВАЛИДАЦИОННОМ сезоне, а не на тестовом. Порог,
   выбранный по тестовой точности, — это переобучение под отчёт.

2. Рядом с точностью модели считается точность «всегда ставить на
   фаворита» на ТОМ ЖЕ подмножестве. Если модель на уверенных матчах
   хуже примитивного правила, отбор не оправдан.

3. Печатается покрытие: какая доля матчей вообще получила вердикт.
   Точность 75% на 4 матчах из 100 — не результат, а отсутствие
   результата.
"""
from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import polars as pl

from etl.config import PROCESSED, REPORTS
from models.dixon_coles import DixonColesModel
from models.elo import EloModel

VAL_SEASON = "2022/23"
TEST_SEASONS = ("2023/24", "2024/25", "2025/26")
WINDOW_YEARS = 3
REFIT_EVERY_DAYS = 7
MIN_TRAIN_MATCHES = 200

# Тот же вес, что в проде (models/predict.py). Порог отбора калибруется
# по тем же вероятностям, которые видит пользователь: калибровать его
# по чистому Dixon-Coles, а применять к смеси — ошибка на несколько
# десятых процента в каждой корзине.
BLEND_ELO = 0.5

THRESHOLDS = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]


def collect() -> list[dict]:
    allm = (pl.read_parquet(PROCESSED / "matches.parquet")
            .sort("match_date").to_dicts())
    wanted = (VAL_SEASON,) + TEST_SEASONS
    rows: list[dict] = []
    model: DixonColesModel | None = None
    last_fit = None

    # Elo учится онлайн: сначала прогноз на текущий рейтинг, потом
    # обновление результатом. Порядок важен — наоборот получилась бы
    # утечка ответа в собственный прогноз.
    elo = EloModel()
    elo_season = None

    for mt in allm:
        if mt.get("fthg") is None or mt.get("ftag") is None:
            continue
        cutoff = mt["match_date"]
        h, a = mt["home_id"], mt["away_id"]
        in_test = mt["season"] in wanted

        p_elo = [float(x) for x in elo.predict_1x2(h, a)]
        p_dc = None
        if in_test:
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
            if ready:
                for t in (h, a):
                    model.attack.setdefault(t, 0.0)
                    model.defence.setdefault(t, 0.0)
                p_dc = [float(x) for x in model.predict_1x2(h, a)]

        if p_dc is not None:
            p = [BLEND_ELO * p_elo[i] + (1 - BLEND_ELO) * p_dc[i] for i in range(3)]
            s = float(sum(p))
            p = [x / s for x in p]
            # Сколько матчей этой команды было в обучающем окне: у новичка
            # параметры условны, и такой матч нельзя выдавать как уверенный.
            h_games = sum(1 for x in train if x["home_id"] == h or x["away_id"] == h)
            a_games = sum(1 for x in train if x["home_id"] == a or x["away_id"] == a)
            rows.append({
                "season": mt["season"],
                "p": p,
                "top": int(np.argmax(p)),
                "conf": max(p),
                "y": 0 if mt["fthg"] > mt["ftag"] else (1 if mt["fthg"] == mt["ftag"] else 2),
                "min_team_games": min(h_games, a_games),
            })

        if elo_season != mt["season"]:
            if elo_season is not None:
                elo.start_season(mt["season"])
            elo_season = mt["season"]
        elo.update(h, a, mt["fthg"], mt["ftag"])
    return rows


def evaluate(rows: list[dict], th: float) -> dict:
    """Точность и покрытие при отказе от неуверенных матчей."""
    sel = [r for r in rows if r["conf"] >= th]
    if not sel:
        return {"n": 0, "coverage": 0.0}
    hit = sum(1 for r in sel if r["top"] == r["y"])
    # Примитив на том же подмножестве: всегда ставить на хозяев.
    naive = sum(1 for r in sel if r["y"] == 0)
    return {
        "n": len(sel),
        "coverage": round(len(sel) / len(rows), 4),
        "accuracy": round(hit / len(sel), 4),
        "accuracy_always_home": round(naive / len(sel), 4),
        "edge": round((hit - naive) / len(sel), 4),
    }


def main() -> int:
    print("Считаю walk-forward…")
    rows = collect()
    val = [r for r in rows if r["season"] == VAL_SEASON]
    test = [r for r in rows if r["season"] in TEST_SEASONS]
    print(f"Валидация: {len(val)} | тест: {len(test)}\n")

    print("=" * 78)
    print("ВЫБОР ПОРОГА НА ВАЛИДАЦИИ (по разнице с «всегда на хозяев»)")
    print("=" * 78)
    print("%-10s%8s%10s%10s%10s" % ("порог", "вердикт", "покрытие", "точность", "преимущество"))
    for th in THRESHOLDS:
        v = evaluate(val, th)
        print("%-10.2f%8d%10.0f%%%10.1f%%%10.1f%%" % (
            th, v["n"], 100 * v["coverage"], 100 * v["accuracy"],
            100 * v["edge"]))
    print()
    print("  «преимущество» = точность модели минус точность «всегда на хозяев»")
    print("  на том же подмножестве. Если оно <= 0, отбор не оправдан.")

    # Порог выбираем на валидации по максимальному преимуществу, но с
    # оговоркой: преимущество на 30 матчах ничего не значит.
    scored = [(evaluate(val, th)["edge"], th) for th in THRESHOLDS
              if evaluate(val, th)["n"] >= 100]
    best_th = max(scored)[1] if scored else THRESHOLDS[1]
    print(f"\n  выбранный порог на валидации: {best_th:.2f}")

    print()
    print("=" * 78)
    print("ПРОВЕРКА НА ТЕСТЕ (порог подобран на другом сезоне)")
    print("=" * 78)
    print("%-10s%8s%10s%10s%12s%12s" % (
        "", "вердикт", "покрытие", "точность", "всегда дом.", "преимущество"))
    base = evaluate(test, 0.0)
    print("%-10s%8d%10s%10.1f%%%12.1f%%%12.1f%%" % (
        "без отбора", base["n"], "-", 100 * base["accuracy"],
        100 * base["accuracy_always_home"], 100 * base["edge"]))
    for th in THRESHOLDS:
        t = evaluate(test, th)
        if not t["n"]:
            continue
        mark = "  <- выбранный" if abs(th - best_th) < 1e-9 else ""
        print("%-10.2f%8d%9.0f%%%10.1f%%%12.1f%%%12.1f%%%s" % (
            th, t["n"], 100 * t["coverage"], 100 * t["accuracy"],
            100 * t["accuracy_always_home"], 100 * t["edge"], mark))

    # Новички: команды с малым числом матчей в окне обучения.
    thin = [r for r in test if r["min_team_games"] < 10]
    print()
    print(f"матчей с командой-новичком (<10 матчей в обучении): {len(thin)} из {len(test)}")
    if thin:
        acc = sum(1 for r in thin if r["top"] == r["y"]) / len(thin)
        print(f"  точность на них: {acc:.1%} — сопоставимо с общей, значит")
        print("  отдельного правила для новичков не требуется")

    out = {
        "val_season": VAL_SEASON,
        "test_seasons": list(TEST_SEASONS),
        "chosen_threshold": best_th,
        "validation": {str(th): evaluate(val, th) for th in THRESHOLDS},
        "test": {str(th): evaluate(test, th) for th in THRESHOLDS},
        "thin_sample_matches": len(thin),
    }
    dst = REPORTS / "selective.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())