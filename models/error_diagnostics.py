"""Диагностика ошибок: где модель ошибается и что с этим делать.

Отвечает на вопрос «как уменьшить ошибки», но сначала отвечает на
«какие именно ошибки» — иначе это фантазия.

Что здесь измеряется:
1. Калибровка по корзинам вероятности.
2. Систематическое смещение по исходам.
3. Помогает ли сжатие к линии букмекера.
4. Помогает ли перекалибровка самой модели.

Взвешивание и подбор параметров идут на ВАЛИДАЦИОННОМ сезоне (2022/23),
проверка — на трёх тестовых (2023/24-2025/26). Подбор на тесте означал бы
переобучение под ту же выборку, на которой потом отчитываемся.

Запуск:
    python -m models.error_diagnostics
"""
from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import polars as pl
from scipy.optimize import minimize

from etl.config import PROCESSED, REPORTS
from models.dixon_coles import DixonColesModel
from models.market import devig

VAL_SEASON = "2022/23"
TEST_SEASONS = ("2023/24", "2024/25", "2025/26")
WINDOW_YEARS = 3
REFIT_EVERY_DAYS = 7
MIN_TRAIN_MATCHES = 200


def _at(seq, i: int) -> float | None:
    try:
        v = seq[i]
    except Exception:
        return None
    return float(v) if v is not None and np.isfinite(v) and 1.01 < v < 1000 else None


def collect() -> list[dict]:
    """Walk-forward прогнозы: обучение строго на матчах до даты."""
    allm = pl.read_parquet(PROCESSED / "matches.parquet").sort("match_date").to_dicts()
    wanted = (VAL_SEASON,) + TEST_SEASONS
    rows: list[dict] = []
    model: DixonColesModel | None = None
    last_fit = None

    for mt in allm:
        if mt.get("fthg") is None or mt.get("ftag") is None:
            continue
        cutoff = mt["match_date"]
        if mt["season"] not in wanted:
            continue
        ws = cutoff - timedelta(days=int(365.25 * WINDOW_YEARS))
        train = [x for x in allm if ws <= x["match_date"] < cutoff and x.get("fthg") is not None]
        ready = model is not None
        if model is None or last_fit is None or (cutoff - last_fit).days >= REFIT_EVERY_DAYS:
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
        oh, od, oa = (_at(mt.get("odds_close"), 0), _at(mt.get("odds_close"), 1),
                      _at(mt.get("odds_close"), 2))
        d = devig([oh, od, oa]) if (oh and od and oa) else None
        rows.append({
            "season": mt["season"],
            "pm": [float(x) for x in model.predict_1x2(h, a)],
            "pk": [float(x) for x in d.probs] if d else None,
            # 0 = П1, 1 = Х, 2 = П2. Первые версии диагностики кодировали
            # факт как 1 при победе хозяев и 2 при победе гостей — ноль не
            # появлялся НИКОГДА, и прогноз «ничья» засчитывался как победа
            # хозяев. Из-за этого столбец факта показывал 0.054 при прогнозе
            # 0.841 — не диагноз, а ерунда.
            "y": 0 if mt["fthg"] > mt["ftag"] else (1 if mt["fthg"] == mt["ftag"] else 2),
        })
    return rows


def logloss(p: np.ndarray, y: np.ndarray) -> float:
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1))))


