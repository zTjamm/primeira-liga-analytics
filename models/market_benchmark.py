"""С кем мы себя на самом деле сравниваем.

Страница «Точность» долгое время мерила нас по строке «market», а это
линия AvgC* — среднее по конторам, в которое заложена маржа каждой из
них (около 6.8%). Это завышает нас: сравниваться надо с тем, кто
зарабатывает меньше всех.

Betfair Exchange (BFEC*) — биржа, маржа около 0.6%. Она в исходных
файлах была всегда, но в конвейер не попадала: колонок не было в списке
WANTED, и из-за этого нормализация их просто не видела. Теперь есть.

Вторая линия в отчёте — азиатский хэндикап (AHCh + AvgCAHH/AvgCAHA).
Это отдельный рынок, и раньше он лежал в данных нетронутым.

Подбор и сравнение — на отложенных сезонах, обучение строго на матчах
до даты прогноза.

Запуск:
    python -m models.market_benchmark
"""
from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import polars as pl

from etl.config import PROCESSED, REPORTS
from models.dixon_coles import DixonColesModel
from models.market import devig, devig_two_way

TEST_SEASONS = ("2023/24", "2024/25", "2025/26")
WINDOW_YEARS = 3
REFIT_EVERY_DAYS = 7
MIN_TRAIN_MATCHES = 200


def _at(seq, i: int) -> float | None:
    try:
        v = seq[i]
    except Exception:
        return None
    if v is None or not np.isfinite(v) or not (1.01 < v < 1000):
        return None
    return float(v)


def collect() -> list[dict]:
    allm = (pl.read_parquet(PROCESSED / "matches.parquet")
            .sort("match_date").to_dicts())
    rows: list[dict] = []
    model: DixonColesModel | None = None
    last_fit = None

    for mt in allm:
        if mt.get("fthg") is None or mt.get("ftag") is None:
            continue
        cutoff = mt["match_date"]
        if mt["season"] not in TEST_SEASONS:
            continue
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

        y = 0 if mt["fthg"] > mt["ftag"] else (1 if mt["fthg"] == mt["ftag"] else 2)
        rec = {"season": mt["season"], "y": y,
               "model": [float(x) for x in model.predict_1x2(h, a)]}

        oh, od, oa = (_at(mt.get("odds_close"), 0), _at(mt.get("odds_close"), 1),
                      _at(mt.get("odds_close"), 2))
        if oh and od and oa:
            d = devig([oh, od, oa])
            if d:
                rec["avg"] = [float(x) for x in d.probs]
                rec["avg_margin"] = float(np.sum([1 / oh, 1 / od, 1 / oa]) - 1.0)

        bh, bd, ba = (_at(mt.get("bfx_close"), 0), _at(mt.get("bfx_close"), 1),
                      _at(mt.get("bfx_close"), 2))
        if bh and bd and ba:
            d = devig([bh, bd, ba])
            if d:
                rec["bfx"] = [float(x) for x in d.probs]
                rec["bfx_margin"] = float(np.sum([1 / bh, 1 / bd, 1 / ba]) - 1.0)

        # Азиатский хэндикап — двусторонний рынок на разницу мячей
        # относительно линии, а не на исход.
        line = mt.get("ah_line")
        ahh, aha = _at(mt.get("ah_odds"), 0), _at(mt.get("ah_odds"), 1)
        if line is not None and ahh and aha:
            q = devig_two_way([ahh, aha])
            if q is not None:
                # Знак линии соответствует колонкам AvgCAHH/AvgCAHA: при
                # положительной линии фаворит — гости, поэтому стороны
                # меняются местами.
                home_covers = mt["fthg"] - mt["ftag"] > line
                rec["ah"] = {"q": [float(q[0]), float(q[1])],
                             "y": 1 if home_covers else 0,
                             "line": float(line),
                             "margin": float(1 / ahh + 1 / aha - 1.0)}
        rows.append(rec)
    return rows


def ll3(sample: list[dict], key: str) -> tuple[float | None, int, float | None]:
    sub = [r for r in sample if r.get(key)]
    if not sub:
        return None, 0, None
    ll = float(-np.mean([np.log(r[key][r["y"]]) for r in sub]))
    acc = float(np.mean([int(np.argmax(r[key]) == r["y"]) for r in sub]))
    marg = float(np.mean([r.get(key + "_margin", np.nan) for r in sub])) if sub else None
    return ll, len(sub), marg, acc


