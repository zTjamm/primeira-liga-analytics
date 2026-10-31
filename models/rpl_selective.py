"""Отбор матчей РФПЛ: свой порог, а не перенесённый из Португалии.

Предыстория. Порог 0.55 был выбран на валидационном сезоне португальского
футбола (models/selective.py) и перенесён на РФПЛ как есть — на сайте это
помечалось «на РФПЛ не валидировался». Журнал показал, что перенос не
работает: из 13 сыгранных матчей вердикт выдан в 3 (отсев 77%), log-loss
1.04975 хуже даже константной модели на тех же данных (1.0247), и пять
поражений подряд при вероятности 0.25–0.30.

Здесь порог подбирается по тем же правилам, что и в португальском
разделе, но на данных РФПЛ:

1. Walk-forward: прогноз на матч строится только по матчам до него.
   Обратного доступа к будущему результату нет ни на одном этапе.

2. Порог выбирается на валидационном сезоне по разнице «точность модели
   минус точность правила "всегда на хозяев" на том же подмножестве».
   Сравнение с примитивом обязательно: на отобранном подмножестве
   точность растёт сама по себе и без него ничего не доказывает.

3. Выбранный порог проверяется на двух тестовых сезонах, которые при
   подборе не участвовали.

4. Печатается покрытие. Точность на трёх матчах из ста — это не
   результат, а отсутствие результата.

Отдельно проверяется рынок тотала (2.5). Ему нужен свой порог, потому что
рынки разные: если на исходе модель часто уверена, то тотал около линии —
и порог, годный для исхода, может отсечь там, где решение ещё есть.

Ещё одна честная оговорка, которую печатает отчёт: у РФПЛ нет котировок
на тотал, поэтому сравнивать с линией букмекера не с чем — базовой линией
служит результат «без отсева», а не рынок.

Запуск:
    python -m models.rpl_selective

Отчёт: data/reports/rpl_selective.json
"""
from __future__ import annotations

import json
from datetime import timedelta

from etl.config import REPORTS
from models.dixon_coles import DixonColesModel
from models.poisson import over_prob
from models.rpl_predict import TRAIN_WINDOW_YEARS, load_matches

# Сезоны, как в португальском разделе: отдельный сезон на подбор,
# отдельные — на проверку. Сезон 2026/27 в проверку не идёт: он ещё
# идёт, и результаты на середине сезона — не тотальное испытание.
#
# CAL_SEASONS — сезон-«ученик» для скользящей проверки калибровки
# (models/calibrate.py): T учим на нём и проверяем на VAL_SEASON.
# В подбор порога он не участвует.
CAL_SEASONS = ("2022/23",)
VAL_SEASON = "2023/24"
TEST_SEASONS = ("2024/25", "2025/26")
WANTED = CAL_SEASONS + (VAL_SEASON,) + TEST_SEASONS

REFIT_EVERY_DAYS = 7
MIN_TRAIN_MATCHES = 200

THRESHOLDS = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]

# Минимум выданных вердиктов, чтобы сравнивать пороги. На РФПЛ сезон —
# около 240 матчей, покрытие при пороге 0.55 исторически около 25%:
# 100 матчей недостижимы, а 40 — это уже выборка, на которой разница
# в 10 пунктов не чистый шум.
MIN_N_PICK = 40

# Итоговое решение по РФПЛ (см. data/reports/rpl_selective.json):
#   исход  — 0.55: подобран на валидации 2023/24 (преимущество +7.9%
#            при n=76) и проверен на тесте 2024/25+2025/26: 69.0% при
#            покрытии 38% против 57.6% у правила «всегда на хозяев».
#   тотал  — 0.65: тотал у РФПЛ шумливее исхода, и 0.55 там отсекал
#            слишком мало (55.6% против 54.9% без отсева).
# load_thresholds() читает их из отчёта, а константы ниже — запасной
# путь на случай, если отчёта ещё нет (свежий сервер, пустой репозиторий).
RPL_VERDICT_THRESHOLD = 0.55
RPL_TOTAL_THRESHOLD = 0.65


def load_thresholds() -> tuple[float, float | None]:
    """(порог по исходу, порог по тоталу) из отчёта подбора.

    Если отчёта нет или он неполон — возвращаем константы выше. Это не
    «тихое согласие с перенесённым порогом», а аварийный путь: без него
    прогноз РФПЛ не собрался бы вовсе, а нерабочий пайплайн хуже
    работающего с честно подписанным порогом.

    None по тоталу — законный результат подбора: «ни один порог не
    поднял точность над игрой без отсева, вердикт не выдаём».
    """
    path = REPORTS / "rpl_selective.json"
    if not path.exists():
        return RPL_VERDICT_THRESHOLD, RPL_TOTAL_THRESHOLD
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return RPL_VERDICT_THRESHOLD, RPL_TOTAL_THRESHOLD
    out_th = (data.get("outcome") or {}).get("chosen")
    total_th = (data.get("total") or {}).get("chosen")
    return (float(out_th) if out_th is not None else RPL_VERDICT_THRESHOLD,
            float(total_th) if total_th is not None else None)


