"""Конфигурация пайплайна: сезоны, пути, код соревнования."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
MODELS = DATA / "models"
REPORTS = DATA / "reports"

for _p in (RAW, PROCESSED, MODELS, REPORTS):
    _p.mkdir(parents=True, exist_ok=True)

# --- football-data.co.uk ---------------------------------------------------
CSV_BASE = "https://www.football-data.co.uk/mmz4281"
DIVISION = "P1"

# Схема имён каталогов на football-data.co.uk нерегулярна:
#   0001 = 2000/01, 09010 = 2009/10, 1920 = 2019/20, 2020 = 2020/21, 2627 = 2026/27
# Поэтому каталоги задаём списком, а не формулой. `season` — метка для данных.
SEASON_DIRS: list[tuple[str, str]] = [
    ("0001", "2000/01"),
    ("0102", "2001/02"),
    ("0203", "2002/03"),
    ("0304", "2003/04"),
    ("0405", "2004/05"),
    ("0506", "2005/06"),
    ("0607", "2006/07"),
    ("0708", "2007/08"),
    ("0809", "2008/09"),
    ("09010", "2009/10"),
    ("1011", "2010/11"),
    ("1112", "2011/12"),
    ("1213", "2012/13"),
    ("1314", "2013/14"),
    ("1415", "2014/15"),
    ("1516", "2015/16"),
    ("1617", "2016/17"),
    ("1718", "2017/18"),
    ("1819", "2018/19"),
    ("1920", "2019/20"),
    ("2020", "2020/21"),
    ("2122", "2021/22"),
    ("2223", "2022/23"),
    ("2324", "2023/24"),
    ("2425", "2024/25"),
    ("2526", "2025/26"),
    ("2627", "2026/27"),
]

CURRENT_SEASON = "2026/27"

# Сезоны, где есть закрывающие коэффициенты 1X2 (AvgCH/AvgCD/AvgCA).
# Раньше доступны только открывающие, а для честного сравнения с рынком нужны закрывающие.
CLOSING_ODDS_FROM = "2019/20"

# --- football-data.org ------------------------------------------------------
FD_ORG_BASE = "http://api.football-data.org/v4"
COMP_CODE = "PPL"


def fd_token() -> str | None:
    tok = os.getenv("FD_ORG_TOKEN")
    if not tok and (ROOT / ".env").exists():
        for raw_line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if line.startswith("FD_ORG_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip("\"'")
    return tok or None


def fixture_horizon_days() -> int:
    return int(os.getenv("FIXTURE_HORIZON_DAYS", "45"))
