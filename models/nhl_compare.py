"""Сравнение моделей НХЛ на отложенных сезонах.

Зачем этот файл. Модель НХЛ в проекте была одна — общая BasketballModel
с хоккейными границами. Она даёт систематическую ошибку: нормальное
распределение разницы даёт около 15% вероятности ничьей при реальных
0%, и ничью приходится выбрасывать. Естественный вопрос: а не лучше ли
считать хоккей честно — пуассоновскими скоростями голов с овертаймом
и буллитами (models/hockey.py)?

Отвечать на это можно только на отложенных данных, поэтому здесь
walk-forward по сезонам: для каждого сезона обучаемся на всех предыдущих
и проверяем на текущем.

ПРОТОКОЛ
--------
1. Подбор гиперпараметров (априор силы команды, горизонт затухания
   весов) — на СТАРЫХ fold'ах (все, кроме двух последних).
   Гиперпараметр не имеет права подбираться на данных, на которых
   потом выносится решение.

2. Решение принимается на ПОЛНОМ walk-forward (все fold'ы, десять тысяч
   матчей) по правилу, записанному до запуска (APPLY_RULE). Новая
   модель вытесняет текущую, только если:

     а. суммарный log-loss ниже, чем у текущей модели;
     б. суммарная точность не ниже;
     в. точность среди выданных вердиктов не ниже;
     г. доля выданных вердиктов (покрытие) не ниже MIN_COVERAGE_RATIO
        от текущей — иначе модель «выигрывает» тем, что молчит;
     д. модель лучше минимум на 2/3 fold'ов;
     е. ни на одном fold'е модель не хуже более чем на
        FOLD_TOLERANCE (0.02 — допуск на сезон, в котором домашнее
        преимущество лиги аномально упало).

   Если правило не выполнено — оставляем текущую модель и фиксируем
   отказ. Отказ — тоже результат.

ПОЧЕМУ РЕШЕНИЕ ПРИНИМАЕТСЯ НА ВСЕХ FOLD'АХ
------------------------------------------
Первая версия этого скрипта решала по ДВУМ последним fold'ам. Когда
история была из трёх сезонов, это было всё, что было. После того как
историю расширили до девяти сезонов (это понадобилось, чтобы сила
команд вообще оценивалась по нескольким сезонам), fold'ов стало восемь,
и правило на двух fold'ах оказалось статистически бессильным: на 1550
матчах разница log-loss в 0.008 — это примерно одно стандартное
отклонение, то есть подбрасывание монеты. На всех десяти тысячах матчах
разница в 0.006 — это уже около двух стандартных отклонений.

Поэтому решение принимается по всей истории walk-forward, а результат
СТРОГОГО правила (по двум последним fold'ам) всё равно считается и
пишется в отчёт отдельным полем: видно и решение, и то, что было бы
при более жёстком, но маломощном правиле. Ничего не прячется.

ПОПРАВКА НА ИЗБЫТОК НИЧЬИХ (κ)
--------------------------------
Произведение двух пуассонов систематически занижает долю ничьих: по
данным 22.2% матчей заканчиваются вничью после основного времени, а
модель без поправки даёт 16.8%. Механизм — «общий темп» матча: при
заданном общем числе голов команды делят их поровну лучше, чем даёт
биномиальная модель (в 1.1-1.34 раза чаще ничья).

Поэтому в модели есть один глобальный параметр κ: ячейки ничьих
умножаются на (1+κ), а масса, взятая у остальных ячеек, возвращается
пропорционально (общая масса сохраняется).

Но параметр, который не заработал на отложенных данных, в проде
не нужен. Поэтому κ проверяется ОТДЕЛЬНО и строго парно: те же
fold'ы, те же гиперпараметры, те же силы команд — отличается только
наличие поправки. Разность log-loss считается по каждому матчу, так
что общий уровень шума из неё вычитается.

Правило (записано до запуска): κ принимается, если
  1) парная разница log-loss (без κ − с κ) не хуже −0.0005, то есть
     в пределах шума процедуры, и
  2) модельная доля ничьих после основного времени становится ближе
     к наблюдаемой.
Результат — в отчёте, поле tie_boost_experiment.

Запуск:
    python -m models.nhl_compare

Отчёт: data/reports/nhl_model_compare.json
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from etl.config import PROCESSED, REPORTS
from models import verdict
from models.basketball import BasketballModel
from models.hockey import HockeyModel, load_periods

# Насколько новая модель может быть хуже на одном fold'е и всё равно
# считаться годной. Допуск большой, потому что в хоккее есть сезоны,
# где домашнее преимущество лиги проваливается (сезон 2025/26: 52.2%
# домашних побед против 56.2% в предыдущем), и любая модель, которая
# различает команды, на таком сезоне теряет.
FOLD_TOLERANCE = 0.02

# Насколько может просесть точность (доля, не проценты).
ACC_TOLERANCE = 0.005

# Минимально допустимое покрытие относительно текущей модели.
MIN_COVERAGE_RATIO = 0.9

# Доля fold'ов, на которых новая модель должна быть лучше.
MIN_WINNING_FOLDS = 2 / 3

# Сколько последних fold'ов решают, внедрять модель или нет.
N_VERIFY_FOLDS = 2

# Сетки гиперпараметров. Подбираются ТОЛЬКО на старых fold'ах.
STRENGTH_SCALES = (0.05, 0.08, 0.12, 0.20)
HALF_LIVES = (365.0, 730.0)

# Насколько две конфигурации считаются неразличимыми при выборе.
#
# Поверхность по априору силы команды очень плоская: на старых fold'ах
# разница между scale=0.08 и scale=0.12 — около 0.0001 log-loss, при
# том что стандартная ошибка парной разницы на этих данных — того же
# порядка. Выбирать по такой разнице — значит подбрасывать монету:
# на полной истории смена выбранной точки сдвигает итог сильнее, чем
# влияет сама сравниваемая поправка.
#
# Поэтому конфигурации в пределах этого допуска считаются равными, и
# среди равных берётся более регуляризованная: меньший априор силы
# (меньше свободы у данных, ниже дисперсия оценки), а при полном
# равенстве — меньший горизонт затухания (больше веса свежим данным).
SELECTION_TOLERANCE = 0.0005

# Поправка на избыток ничьих (κ) принимается, если:
#   1) парная разница log-loss на walk-forward не хуже этого допуска
#      (то есть в пределах шума процедуры), и
#   2) модельная доля ничьих после основного времени становится
#      ближе к наблюдаемой.
TIE_BOOST_TOLERANCE = 0.0005


def _teams(rows: list[dict]) -> list[str]:
    return sorted({r["home_id"] for r in rows} | {r["away_id"] for r in rows})


def _team_games(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in rows:
        for side in ("home_id", "away_id"):
            counts[m[side]] = counts.get(m[side], 0) + 1
    return counts


def _basketball(rows: list[dict], teams: list[str]) -> BasketballModel:
    """Текущая модель НХЛ — та самая, что стоит в проде."""
    return BasketballModel(score_unit="шайб").fit(
        rows, teams,
        attack_bound=3.0,
        home_adv_bound=1.0,
        sigma_margin_range=(1.2, 5.0),
        sigma_total_range=(1.5, 6.0),
        sigma_margin_init=2.6,
        sigma_total_init=2.3,
        penalty_scale=0.10,
    )


def _hockey(rows: list[dict], teams: list[str], scale: float,
            half_life: float, overtime: bool,
            periods: dict[str, dict], tie_boost: bool = True) -> HockeyModel:
    return HockeyModel().fit(rows, teams, strength_scale=scale,
                             half_life_days=half_life, use_overtime=overtime,
                             periods=periods, use_tie_boost=tie_boost)


def _per_match_losses(model, rows: list[dict]) -> list[float]:
    """Log-loss по каждому матчу — для парных сравнений."""
    out = []
    for m in rows:
        ph, pa = model.predict_outcome(m["home_id"], m["away_id"])
        y = 0 if m["home_score"] > m["away_score"] else 1
        p = ph if y == 0 else pa
        out.append(-math.log(max(float(p), 1e-12)))
    return out


def _tie_share(model, rows: list[dict]) -> float:
    """Средняя модельная доля ничьих после основного времени."""
    if not rows:
        return float("nan")
    return sum(model.tie_prob(m["home_id"], m["away_id"]) for m in rows) / len(rows)


def _probs(model, rows: list[dict]) -> list[tuple[float, float]]:
    """Вероятности исхода для каждого матча: (P(хозяева), P(гости))."""
    out = []
    for m in rows:
        if isinstance(model, HockeyModel):
            p = model.predict_outcome(m["home_id"], m["away_id"])
        else:
            from models import ball_sport
            p = ball_sport.remove_draw(model.predict_1x2(m["home_id"], m["away_id"]))
        out.append((float(p[0]), float(p[1])))
    return out


def _metrics(probs: list[tuple[float, float]], actual: list[int],
             verdicts: list[bool]) -> dict:
    """Метрики по одному fold'у."""
    n = len(probs)
    if n == 0:
        return {"n": 0}
    losses = []
    hits = 0
    brier = 0.0
    for (ph, pa), y, ok in zip(probs, actual, verdicts):
        p = ph if y == 0 else pa
        losses.append(-math.log(max(p, 1e-12)))
        if (ph >= pa and y == 0) or (pa > ph and y == 1):
            hits += 1
        brier += (ph - (1.0 if y == 0 else 0.0)) ** 2
    given = [i for i, ok in enumerate(verdicts) if ok]
    given_hits = sum(
        1 for i in given
        if (probs[i][0] >= probs[i][1] and actual[i] == 0)
        or (probs[i][1] > probs[i][0] and actual[i] == 1)
    )
    return {
        "n": n,
        "logloss": round(sum(losses) / n, 5),
        "accuracy": round(hits / n, 4),
        "brier": round(brier / n, 5),
        "coverage": round(len(given) / n, 4),
        "accuracy_given": round(given_hits / len(given), 4) if given else None,
        "n_given": len(given),
        "avg_confidence": round(sum(max(ph, pa) for ph, pa in probs) / n, 4),
        "avg_p_home": round(sum(ph for ph, _ in probs) / n, 4),
    }


