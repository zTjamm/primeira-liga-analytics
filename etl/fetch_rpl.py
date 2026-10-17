"""Загрузка и нормализация данных РФПЛ.

Два источника, и это принципиально:

* результаты — football-data.co.uk/new/RUS.csv (полная история с 2012/13);
* расписание — официальный API premierliga.ru, потому что CSV содержит
  только сыгранные матчи.

API официального сайта РФПЛ устроен так: базовый адрес https://api.premierliga.ru,
все запросы POST с JSON-телом. GET возвращает 405. Матчи лежат в /api/getMatches
с обязательным полем tournament (id текущего турнира берётся из /api/init),
клубы — в /api/getClubs (пустой JSON `{}`).

Ключевой момент для изоляции: в API id клубов числовые, а в CSV — английские
названия. Соответствие держится в CLUB_ID_TO_NAME и покрывает ровно те 16
клубов, что играют в сезоне 2026/27. Незнакомый id — это ошибка данных,
и мы падаем громко, а не подставляем пустое имя в прогноз.
"""
from __future__ import annotations

import io
from datetime import datetime

import polars as pl
import requests

from .config import RAW

URL = "https://www.football-data.co.uk/new/RUS.csv"
RAW_PATH = RAW / "rpl.csv"

# --- Официальный API РФПЛ (расписание) ---
API_BASE = "https://api.premierliga.ru"
API_HEADERS = {
    "User-Agent": "rpl-analytics/1.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# id клуба в API -> название в CSV от football-data.co.uk.
# Покрывает клубы сезона 2026/27. Незнакомый id при разборе расписания
# поднимает KeyError — лучше упасть, чем молча выдать прогноз на пустую команду.
CLUB_ID_TO_NAME = {
    1: "Spartak Moscow",
    2: "CSKA Moscow",
    3: "Zenit",
    4: "Rubin Kazan",
    5: "Lokomotiv Moscow",
    7: "Dynamo Moscow",
    10: "Krylya Sovetov",
    11: "FK Rostov",
    125: "Dynamo Makhachkala",
    264: "Fakel Voronezh",
    444: "Baltika",
    504: "Orenburg",
    584: "Krasnodar",
    702: "Akhmat Grozny",
    807: "Akron Togliatti",
    808: "Rodina Moscow",
}

# Колонки, которые забираем из CSV
WANTED = [
    "Date", "Time", "Home", "Away", "HG", "AG", "Res",
    "PSCH", "PSCD", "PSCA",
    "MaxCH", "MaxCD", "MaxCA",
    "AvgCH", "AvgCD", "AvgCA",
]

INT_COLS = ["HG", "AG"]  # исходные имена, оставлены для справки: cast идёт по новым
FLOAT_COLS = ["PSCH", "PSCD", "PSCA", "MaxCH", "MaxCD", "MaxCA", "AvgCH", "AvgCD", "AvgCA"]


def fetch() -> str:
    """Скачивает CSV и возвращает текст."""
    r = requests.get(URL, timeout=30)
    r.encoding = "utf-8-sig"
    r.raise_for_status()
    return r.text


def parse(csv_text: str) -> pl.DataFrame:
    """Парсит CSV в DataFrame с нормализованными колонками."""
    df = pl.read_csv(io.StringIO(csv_text), infer_schema_length=0)

    # Переименовываем колонки в стандартные для нашего проекта
    rename = {}
    if "Date" in df.columns:
        rename["Date"] = "match_date"
    if "Time" in df.columns:
        rename["Time"] = "kickoff_time"
    if "Home" in df.columns:
        rename["Home"] = "home_name"
    if "Away" in df.columns:
        rename["Away"] = "away_name"
    if "HG" in df.columns:
        rename["HG"] = "fthg"
    if "AG" in df.columns:
        rename["AG"] = "ftag"
    if "Res" in df.columns:
        rename["Res"] = "ftr"
    if "PSCH" in df.columns:
        rename["PSCH"] = "odds_h"
    if "PSCD" in df.columns:
        rename["PSCD"] = "odds_d"
    if "PSCA" in df.columns:
        rename["PSCA"] = "odds_a"

    df = df.rename(rename)

    # Парсим дату (формат dd/mm/yyyy)
    if "match_date" in df.columns:
        df = df.with_columns(
            pl.col("match_date").str.strptime(pl.Date, "%d/%m/%Y", strict=False).alias("match_date")
        )

    # Сезон вычисляем из даты (РФПЛ: июль-май)
    if "match_date" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("match_date").dt.month() >= 7)
            .then(pl.col("match_date").dt.year().cast(pl.String) + "/" + (pl.col("match_date").dt.year() + 1).cast(pl.String).str.slice(2, 4))
            .otherwise((pl.col("match_date").dt.year() - 1).cast(pl.String) + "/" + pl.col("match_date").dt.year().cast(pl.String).str.slice(2, 4))
            .alias("season")
        )

    # Приводим типы. Внимание на имена: колонки уже переименованы выше,
    # поэтому здесь используются новые имена. Раньше здесь стояли исходные
    # "HG"/"AG", условие `if c in df.columns` не выполнялось, и cast
    # молча пропускался — счёт оставался строкой (нашли тестом tests.test_rpl).
    for c in ("fthg", "ftag"):
        if c in df.columns:
            df = df.with_columns(pl.col(c).cast(pl.Int32, strict=False))
    for c in ("odds_h", "odds_d", "odds_a", "MaxCH", "MaxCD", "MaxCA",
              "AvgCH", "AvgCD", "AvgCA"):
        if c in df.columns:
            df = df.with_columns(pl.col(c).cast(pl.Float64, strict=False))

    # Генерируем match_id из даты и команд
    df = df.with_columns(
        pl.format("{}_{}_{}", pl.col("match_date").cast(pl.String), pl.col("home_name"), pl.col("away_name")).alias("match_id")
    )

    # Удаляем строки без даты и команд — это мусор в конце CSV, не расписание
    df = df.filter(
        pl.col("match_date").is_not_null()
        & pl.col("home_name").is_not_null()
        & pl.col("away_name").is_not_null()
    )

    return df


