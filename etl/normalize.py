"""Приведение сырых данных к единой схеме.

Главная проблема, которую здесь решаем: один и тот же матч может прийти
из двух источников (CSV отстаёт, API — актуальный), а названия клубов
в них разные. Нужен один ключ, чтобы строки не задваивались.

Всё считается векторными выражениями polars, без Python-UDF по строкам:
на 7500 матчей разница заметна, а код остаётся читаемым.
"""
from __future__ import annotations

import polars as pl

from . import teams as T
from .config import CLOSING_ODDS_FROM

MATCH_SCHEMA = {
    "match_id": pl.String,
    "season": pl.String,
    "match_date": pl.Date,
    "home_id": pl.String,
    "away_id": pl.String,
    "home_name": pl.String,
    "away_name": pl.String,
    "fthg": pl.Int32,
    "ftag": pl.Int32,
    "ftr": pl.String,
    "htr": pl.String,
    "hthg": pl.Int32,
    "htag": pl.Int32,
    "xg_home": pl.Float64,
    "xg_away": pl.Float64,
    "shots_home": pl.Int32,
    "shots_away": pl.Int32,
    "sot_home": pl.Int32,
    "sot_away": pl.Int32,
    "corners_home": pl.Int32,
    "corners_away": pl.Int32,
    "fouls_home": pl.Int32,
    "fouls_away": pl.Int32,
    "yellow_home": pl.Int32,
    "yellow_away": pl.Int32,
    "red_home": pl.Int32,
    "red_away": pl.Int32,
    "odds_open": pl.List(pl.Float64),      # [H, D, A] средние открывающие
    "odds_close": pl.List(pl.Float64),     # [H, D, A] средние закрывающие
    "ou25_close": pl.List(pl.Float64),     # [Over2.5, Under2.5] закрывающие
    # Betfair Exchange — биржевой рынок с минимальной
    # маржой (около 0.6% против у среднего по
    # конторам, от 6.8%). Именно он точнее сравнения,
    # поэтому и сравниваться с ним не симещный.
    "bfx_close": pl.List(pl.Float64),      # [H, D, A] биржа закрывающая
    "bfx_over25": pl.List(pl.Float64),     # [Over2.5, Under2.5] биржа
    # Азиатский хэндикап: AHCh — сама линия, AvgCAHH и AvgCAHA —
    # цены по обеим сторонам. Единственный рынок,
    # который лежит в данных без дела.
    "ah_line": pl.Float64,                 # линия хэндикапа, отрицательно
    "ah_odds": pl.List(pl.Float64),        # [хозяев, гости] цены
    "has_closing": pl.Boolean,
    "source": pl.String,
}

CSV_NAME_TO_ID = dict(T.CSV_TO_ID)
ID_TO_NAME = {tid: meta["name"] for tid, meta in T.TEAMS.items()}
ID_TO_FD_ID = {tid: meta["fd"] for tid, meta in T.TEAMS.items() if meta["fd"] is not None}

NULL_LIST = pl.lit(None, dtype=pl.List(pl.Float64))



# Колонки коэффициентов, которых нет в части сезонов: Betfair Exchange
# появился не сразу, азиатский хэндикап — тоже. Раньше на это опирались
# только колонки, которые есть во всех файлах, и потому проблемы не было.
# Теперь недостающие создаются заполненными null ДО попытки их прочитать:
# иначе нормализация падает с ColumnNotFoundError на первом старом сезоне.
OPTIONAL_ODDS_COLS: list[str] = [
    "BFECH", "BFECD", "BFECA", "BFEC>2.5", "BFEC<2.5",
    "AHCh", "AvgCAHH", "AvgCAHA",
]


def ensure_odds_columns(df: pl.DataFrame) -> pl.DataFrame:
    for c in OPTIONAL_ODDS_COLS:
        if c not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias(c))
    return df

