"""Скользящая проверка калибровки вероятностей для трёх видов спорта.

Проблема, из- которой модуль появился. В журнале баскетбола все прогнозы
лежат в узком диапазоне 0.50–0.61, а фактическая точность при этом около
95%: модель не выражает уверенность. В журнале футбола — жалоба ровно
обратная: при среднем прогнозе 0.51 в корзине [0.4, 0.6) попали все
матчи подряд. Обе жалобы могут быть и правдой, и шумом выборки из двух
десятков матчей. Решать по журналу нельзя — только на истории.

Проверка устроена так, чтобы на ней нельзя было самообмануться:

1. Скользящее «учил на сезоне N → мерил на N+1» для каждой пары соседних
   сезонов. Калибровка подбирается на одном сезоне и проверяется на
   следующем, который при подборе не участвовал.

2. Правило внедрения записано до запуска (APPLY_RULE): калибровка
   применяется, только если средняя дельта log-loss отрицательна, ни одна
   пара не хуже BAD_TOLERANCE и — если сезон-держак достаточно большой —
   не хуже он сам. Точность при этом обязана не измениться: температура
   монотонна, argmax тот же, изменилась бы точность — значит, в проверке
   баг, а не успех.

3. Отчёт пишется всегда, даже когда калибровка не помогла. Прод читает
   поле applied: false — и оставляет сырые вероятности. Отказ внедрять —
   тоже результат, и прятать его было бы обманом.

Запуск:
    python -m models.calibrate

Отчёт: data/reports/calibration.json
"""
from __future__ import annotations

import json

from etl.config import REPORTS
from . import temperature

# Сколько сезонных матчей нужно, чтобы вообще учить и проверять T.
# Ниже этих чисел подбор температуры — подгонка под шум.
MIN_TRAIN_ROWS = 80
MIN_TEST_ROWS = 40

# Насколько хуже сырого log-loss допустимо в одной паре сезонов.
# Мелкие просадки в пределах этой величины считаются шумом, а не
# доказательством «калибровка вредит».
BAD_TOLERANCE = 0.002

APPLY_RULE = (
    "средняя дельта log-loss по парам < 0, ни одна пара не хуже "
    f"{BAD_TOLERANCE}, сезон-держак (если он достаточно большой) тоже не хуже"
)

BINS = ((0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.8), (0.8, 1.0))


def _bins(rows: list[tuple[list[float], int]], t: float | None) -> list[dict]:
    """Корзины по максимальной вероятности: среднее предсказание против
    фактической частоты. Это единственное, что показывает НАПРАВЛЕНИЕ
    ошибки калибровки — занижает модель уверенность или переоценивает."""
    out = []
    for lo, hi in BINS:
        g = []
        for p, y in rows:
            q = temperature.apply(p, t)
            if lo <= max(q) < hi:
                g.append((p, y, q))
        if not g:
            continue
        acc = sum(1 for p, y, _ in g if p.index(max(p)) == y) / len(g)
        avg = sum(max(q) for _, _, q in g) / len(g)
        out.append({"bin": f"[{lo},{hi})", "n": len(g),
                    "accuracy": round(acc, 4), "avg_p": round(avg, 4),
                    "gap": round(avg - acc, 4)})
    return out


def _accuracy(rows: list[tuple[list[float], int]], t: float | None) -> float:
    if not rows:
        return float("nan")
    hit = 0
    for p, y in rows:
        q = temperature.apply(p, t)
        if q.index(max(q)) == y:
            hit += 1
    return hit / len(rows)


