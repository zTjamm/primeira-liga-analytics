"""Скачивание CSV с football-data.co.uk.

Схема колонок менялась между сезонами (от 22 до 119 колонок), поэтому читаем
всё строго по заголовку и приводим к единому набору. Индексы не используем
нигде — иначе добавление одной колонки в будущем сезоне тихо сломает пайплайн.
"""
from __future__ import annotations

import io
import time
from datetime import date, datetime

import polars as pl
import requests

from .config import CSV_BASE, DIVISION, RAW, SEASON_DIRS

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "primeira-liga-analytics/1.0"})

# Колонки, которые забираем. Всё, чего нет в конкретном сезоне, будет null.
WANTED = [
    # результат
    "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
    "HTHG", "HTAG", "HTR",
    # xG (только с сезона 2026/27)
    "HxG", "AxG",
    # статистика матча
    "HS", "AS", "HST", "AST", "HF", "AF", "HC", "AC", "HY", "AY", "HR", "AR",
    # открывающие коэффициенты
    "B365H", "B365D", "B365A", "AvgH", "AvgD", "AvgA",
    # закрывающие коэффициенты (с 2019/20)
    "AvgCH", "AvgCD", "AvgCA", "B365CH", "B365CD", "B365CA",
    "PSCH", "PSCD", "PSCA", "MaxCH", "MaxCD", "MaxCA",
    # закрывающие тоталы 2.5
    "AvgC>2.5", "AvgC<2.5", "B365C>2.5", "B365C<2.5",
    # азиатский гандикап
    "AHCh", "AvgCAHH", "AvgCAHA",
]

INT_COLS = [
    "FTHG", "FTAG", "HTHG", "HTAG", "HS", "AS", "HST", "AST",
    "HF", "AF", "HC", "AC", "HY", "AY", "HR", "AR",
]
FLOAT_COLS = [
    "HxG", "AxG",
    "B365H", "B365D", "B365A", "AvgH", "AvgD", "AvgA",
    "AvgCH", "AvgCD", "AvgCA", "B365CH", "B365CD", "B365CA",
    "PSCH", "PSCD", "PSCA", "MaxCH", "MaxCD", "MaxCA",
    "AvgC>2.5", "AvgC<2.5", "B365C>2.5", "B365C<2.5",
    "AHCh", "AvgCAHH", "AvgCAHA",
]


def _parse_date(raw: str | None) -> date | None:
    """Даты встречаются в трёх форматах: dd/mm/yy, dd/mm/yyyy и с временем впереди."""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_time(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def fetch_season(season_dir: str, season: str) -> pl.DataFrame:
    """Скачивает один сезон и возвращает нормализованный DataFrame."""
    url = f"{CSV_BASE}/{season_dir}/{DIVISION}.csv"
    local = RAW / f"{season_dir}.csv"

    if local.exists():
        text = local.read_text(encoding="utf-8", errors="replace")
    else:
        resp = SESSION.get(url, timeout=60)
        resp.raise_for_status()
        text = resp.content.decode("latin-1", errors="replace")
        local.write_text(text, encoding="utf-8")
        time.sleep(0.3)  # вежливо к источнику

    raw = pl.read_csv(
        io.StringIO(text),
        has_header=True,
        infer_schema_length=0,      # всё строками: сами приведём типы
        ignore_errors=True,
        truncate_ragged_lines=True,
    )

    # Чистим заголовки: \r, пробелы, пустые колонки
    clean = [c.strip().replace("\r", "") for c in raw.columns]
    df = raw.rename(dict(zip(raw.columns, clean)))
    df = df.drop([c for c in df.columns if not c])

    present = [c for c in WANTED if c in df.columns]
    missing = [c for c in WANTED if c not in df.columns]
    df = df.select(present)

    df = df.with_columns(
        pl.col("Date").map_elements(_parse_date, return_dtype=pl.Date).alias("match_date")
        if "Date" in df.columns
        else pl.lit(None, dtype=pl.Date).alias("match_date")
    )
    if "Time" in df.columns:
        df = df.with_columns(
            pl.col("Time").map_elements(_parse_time, return_dtype=pl.String).alias("kickoff")
        )
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.String).alias("kickoff"))

    for c in INT_COLS:
        if c in df.columns:
            df = df.with_columns(
                pl.col(c).str.strip_chars().cast(pl.Int32, strict=False).alias(c)
            )
    for c in FLOAT_COLS:
        if c in df.columns:
            df = df.with_columns(
                pl.col(c).str.strip_chars().cast(pl.Float64, strict=False).alias(c)
            )

    # Ключевые колонки приводим к нижнему регистру сразу; остальные
    # (коэффициенты, статистика) переименуются в normalize по общей карте.
    df = df.rename(
        {"HomeTeam": "hometeam", "AwayTeam": "awayteam", "FTR": "ftr", "HTR": "htr",
         "FTHG": "fthg", "FTAG": "ftag", "HTHG": "hthg", "HTAG": "htag"}
    )
    df = df.with_columns(
        pl.col("hometeam").str.strip_chars().alias("home_csv"),
        pl.col("awayteam").str.strip_chars().alias("away_csv"),
        pl.col("ftr").str.strip_chars().alias("ftr"),
        pl.col("htr").str.strip_chars().alias("htr"),
    ).drop("hometeam", "awayteam")  # ftr/htr уже в финальном виде, их не трогаем

    df = df.with_columns(
        pl.lit(season).alias("season"),
        pl.lit(season_dir).alias("season_dir"),
        pl.lit(",".join(missing)).alias("missing_cols"),
    )

    # Убираем хвостовые пустые строки — в части сезонов они есть
    df = df.filter(pl.col("home_csv").is_not_null() & (pl.col("home_csv") != ""))
    return df


def fetch_all() -> pl.DataFrame:
    frames = []
    for season_dir, season in SEASON_DIRS:
        df = fetch_season(season_dir, season)
        frames.append(df)
    out = pl.concat(frames, how="diagonal_relaxed")
    return out.sort(["match_date", "home_csv"])