def collect(verbose: bool = True) -> list[dict]:
    """Walk-forward прогнозы по РФПЛ: только прошлое, никакого подглядывания."""
    allm = load_matches()
    wanted = set(WANTED)
    rows: list[dict] = []
    model: DixonColesModel | None = None
    last_fit = None
    fitted = 0

    for mt in allm:
        if mt.get("fthg") is None:
            continue
        if mt["season"] not in wanted:
            continue
        cutoff = mt["match_date"]
        h, a = mt["home_id"], mt["away_id"]

        train = [
            x for x in allm
            if cutoff - timedelta(days=int(365.25 * TRAIN_WINDOW_YEARS))
            <= x["match_date"] < cutoff
            and x.get("fthg") is not None
        ]
        ready = model is not None
        if model is None or last_fit is None or (cutoff - last_fit).days >= REFIT_EVERY_DAYS:
            if len(train) >= MIN_TRAIN_MATCHES:
                teams = sorted({x["home_id"] for x in train} | {x["away_id"] for x in train})
                model = DixonColesModel().fit(train, teams)
                last_fit = cutoff
                ready = True
                fitted += 1
                if verbose:
                    print(f"  fit на {cutoff}: {len(train)} матчей, {len(teams)} команд")
        if not ready:
            continue

        for t in (h, a):
            model.attack.setdefault(t, 0.0)
            model.defence.setdefault(t, 0.0)

        p_dc = model.predict_1x2(h, a)
        p = [float(x) for x in p_dc]
        s = sum(p)
        p = [x / s for x in p]
        over, _under = over_prob(model.score_matrix(h, a), 2.5)

        y = {"H": 0, "D": 1, "A": 2}[mt["ftr"]]
        h_games = sum(1 for x in train if x["home_id"] == h or x["away_id"] == h)
        a_games = sum(1 for x in train if x["home_id"] == a or x["away_id"] == a)
        rows.append({
            "season": mt["season"],
            "match_date": cutoff,
            "p": p,
            "top": p.index(max(p)),
            "conf": max(p),
            "y": y,
            "min_team_games": min(h_games, a_games),
            "p_over": float(over),
            "over": 1 if int(mt["fthg"]) + int(mt["ftag"]) > 2 else 0,
        })

    if verbose:
        print(f"  прогнозов: {len(rows)}, переподборов модели: {fitted}")
    return rows


def split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    val = [r for r in rows if r["season"] == VAL_SEASON]
    test = [r for r in rows if r["season"] in TEST_SEASONS]
    return val, test


def evaluate_outcome(rows: list[dict], th: float) -> dict:
    """Точность и покрытие по исходу при отказе от неуверенных матчей."""
    sel = [r for r in rows if r["conf"] >= th]
    if not sel:
        return {"n": 0, "coverage": 0.0}
    hit = sum(1 for r in sel if r["top"] == r["y"])
    naive = sum(1 for r in sel if r["y"] == 0)     # «всегда на хозяев»
    return {
        "n": len(sel),
        "coverage": round(len(sel) / len(rows), 4),
        "accuracy": round(hit / len(sel), 4),
        "accuracy_always_home": round(naive / len(sel), 4),
        "edge": round((hit - naive) / len(sel), 4),
    }


def evaluate_total(rows: list[dict], th: float) -> dict:
    """То же для тотала: conf = max(ТБ, ТМ), как в models/verdict.py."""
    sel = [r for r in rows if max(r["p_over"], 1 - r["p_over"]) >= th]
    if not sel:
        return {"n": 0, "coverage": 0.0}
    hit = sum(1 for r in sel if (r["p_over"] >= 0.5) == bool(r["over"]))
    naive = sum(1 for r in sel if r["over"])       # «всегда ТБ 2.5»
    return {
        "n": len(sel),
        "coverage": round(len(sel) / len(rows), 4),
        "accuracy": round(hit / len(sel), 4),
        "accuracy_always_over": round(naive / len(sel), 4),
        "edge": round((hit - naive) / len(sel), 4),
    }


def _pick_threshold(candidates: list[tuple[float, dict]], key: str) -> float | None:
    """Лучший порог по преимуществу над примитивом при достаточном покрытии.

    Порог не выбирается по точности: точность растёт монотонно с порогом
    и на четырёх матчах доходит до 100%. Выбирается по преимуществу перед
    примитивом — модели есть что доказывать именно на отобранном.
    """
    ok = [(edge, th) for th, m in candidates
          if m.get("n", 0) >= MIN_N_PICK
          for edge in [m.get("edge", -1)]]
    if not ok:
        return None
    return max(ok)[1]