def fetch_fixtures() -> pl.DataFrame:
    """Загружает расписание и результаты из официального API РФПЛ.

    Возвращает ВСЕ матчи турнира — и сыгранные, и будущие. Сыгранные
    нужны журналу: CSV с результатами обновляется с задержкой в недели
    (в августе файл обрывался на 02.08, а лига играла дальше), и полагаться
    на него для резолва прогнозов нельзя. API отдаёт счёт сразу.

    Дата в API приходит в виде "2026-10-18UTC14:00:00": формат нестандартный,
    UTC написан без разделителя, поэтому парсим вручную, а не strftime.
    """
    import json
    from datetime import date, datetime as dt

    # /api/init — единственный GET-эндпоинт: POST на него даёт 405
    init = requests.get(f"{API_BASE}/api/init", headers=API_HEADERS, timeout=20)
    init.raise_for_status()
    tournament = int(json.loads(init.text)["currentTournament"])

    r = requests.post(
        f"{API_BASE}/api/getMatches",
        headers=API_HEADERS,
        json={"tournament": tournament},
        timeout=30,
    )
    r.raise_for_status()
    stages = r.json()

    # Названия туров ("Тур 11") лежат отдельно, в /api/getStages
    rs = requests.post(
        f"{API_BASE}/api/getStages",
        headers=API_HEADERS,
        json={"tournament": tournament},
        timeout=20,
    )
    rs.raise_for_status()
    stage_names = {int(s["id"]): s.get("name", f"Тур {s['id']}") for s in rs.json()}

    rows = []
    for st in stages:
        stage_name = stage_names.get(int(st.get("stage", 0)), str(st.get("stage", "")))
        for m in st.get("matches", []):
            raw = m.get("date") or ""
            # "2026-10-18UTC14:00:00"
            if "UTC" not in raw:
                continue
            day_s, time_s = raw.split("UTC", 1)
            y, mo, da = (int(x) for x in day_s.split("-"))
            hh, mm = (int(x) for x in time_s.split(":")[:2])
            # Счёт null до окончания матча — так будущий матч отличается
            # от сыгранного, и журнал не примет непоставленную игру за факт.
            gh, ga = m.get("goalH"), m.get("goalA")
            played = gh is not None and ga is not None
            rows.append({
                "match_id": f"rpl_{m['id']}",
                "api_id": int(m["id"]),
                "match_date": date(y, mo, da),
                "kickoff_utc": dt(y, mo, da, hh, mm).isoformat() + "Z",
                "home_name": CLUB_ID_TO_NAME[int(m["clubH"])],
                "away_name": CLUB_ID_TO_NAME[int(m["clubA"])],
                "fthg": int(gh) if played else None,
                "ftag": int(ga) if played else None,
                "ftr": ("H" if gh > ga else "A" if ga > gh else "D") if played else None,
                "stage_id": int(st.get("stage", 0)),
                "matchday": stage_name,
                "city": m.get("city") or "",
                "stadium": m.get("stadium") or "",
            })

    df = pl.DataFrame(rows, schema={
        "match_id": pl.String, "api_id": pl.Int64,
        "match_date": pl.Date, "kickoff_utc": pl.String,
        "home_name": pl.String, "away_name": pl.String,
        "fthg": pl.Int32, "ftag": pl.Int32, "ftr": pl.String,
        "stage_id": pl.Int64, "matchday": pl.String,
        "city": pl.String, "stadium": pl.String,
    })
    return df.sort("match_date")


