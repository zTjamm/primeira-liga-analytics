"""Стоит ли расширять проект на другие лиги.

Вопрос задаётся как «у нас данные только для этой лиги», и ответ на него
раздваивается. Два разных вопроса, их важно не путать:

1. РАБОТАЕТ ЛИ МОДЕЛЬ В ДРУГИХ ЛИГАХ. Если на английской или немецкой
   она окажется хуже, чем на португальской, то расширение добавит
   страницы с заведомо худшим качеством.

2. ПОМОГАЮТ ЛИ ДАННЫЕ ДРУГИХ ЛИГ ПОРТУГАЛЬСКИМ ПРОГНОЗАМ. Это отдельная
   гипотеза, и она направлена против расширения: параметры команд у нас
   per-team, и испанские матчи ничего не говорят о Sporting. Проверяется
   напрямую — обучить на одной лиге и на объединении, сравнить на одних и
   тех же португальских тестовых матчах.

Обе лиги живут в одном бесплатном источнике с одинаковой структурой
файлов, так что сравнение честное: один код, одни параметры, одни
метрики.

Запуск:
    python -m models.league_scan
"""
from __future__ import annotations

import io
import json
import math
import time
from datetime import timedelta

import numpy as np
import polars as pl
import requests

from etl.config import CSV_BASE, RAW
from models.dixon_coles import DixonColesModel
from models.market import devig

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "primeira-liga-analytics/1.0"})

# Коды лиг football-data.co.uk. Наша — P1.
LEAGUES = {
    "P1": "Португалия",
    "E0": "Англия",
    "SP1": "Испания",
    "D1": "Германия",
    "I1": "Италия",
    "F1": "Франция",
    "N1": "Нидерланды",
}

# Шесть сезонов: два под валидацию, три под тест, один в запас.
VAL_SEASONS = ("2223", "2324", "2425", "2526")
TEST_SEASONS = ("2324", "2425", "2526")

WINDOW_YEARS = 3
REFIT_EVERY_DAYS = 7
MIN_TRAIN_MATCHES = 200


def season_name(code: str) -> str:
    return f"{code[:2]}/{code[2:]}"


def fetch(code: str, season: str) -> pl.DataFrame | None:
    """Качает CSV лиги за сезон. Файл кэшируется на диске."""
    path = RAW / f"{code}_{season}.csv"
    if path.exists():
        return pl.read_csv(io.BytesIO(path.read_bytes()), infer_schema_length=0)
    url = f"{CSV_BASE}/{season}/{code}.csv"
    try:
        r = SESSION.get(url, timeout=60)
        if r.status_code != 200:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return pl.read_csv(io.BytesIO(r.content), infer_schema_length=0)
    except Exception:
        return None