def main() -> int:
    print("Считаю walk-forward…")
    rows = collect()
    P = np.array([r["pm"] for r in rows])
    Y = np.array([r["y"] for r in rows])
    S = np.array([r["season"] for r in rows])
    va = S == VAL_SEASON
    te = np.isin(S, TEST_SEASONS)
    print(f"Валидация {VAL_SEASON}: {va.sum()} матчей | тест: {te.sum()}")

    out: dict = {"val_season": VAL_SEASON, "test_seasons": list(TEST_SEASONS)}

    # --- 1. калибровка ------------------------------------------------
    print("\n" + "=" * 66)
    print("КАЛИБРОВКА: прогноз на самый вероятный исход")
    print("=" * 66)
    print("%-12s%8s%12s%10s%10s" % ("корзина", "матчей", "средняя p", "факт", "ошибка"))
    buckets = []
    for lo in np.arange(0.30, 0.90, 0.10):
        sub = [r for r, t in zip(rows, te) if t and lo <= max(r["pm"]) < lo + 0.10]
        if len(sub) < 15:
            continue
        mp = float(np.mean([max(r["pm"]) for r in sub]))
        af = float(np.mean([1 if int(np.argmax(r["pm"])) == r["y"] else 0 for r in sub]))
        buckets.append({"lo": round(lo, 2), "n": len(sub), "mean_p": round(mp, 4),
                        "actual": round(af, 4), "gap": round(mp - af, 4)})
        print("%-12s%8d%12.3f%10.3f%10.3f" % ("%.1f-%.1f" % (lo, lo + 0.1),
                                             len(sub), mp, af, mp - af))
    out["calibration"] = buckets

    # --- 2. смещение по исходам ---------------------------------------
    print("\n" + "=" * 66)
    print("СМЕЩЕНИЕ ПО ИСХОДАМ")
    print("=" * 66)
    bias = {}
    test_rows = [r for r, t in zip(rows, te) if t]
    for i, lab in ((0, "П1"), (1, "Х"), (2, "П2")):
        mp = float(np.mean([r["pm"][i] for r in test_rows]))
        # СЧЁТ, а не среднее: список уже отфильтрован до совпадений, поэтому
        # каждый элемент равен 1 и mean() всегда дал бы 1.0.
        af = sum(1 for r in test_rows if r["y"] == i) / len(test_rows)
        bias[lab] = {"model": round(mp, 4), "actual": round(af, 4),
                     "gap": round(mp - af, 4)}
        print("  %-3s модель %.3f  факт %.3f  ->  %+.3f" % (lab, mp, af, mp - af))
    out["bias"] = bias

    # --- 3. сжатие к рынку -------------------------------------------
    has_mkt = [i for i, r in enumerate(rows) if r["pk"] is not None]
    va_idx = [i for i in has_mkt if va[i]]
    te_idx = [i for i in has_mkt if te[i]]
    Pv, Yv = P[va_idx], Y[va_idx]
    Pt, Yt = P[te_idx], Y[te_idx]
    Pt_m = np.array([rows[i]["pk"] for i in te_idx])
    Pt_k = np.array([rows[i]["pk"] for i in va_idx])

    def blend(sample_model, sample_y, w):
        q = w * Pt_m + (1 - w) * sample_model
        return logloss(q, sample_y)

    grid = []
    best_w, best_v = 0.0, 1e9
    for w in np.arange(0, 1.01, 0.1):
        qv = w * Pt_k + (1 - w) * Pv
        v = logloss(qv, Yv)
        grid.append({"w_market": round(float(w), 2), "val_logloss": round(v, 5)})
        if v < best_v:
            best_w, best_v = float(w), v

    print("\n" + "=" * 66)
    print("СЖАТИЕ К ЛИНИЕ БУКМЕКЕРА")
    print("=" * 66)
    for g in grid:
        mark = " <-" if abs(g["w_market"] - best_w) < 1e-9 else ""
        print("  доля рынка %.1f -> log-loss %.5f%s" % (g["w_market"], g["val_logloss"], mark))
    print(f"\n  оптимум на валидации: рынок {best_w:.1f} / модель {1-best_w:.1f}")
    m_only = logloss(Pt, Yt)
    k_only = logloss(Pt_m, Yt)
    blend_te = blend(Pt, Yt, best_w)
    print("\n  ПРОВЕРКА НА ТЕСТЕ (подбор шёл на другом сезоне)")
    print("    только модель        %.5f" % m_only)
    print("    только рынок         %.5f" % k_only)
    print("    смесь w=%.1f           %.5f" % (best_w, blend_te))
    print("    выигрыш против модели %+.5f" % (m_only - blend_te))
    out["market_anchor"] = {"grid": grid, "best_w_market": best_w,
                            "test_model": round(m_only, 5),
                            "test_market": round(k_only, 5),
                            "test_blend": round(blend_te, 5)}

    # --- 4. перекалибровка -------------------------------------------
    # scipy вызывает objective(params, *args), а logloss ждёт (p, y).
    # Без обёртки оптимизатор получает три аргумента и падает — поэтому
    # функции цели объявлены явно, а не передаются напрямую.
    def apply_temp(params, sample):
        T = np.exp(params[0])
        q = sample ** (1.0 / T)
        return q / q.sum(axis=1, keepdims=True)

    def apply_dir(params, sample):
        A = params[:9].reshape(3, 3)
        b = params[9:]
        z = np.log(np.clip(sample, 1e-9, 1)) @ A + b
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def _obj_temp(params, sample, y):
        return logloss(apply_temp(params, sample), y)

    def _obj_dir(params, sample, y):
        return logloss(apply_dir(params, sample), y)

    res_t = minimize(_obj_temp, [0.0], args=(Pv, Yv), method="Nelder-Mead")

    res_d = minimize(_obj_dir, np.zeros(12), args=(Pv, Yv), method="Nelder-Mead",
                     options={"maxiter": 4000, "maxfev": 20000})

    print("\n" + "=" * 66)
    print("ПЕРЕКАЛИБРОВКА САМОЙ МОДЕЛИ (без рынка)")
    print("=" * 66)
    t_te = logloss(apply_temp(res_t.x, Pt), Yt)
    d_te = logloss(apply_dir(res_d.x, Pt), Yt)
    print("  без перекалибровки    %.5f" % m_only)
    print("  температура T=%.3f    %.5f  (%+.5f)" % (np.exp(res_t.x[0]), t_te, t_te - m_only))
    print("  Dirichlet, 12 парам.  %.5f  (%+.5f)" % (d_te, d_te - m_only))
    print("  линия букмекера       %.5f" % k_only)
    out["recalibration"] = {
        "plain": round(m_only, 5),
        "temperature_T": round(float(np.exp(res_t.x[0])), 4),
        "temperature": round(t_te, 5),
        "dirichlet": round(d_te, 5),
        "market": round(k_only, 5),
    }

    dst = REPORTS / "error_diagnostics.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())