def check_sport(name: str, by_season: dict[str, list], order: list[str]) -> dict:
    """Скользящие пары + отбор сезона-держака + итоговое T."""
    checks = []
    for a, b in zip(order, order[1:]):
        val, test = by_season.get(a, []), by_season.get(b, [])
        if len(val) < MIN_TRAIN_ROWS or len(test) < MIN_TEST_ROWS:
            checks.append({"train": a, "test": b, "n_train": len(val),
                           "n_test": len(test), "skipped": "мало данных"})
            continue
        t = temperature.fit(val)
        raw, cal = temperature.mean_nll(test), temperature.mean_nll(test, t)
        checks.append({
            "train": a, "test": b, "n_train": len(val), "n_test": len(test),
            "T": round(t, 4),
            "logloss_raw": round(raw, 5), "logloss_cal": round(cal, 5),
            "delta": round(cal - raw, 5),
            "accuracy_raw": round(_accuracy(test, None), 4),
            "accuracy_cal": round(_accuracy(test, t), 4),
        })

    # T для прода: учим на всех полных сезонах, кроме последнего —
    # последний остаётся свободным для проверки.
    fit_seasons = order[:-1]
    fit_rows = [(p, y) for s in fit_seasons for p, y in by_season.get(s, [])]
    hold_season = order[-1] if order else None
    hold_rows = by_season.get(hold_season, []) if hold_season else []

    block: dict = {
        "n_seasons": len(order),
        "seasons": order,
        "fit_seasons": fit_seasons,
        "checks": checks,
        "apply_rule": APPLY_RULE,
    }

    real = [c for c in checks if not c.get("skipped")]
    if len(fit_rows) >= MIN_TRAIN_ROWS:
        t_final = temperature.fit(fit_rows)
        block["T"] = round(t_final, 4)
        block["n_fit_rows"] = len(fit_rows)

        if len(hold_rows) >= MIN_TEST_ROWS:
            raw, cal = temperature.mean_nll(hold_rows), temperature.mean_nll(hold_rows, t_final)
            block["holdout"] = {
                "season": hold_season, "n": len(hold_rows),
                "logloss_raw": round(raw, 5), "logloss_cal": round(cal, 5),
                "delta": round(cal - raw, 5),
                "accuracy_raw": round(_accuracy(hold_rows, None), 4),
                "accuracy_cal": round(_accuracy(hold_rows, t_final), 4),
                "bins_raw": _bins(hold_rows, None),
                "bins_cal": _bins(hold_rows, t_final),
            }
        else:
            block["holdout"] = {"season": hold_season, "n": len(hold_rows),
                                "skipped": "мало данных"}
    else:
        block["T"] = None
        block["n_fit_rows"] = len(fit_rows)

    # Правило внедрения.
    if not real or block["T"] is None:
        applied, why = False, "нет ни одной пары сезонов с достаточным объёмом"
    else:
        deltas = [c["delta"] for c in real]
        mean_delta = sum(deltas) / len(deltas)
        bad_pair = [c for c in real if c["delta"] > BAD_TOLERANCE]
        hold = block.get("holdout") or {}
        hold_bad = bool(hold.get("n", 0) >= MIN_TEST_ROWS and hold.get("delta", 0) > 0)
        applied = mean_delta < 0 and not bad_pair and not hold_bad
        if applied:
            why = (f"средняя дельта {mean_delta:+.5f} < 0, худших пар нет, "
                   "держак не хуже")
        else:
            parts = []
            if mean_delta >= 0:
                parts.append(f"средняя дельта {mean_delta:+.5f} >= 0")
            if bad_pair:
                parts.append("пары хуже допустимого: "
                             + ", ".join(f"{c['train']}→{c['test']} {c['delta']:+.5f}"
                                         for c in bad_pair))
            if hold_bad:
                parts.append(f"держак {hold['season']} хуже ({hold['delta']:+.5f})")
            why = "; ".join(parts)

    block["improved_pairs"] = sum(1 for c in real if c["delta"] < 0)
    block["pairs"] = len(real)
    block["applied"] = applied
    block["applied_reason"] = why
    return block


def _football() -> tuple[dict, list[str]]:
    from . import selective
    print("  футбол: walk-forward Dixon-Coles…")
    rows = selective.collect()
    order = [selective.VAL_SEASON] + list(selective.TEST_SEASONS)
    by = {s: [(r["p"], r["y"]) for r in rows if r["season"] == s] for s in order}
    return by, order


def _basketball() -> tuple[dict, list[str]]:
    from .bt_backtest import load, run
    print("  баскетбол: walk-forward…")
    preds = run(load().to_dicts(), verbose=False)
    idx = {"H": 0, "D": 1, "A": 2}
    by: dict[str, list] = {}
    for p in preds:
        by.setdefault(p.season, []).append((list(p.model_probs), idx[p.actual]))
    order = sorted(by)
    return by, order


def _rpl() -> tuple[dict, list[str]]:
    from .rpl_selective import collect
    print("  РФПЛ: walk-forward Dixon-Coles…")
    rows = collect(verbose=False)
    order = sorted({r["season"] for r in rows})
    by = {s: [(r["p"], r["y"]) for r in rows if r["season"] == s] for s in order}
    return by, order


def main() -> int:
    report: dict = {}
    for sport, loader in (("football", _football), ("basketball", _basketball),
                          ("rpl", _rpl)):
        print(f"\n=== {sport} ===")
        by, order = loader()
        block = check_sport(sport, by, order)
        report[sport] = block
        print(f"  сезоны: {', '.join(order)}")
        for c in block["checks"]:
            if c.get("skipped"):
                print(f"    {c['train']} -> {c['test']}: {c['skipped']} "
                      f"(n_train {c['n_train']}, n_test {c['n_test']})")
            else:
                print(f"    {c['train']} -> {c['test']}: T={c['T']:.3f} "
                      f"logloss {c['logloss_raw']:.5f} -> {c['logloss_cal']:.5f} "
                      f"({c['delta']:+.5f}) | точность {c['accuracy_raw']:.3f} "
                      f"-> {c['accuracy_cal']:.3f} | n={c['n_test']}")
        hold = block.get("holdout") or {}
        if hold.get("skipped"):
            print(f"  держак {hold['season']}: {hold['skipped']} (n={hold['n']})")
        elif hold:
            print(f"  держак {hold['season']}: n={hold['n']} "
                  f"logloss {hold['logloss_raw']:.5f} -> {hold['logloss_cal']:.5f} "
                  f"({hold['delta']:+.5f})")
        print(f"  T={block['T']} | улучшилось пар {block['improved_pairs']}/"
              f"{block['pairs']} | ВНЕДРЯТЬ: {block['applied']}")
        print(f"  причина: {block['applied_reason']}")

    dst = REPORTS / "calibration.json"
    dst.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