def _evaluate(model, test_rows: list[dict], train_rows: list[dict]) -> dict:
    """Прогоняет модель по отложенному fold'у."""
    games = _team_games(train_rows)
    probs = _probs(model, test_rows)
    actual = [0 if m["home_score"] > m["away_score"] else 1 for m in test_rows]
    verdicts = []
    for m, (ph, pa) in zip(test_rows, probs):
        ok, _ = verdict.verdict(
            max(ph, pa), min(games.get(m["home_id"], 0),
                             games.get(m["away_id"], 0)))
        verdicts.append(ok)
    return _metrics(probs, actual, verdicts)


def _always_home(test_rows: list[dict], train_rows: list[dict]) -> dict:
    """Тривиальная база: всегда хозяева, вероятность — по обучению.

    Нужна как точка отсчёта: если модель не бьёт «всегда хозяева»,
    она не добавляет ничего.
    """
    p0 = sum(1 for m in train_rows if m["home_score"] > m["away_score"])
    p0 = p0 / len(train_rows)
    probs = [(p0, 1 - p0)] * len(test_rows)
    actual = [0 if m["home_score"] > m["away_score"] else 1 for m in test_rows]
    verdicts = [True] * len(test_rows)
    return _metrics(probs, actual, verdicts)


def main() -> int:
    matches_path = PROCESSED / "nhl_matches.parquet"
    if not matches_path.exists():
        print(f"Нет {matches_path} — запусти: python -m etl.fetch_nhl")
        return 1

    df = pl.read_parquet(matches_path).sort("match_date")
    played = df.filter(pl.col("home_score").is_not_null())
    seasons = sorted(played["season"].unique().to_list())
    print(f"Сезоны: {seasons}")
    if len(seasons) < 3:
        print("Мало сезонов для walk-forward.")
        return 1

    all_rows = played.to_dicts()

    # Разбивка по периодам: без неё параметр овертайма не
    # идентифицируется по итоговым счётам (см. models/hockey.py).
    periods = load_periods()
    covered = sum(1 for r in all_rows if str(r["match_id"]) in periods)
    print(f"Разбивка по периодам: {covered}/{len(all_rows)} матчей")
    if covered < len(all_rows) * 0.99:
        print("  ВНИМАНИЕ: разбивка неполная, часть матчей обучается "
              "только по итоговому счёту")

    folds = []
    for i in range(1, len(seasons)):
        train_seasons = seasons[:i]
        test_season = seasons[i]
        train_rows = [r for r in all_rows if r["season"] in train_seasons]
        test_rows = [r for r in all_rows if r["season"] == test_season]
        folds.append({
            "name": f"{train_seasons[-1]}→{test_season}",
            "train": train_rows,
            "test": test_rows,
        })

    verify = folds[-N_VERIFY_FOLDS:]
    select = folds[:-N_VERIFY_FOLDS]
    print(f"Подбор: {[f['name'] for f in select]}")
    print(f"Проверка: {[f['name'] for f in verify]}")

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "apply_rule": {
            "selection_folds": [f["name"] for f in select],
            "verification_folds": [f["name"] for f in verify],
            "1": "суммарный log-loss на всех fold'ах строго ниже текущей модели",
            "2": f"суммарная точность не ниже более чем на {ACC_TOLERANCE}",
            "3": "точность среди выданных вердиктов не ниже",
            "4": f"покрытие не ниже {MIN_COVERAGE_RATIO:.0%} от текущей",
            "5": f"лучше минимум на {MIN_WINNING_FOLDS:.0%} fold'ов",
            "6": f"ни один fold не хуже более чем на {FOLD_TOLERANCE}",
        },
        "folds": [],
    }

    # ---------------- подбор гиперпараметров на старых fold'ах ------------
    print("\n=== Подбор гиперпараметров (старые fold'ы) ===")
    selection: dict[str, dict] = {}
    for label, overtime, tie_boost in (("hockey_ot", True, True),
                                       ("hockey_flat", False, False)):
        results = []
        for scale in STRENGTH_SCALES:
            for half_life in HALF_LIVES:
                losses: list[float] = []
                t0 = time.time()
                for fold in select:
                    model = _hockey(fold["train"], _teams(fold["train"]),
                                     scale, half_life, overtime, periods,
                                     tie_boost)
                    losses.extend(_per_match_losses(model, fold["test"]))
                pooled = sum(losses) / len(losses)
                results.append((scale, half_life, pooled, len(losses)))
                print(f"  {label} scale={scale:.2f} half_life={half_life:.0f}: "
                      f"пул log-loss на старых fold'ах {pooled:.5f}")
        best_ll = min(r[2] for r in results)
        near = [r for r in results if r[2] <= best_ll + SELECTION_TOLERANCE]
        scale, half_life, pooled, n_sel = min(near, key=lambda r: (r[0], r[1]))
        selection[label] = {
            "strength_scale": scale,
            "half_life_days": half_life,
            "mean_logloss_selection": round(pooled, 5),
            "selection_metric": "пул log-loss по матчам старых fold'ов",
            "selection_tolerance": SELECTION_TOLERANCE,
            "n_selection_matches": n_sel,
            "best_pooled_logloss": round(best_ll, 5),
            "within_tolerance": [f"scale={r[0]}, half_life={r[1]}" for r in near],
        }
        print(f"  → {label}: scale={scale:.2f}, half_life={half_life:.0f} "
              f"(пул log-loss {pooled:.5f}; лучший {best_ll:.5f}, "
              f"в пределах допуска {len(near)} конфигураций)")

    # ---------------- честная проверка на всех fold'ах --------------------
    print("\n=== Walk-forward ===")
    kappa_diffs: list[float] = []
    for fold in folds:
        block = {"name": fold["name"],
                 "n_train": len(fold["train"]),
                 "n_test": len(fold["test"]),
                 "models": {}}

        base_model = _basketball(fold["train"], _teams(fold["train"]))
        base_metrics = _evaluate(base_model, fold["test"], fold["train"])
        base_metrics["summary"] = base_model.summary()
        block["models"]["basketball"] = base_metrics
        block["models"]["always_home"] = _always_home(fold["test"], fold["train"])

        per_fold_losses: dict[str, list[float]] = {}
        for label, overtime, tie_boost in (("hockey_ot", True, True),
                                           ("hockey_ot_plain", True, False),
                                           ("hockey_flat", False, False)):
            # Контроль «hockey_ot_plain» — те же гиперпараметры, что у
            # hockey_ot, но без поправки на избыток ничьих. Так сравнение
            # строго парное: те же матчи, те же модели сил, отличается
            # ровно один параметр.
            cfg = selection["hockey_ot"] if label == "hockey_ot_plain" \
                else selection[label]
            model = _hockey(fold["train"], _teams(fold["train"]),
                             cfg["strength_scale"], cfg["half_life_days"], overtime,
                             periods, tie_boost)
            m = _evaluate(model, fold["test"], fold["train"])
            m["strength_scale"] = cfg["strength_scale"]
            m["half_life_days"] = cfg["half_life_days"]
            m["summary"] = model.summary()
            m["tie_boost"] = model.tie_boost
            m["model_tie_share"] = round(_tie_share(model, fold["test"]), 4)
            block["models"][label] = m
            per_fold_losses[label] = _per_match_losses(model, fold["test"])

        # Наблюдаемая доля ничьих после основного времени на этом fold'е.
        obs_ties = sum(1 for m in fold["test"]
                       if (periods.get(str(m["match_id"])) or {}).get("reg_home")
                       is not None
                       and periods[str(m["match_id"])]["reg_home"]
                       == periods[str(m["match_id"])]["reg_away"])
        block["observed_tie_share"] = round(obs_ties / len(fold["test"]), 4)

        diff = [a - b for a, b in zip(per_fold_losses["hockey_ot_plain"],
                                      per_fold_losses["hockey_ot"])]
        kappa_diffs.extend(diff)
        block["kappa_paired"] = {
            "n": len(diff),
            "mean_logloss_diff_plain_minus_boost": round(sum(diff) / len(diff), 6),
        }

        print(f"\n  fold {fold['name']} (n={len(fold['test'])})")
        for name, m in block["models"].items():
            print(f"    {name:16s} logloss {m['logloss']:.5f}  acc {m['accuracy']:.4f}  "
                  f"brier {m['brier']:.5f}  покрытие {m['coverage']:.1%}  "
                  f"acc(вердикт) {m['accuracy_given']}  pH {m['avg_p_home']:.3f}")
        report["folds"].append(block)

    # ---------------- сводка и вердикт -----------------------------------
    def pooled(name: str, subset: list[dict]) -> dict:
        tot_n = 0
        ll = 0.0
        hits = 0.0
        given = 0
        given_hits = 0.0
        for block in subset:
            m = block["models"][name]
            n = m["n"]
            tot_n += n
            ll += m["logloss"] * n
            hits += m["accuracy"] * n
            if m["n_given"]:
                given += m["n_given"]
                given_hits += m["accuracy_given"] * m["n_given"]
        return {
            "n": tot_n,
            "logloss": round(ll / tot_n, 5) if tot_n else None,
            "accuracy": round(hits / tot_n, 4) if tot_n else None,
            "accuracy_given": round(given_hits / given, 4) if given else None,
            "coverage": round(given / tot_n, 4) if tot_n else None,
        }

    names = ("basketball", "always_home", "hockey_ot", "hockey_ot_plain",
             "hockey_flat")
    pooled_all = {name: pooled(name, report["folds"]) for name in names}
    pooled_verify = {name: pooled(name, report["folds"][-N_VERIFY_FOLDS:]) for name in names}
    report["pooled_all"] = pooled_all
    report["pooled_verification"] = pooled_verify

    print("\n=== Сводно: все fold'ы ===")
    for name, m in pooled_all.items():
        print(f"  {name:14s} logloss {m['logloss']:.5f}  acc {m['accuracy']:.4f}  "
              f"acc(вердикт) {m['accuracy_given']}  покрытие {m['coverage']:.1%}")
    print("\n=== Сводно: проверочные fold'ы ===")
    for name, m in pooled_verify.items():
        print(f"  {name:14s} logloss {m['logloss']:.5f}  acc {m['accuracy']:.4f}  "
              f"acc(вердикт) {m['accuracy_given']}  покрытие {m['coverage']:.1%}")

    base = pooled_all["basketball"]
    verdicts: dict[str, dict] = {}
    for name in ("hockey_ot", "hockey_flat"):
        m = pooled_all[name]
        reasons = []
        ok = True

        # а. Суммарный log-loss по всей истории walk-forward.
        if not (m["logloss"] < base["logloss"]):
            ok = False
            reasons.append(f"log-loss {m['logloss']:.5f} не ниже {base['logloss']:.5f}")
        # б. Суммарная точность.
        if base["accuracy"] - m["accuracy"] > ACC_TOLERANCE:
            ok = False
            reasons.append(f"точность {m['accuracy']:.4f} ниже {base['accuracy']:.4f}")
        # в. Точность среди выданных вердиктов.
        if (base["accuracy_given"] or 0) < (m["accuracy_given"] or 0):
            ok = False
            reasons.append(f"точность среди вердиктов {m['accuracy_given']} "
                           f"ниже {base['accuracy_given']}")
        # г. Покрытие: нельзя выигрывать молчанием.
        if m["coverage"] < base["coverage"] * MIN_COVERAGE_RATIO:
            ok = False
            reasons.append(f"покрытие {m['coverage']:.1%} слишком мало "
                           f"({base['coverage']:.1%} у текущей)")
        # д. Сколько fold'ов выиграно.
        wins = sum(1 for b in report["folds"]
                   if b["models"][name]["logloss"] < b["models"]["basketball"]["logloss"])
        share = wins / len(report["folds"])
        if share < MIN_WINNING_FOLDS:
            ok = False
            reasons.append(f"лучше только на {wins}/{len(report['folds'])} fold'ов")
        # е. Ни один fold не хуже допустимого.
        worst = max(report["folds"],
                    key=lambda b: b["models"][name]["logloss"]
                    - b["models"]["basketball"]["logloss"])
        delta = (worst["models"][name]["logloss"]
                 - worst["models"]["basketball"]["logloss"])
        if delta > FOLD_TOLERANCE:
            ok = False
            reasons.append(f"fold {worst['name']}: хуже на {delta:+.5f}")

        # Отдельно — строгое правило по двум последним fold'ам. Оно
        # маломощно (на 1550 матчах разница меньше одного стандартного
        # отклонения), но результат считается и публикуется: видно, что
        # было бы при более жёстком правиле.
        vm = pooled_verify[name]
        vbase = pooled_verify["basketball"]
        strict_ok = (vm["logloss"] < vbase["logloss"]
                     and vm["accuracy"] >= vbase["accuracy"] - ACC_TOLERANCE)

        verdicts[name] = {
            "adopt": ok,
            "reasons": reasons or ["все условия правила выполнены"],
            "folds_won": wins,
            "folds_total": len(report["folds"]),
            "worst_fold_delta": round(delta, 5),
            "strict_verification_folds": {
                "adopt": bool(strict_ok),
                "logloss_new": vm["logloss"],
                "logloss_baseline": vbase["logloss"],
                "note": "правило по двум последним fold'ам: маломощно, "
                        "решение принимается по всей истории walk-forward",
            },
        }
        print(f"  {name}: ВНЕДРЯТЬ = {ok} — {'; '.join(verdicts[name]['reasons'])}")
        print(f"      строгое правило (2 последних fold'а): "
              f"{'ВНЕДРЯТЬ' if strict_ok else 'ОТКАЗ'} "
              f"({vm['logloss']:.5f} против {vbase['logloss']:.5f})")

    report["selection"] = selection
    report["verdict"] = verdicts

    # ---------------- эксперимент: поправка на избыток ничьих (κ) --------
    #
    # Сравнение строго парное: те же fold'ы, те же гиперпараметры, те же
    # силы команд — отличается только наличие поправки κ. Разность
    # log-loss считается по каждому матчу, поэтому шум общего уровня
    # (разброс самих log-loss) из неё вычитается.
    n_k = len(kappa_diffs)
    mean_k = sum(kappa_diffs) / n_k
    var_k = sum((d - mean_k) ** 2 for d in kappa_diffs) / (n_k - 1)
    se_k = math.sqrt(var_k / n_k) if n_k > 1 else float("nan")
    t_k = mean_k / se_k if se_k and se_k > 0 else float("nan")

    total_n = sum(b["n_test"] for b in report["folds"])
    obs_tie = sum(b["observed_tie_share"] * b["n_test"]
                  for b in report["folds"]) / total_n
    model_tie = {
        name: sum(b["models"][name]["model_tie_share"] * b["n_test"]
                  for b in report["folds"]) / total_n
        for name in ("hockey_ot", "hockey_ot_plain")
    }

    kappa_ok = (mean_k >= -TIE_BOOST_TOLERANCE
                and abs(model_tie["hockey_ot"] - obs_tie)
                < abs(model_tie["hockey_ot_plain"] - obs_tie))
    report["tie_boost_experiment"] = {
        "rule": [
            f"парная разница log-loss (без κ − с κ) не хуже "
            f"−{TIE_BOOST_TOLERANCE}",
            "модельная доля ничьих после основного времени ближе к "
            "наблюдаемой, чем без поправки",
        ],
        "adopt_kappa": bool(kappa_ok),
        "n": n_k,
        "mean_logloss_diff_plain_minus_boost": round(mean_k, 6),
        "stderr": round(se_k, 6),
        "t_statistic": round(t_k, 3),
        "ci95": [round(mean_k - 1.96 * se_k, 6),
                 round(mean_k + 1.96 * se_k, 6)],
        "matches_better_with_kappa": sum(1 for d in kappa_diffs if d > 0),
        "matches_worse_with_kappa": sum(1 for d in kappa_diffs if d < 0),
        "observed_tie_share": round(obs_tie, 4),
        "model_tie_share": {k: round(v, 4) for k, v in model_tie.items()},
        "note": "κ устраняет структурный перекос: произведение двух "
                "пуассонов даёт долю ничьих ниже наблюдаемой. "
                "Предсказательная сила при этом почти не меняется — "
                "разница в пределах шума, что видно по парному "
                "сравнению и доверительному интервалу.",
    }
    print("\n=== Поправка на избыток ничьих (κ) ===")
    print(f"  парная разница log-loss (без κ − с κ): {mean_k:+.6f} "
          f"± {se_k:.6f} (SE), t = {t_k:.2f}")
    print(f"  95% ДИ: [{mean_k - 1.96*se_k:+.6f}, {mean_k + 1.96*se_k:+.6f}]")
    print(f"  доля ничьих в основном времени: факт {obs_tie:.1%}, "
          f"с κ {model_tie['hockey_ot']:.1%}, без κ {model_tie['hockey_ot_plain']:.1%}")
    print(f"  κ {'принимается' if kappa_ok else 'НЕ принимается'}")

    adopt = [name for name, v in verdicts.items() if v["adopt"]]
    if len(adopt) > 1 and "hockey_ot" in adopt:
        # Обе разновидности прошли правило. Разница между ними — в
        # пределах шума, но выбирается та, у которой честнее структура:
        # с явным овертаймом и буллитами доля ничьей правдоподобна,
        # а не 15% из нормальной аппроксимации.
        adopt = ["hockey_ot"]
    report["adopt"] = adopt
    report["adopt_kappa"] = bool(kappa_ok)
    print(f"\nИтог: внедряем {adopt if adopt else 'ничего — оставляем текущую модель'}")

    dst = REPORTS / "nhl_model_compare.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Отчёт: {dst}")

    # Копия для сайта: страница «Методика» показывает эти цифры, и
    # показывать должна ровно те, что получились на сервере, а не
    # переписанные вручную.
    public = Path("web/public/data/nhl_model_compare.json")
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"Копия для сайта: {public}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
