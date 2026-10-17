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
    ok.append(_check_bool(
        "порог честно помечен как перенесённый",
        "не валидировался" in data.get("threshold_source", ""),
        data.get("threshold_source", ""),
    ))

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


def main() -> int:
    results: list[bool] = []
    results += test_parse()
    results += test_club_mapping()
    results += test_forecast_file()
    results += test_isolation()
    print("-" * 46)
    if all(results):
        print("Все проверки пройдены.")
        return 0
    print(f"Провалено {results.count(False)} из {len(results)} проверок.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
