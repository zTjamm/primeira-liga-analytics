"""Стоит ли показывать «тот прогноз, у которого вероятность выше».

Пользователь заметил логичную вещь: карточка матча показывает один исход
(П1 или П2), хотя у нас есть ещё тотал, и у обоих рынков есть своя
вероятность. Казалось бы — выводи то, что вероятнее.

Здесь это и проверяется, потому что «более вероятно» и «выгоднее ставить» —
разные вещи, и разница здесь решающая.

Ключевой момент, который обычно упускают: вероятности из разных рынков
нельзя сравнивать друг с другом. Тотал 2.5 — ставка на конкретное число
голов, П1 — на исход матча. События пересекаются, шкалы разные. Фраза
«тотал 62%, исход 58%, значит тотал надёжнее» не имеет смысла: это
сравнение груш с яблоками. Оба числа могут быть верными одновременно.

Что можно сравнить честно, так это:

1. Точность на одном и том же рынке — модель против закрывающей линии.
   log-loss ниже = прогноз ближе к правде.
2. Доходность стратегии «всегда брать более вероятное». Почти всегда
   минус: линия букмекера уже впитала ту же информацию, поэтому на
   вероятностных фаворитах коэффициенты срезаны.

Именно вторая проверка отвечает на исходный вопрос пользователя: можно
ли вообще что-то рекомендовать, если стратегия «бери более вероятное»
проигрывает.

Запуск:
    python -m models.market_totals
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import polars as pl

from etl.config import PROCESSED, REPORTS
from models.dixon_coles import DixonColesModel
from models.market import devig, devig_two_way
from models.poisson import over_prob

# Тестовые сезоны — те же, что в основном бэктесте, иначе цифры на сайте
# и здесь будут о разных матчах и сравнивать их будет нельзя.
TEST_SEASONS = ("2023/24", "2024/25", "2025/26")

# Те же параметры окна и переобучения, что в основном бэктесте. Расхождение
# сделало бы две разные метрики на одном сайте, и сравнивать их было бы нельзя.
DC_WINDOW_YEARS = 3
REFIT_EVERY_DAYS = 7
MIN_TRAIN_MATCHES = 200


def _at(seq, i: int) -> float | None:
    """Безопасное извлечение коэффициента из списка.

    Списки в parquet бывают короче трёх элементов, а внутри — None и
    невозможные значения (1.0 или 1000). Мусорный коэффициент хуже
    отсутствующего: по нему посчитается доходность, которой не было.
    """
    try:
        v = seq[i]
    except (TypeError, IndexError, KeyError):
        return None
    if v is None or not np.isfinite(v) or not (1.01 < v < 1000):
        return None
    return float(v)


def logloss(p: float, y: int) -> float:
    return -np.log(max(p, 1e-12))


def brier(p: float, y: int) -> float:
    return (p - y) ** 2


def walk_forward() -> pl.DataFrame:
    """Строит прогноз по каждому матчу на данных строго до его даты.

    Отдельный walk-forward вместо готового бэктеста: класс Prediction там
    не хранит тотал, а переписывать его ради одной проверки — лишний риск
    для работающего кода.

    Ключевое: обучение идёт на матчах с датой СТРОГО МЕНЬШЕ даты прогноза.
    Первый вариант этого модуля обучался на всём сезоне сразу, включая
    матчи после прогнозируемого, и показывал модель лучше линии на 0.118
    log-loss — это была утечка, а не результат. Возраст команды на момент
    матча — это информация, которой в момент прогноза ещё нет.
    """
    m = pl.read_parquet(PROCESSED / "matches.parquet")
    all_matches = m.sort("match_date").to_dicts()
    rows: list[dict] = []

    test = [x for x in all_matches if x["season"] in TEST_SEASONS]
    model: DixonColesModel | None = None
    last_fit = None

    for mt in test:
        # Именно «is None», а не «falsy»: счёт 0:0 — это полноценный матч,
        # а ноль в Python ложь. Проверка на falsy молча выбрасывала четверть
        # выборки, из-за чего совпадения исхода и тотала выходили невозможными.
        if mt.get("fthg") is None or mt.get("ftag") is None:
            continue
        cutoff = mt["match_date"]
        if model is None or last_fit is None or (cutoff - last_fit).days >= REFIT_EVERY_DAYS:
            window_start = cutoff - timedelta(days=int(365.25 * DC_WINDOW_YEARS))
            train = [x for x in all_matches
                     if window_start <= x["match_date"] < cutoff and x.get("fthg") is not None]
            if len(train) < MIN_TRAIN_MATCHES:
                continue  # окно не набрало массу — честно не прогнозируем
            teams = sorted({x["home_id"] for x in train} | {x["away_id"] for x in train})
            model = DixonColesModel().fit(train, teams)
            last_fit = cutoff
            for t in (mt["home_id"], mt["away_id"]):
                model.attack.setdefault(t, 0.0)
                model.defence.setdefault(t, 0.0)

        try:
            p1x2 = model.predict_1x2(mt["home_id"], mt["away_id"])
            over, _ = over_prob(model.score_matrix(mt["home_id"], mt["away_id"]), 2.5)
        except Exception:
            continue
        if not np.isfinite(p1x2).all() or not (0 < over < 1):
            continue

        # Коэффициенты хранятся списками: odds_close = [H, D, A] с маржой,
        # ou25_close = [больше 2.5, меньше 2.5]. Снятие маржи — devig ниже.
        oh = _at(mt.get("odds_close"), 0)
        od = _at(mt.get("odds_close"), 1)
        oa = _at(mt.get("odds_close"), 2)
        mo = _at(mt.get("ou25_close"), 0)
        mu = _at(mt.get("ou25_close"), 1)

        row = {
            "season": mt["season"],
            "match_date": str(mt["match_date"]),
            "home": mt["home_name"],
            "away": mt["away_name"],
            "p_home": float(p1x2[0]),
            "p_draw": float(p1x2[1]),
            "p_away": float(p1x2[2]),
            "p_over25": float(over),
            "goals_total": int(mt["fthg"] + mt["ftag"]),
            "y_home": int(mt["fthg"] > mt["ftag"]),
            "y_draw": int(mt["fthg"] == mt["ftag"]),
            "y_away": int(mt["fthg"] < mt["ftag"]),
            "y_over25": int(mt["fthg"] + mt["ftag"] > 2.5),
            "y_under25": int(mt["fthg"] + mt["ftag"] <= 2.5),
            "p_under25": 1.0 - float(over),
        }
        if oh and od and oa:
            d = devig([oh, od, oa])
            if d is not None:
                row |= {"q_home": float(d.probs[0]), "q_draw": float(d.probs[1]),
                        "q_away": float(d.probs[2]),
                        # Коэффициенты с маржой — по ним реально ставят.
                        # Без них доходность посчитать нечем.
                        "odds_home": oh, "odds_away": oa}
        if mo and mu:
            q = devig_two_way([mo, mu])
            if q is not None:
                row |= {"q_over25": float(q[0]), "q_under25": float(q[1]),
                        "odds_over25": mo, "odds_under25": mu}
        rows.append(row)

    return pl.DataFrame(rows)


def score(rows: list[dict], pk: str, qk: str, yk: str) -> dict:
    """Log-loss, Brier, точность и калибровка — модель против линии."""
    have = [r for r in rows if r.get(pk) is not None and r.get(qk) is not None]
    if not have:
        return {"n": 0}
    p = np.array([r[pk] for r in have], dtype=float)
    q = np.array([r[qk] for r in have], dtype=float)
    y = np.array([r[yk] for r in have], dtype=float)
    return {
        "n": len(have),
        "freq": round(float(y.mean()), 4),
        "logloss_model": round(float(np.mean([-np.log(np.clip(p, 1e-12, 1))])), 5),
        "logloss_line": round(float(np.mean([-np.log(np.clip(q, 1e-12, 1))])), 5),
        "brier_model": round(float(np.mean((p - y) ** 2)), 5),
        "brier_line": round(float(np.mean((q - y) ** 2)), 5),
        "accuracy_model": round(float(np.mean((p > 0.5) == y)), 4),
        "accuracy_line": round(float(np.mean((q > 0.5) == y)), 4),
        # средняя вероятность минус фактическая частота: 0 = идеально
        "calib_model": round(float(p.mean() - y.mean()), 4),
        "calib_line": round(float(q.mean() - y.mean()), 4),
    }


def roi(rows: list[dict], pk: str, ok: str, yk: str,
        opp_pk: str, opp_ok: str, opp_yk: str) -> dict:
    """Стратегия «бери то, что вероятнее», с реальными коэффициентами.

    Аргументы разнесены явно, потому что здесь легко и незаметно сравнить
    вероятность с исходом. Три ошибки, каждая из которых давала правдоподобную
    но невозможную доходность — все пойманы на этой функции:

    1. Коэффициент 1/p вместо коэффициента букмекера. 1/p — цена рынка без
       маржи, и на ней стратегия по определению в ноль. Выдавало +183%.

    2. Сравнение p с 0.5 вместо p с вероятностью противоположного исхода.
       При p_home < 0.5 «более вероятный исход» — часто ничья, а не гости:
       ставя на гостей, платили коэффициент андердога. Средний коэффициент
       выходил 2.19 вместо 1.86, ROI +70%.

    3. Сравнение вероятности с фактическим исходом (p >= y, где y — 0 или 1).
       Тут выигрыш получался в 100% случаев: тотал больше 2.5 выигрывал
       всегда, потому что условие почти всегда истинно.

    Теперь вероятности сравниваются только с вероятностями, исходы — только
    с исходами, а коэффициент берётся у того рынка, который выбран.
    """
    need = (pk, ok, yk, opp_pk, opp_ok, opp_yk)
    have = [r for r in rows
            if all(r.get(k) is not None for k in need)
            and 0.02 < r[pk] < 0.98 and 0.02 < r[opp_pk] < 0.98]
    if not have:
        return {"bets": 0}

    bets, odds = [], []
    for r in have:
        # Более вероятный из двух исходов рынка.
        if r[pk] >= r[opp_pk]:
            price, pick = r[ok], r[yk]
        else:
            price, pick = r[opp_ok], r[opp_yk]
        if not (1.01 < price < 1000.0):
            continue
        odds.append(price)
        bets.append(price * int(pick == 1) - 1.0)

    if not bets:
        return {"bets": 0}
    return {
        "bets": len(bets),
        "wins": sum(1 for b in bets if b > 0),
        "hit_rate": round(sum(1 for b in bets if b > 0) / len(bets), 4),
        "avg_odds": round(float(np.mean(odds)), 3),
        "roi": round(float(np.mean(bets)), 4),
        "total_units": round(float(np.sum(bets)), 2),
    }


def main() -> int:
    print("Считаю walk-forward по тоталу и исходу…")
    df = walk_forward()
    rows = df.to_dicts()
    print(f"Строк: {df.height}  (сезоны {', '.join(TEST_SEASONS)})")

    out = {
        "test_seasons": list(TEST_SEASONS),
        "markets": {
            "outcome_home": score(rows, "p_home", "q_home", "y_home"),
            "outcome_away": score(rows, "p_away", "q_away", "y_away"),
            "total_25": score(rows, "p_over25", "q_over25", "y_over25"),
        },
        "strategy": {
            # Наша модель как источник выбора.
            "winner_1x2_model": roi(rows, "p_home", "odds_home", "y_home",
                                    "p_away", "odds_away", "y_away"),
            "total_25_model": roi(rows, "p_over25", "odds_over25", "y_over25",
                                  "p_under25", "odds_under25", "y_under25"),
            # Контроль: выбор по самой линии букмекера. Если модель полезна,
            # она обязана обыгрывать эту стратегию. Если нет — она хуже рынка
            # даже там, где рынок заведомо эффективен.
            "winner_1x2_market": roi(rows, "q_home", "odds_home", "y_home",
                                     "q_away", "odds_away", "y_away"),
            "total_25_market": roi(rows, "q_over25", "odds_over25", "y_over25",
                                   "q_under25", "odds_under25", "y_under25"),
        },
    }

    # Совпадение рынков: показывает, почему их вероятности не сравнимы.
    both = df.filter((pl.col("y_home") == 1) & (pl.col("y_over25") == 1)).height
    home_n = df.filter(pl.col("y_home") == 1).height
    over_n = df.filter(pl.col("y_over25") == 1).height
    out["overlap"] = {
        "home_wins": home_n,
        "over_25": over_n,
        "both": both,
        "note": (
            "Домашние победы и тоталы больше 2.5 — разные события, они "
            "пересекаются, но не совпадают. Поэтому вероятности из разных "
            "рынков нельзя ставить рядом и выбирать по величине."
        ),
    }

    (REPORTS / "market_totals.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 74)
    print(f"ТЕСТ: {', '.join(TEST_SEASONS)}")
    print("=" * 74)

    names = {
        "outcome_home": ("ПОБЕДА ХОЗЯЕВ", "q_home"),
        "outcome_away": ("ПОБЕДА ГОСТЕЙ", "q_away"),
        "total_25": ("ТОТАЛ БОЛЬШЕ 2.5", "q_over25"),
    }
    for key, (title, _) in names.items():
        s = out["markets"][key]
        if not s.get("n"):
            print(f"\n{title}: нет сопоставимых матчей")
            continue
        better = "МЫ ЛУЧШЕ" if s["logloss_model"] < s["logloss_line"] else "ЛИНИЯ ЛУЧШЕ"
        print(f"\n{title}  (матчей с линией: {s['n']}, всего рынка: "
              f"{df.height if key == 'outcome_home' else '-'})")
        print(f"  log-loss  модель {s['logloss_model']:.5f}  линия {s['logloss_line']:.5f}"
              f"   {better} на {abs(s['logloss_model'] - s['logloss_line']):.5f}")
        print(f"  Brier     модель {s['brier_model']:.5f}  линия {s['brier_line']:.5f}")
        print(f"  точность  модель {s['accuracy_model']:.1%}     линия {s['accuracy_line']:.1%}")
        print(f"  калибровка (вероятность минус частота; 0 = идеально):")
        print(f"            модель {s['calib_model']:+.4f}   линия {s['calib_line']:+.4f}")

    print("\n" + "=" * 74)
    print("СТРАТЕГИЯ «ВСЕГДА БЕРИ БОЛЕЕ ВЕРОЯТНОЕ»")
    print("=" * 74)
    for label, km, kk in (
            ("ИСХОД МАТЧА", "winner_1x2_model", "winner_1x2_market"),
            ("ТОТАЛ 2.5", "total_25_model", "total_25_market")):
        m_s, l_s = out["strategy"][km], out["strategy"][kk]
        if not m_s.get("bets") or not l_s.get("bets"):
            print(f"\n{label}: ставок не было")
            continue
        print(f"\n{label}  —  стратегия «бери то, что вероятнее»")
        print(f"  по нашей модели:   угадано {m_s['hit_rate']:.1%}  "
              f"ср.коэф {m_s['avg_odds']:.2f}  ROI {m_s['roi']:+.2%}")
        print(f"  по линии букмекера: угадано {l_s['hit_rate']:.1%}  "
              f"ср.коэф {l_s['avg_odds']:.2f}  ROI {l_s['roi']:+.2%}")
        d = m_s["roi"] - l_s["roi"]
        print(f"  наша модель против рынка: {d:+.2%} на единицу ставки  — "
              f"{'МОДЕЛЬ ЛУЧШЕ' if d > 0.02 else ('паритет' if d > -0.02 else 'РЫНОК ЛУЧШЕ')}")

    print("\n" + "=" * 74)
    print("ПОЧЕМУ НЕЛЬЗЯ ВЫБИРАТЬ РЫНОК ПО ВЕЛИЧИНЕ ВЕРОЯТНОСТИ")
    print("=" * 74)
    ov = out["overlap"]
    print(f"  Побед хозяев: {ov['home_wins']}   тоталов больше 2.5: {ov['over_25']}   "
          f"совпало: {ov['both']}")
    print("  Это разные ставки на разные события. «0.62 на тотал против 0.58")
    print("  на исход» — сравнение несравнимого.")

    print(f"\nОтчёт: {REPORTS / 'market_totals.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())