def normalise(df: pl.DataFrame, code: str, season: str) -> list[dict]:
    """CSV → внутренний формат. Идентификаторы команд — их названия."""
    need = {"HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    if not need <= set(df.columns):
        return []
    d = df.rename({"HomeTeam": "home", "AwayTeam": "away",
                   "FTHG": "fthg", "FTAG": "ftag", "Date": "date_raw"})
    cols = [c for c in ("home", "away", "fthg", "ftag", "date_raw",
                        "AvgCH", "AvgCD", "AvgCA") if c in d.columns]
    d = d.select(cols)
    out = []
    for r in d.iter_rows(named=True):
        hg, ag = r.get("fthg"), r.get("ftag")
        if hg is None or ag is None or not r.get("home") or not r.get("away"):
            continue
        try:
            hg, ag = int(hg), int(ag)
            day = int(str(r["date_raw"])[0:2])
            mon = int(str(r["date_raw"])[3:5])
            yr = int(str(r["date_raw"])[6:8])
            year = 2000 + yr if yr < 50 else 1900 + yr
            dte = __import__("datetime").date(year, mon, day)
        except Exception:
            continue
        if not (0 <= hg <= 15 and 0 <= ag <= 15):
            continue
        # Сезон хранится кодом ("2324"), а не в виде "23/24": фильтр
        # теста сравнивает с TEST_SEASONS, где коды, и с человеческим видом
        # не совпало бы ни одно сравнение — прогнозов получалось ноль.
        rec = {"league": code, "season": season,
               "match_date": dte, "home_id": r["home"], "away_id": r["away"],
               "fthg": hg, "ftag": ag}
        for k in ("AvgCH", "AvgCD", "AvgCA"):
            v = r.get(k)
            rec[k.lower()] = float(v) if v is not None and v == v and 1.01 < float(v) < 1000 else None
        out.append(rec)
    return out


def load_league(code: str) -> list[dict]:
    rows: list[dict] = []
    for s in VAL_SEASONS:
        df = fetch(code, s)
        if df is None:
            continue
        rows.extend(normalise(df, code, s))
        time.sleep(0.3)
    return rows


def walk_forward(rows: list[dict], test_seasons: tuple[str, ...],
                 extra_train: list[dict] | None = None) -> list[dict]:
    """Прогноз по каждому тестовому матчу на данных строго до его даты.

    extra_train добавляет матчи других лиг в обучение — так проверяется,
    помогают ли чужие данные.
    """
    pool = rows + (extra_train or [])
    pool.sort(key=lambda m: m["match_date"])
    out: list[dict] = []
    model: DixonColesModel | None = None
    last_fit = None

    for mt in rows:
        if mt["season"] not in test_seasons:
            continue
        cutoff = mt["match_date"]
        ws = cutoff - timedelta(days=int(365.25 * WINDOW_YEARS))
        # Окно по дате, но лига сохраняется: у неё своя база забитых.
        train = [x for x in pool
                 if ws <= x["match_date"] < cutoff]
        ready = model is not None
        if (model is None or last_fit is None
                or (cutoff - last_fit).days >= REFIT_EVERY_DAYS):
            if len(train) >= MIN_TRAIN_MATCHES:
                teams = sorted({x["home_id"] for x in train} | {x["away_id"] for x in train})
                try:
                    model = DixonColesModel().fit(train, teams)
                    last_fit = cutoff
                    ready = True
                except Exception:
                    model = None
        if not ready or model is None:
            continue
        for t in (mt["home_id"], mt["away_id"]):
            model.attack.setdefault(t, 0.0)
            model.defence.setdefault(t, 0.0)
        try:
            p = model.predict_1x2(mt["home_id"], mt["away_id"])
        except Exception:
            continue
        out.append({"y": 0 if mt["fthg"] > mt["ftag"] else (1 if mt["fthg"] == mt["ftag"] else 2),
                    "p": [float(x) for x in p],
                    "line": mt.get("avgch"), "line_d": mt.get("avgcd"), "line_a": mt.get("avgca")})
    return out


def score(preds: list[dict]) -> dict:
    if not preds:
        return {"n": 0}
    n = len(preds)
    ll = float(-np.mean([math.log(max(r["p"][r["y"]], 1e-12)) for r in preds]))
    acc = float(np.mean([int(np.argmax(r["p"]) == r["y"]) for r in preds]))
    # наивный: исторические доли П1/Х/П2 по этой же выборке
    ys = [r["y"] for r in preds]
    naive = [ys.count(0) / n, ys.count(1) / n, ys.count(2) / n]
    ll_naive = float(-np.mean([math.log(max(naive[r["y"]], 1e-12)) for r in preds]))
    res = {"n": n, "logloss": round(ll, 5), "accuracy": round(acc, 4),
           "logloss_naive": round(ll_naive, 5)}
    # Снятие маржи — ПОСТРОЧНАЯ операция: у каждого матча своя тройка
    # коэффициентов. В первой версии devig считался один раз по первой
    # строке и применялся ко всем, из-за чего «линия» показывала
    # несуществующие 1.55 log-loss по Англии и выглядела заведомо
    # проигравшей.
    lls = []
    for r in preds:
        if not (r["line"] and r["line_d"] and r["line_a"]):
            continue
        d = devig([r["line"], r["line_d"], r["line_a"]])
        if d is None:
            continue
        q = [float(x) for x in d.probs]
        lls.append(-math.log(max(q[r["y"]], 1e-12)))
    if lls:
        res["logloss_market"] = round(float(np.mean(lls)), 5)
        res["n_market"] = len(lls)
    return res


def main() -> int:
    print("Качаю лиги…")
    data: dict[str, list[dict]] = {}
    for code, name in LEAGUES.items():
        rows = load_league(code)
        data[code] = rows
        print("  %-4s %-12s матчей: %5d" % (code, name, len(rows)))

    out: dict = {"leagues": {}, "test_seasons": list(TEST_SEASONS)}

    print()
    print("=" * 86)
    print("ВОПРОС 1. РАБОТАЕТ ЛИ МОДЕЛЬ В ЭТОЙ ЛИГЕ")
    print("=" * 86)
    print("%-12s%6s%11s%11s%11s%11s" % (
        "лига", "матчей", "модель", "наивный", "линия", "вывод"))
    print("-" * 86)
    for code, name in LEAGUES.items():
        preds = walk_forward(data[code], TEST_SEASONS)
        s = score(preds)
        if not s.get("n"):
            print("%-12s%6d   данных нет" % (name, len(data[code])))
            continue
        s["name"] = name
        s["code"] = code
        s["history"] = len(data[code])
        out["leagues"][code] = s
        mk = s.get("logloss_market")
        verdict = "нет линии"
        if mk:
            gap = s["logloss"] - mk
            verdict = "лучше линии" if gap < 0 else "хуже линии"
            verdict += " на %.3f" % abs(gap)
        print("%-12s%6d%11.5f%11.5f%11s%11s" % (
            name, s["n"], s["logloss"], s["logloss_naive"],
            ("%.5f" % mk) if mk else "-", verdict))

    print()
    print("=" * 86)
    print("ВОПРОС 2. ПОМОГАЮТ ЛИ ДАННЫЕ ДРУГИХ ЛИГ ПОРТУГАЛЬСКИМ ПРОГНОЗАМ")
    print("=" * 86)
    base = score(walk_forward(data["P1"], TEST_SEASONS))
    print("  обучение только на Португалии:        log-loss %.5f (%d матчей)"
          % (base["logloss"], base["n"]))
    # По очереди добавляем по одной чужой лиге.
    for code, name in LEAGUES.items():
        if code == "P1" or not data[code]:
            continue
        extra = data[code]
        s = score(walk_forward(data["P1"], TEST_SEASONS, extra_train=extra))
        d = s["logloss"] - base["logloss"]
        verdict = "ЛУЧШЕ" if d < -0.0005 else ("хуже" if d > 0.0005 else "без разницы")
        print("  + %-12s (%4d чужих):  log-loss %.5f  (%+.5f)  %s"
              % (name, len(extra), s["logloss"], d, verdict))
        out.setdefault("cross", {})[code] = {
            "logloss": s["logloss"], "delta": round(d, 5), "extra_matches": len(extra)}
    out["portugal_baseline"] = base

    dst = REPORTS = None
    from etl.config import REPORTS as R
    dst = R / "league_scan.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())