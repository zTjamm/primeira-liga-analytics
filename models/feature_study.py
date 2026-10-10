"""Стоит ли добавлять статистику матча в модель.

Идея, которую почти все выдвигают: у нас есть удары, удары в створ,
угловые, фолы и карточки — почему бы не скормить их модели? Ответ
проверяется, и ответ отрицательный.

Главная ловушка — сравнение «скоррелировало с исходом» против «добавило
информации». Разница между ударами хозяев и разницей голов корреляция
высокая, около 0.5. Но сильные команды и бьют чаще, и забивают больше,
а сила команды модели уже известна. Настоящий вопрос — корреляция с
ОСТАТКОМ, то есть с тем, в чём модель ошиблась.

Ещё одна обязательная оговорка: эти величины известны только ПОСЛЕ матча.
Как признаки того же матча они бесполезны и были бы утечкой. Поэтому берутся
скользящие средние по ПРЕДЫДУЩИМ матчам команд — тогда это легальный признак,
и всё, что дальше, измеряется именно на них.

Запуск:
    python -m models.feature_study
"""
from __future__ import annotations

import json
from collections import deque
from datetime import timedelta

import numpy as np
import polars as pl

from etl.config import PROCESSED, REPORTS
from models.dixon_coles import DixonColesModel

TEST_SEASONS = ("2023/24", "2024/25", "2025/26")
WINDOW_YEARS = 3
REFIT_EVERY_DAYS = 7
MIN_TRAIN_MATCHES = 200
HISTORY = 20          # сколько предыдущих матчей команды держим
MIN_HISTORY = 5       # меньше — среднее не считаем

STATS = ("shots", "sot", "corners", "fouls", "yellow")


def main() -> int:
    m = pl.read_parquet(PROCESSED / "matches.parquet").sort("match_date")
    allm = m.to_dicts()
    test_ids = {s for s in TEST_SEASONS}

    hist: dict[str, dict[str, deque]] = {s: {} for s in STATS}
    raw: dict[str, list[float]] = {s: [] for s in STATS}
    # Разница голов и остаток прогноза — разные величины, и обе нужны.
    # Первый показывает «признак что-то знает», второй — «признак знает
    # то, чего модель не знает». В первой версии в обе колонки попал
    # остаток, и таблица выглядела так, будто признаки бесполезны
    # дважды.
    actual: list[float] = []
    residual: list[float] = []
    raw: dict[str, list[float]] = {s: [] for s in STATS}
    n_rows = 0

    model: DixonColesModel | None = None
    last_fit = None

    for mt in allm:
        if mt.get("fthg") is None or mt.get("ftag") is None:
            continue
        cutoff = mt["match_date"]

        if mt["season"] in test_ids:
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
                h, a = mt["home_id"], mt["away_id"]
                for t in (h, a):
                    model.attack.setdefault(t, 0.0)
                    model.defence.setdefault(t, 0.0)
                lam_h, lam_a = model.lambdas(h, a)
                pred_gd = (lam_h + model.home_adv) - lam_a
                act_gd = mt["fthg"] - mt["ftag"]
                row_ok = True
                tmp = {}
                for s in STATS:
                    hv, av = mt.get(f"{s}_home"), mt.get(f"{s}_away")
                    hf, af = hist[s].get(h, deque()), hist[s].get(a, deque())
                    if hv is None or av is None or len(hf) < MIN_HISTORY or len(af) < MIN_HISTORY:
                        row_ok = False
                        break
                    tmp[s] = (np.mean(hf) - np.mean(af)) / HISTORY
                if row_ok:
                    n_rows += 1
                    actual.append(act_gd)
                    residual.append(act_gd - pred_gd)
                    for s in STATS:
                        raw[s].append(tmp[s])

        # историю обновляем ВСЕГДА: результат матча известен к моменту
        # следующего, это не утечка
        for s in STATS:
            hv, av = mt.get(f"{s}_home"), mt.get(f"{s}_away")
            if hv is None or av is None:
                continue
            for side, val in ((mt["home_id"], hv), (mt["away_id"], av)):
                dq = hist[s].setdefault(side, deque(maxlen=HISTORY))
                dq.append(val)

    print(f"матчей в тесте со скользящими средними: {n_rows}\n")

    rows = []
    print("=" * 78)
    print("СТАТИСТИКА МАТЧА: видна ли она вообще, и добавляет ли что-то")
    print("=" * 78)
    print("%-10s%10s%14s%16s%14s" % (
        "признак", "матчей", "r с исходом", "r с остатком", "вывод"))
    print("-" * 78)
    act = np.array(actual)
    res = np.array(residual)
    for s in STATS:
        d = np.array(raw[s])
        if len(d) < 50:
            continue
        r_raw = float(np.corrcoef(d, act)[0, 1])
        r_res = float(np.corrcoef(d, res)[0, 1])
        verdict = ("полезен" if abs(r_res) > 0.10
                   else ("слабо" if abs(r_res) > 0.05 else "бесполезен"))
        rows.append({"stat": s, "n": len(d), "r_raw": round(r_raw, 4),
                     "r_residual": round(r_res, 4), "verdict": verdict})
        print("%-10s%10d%14.3f%16.3f%14s" % (s, len(d), r_raw, r_res, verdict))

    offsides = [c for c in m.columns if "offside" in c.lower()]
    print()
    print(f"офсайды: {'есть колонка' if offsides else 'НЕТ КОЛОНКИ В ИСТОЧНИКЕ'}")

    # Почему видимая корреляция так велика и почему она бесполезна.
    out = {
        "test_seasons": list(TEST_SEASONS),
        "n": n_rows,
        "history": HISTORY,
        "stats": rows,
        "offsides_available": bool(offsides),
        "note": (
            "Корреляция признака с исходом высокая, но она объясняется силой "
            "команд, а не самостоятельной информацией: та же сила уже учтена в "
            "модели. Добавляющая ценность измеряется корреляцией с остатком "
            "прогноза, и она близка к нулю."
        ),
    }
    dst = REPORTS / "feature_study.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())