def load() -> pl.DataFrame:
    """Скачивает CSV заново и парсит; кэш — только как запасной вариант.

    Здесь намеренно нет «прочитать с диска, если файл есть»: именно так
    VTB-пайплайн 187 прогонов подряд отдавал устаревшие данные. Сеть
    в приоритете, кэш используется только при ошибке сети — и тогда
    ошибка озвучивается, а не прячется.
    """
    try:
        text = fetch()
    except Exception as e:
        if not RAW_PATH.exists():
            raise
        print(f"  СЕТЬ НЕДОСТУПНА ({type(e).__name__}), беру кэш {RAW_PATH}")
        text = RAW_PATH.read_text(encoding="utf-8")
    else:
        RAW_PATH.write_text(text, encoding="utf-8")
    return parse(text)


def save(df: pl.DataFrame, path) -> None:
    """Сохраняет DataFrame в parquet."""
    df.write_parquet(path)


def main() -> int:
    print("Загрузка данных РФПЛ...")
    df = load()
    print(f"  Всего матчей: {df.height}")
    print(f"  Сезоны: {df['season'].unique().sort().to_list()}")
    print(f"  Матчей с коэффициентами: {df.filter(pl.col('odds_h').is_not_null()).height}")

    # Сохраняем
    from .config import PROCESSED
    save(df, PROCESSED / "rpl_matches.parquet")
    print(f"  Сохранено: {PROCESSED / 'rpl_matches.parquet'}")

    # Расписание и результаты из официального API
    print("Загрузка расписания РФПЛ из premierliga.ru...")
    try:
        fx = fetch_fixtures()
        fx.write_parquet(PROCESSED / "rpl_fixtures.parquet")
        upcoming = fx.filter(pl.col("fthg").is_null())
        played = fx.filter(pl.col("fthg").is_not_null())
        nxt = upcoming.head(1)
        when = f"{nxt.row(0, named=True)['match_date']}" if nxt.height else "—"
        print(f"  Всего матчей турнира: {fx.height} "
              f"(сыграно {played.height}, предстоит {upcoming.height}), "
              f"ближайший: {when}")
    except Exception as e:
        # Расписание — отдельный шаг: его сбой не должен убивать загрузку
        # результатов, но и не должен пройти молча.
        print(f"  ОШИБКА расписания ({type(e).__name__}): {e}")
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
