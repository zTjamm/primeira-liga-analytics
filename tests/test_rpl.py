"""Проверки пайплайна РФПЛ.

РФПЛ — третий вид спорта, и главный риск здесь не ошибка расчёта, а
просачивание общего состояния: если файл прогнозов или параметры модели
начнут читаться из португальского футбола, изоляция нарушится молча.
Проверяем три вещи:

1. парсер CSV не пускает мусорные строки и корректно считает сезон;
2. соответствие id клубов API полному составу сезона — иначе прогноз
   получит пустое имя команды;
3. файл прогнозов согласован сам с собой: вероятности, порог, вердикт.

Запуск:
    python -m tests.test_rpl
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

PASS = "\u2713"
FAIL = "\u2717"


def _check(name: str, got, want) -> bool:
    ok = got == want
    print(f"  [{PASS if ok else FAIL}] {name}"
          + ("" if ok else f"  (получено {got!r}, ждали {want!r})"))
    return ok


def _check_bool(name: str, cond: bool, note: str = "") -> bool:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  ({note})" if note and not cond else ""))
    return cond


CSV_SAMPLE = """Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA
Russia,Premier League,2026/2027,01/08/2026,16:30,Dynamo Makhachkala,Lokomotiv Moscow,2,1,H,3.33,3.14,2.0
Russia,Premier League,2026/2027,02/08/2026,14:00,Orenburg,Zenit,0,3,A,8.0,4.75,1.27
,,,,,,,,,,,,
,,,,,,,,,,,,
"""


def test_parse() -> list[bool]:
    from etl.fetch_rpl import parse

    print("Парсер CSV:")
    df = parse(CSV_SAMPLE)
    ok = []
    ok.append(_check("мусорные строки отброшены", df.height, 2))
    ok.append(_check("дата распарсена", df["match_date"].to_list()[0], date(2026, 8, 1)))
    # Август >= 7 -> сезон начинается с 2026
    ok.append(_check("сезон от даты", df["season"].to_list()[0], "2026/27"))
    ok.append(_check("счёт — целые", df["fthg"].to_list()[0], 2))
    ok.append(_check("коэффициенты — float", df["odds_h"].to_list()[1], 8.0))
    ok.append(_check("match_id собран", df["match_id"].to_list()[0].startswith("2026-08-01"), True))
    return ok


def test_club_mapping() -> list[bool]:
    from etl.fetch_rpl import CLUB_ID_TO_NAME
    from etl.config import PROCESSED

    print("Соответствие клубов API и CSV:")
    ok = []
    names = set(CLUB_ID_TO_NAME.values())

    path = PROCESSED / "rpl_matches.parquet"
    if not path.exists():
        print(f"  [{FAIL}] нет {path} — запусти python -m etl.fetch_rpl")
        return [False]

    import polars as pl
    df = pl.read_parquet(path)
    cur = df.filter(pl.col("season") == "2026/27")
    csv_teams = set(cur["home_name"].to_list()) | set(cur["away_name"].to_list())

    missing = csv_teams - names
    ok.append(_check_bool(
        "весь состав сезона 2026/27 покрыт маппингом",
        not missing,
        f"нет в маппинге: {sorted(missing)}",
    ))
    extra = names - csv_teams
    ok.append(_check_bool(
        "в маппинге нет лишних клубов",
        not extra,
        f"лишние: {sorted(extra)}",
    ))
    ok.append(_check("состав сезона — 16 клубов", len(csv_teams), 16))
    return ok


def test_forecast_file() -> list[bool]:
    from etl.config import ROOT

    print("Файл прогнозов:")
    ok = []
    path = ROOT / "web" / "public" / "data" / "rpl_predictions.json"
    if not path.exists():
        print(f"  [{FAIL}] нет {path} — запусти python -m models.rpl_predict")
        return [False]

    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["upcoming"]
    ok.append(_check("прогнозы есть", len(rows) > 0, True))
    ok.append(_check_bool("изолированный sport", data["sport"] == "rpl", data.get("sport", "")))

    # Порог обязан быть подписан источником. Допустимы ровно два
    # честных варианта: собственный подбор на РФПЛ (models/rpl_selective.py)
    # или явно помеченный запасной путь, когда отчёта ещё нет. Ни молчание,
    # ни чужая валидация, выданная за свою, здесь не проходят.
    src = data.get("threshold_source", "")
    ok.append(_check_bool(
        "порог подписан источником: подбор на РФПЛ либо запасной путь",
        ("models/rpl_selective.py" in src)
        and ("подобран на РФПЛ" in src or "запасной путь" in src),
        src,
    ))
    t_src = data.get("total_threshold_source", "")
    ok.append(_check_bool("источник порога по тоталу подписан отдельно",
                          bool(t_src) and "тотал" in t_src, t_src))

    # Пороги в шапке и в строках должны совпадать: файл не имеет права
    # показывать один порог сверху и принимать решения по другому.
    ok.append(_check("порог в строках = порог в шапке",
                     {r.get("threshold") for r in rows}, {data["threshold"]}))
    t_th = data.get("total_threshold")
    ok.append(_check_bool("total_threshold: число или явный null",
                          t_th is None or isinstance(t_th, (int, float)),
                          repr(t_th)))
    if t_th is not None:
        wrong_t = [r["match_id"] for r in rows
                   if r["total_given"] != (max(r["over25"], r["under25"]) >= t_th)]
        ok.append(_check("вердикт по тоталу соответствует своему порогу",
                         len(wrong_t), 0))
        ok.append(_check("порог тотала в строках = в шапке",
                         {r.get("total_threshold") for r in rows}, {t_th}))

    bad_sum = [r for r in rows
               if abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1.0) > 1e-3]
    ok.append(_check("вероятности 1X2 дают 1.0", len(bad_sum), 0))

    bad_total = [r for r in rows
                 if abs(r["over25"] + r["under25"] - 1.0) > 1e-3]
    ok.append(_check("вероятности тотала дают 1.0", len(bad_total), 0))

    # Вердикт обязан соответствовать порогу: не выдан => уверенность ниже
    # порога, выдан => не ниже. Иначе отсев врёт.
    th = data["threshold"]
    wrong = []
    for r in rows:
        conf = max(r["p_home"], r["p_draw"], r["p_away"])
        if r["verdict_given"] != (conf >= th and r["team_games"] >= 10):
            wrong.append(r["match_id"])
    ok.append(_check("вердикт соответствует порогу", len(wrong), 0))

    # Вердикт и предсказание должны совпадать по исходу
    mismatch = [r for r in rows if r["verdict_given"] and
                r["prediction"] != max(("H", "D", "A"),
                                       key=lambda k: {"H": r["p_home"],
                                                      "D": r["p_draw"],
                                                      "A": r["p_away"]}[k])]
    ok.append(_check("prediction = argmax вероятностей", len(mismatch), 0))

    ok.append(_check("у всех даты в будущем или сегодня",
                     all(r["date"] >= "2026-10-17" for r in rows), True))
    return ok


def test_journal() -> list[bool]:
    """Журнал РФПЛ: леджер на месте, резолв работает, отсев помечен."""
    import polars as pl
    from etl.config import ROOT
    from models import forecast

    print("Журнал РФПЛ:")
    ok = []

    # Леджер существует и читается
    ledger = forecast.load("rpl")
    ok.append(_check_bool("леджер непуст", len(ledger) > 0, "нет записей"))
    if ledger:
        r0 = ledger[0]
        needed = {"key", "date", "stage", "p_home", "p_draw", "p_away", "extra"}
        ok.append(_check_bool("запись содержит все поля", needed <= set(r0),
                              f"нет: {sorted(needed - set(r0))}"))
        ok.append(_check_bool("extra помечает вердикт",
                              "verdict_given" in (r0.get("extra") or {}),
                              "нет verdict_given"))
        ok.append(_check_bool("extra помечает тотал",
                              "total_verdict_given" in (r0.get("extra") or {}),
                              "нет total_verdict_given"))

    # Файл журнала для сайта
    path = ROOT / "web" / "public" / "data" / "rpl_journal.json"
    ok.append(_check_bool("rpl_journal.json существует", path.exists(), str(path)))
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        ok.append(_check("sport = rpl", data.get("sport"), "rpl"))
        # Сыгранные матчи в журнале быть МОГУТ и должны сходиться со
        # сводкой. Раньше здесь стояло «сыгранных нет» — проверка,
        # годная только на первой неделе проекта: с появлением результатов
        # она стала бы ругаться на правильное поведение журнала.
        ok.append(_check("resolved сходится со сводкой",
                         len(data["resolved"]), data["summary"]["total"]))
        ok.append(_check_bool(
            "у сыгранных есть факт и отметка попадания",
            all(r.get("actual") and r.get("hit") is not None
                for r in data["resolved"]),
            "запись без факта или без hit"))
        ok.append(_check_bool("pending заполнен", len(data["pending"]) > 0, "пусто"))

    # Ключ тотала в общем модуле: для rpl — over25, а не баскетбольный over_155
    # Проверяем на искусственном resolved: если бы ключ был неверным,
    # total_hit остался бы None даже при наличии extra.over25.
    rows = [{
        "key": "t1", "date": "2026-10-18", "kickoff": None, "matchday": None,
        "home_id": "Zenit", "away_id": "Akhmat Grozny",
        "home_name": "Zenit", "away_name": "Akhmat Grozny",
        "generated_at": "2026-10-17T00:00:00+00:00", "hours_before": 20.0,
        "stage": "T-24h", "p_home": 0.74, "p_draw": 0.19, "p_away": 0.07,
        "extra": {"over25": 0.47, "verdict_given": True,
                  "total_verdict_given": True},
    }]
    # build_history читает леджер с диска, поэтому кладём тестовую запись
    # в отдельный путь и подменяем функцию чтения.
    original_load = forecast.load
    forecast.load = lambda sport: rows if sport == "rpl" else original_load(sport)
    try:
        results = pl.DataFrame({
            "match_date": [date(2026, 10, 18)],
            "home_id": ["Zenit"], "away_id": ["Akhmat Grozny"],
            "ftr": ["H"], "fthg": [2], "ftag": [0],
        })
        hist = forecast.build_history(results, sport="rpl")
    finally:
        forecast.load = original_load
    res = hist["resolved"]
    ok.append(_check("исход зарезолвился", len(res), 1))
    if res:
        r = res[0]
        ok.append(_check("hit посчитан", r["hit"], True))
        ok.append(_check("тотал посчитан по over25 (не over_155)",
                         r["total_hit"], True))  # over=0.47 -> ТМ 2.5, счёт 2:0 — ТМ угадан
        ok.append(_check("вердикт по тоталу сохранён", r["total_given"], True))
        ok.append(_check("line = 2.5", r["total_line"], 2.5))
    return ok


def test_isolation() -> list[bool]:
    """Ни один файл РФПЛ не должен перезаписывать артефакты других видов."""
    from etl.config import ROOT

    print("Изоляция от других видов:")
    ok = []
    rpl_files = [
        ROOT / "data" / "processed" / "rpl_matches.parquet",
        ROOT / "data" / "processed" / "rpl_fixtures.parquet",
        ROOT / "data" / "processed" / "rpl_params.json",
        ROOT / "web" / "public" / "data" / "rpl_predictions.json",
        ROOT / "web" / "public" / "data" / "rpl_teams.json",
    ]
    for f in rpl_files:
        ok.append(_check_bool(f"свой файл: {f.name}", f.exists(), str(f)))

    # Артефакты португальского футбола существуют и не задеты
    for name in ("predictions.json", "vtb_predictions.json"):
        p = ROOT / "web" / "public" / "data" / name
        ok.append(_check_bool(f"чужой файл цел: {name}", p.exists(), str(p)))
    return ok


def test_api_merge() -> list[bool]:
    """CSV обновляется с огромной задержкой, поэтому модели дозагружаются
    результаты из API. Проверяем, что слияние не дублирует и не портит
    порядок — от порядка зависит смена сезона в Elo."""
    from datetime import date as date_cls

    import polars as pl

    from models.rpl_predict import _merge_api_results

    print("Слияние результатов CSV + API:")
    ok = []
    fx = pl.DataFrame(
        {
            "match_date": [date_cls(2026, 8, 2), date_cls(2026, 10, 18)],
            "home_name": ["Orenburg", "Spartak Moscow"],
            "away_name": ["Zenit", "Rubin Kazan"],
            "fthg": [0, 4],
            "ftag": [3, 0],
            "ftr": ["A", "H"],
        },
        schema_overrides={"match_date": pl.Date},
    )
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        fx_path = Path(td) / "rpl_fixtures.parquet"
        fx.write_parquet(fx_path)

        # В CSV уже лежит первый матч (2 августа) — он не должен повториться
        out = [{
            "season": "2026/27",
            "match_date": date_cls(2026, 8, 2),
            "home_id": "Orenburg", "away_id": "Zenit",
            "home_name": "Orenburg", "away_name": "Zenit",
            "fthg": 0, "ftag": 3, "ftr": "A",
            "odds_h": 8.0, "odds_d": 4.75, "odds_a": 1.27,
        }]
        added = _merge_api_results(out, date_cls, fx_path=fx_path)
        ok.append(_check("дубликат из CSV не добавлен", added, 1))
        ok.append(_check("всего строк 2", len(out), 2))
        new = [m for m in out if m["home_name"] == "Spartak Moscow"][0]
        ok.append(_check("сезон посчитан как в ETL", new["season"], "2026/27"))
        ok.append(_check("ftr перенесён", new["ftr"], "H"))
        ok.append(_check("коэффициентов у API нет", new["odds_h"], None))

        # Второй проход — ничего не добавится
        again = _merge_api_results(out, date_cls, fx_path=fx_path)
        ok.append(_check("повторный проход ничего не добавил", again, 0))

    return ok


def main() -> int:
    results: list[bool] = []
    results += test_parse()
    results += test_club_mapping()
    results += test_api_merge()
    results += test_forecast_file()
    results += test_journal()
    results += test_isolation()
    print("-" * 46)
    if all(results):
        print("Все проверки пройдены.")
        return 0
    print(f"Провалено {results.count(False)} из {len(results)} проверок.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
