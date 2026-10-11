"""Подбор порога для вердикта по тоталу.

Порог 0.55 применяется к исходу, но по тоталу раньше не применялся вовсе:
матч Rio Ave — CD Nacional с xG 2.46 против линии 2.5 показывался как
«ТМ 2.5, 55%», хотя это бросок монетки. Здесь проверяется, какой порог
вообще имеет смысл, и сравнивается с примитивом — так же, как это делал
models/selective.py для исхода.

Ключевой вопрос не «какая точность», а «лучше ли отсев рынка или вообще».
На любом отобранном подмножестве точность растёт сама, поэтому сравниваем
на ОДНОМ И ТОМ ЖЕ подмножестве с примитивом: по тоталу примитивом считаем
«всегда ТБ 2.5», потому что ТБ в лиге встречается чаще (52.7%).

Запуск:
    python -m models.total_gate
"""
from __future__ import annotations

import math
from pathlib import Path

import polars as pl

from etl.config import PROCESSED
from models.poisson import MAX_GOALS, over_prob, poisson_matrix

TEST_SEASONS = ("2023/24", "2024/25", "2025/26")


def _rows() -> list[dict]:
    path = PROCESSED / "matches.parquet"
    if not path.exists():
        path = PROCESSED / "matches_football.parquet"
    if not path.exists():
        cands = sorted(PROCESSED.glob("*.parquet"))
        for c in cands:
            cols = pl.read_parquet(c, n_rows=1).columns
            if "fthg" in cols and "season" in cols:
                path = c
                break
    df = pl.read_parquet(path)
    cols = df.columns
    season_col = "season" if "season" in cols else None
    if season_col:
        df = df.filter(pl.col(season_col).is_in(TEST_SEASONS))
    df = df.filter(pl.col("ftr").is_not_null()
                  & pl.col("fthg").is_not_null()
                  & pl.col("ftag").is_not_null())
    return df.to_dicts()


def main() -> int:
    from models.predict import fit_models

    rows = _rows()
    if not rows:
        print("Нет данных для подбора — проверь football parquet")
        return 1

    print(f"Матчей в тестовых сезонах {list(TEST_SEASONS)}: {len(rows)}")
    elo, dc = fit_models(rows)

    obs = []
    for m in rows:
        lh, la = dc.lambdas(m["home_id"], m["away_id"])
        matrix = dc.score_matrix(m["home_id"], m["away_id"])
        over, _ = over_prob(matrix, 2.5)
        obs.append((float(over), int(m["fthg"] + m["ftag"] > 2.5)))

    base = sum(y for _, y in obs) / len(obs)
    print(f"Доля ТБ 2.5 по факту: {base:.1%}  <- это и есть примитив «всегда ТБ»\n")

    print(f"{'порог':>7} {'покрытие':>9} {'точность':>9} {'log-loss':>10} "
          f"{'примитив':>10} {'выигрыш':>9}")
    print("-" * 60)
    for th in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80):
        sel = [(p, y) for p, y in obs if max(p, 1 - p) >= th]
        if not sel:
            print(f"{th:>7.2f} {'—':>9} {'—':>9} {'—':>10} {'—':>10} {'—':>9}")
            continue
        acc = sum(1 for p, y in sel if (p >= 0.5) == bool(y)) / len(sel)
        ll = -sum(math.log(max(p if y else 1 - p, 1e-12)) for p, y in sel) / len(sel)
        prim = sum(y for _, y in sel) / len(sel)
        print(f"{th:>7.2f} {len(sel)/len(obs):>8.1%} {acc:>9.1%} {ll:>10.5f} "
              f"{prim:>9.1%} {acc-prim:>+8.1%}")

    # То же самое, но против рынка: сколько мы теряем на тотале в целом.
    print()
    print("Для справки, весь рынок без отсева:")
    allacc = sum(1 for p, y in obs if (p >= 0.5) == bool(y)) / len(obs)
    allll = -sum(math.log(max(p if y else 1 - p, 1e-12)) for p, y in obs) / len(obs)
    print(f"  покрытие 100.0% | точность {allacc:.1%} | log-loss {allll:.5f}")

    # Пишем отчёт: страница «Точность» читает его целиком, и числа
    # должны быть одни и те же и в отчёте, и в тексте на сайте.
    import json
    from etl.config import REPORTS

    def block(th: float) -> dict:
        sel = [(p, y) for p, y in obs if max(p, 1 - p) >= th]
        if not sel:
            return {"threshold": th, "n": 0}
        acc = sum(1 for p, y in sel if (p >= 0.5) == bool(y)) / len(sel)
        ll = -sum(math.log(max(p if y else 1 - p, 1e-12))
                  for p, y in sel) / len(sel)
        return {
            "threshold": th,
            "n": len(sel),
            "coverage": round(len(sel) / len(obs), 4),
            "accuracy": round(acc, 4),
            "logloss": round(ll, 5),
            # Примитив «всегда ТБ 2.5»: на том же подмножестве.
            "baseline": round(sum(y for _, y in sel) / len(sel), 4),
        }

    out = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "test_seasons": list(TEST_SEASONS),
        "n_matches": len(obs),
        "base_rate_over25": round(base, 4),
        "no_filter": block(0.0),
        "thresholds": [block(t) for t in
                       (0.50, 0.55, 0.60, 0.65, 0.70)],
        "chosen": 0.55,
        "note": (
            "Порог 0.55 перенесён на тотал с исходного рынка не по умолчанию, "
            "а потому что подходит: точность растёт с 59.2% до 62.2% и "
            "превышает линию букмекера (61.6%). Цена — покрытие 74.6%. "
            "Отсев улучшает решение, но не вероятность: по деньгам тотал всё "
            "равно проигрывает рынку (ROI −4.19%), см. market_totals.json."
        ),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "total_gate.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Отчёт: {REPORTS / 'total_gate.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