def _clean_ah_line(col: pl.Expr) -> pl.Expr:
    """Линия азиатского хэндикапа приходит строкой и часто мусорной.

    В файлах встречаются "", "-", "0", значения вроде "AHh" из заголовка
    неудачных загрузок и пропуски. Оставляем только числа в разумном
    диапазоне: футбольный хэндикап не бывает вне ±10 голов.
    """
    parsed = (
        pl.col("AHCh") if False else col
    ).cast(pl.String).str.strip_chars().cast(pl.Float64, strict=False)
    return pl.when(parsed.is_between(-10.0, 10.0)).then(parsed).otherwise(None)


def _odds_expr(cols: list[str], lo: float = 1.0, hi: float = 1000.0) -> pl.Expr:
    """Собирает список коэффициентов, но только если все значения осмысленны.

    Частично заполненные строки (один коэффициент есть, два нет) — мусор,
    из них нельзя вывести вероятности, поэтому роняем целиком в null.
    """
    valid = pl.all_horizontal([pl.col(c).is_between(lo, hi) for c in cols])
    lst = pl.concat_list([pl.col(c) for c in cols])
    return pl.when(valid).then(lst).otherwise(NULL_LIST)


def _finalize(df: pl.DataFrame) -> pl.DataFrame:
    cols = list(MATCH_SCHEMA.keys())
    for c in cols:
        if c not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=MATCH_SCHEMA[c]).alias(c))
    return df.select([pl.col(c).cast(MATCH_SCHEMA[c], strict=False) for c in cols])


def match_id(season: str, match_date, home_id: str, away_id: str) -> str:
    return f"{season}|{match_date}|{home_id}|{away_id}"


def normalize_csv(df: pl.DataFrame) -> pl.DataFrame:
    """CSV → единая схема. Клубы, которых нет в справочнике, считаем ошибкой:
    молча выкидывать их нельзя — это либо опечатка в справочнике, либо потеря данных."""
    df = df.with_columns(
        pl.col("home_csv").str.strip_chars().replace_strict(CSV_NAME_TO_ID, default=None).alias("home_id"),
        pl.col("away_csv").str.strip_chars().replace_strict(CSV_NAME_TO_ID, default=None).alias("away_id"),
    )

    unknown = df.filter(pl.col("home_id").is_null() | pl.col("away_id").is_null())
    if unknown.height:
        bad = sorted(set(unknown["home_csv"].to_list() + unknown["away_csv"].to_list()))
        raise ValueError(
            "Неизвестные клубы в CSV — добавь их в etl/teams.py: " + ", ".join(str(b) for b in bad)
        )

    df = df.with_columns(
        (pl.col("season") + "|" + pl.col("match_date").cast(pl.String) + "|"
         + pl.col("home_id") + "|" + pl.col("away_id")).alias("match_id"),
        pl.col("home_id").replace_strict(ID_TO_NAME, default=None).alias("home_name"),
        pl.col("away_id").replace_strict(ID_TO_NAME, default=None).alias("away_name"),
    )

    df = ensure_odds_columns(df)
    df = df.filter(pl.col("fthg").is_not_null() & pl.col("ftag").is_not_null())
    df = df.filter(pl.col("fthg").is_between(0, 15) & pl.col("ftag").is_between(0, 15))

    df = df.with_columns(
        _odds_expr(["AvgH", "AvgD", "AvgA"]).alias("odds_open"),
        _odds_expr(["AvgCH", "AvgCD", "AvgCA"]).alias("odds_close"),
        _odds_expr(["AvgC>2.5", "AvgC<2.5"]).alias("ou25_close"),
        _odds_expr(["BFECH", "BFECD", "BFECA"]).alias("bfx_close"),
        _odds_expr(["BFEC>2.5", "BFEC<2.5"]).alias("bfx_over25"),
        _odds_expr(["AvgCAHH", "AvgCAHA"]).alias("ah_odds"),
        _clean_ah_line(pl.col("AHCh")).alias("ah_line"),
    )

    df = df.with_columns(
        (pl.col("odds_close").is_not_null() & (pl.col("season") >= CLOSING_ODDS_FROM)).alias("has_closing"),
        pl.lit("csv").alias("source"),
    ).rename(
        {
            "HxG": "xg_home", "AxG": "xg_away",
            "HS": "shots_home", "AS": "shots_away",
            "HST": "sot_home", "AST": "sot_away",
            "HC": "corners_home", "AC": "corners_away",
            "HF": "fouls_home", "AF": "fouls_away",
            "HY": "yellow_home", "AY": "yellow_away",
            "HR": "red_home", "AR": "red_away",
        }
    )
    return _finalize(df)