def main() -> int:
    print("Собираю walk-forward прогнозы РФПЛ…")
    rows = collect()
    val, test = split(rows)
    print(f"Валидация {VAL_SEASON}: {len(val)} | тест {', '.join(TEST_SEASONS)}: {len(test)}\n")

    report: dict = {
        "val_season": VAL_SEASON,
        "test_seasons": list(TEST_SEASONS),
        "min_n_pick": MIN_N_PICK,
        "method": "walk-forward Dixon-Coles, окно 6 лет, переподбор раз в 7 дней",
    }

    # ---------- исход ----------
    print("=" * 78)
    print(f"ПОДБОР ПОРОГА ПО ИСХОДУ (валидация {VAL_SEASON})")
    print("=" * 78)
    print("%-10s%8s%10s%10s%14s%10s" % ("порог", "вердикт", "покрытие", "точность",
                                         "всегда дома", "преимущество"))
    cand = []
    for th in THRESHOLDS:
        m = evaluate_outcome(val, th)
        cand.append((th, m))
        if m["n"]:
            print("%-10.2f%8d%10.0f%%%10.1f%%%14.1f%%%10s" % (
                th, m["n"], 100 * m["coverage"], 100 * m["accuracy"],
                100 * m["accuracy_always_home"],
                f"{100*m['edge']:+.1f}%" if m["n"] >= MIN_N_PICK else "(мало)"))
        else:
            print("%-10.2f%8d" % (th, 0))
    best = _pick_threshold(cand, "edge")
    print(f"\n  выбранный порог (максимум преимущества при n>={MIN_N_PICK}): {best}")
    report["outcome"] = {
        "validation": {str(th): m for th, m in cand},
        "chosen": best,
    }

    # ---------- тотал ----------
    print()
    print("=" * 78)
    print(f"ПОДБОР ПОРОГА ПО ТОТАЛУ 2.5 (валидация {VAL_SEASON})")
    print("=" * 78)
    base_all = evaluate_total(val, 0.0)
    print(f"  без отсева: n={base_all['n']} точность {100*base_all['accuracy']:.1f}% "
          f"(база «всегда ТБ» {100*base_all['accuracy_always_over']:.1f}%)")
    print("%-10s%8s%10s%10s%14s%10s" % ("порог", "вердикт", "покрытие", "точность",
                                         "всегда ТБ", "преимущество"))
    cand_t = []
    for th in THRESHOLDS:
        m = evaluate_total(val, th)
        cand_t.append((th, m))
        if m["n"]:
            print("%-10.2f%8d%10.0f%%%10.1f%%%14.1f%%%10s" % (
                th, m["n"], 100 * m["coverage"], 100 * m["accuracy"],
                100 * m["accuracy_always_over"],
                f"{100*m['edge']:+.1f}%" if m["n"] >= MIN_N_PICK else "(мало)"))
    # По тоталу выбираем по точности относительно базы «без отсева»:
    # котировок на тотал у РФПЛ нет, сравниваться не с чем.
    ok_t = [(m["accuracy"], th) for th, m in cand_t
            if m.get("n", 0) >= MIN_N_PICK
            and m["accuracy"] > base_all["accuracy"]]
    best_t = max(ok_t)[1] if ok_t else None
    print(f"\n  выбранный порог по тоталу: {best_t}")
    if best_t is None:
        print("  ни один порог не поднял точность над «без отсева» при n>="
              f"{MIN_N_PICK} — вердикт по тоталу на РФПЛ не выдаём")
    report["total"] = {
        "without_gate": base_all,
        "validation": {str(th): m for th, m in cand_t},
        "chosen": best_t,
    }

    # ---------- проверка на тесте ----------
    print()
    print("=" * 78)
    print("ПРОВЕРКА НА ТЕСТЕ (порог подобран на другом сезоне)")
    print("=" * 78)
    for label, chosen, fn, base_key in (
        ("исход", best, evaluate_outcome, "accuracy_always_home"),
        ("тотал", best_t, evaluate_total, "accuracy_always_over"),
    ):
        if chosen is None:
            print(f"  {label}: порог не выбран — проверять нечего")
            report[f"test_{label}"] = None
            continue
        base = fn(test, 0.0)
        m = fn(test, chosen)
        print(f"  {label}:")
        print(f"    без отсева: n={base['n']} точность {100*base['accuracy']:.1f}% "
              f"(примитив {100*base[base_key]:.1f}%)")
        if m["n"]:
            print(f"    порог {chosen}: n={m['n']} покрытие {100*m['coverage']:.0f}% "
                  f"точность {100*m['accuracy']:.1f}% "
                  f"(примитив {100*m[base_key]:.1f}%, "
                  f"преимущество {100*m['edge']:+.1f}%)")
        report[f"test_{label}"] = {"chosen": chosen, "without_gate": base, "at_threshold": m}

    # Новички: тот же контроль, что и в португальском разделе.
    thin = [r for r in test if r["min_team_games"] < 10]
    print(f"\n  матчей с командой-новичком (<10 матчей в обучении): "
          f"{len(thin)} из {len(test)}")
    report["thin_sample_matches"] = len(thin)

    dst = REPORTS / "rpl_selective.json"
    dst.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