def main() -> int:
    print("Считаю walk-forward…")
    rows = collect()
    print(f"матчей в тесте: {len(rows)}\n")

    out: dict = {"test_seasons": list(TEST_SEASONS), "n": len(rows), "lines": {}}

    print("=" * 74)
    print("ЭТАЛОН ДЛЯ СРАВНЕНИЯ (исход матча, чем ниже log-loss — тем точнее)")
    print("=" * 74)
    print("%-34s%8s%11s%10s%10s" % ("линия", "матчей", "log-loss", "маржа", "точность"))
    print("-" * 74)

    # Модель — общий знаменатель, она одинакова для всех строк.
    m_ll = float(-np.mean([np.log(r["model"][r["y"]]) for r in rows]))
    m_acc = float(np.mean([int(np.argmax(r["model"]) == r["y"]) for r in rows]))
    print("%-34s%8d%11.5f%10s%9.1f%%" % ("наша модель", len(rows), m_ll, "-", 100 * m_acc))
    out["lines"]["model"] = {"n": len(rows), "logloss": round(m_ll, 5),
                             "accuracy": round(m_acc, 4)}

    for key, label in (("avg", "среднее по конторам (было «рынок»)"),
                       ("bfx", "Betfair Exchange")):
        ll, n, marg, acc = ll3(rows, key)
        if ll is None:
            print("%-34s%8s  нет данных" % (label, "-"))
            continue
        print("%-34s%8d%11.5f%9.2f%%%9.1f%%" % (label, n, ll, 100 * marg, 100 * acc))
        out["lines"][key] = {"n": n, "logloss": round(ll, 5),
                             "margin": round(marg, 4), "accuracy": round(acc, 4)}

    # Честное сравнение только на общих матчах: у линий разное покрытие,
    # а метрики на разных выборках несравнимы.
    both = [r for r in rows if r.get("avg") and r.get("bfx")]
    print()
    if both:
        print("=" * 74)
        print(f"НА ОДНИХ И ТЕХ ЖЕ {len(both)} МАТЧАХ (покрытия линий разные)")
        print("=" * 74)
        print("%-34s%11s%12s" % ("", "log-loss", "маржа"))
        for key, label in (("model", "наша модель"), ("avg", "среднее по конторам"),
                           ("bfx", "Betfair Exchange")):
            ll = float(-np.mean([np.log(r[key][r["y"]]) for r in both]))
            mk = r"".join(["-"])
            if key != "model":
                mk = "%.2f%%" % (100 * np.mean([r[key + "_margin"] for r in both]))
            print("%-34s%11.5f%12s" % (label, ll, mk))
        gap_avg = float(-np.mean([np.log(r["model"][r["y"]]) for r in both])) - \
                  float(-np.mean([np.log(r["avg"][r["y"]]) for r in both]))
        gap_bfx = float(-np.mean([np.log(r["model"][r["y"]]) for r in both])) - \
                  float(-np.mean([np.log(r["bfx"][r["y"]]) for r in both]))
        print()
        print(f"  разрыв со средним по конторам: {gap_avg:+.5f}")
        print(f"  разрыв с биржей:               {gap_bfx:+.5f}")
        print(f"  мера против нас была завышена на {gap_avg - gap_bfx:.5f}")
        out["common"] = {"n": len(both), "gap_avg": round(gap_avg, 5),
                         "gap_bfx": round(gap_bfx, 5)}

    # Азиатский хэндикап
    ah = [r for r in rows if r.get("ah")]
    print()
    print("=" * 74)
    print("АЗИАТСКИЙ ХЭНДИКАП (двусторонний рынок на разницу мячей)")
    print("=" * 74)
    if ah:
        ll = float(-np.mean([np.log(r["ah"]["q"][r["ah"]["y"]]) for r in ah]))
        acc = float(np.mean([int(np.argmax(r["ah"]["q"]) == r["ah"]["y"]) for r in ah]))
        mk = float(np.mean([r["ah"]["margin"] for r in ah]))
        lines = sorted({r["ah"]["line"] for r in ah})
        print(f"  матчей с линией: {len(ah)}")
        print(f"  log-loss {ll:.5f}   маржа {100*mk:.2f}%   точность {100*acc:.1f}%")
        print(f"  используемые линии: {', '.join('%+.1f' % x for x in lines)}")
        out["asian_handicap"] = {"n": len(ah), "logloss": round(ll, 5),
                                 "margin": round(mk, 4), "accuracy": round(acc, 4),
                                 "lines": lines}
        print()
        print("  Отдельный рынок, не сравнимый с 1X2: это ставка на разницу")
        print("  мячей относительно линии, а не на исход матча.")
    else:
        print("  нет данных")

    dst = REPORTS / "market_benchmark.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())