def resolve_ids(df: pl.DataFrame) -> pl.DataFrame:
    """Добавляет home_id/away_id по названиям из football-data.org.

    Отдельная функция, потому что расписание будущих матчей ещё не имеет
    результата, и normalize_api его отфильтровывает — а ID нужны и там.
    Ничего не фильтрует и ничего не отбрасывает.
    """
    if df.height == 0:
        return df.with_columns(pl.lit(None, dtype=pl.String).alias("home_id"),
                               pl.lit(None, dtype=pl.String).alias("away_id"))

    fd_name_to_id = {meta["name"]: tid for tid, meta in T.TEAMS.items() if meta["fd"] is not None}
    df = df.with_columns(
        pl.col("home_name").replace_strict(fd_name_to_id, default=None).alias("home_id"),
        pl.col("away_name").replace_strict(fd_name_to_id, default=None).alias("away_id"),
    )

    unknown = df.filter(pl.col("home_id").is_null() | pl.col("away_id").is_null())
    if unknown.height:
        bad = sorted(set(unknown["home_name"].to_list() + unknown["away_name"].to_list()))
        raise ValueError("Неизвестные клубы в API — добавь в etl/teams.py: " + ", ".join(str(b) for b in bad))
    return df


def normalize_api(df: pl.DataFrame, season: str) -> pl.DataFrame:
    """API → та же схема. Отсюда берём будущие матчи и догруженные результаты."""
    if df.height == 0:
        return pl.DataFrame(schema=MATCH_SCHEMA)

    df = resolve_ids(df)
    df = df.with_columns(
        pl.lit(season).alias("season"),
        (pl.lit(season) + "|" + pl.col("match_date").cast(pl.String) + "|"
         + pl.col("home_id") + "|" + pl.col("away_id")).alias("match_id"),
        pl.col("home_id").replace_strict(ID_TO_NAME, default=None).alias("home_name"),
        pl.col("away_id").replace_strict(ID_TO_NAME, default=None).alias("away_name"),
        pl.lit("api").alias("source"),
    )

    stats = [
        "xg_home", "xg_away", "shots_home", "shots_away", "sot_home", "sot_away",
        "corners_home", "corners_away", "fouls_home", "fouls_away",
        "yellow_home", "yellow_away", "red_home", "red_away",
        "hthg", "htag", "htr", "odds_open", "odds_close", "ou25_close",
        "bfx_close", "bfx_over25", "ah_line", "ah_odds",
    ]
    df = df.with_columns([pl.lit(None, dtype=MATCH_SCHEMA[c]).alias(c) for c in stats])
    df = df.with_columns(pl.lit(False).alias("has_closing"))

    df = df.filter(pl.col("fthg").is_not_null() & pl.col("ftag").is_not_null())
    return _finalize(df)


def merge_sources(csv_df: pl.DataFrame, api_df: pl.DataFrame) -> pl.DataFrame:
    """CSV и API пересекаются по match_id. CSV берём первым: в нём есть
    статистика матча и коэффициенты, которых в API просто нет."""
    if api_df.height == 0:
        return csv_df.sort("match_date")
    return (
        pl.concat([csv_df, api_df], how="diagonal_relaxed")
        .unique(subset=["match_id"], keep="first", maintain_order=True)
        .sort("match_date")
    )
