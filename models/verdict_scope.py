"""Считать ли тотал в вердикт.

Сейчас вердикт «угадано / мимо» в журнале определяется ТОЛЬКО исходом
матча: берётся argmax из трёх вероятностей П1/Х/П2 и сравнивается с
фактом. Тотал 2.5 показывается на карточке, но в вердикт не входит.

Это не обязательно ошибка, но вопрос «почему» честный, и ответ должен
опираться на числа, а не на вкус. Меряем три варианта на отложенных
сезонах:

  1. только исход — как сейчас;
  2. только тотал;
  3. оба рынка сразу — оба угаданы.

Если третий вариант лучше первых двух, тотал deserves места в вердикте.
Если хуже — оставить как есть, и сказать об этом прямо.

Замер идёт на тех же отложенных сезонах и с тем же walk-forward, что и
основной бэктест, иначе цифры нельзя сопоставлять с опубликованными.

Запуск:
    python -m models.verdict_scope
"""
from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import polars as pl

from etl.config import PROCESSED, REPORTS
from models.dixon_coles import DixonColesModel
from models.poisson import over_prob

TEST_SEASONS = ("2023/24", "2024/25", "2025/26")
WINDOW_YEARS = 3
REFIT_EVERY_DAYS = 7
MIN_TRAIN_MATCHES = 200

# Порог вердикта в проекте — из models/verdict.py. Здесь он не нужен:
# вопрос о том, что считать попаданием, а не о том, когда показывать.
def main() -> int:
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
        if not ready or model is None:
            continue
        h, a = mt["home_id"], mt["away_id"]
        for t in (h, a):
            model.attack.setdefault(t, 0.0)
            model.defence.setdefault(t, 0.0)
        p = [float(x) for x in model.predict_1x2(h, a)]
        over, _ = over_prob(model.score_matrix(h, a), 2.5)
        rows.append({
            "p": p,
            "over25": float(over),
            "y": 0 if mt["fthg"] > mt["ftag"] else (1 if mt["fthg"] == mt["ftag"] else 2),
            "tot_over": mt["fthg"] + mt["ftag"] > 2.5,
        })

    n = len(rows)
    if n == 0:
        raise SystemExit("нет строк")

    hit12 = sum(1 for r in rows if int(np.argmax(r["p"])) == r["y"])
    hitT = sum(1 for r in rows if (r["over25"] >= 0.5) == r["tot_over"])
    both = sum(1 for r in rows
               if int(np.argmax(r["p"])) == r["y"]
               and (r["over25"] >= 0.5) == r["tot_over"])
    # средняя доля рынков, в которых ошиблись — честнее, чем «оба сразу»
    wrong = sum((int(np.argmax(r["p"])) != r["y"]) + ((r["over25"] >= 0.5) != r["tot_over"])
                for r in rows) / n

    print("матчей в тесте:", n)
    print()
    print("%-34s%10s%10s" % ("вариант вердикта", "попаданий", "точность"))
    print("-" * 56)
    print("%-34s%10d%9.1f%%" % ("только исход (как сейчас)", hit12, 100 * hit12 / n))
    print("%-34s%10d%9.1f%%" % ("только тотал 2.5", hitT, 100 * hitT / n))
    print("%-34s%10d%9.1f%%" % ("оба рынка должны совпасть", both, 100 * both / n))
    print()
    print("доля рынков, где ошиблись: %.3f" % wrong)
    print()
    print("Обратите внимание: «оба сразу» — это не сумма двух метрик.")
    print("Ошибиться хотя бы на одном рынке и потерять всё сразу.")
    print("Поэтому даже при равной точности рынков комбинация даст худший")
    print("результат, чем любой из них по отдельности.")

    # Насколько рынки независимы: если независимы, совпадений будет 0.5*0.5.
    indep = (hit12 / n) * (hitT / n)
    print()
    print("Проверка независимости рынков:")
    print("  доля попаданий обоих рынков:      %.4f" % (both / n))
    print("  произведение долей (если независ.): %.4f" % indep)
    print("  -> рынки %s" % ("независимы" if abs(both / n - indep) < 0.05
                            else "заметно связаны"))

    out = {"n": n,
           "outcome_only": round(hit12 / n, 4),
           "total_only": round(hitT / n, 4),
           "both": round(both / n, 4),
           "wrong_share": round(wrong, 4),
           "independent": round(indep, 4),
           "note": "Вердикт в журнале считается по исходу; тотал не учитывается."}
    dst = REPORTS / "verdict_scope.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())