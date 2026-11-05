"""Сбор данных NBA из официального API NBA (через библиотеку nba_api).
Ключ не нужен.

Источники два, датасет один:

  * LeagueGameFinder — статистика сыгранных матчей. Счёт берётся оттуда,
    потому что это данные box score, а не сводка календаря;
  * ScheduleLeagueV2 — календарь сезона: кто дома, кто в гостях, время
    начала матча (UTC) и то, что ещё не сыграно.

Раньше источников было три, и все три жили своей жизнью:
etl/fetch_nba.py (только история, кэш «навсегда» — сезон записался и
больше не обновлялся), etl/fetch_nba_fixtures.py (фикстуры без времени
начала) и etl/fetch_nba_fixtures_v3*.py (фикстуры через ScoreboardV3,
обход по дням). Ни один из них не был в цикле обновления сервера, и
результат был такой: в журнале NBA не было матчей за последний день, а
расписание на сайте жило своей жизнью.

Теперь источник один — data/processed/nba_matches.parquet. Его
обновляет этот модуль: прошлые сезоны берутся из кэша, текущий
перезапрашивается каждый прогон (два запроса к API, несколько секунд).

Даты матчей — местные (восточное время NBA), как их отдаёт API.
Время начала — в UTC: по нему единственно можно сравнивать матч с
текущим моментом.

Запуск:
    python -m etl.fetch_nba
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import polars as pl
from nba_api.stats.endpoints import leaguegamefinder, scheduleleaguev2

from etl.config import PROCESSED, RAW, REPORTS

NBA_LEAGUE_ID = "00"

# Сколько сезонов держим в данных.
#
# Три сезона — как и было раньше. Модель обучается на всех сыгранных
# матчах, и лишние сезоны тут ничего не дают: баскетбольная модель
# гасит вес матчам на горизонте двух лет (models/basketball.py).
SEASONS_COUNT = 3

# Сколько матчей в завершённом сезоне NBA.
#
# Регулярный чемпионат — 1 230 матчей (30 команд × 82 игры, каждая игра
# засчитывается обеим сторонам), плей-офф — 171. Итого 1 401.
#
# Смысл таблицы — самопроверка сбора: сезон, где число матчей не
# совпало, означает потерянный запрос. Молча такое проходило: кэш
# сезона писался один раз и больше не проверялся, поэтому потерянные
# запросы были видны только по тому, что в журнале нет свежих матчей.
FULL_SEASON_MATCHES = {
    "2024-25": 1401,
    "2025-26": 1401,
}

# Сколько раз повторять неудачный запрос.
#
# Один запрос — это весь сезон (1 401 матч или календарь на полгода), и
# молча потерянный запрос выглядит как «сезон стал короче»: никто не
# увидит, что запрос не прошёл. Поэтому неудача повторяется, и только
# после этого запрос считается потерянным.
FETCH_ATTEMPTS = 3

# Пауза между попытками и вежливость к API.
RETRY_PAUSE = 1.5
RATE_LIMIT_DELAY = 0.3

SESSION_CACHE = RAW / "nba_cache"
SESSION_CACHE.mkdir(parents=True, exist_ok=True)

# Обязательные колонки кэша.
#
# Кэш, собранный старой версией модуля, этих колонок не содержит (в нём
# не было времени начала матча), поэтому он пересобирается, а не
# молча используется: иначе расписание осталось бы без времени начала
# навсегда.
REQUIRED_COLUMNS = (
    "match_id", "season", "match_date", "kickoff_utc",
    "home_id", "home_name", "away_id", "away_name",
    "home_score", "away_score", "is_played",
)

# Схема датасета. Задаётся явно, а не «как получится»: строки из кэша и
# из сети должны складываться в одну таблицу, иначе polars расставит
# типы по-разному (и падение будет выглядеть как ошибка в данных, а не
# в коде).
SCHEMA: dict[str, pl.DataType] = {
    "match_id": pl.Utf8,
    "season": pl.Utf8,
    "match_date": pl.Date,
    "kickoff_utc": pl.Utf8,
    "home_id": pl.Utf8,
    "home_name": pl.Utf8,
    "away_id": pl.Utf8,
    "away_name": pl.Utf8,
    "home_score": pl.Int32,
    "away_score": pl.Int32,
    "is_played": pl.Boolean,
    "home_win": pl.Int32,
    "total_points": pl.Int32,
    "home_fg_pct": pl.Float64,
    "away_fg_pct": pl.Float64,
    "home_3p_pct": pl.Float64,
    "away_3p_pct": pl.Float64,
    "home_reb": pl.Int32,
    "away_reb": pl.Int32,
    "home_ast": pl.Int32,
    "away_ast": pl.Int32,
}

# Сколько дней без сыгранных матчей считать тревогой.
#
# Проверка нужна для главной беды этого модуля: данные замирают, сайт
# показывает вчерашнее, и это видно только по дате последнего матча.
# В межсезонье (лето) матчей нет неделями — там тревоги не поднимаем,
# а ориентируемся на то, есть ли в календаре будущие матчи.
STALE_DAYS = 4


def _cache_path(season: str) -> Path:
    return SESSION_CACHE / f"nba_{season}.parquet"


# --------------------------------------------------------------------------
# Сезоны


def _season_start_year(d: date) -> int:
    """Год начала сезона NBA, в который попадает дата d.

    Сезон идёт с октября по июнь: матчи января — это сезон прошлого
    октября, а матчи июля — всё ещё тот же сезон (плей-офф только что
    закончился). Новый сезон начинается в октябре.
    """
    return d.year if d.month >= 10 else d.year - 1


def _season_label(start_year: int) -> str:
    """Метка сезона в формате NBA: 2026-27, а не 2026-2027.

    Формат важен: API принимает сезон только в таком виде и на неверном
    отвечает ошибкой без поля resultSet.
    """
    return f"{start_year}-{str(start_year + 1)[2:]}"


def _seasons(today: date | None = None) -> list[str]:
    """Сезоны в данных: текущий и два предыдущих."""
    today = today or date.today()
    y = _season_start_year(today)
    return [_season_label(y - i) for i in range(SEASONS_COUNT)]


def _refresh_seasons(today: date | None = None) -> set[str]:
    """Сезоны, которые надо обновлять из сети.

    Текущий сезон обновляется всегда. Следующий — когда он уже близко:
    расписание нового сезона NBA публикуют в августе, и если не начать
    тянуть его заранее, на сайте останется календарь прошлого года.
    """
    today = today or date.today()
    y = _season_start_year(today)
    out = {_season_label(y)}
    next_start = date(y + 1, 10, 1)
    if (next_start - today).days <= 45:
        out.add(_season_label(y + 1))
    return out


# --------------------------------------------------------------------------
# Запросы


def _fetch_with_retry(label: str, call):
    """Запрос с повторами. None — запрос не удался даже после повторов."""
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return call()
        except Exception as e:
            print(f"  Ошибка {label} (попытка {attempt}/{FETCH_ATTEMPTS}): {e}")
            if attempt < FETCH_ATTEMPTS:
                time.sleep(RETRY_PAUSE * attempt)
    print(f"  {label}: запрос потерян")
    return None


def _float(row, key: str) -> float | None:
    """Число из строки статистики; None, если поля нет или оно пустое."""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return None
    return None if pd.isna(value) else float(value)


def _int(row, key: str) -> int | None:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return None
    return None if pd.isna(value) else int(value)


def _schedule_team_name(row, prefix: str) -> str:
    """Полное название команды из календаря: город + название.

    Расписание отдаёт «Phoenix» и «Suns» отдельно, а статистика — уже
    готовое «Phoenix Suns». Собираем полное имя, чтобы названия в
    журнале (сыгранные и будущие матчи) не расходились.
    """
    city = str(row.get(f"{prefix}Team_teamCity") or "").strip()
    name = str(row.get(f"{prefix}Team_teamName") or "").strip()
    full = f"{city} {name}".strip()
    return full or name or city


def _kickoff(value) -> str | None:
    """Время начала матча в UTC.

    У матчей без известного времени API отдаёт заглушку 1900-01-01 —
    это не дата, и такое время в прогнозы попадать не должно.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.startswith("1900"):
        return None
    return text.replace("+00:00", "Z")


def _fetch_schedule(season: str) -> list[dict] | None:
    """Календарь сезона: кто дома, кто в гостях, время начала, счёт.

    ScheduleLeagueV2 отдаёт весь сезон целиком: сыгранные (gameStatus=3,
    со счётом) и будущие (gameStatus=1, с временем начала в UTC).
    Время начала указано и для сыгранных — оно пригодится, когда матч
    переносится: новую дату знает только расписание.
    """
    def _call():
        schedule = scheduleleaguev2.ScheduleLeagueV2(
            league_id=NBA_LEAGUE_ID,
            season=season,
            timeout=30,
        )
        frames = schedule.get_data_frames()
        return frames[0] if frames else None

    table = _fetch_with_retry(f"сезон {season} (календарь)", _call)
    if table is None:
        return None
    if table.empty:
        return []

    records: list[dict] = []
    # to_dict("records"), а не to_dicts(): это pandas, у него нет
    # to_dicts — такой метод есть у polars.
    for row in table.to_dict("records"):
        game_id = row.get("gameId")
        if game_id is None:
            continue
        status = row.get("gameStatus")
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = 0
        postponed = str(row.get("postponedStatus") or "N").upper()
        if postponed not in ("N", ""):
            # Перенесённый матч: в расписании у него новая дата, но
            # играть он может и не состояться. Такие не показываем.
            continue

        raw_date = row.get("gameDate")
        match_date = pd.to_datetime(raw_date, format="%m/%d/%Y", errors="coerce")
        if pd.isna(match_date):
            match_date = pd.to_datetime(row.get("gameDateEst"), errors="coerce")
        if pd.isna(match_date):
            continue

        played = status == 3
        records.append({
            "match_id": str(game_id),
            "season": season,
            "match_date": match_date.date(),
            "kickoff_utc": _kickoff(row.get("gameDateTimeUTC")),
            "home_id": str(row.get("homeTeam_teamId")),
            "home_name": _schedule_team_name(row, "home"),
            "away_id": str(row.get("awayTeam_teamId")),
            "away_name": _schedule_team_name(row, "away"),
            "home_score": None,
            "away_score": None,
            "is_played": played,
            "status": str(row.get("gameStatusText") or ""),
            "home_win": None,
            "total_points": None,
            "home_fg_pct": None,
            "away_fg_pct": None,
            "home_3p_pct": None,
            "away_3p_pct": None,
            "home_reb": None,
            "away_reb": None,
            "home_ast": None,
            "away_ast": None,
        })
    return records


def _split_teams(group, sides: tuple[str, str] | None):
    """Кто хозяева, а кто гости. Возвращает строки (одна или несколько).

    Сначала — по календарю: там команды размечены прямо. У части матчей
    статистика отдаёт одинаковый MATCHUP в обеих строках (у «DAL @ HOU»
    обе строки были одинаковыми), и разбор по строке такие матчи
    терял: за сезон выпадало около девяти игр, молча и без следа.

    Строк может быть больше двух: у матчей NBA Global Games
    (интернациональных, например «Hapoel Jerusalem B.C.» против Brooklyn
    Nets) в статистике три строки — очки гостей разбиты на две части
    (16 и 72). Такие матчи старый код отбрасывал целиком, а очки гостей
    надо сложить.

    Если матча в календаре нет (бывает: за сезон одна игра туда не
    попадает) — разбор по MATCHUP, как раньше.
    """
    if sides:
        home_id, away_id = sides
        home_rows = group[group["TEAM_ID"].astype(str) == str(home_id)]
        away_rows = group[group["TEAM_ID"].astype(str) == str(away_id)]
        if len(home_rows) >= 1 and len(away_rows) >= 1:
            return home_rows, away_rows
    home_rows = group[group["MATCHUP"].str.contains("vs.", na=False)]
    away_rows = group[group["MATCHUP"].str.contains("@", na=False)]
    if home_rows.empty or away_rows.empty:
        return None, None
    return home_rows, away_rows


def _team_points(rows) -> int | None:
    """Очки команды: сумма по всем её строкам."""
    values = []
    for _, row in rows.iterrows():
        try:
            value = row["PTS"]
        except (KeyError, IndexError, TypeError):
            return None
        if pd.isna(value):
            return None
        values.append(int(value))
    if not values:
        return None
    return sum(values)


def _team_share(rows, key: str) -> float | None:
    """Доля (процент попаданий) команды.

    У гостей Global Games две строки, и проценты у них не складываются:
    берём среднее, взвешенное по очкам. У обычных команд одна строка,
    поэтому результат совпадает с исходным значением.
    """
    values: list[float] = []
    weights: list[float] = []
    for _, row in rows.iterrows():
        value = _float(row, key)
        if value is None:
            continue
        points = _float(row, "PTS") or 0.0
        values.append(value)
        weights.append(max(points, 1.0))
    if not values:
        return None
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def _team_sum(rows, key: str) -> int | None:
    """Счётчик команды (подборы, передачи): сумма по строкам."""
    values = []
    for _, row in rows.iterrows():
        value = _int(row, key)
        if value is None:
            continue
        values.append(value)
    if not values:
        return None
    return sum(values)


def _stats_team_name(rows) -> str:
    """Название команды из статистики: самое длинное из строк (у гостей
    Global Games в одной строке может быть короткое имя)."""
    names = [str(row["TEAM_NAME"]).strip() for _, row in rows.iterrows()
             if row.get("TEAM_NAME")]
    if not names:
        return ""
    return max(names, key=len)


def _fetch_played(season: str, sides: dict[str, tuple[str, str]]) -> list[dict] | None:
    """Сыгранные матчи сезона: статистика из box score.

    LeagueGameFinder отдаёт по две строки на матч (по одной на команду)
    и только для сыгранных игр: будущие матчи в ответе не появляются
    вовсе — это проверялось отдельным запросом с диапазоном дат, и API
    отдал ноль строк на будущее. Поэтому за расписание отвечает другой
    эндпоинт (см. _fetch_schedule).
    """
    def _call():
        finder = leaguegamefinder.LeagueGameFinder(
            league_id_nullable=NBA_LEAGUE_ID,
            season_nullable=season,
            timeout=30,
        )
        return finder.get_data_frames()[0]

    games = _fetch_with_retry(f"сезон {season} (статистика)", _call)
    if games is None:
        return None
    if games.empty:
        return []

    records: list[dict] = []
    for game_id, group in games.groupby("GAME_ID", sort=False):
        home_rows, away_rows = _split_teams(group, sides.get(str(game_id)))
        if home_rows is None or away_rows is None:
            continue
        home_pts = _team_points(home_rows)
        away_pts = _team_points(away_rows)
        if home_pts is None or away_pts is None:
            continue

        first = home_rows.iloc[0]
        match_date = pd.to_datetime(first["GAME_DATE"], errors="coerce")
        if pd.isna(match_date):
            continue

        records.append({
            "match_id": str(game_id),
            "season": season,
            "match_date": match_date.date(),
            "kickoff_utc": None,
            "home_id": str(first["TEAM_ID"]),
            "home_name": _stats_team_name(home_rows),
            "away_id": str(away_rows.iloc[0]["TEAM_ID"]),
            "away_name": _stats_team_name(away_rows),
            "home_score": home_pts,
            "away_score": away_pts,
            "is_played": True,
            "home_win": int(home_pts > away_pts),
            "total_points": home_pts + away_pts,
            "home_fg_pct": _team_share(home_rows, "FG_PCT"),
            "away_fg_pct": _team_share(away_rows, "FG_PCT"),
            "home_3p_pct": _team_share(home_rows, "FG3_PCT"),
            "away_3p_pct": _team_share(away_rows, "FG3_PCT"),
            "home_reb": _team_sum(home_rows, "REB"),
            "away_reb": _team_sum(away_rows, "REB"),
            "home_ast": _team_sum(home_rows, "AST"),
            "away_ast": _team_sum(away_rows, "AST"),
        })
    return records


# --------------------------------------------------------------------------
# Кэш и сборка сезона


def _read_cache(season: str) -> tuple[list[dict], str | None]:
    """Читает кэш сезона. Возвращает (строки, причина негодности).

    Причина нужна, чтобы в лог попадало не «кэш плохой», а что именно
    с ним не так: старый формат, дубли или неполнота.
    """
    cache_file = _cache_path(season)
    if not cache_file.exists():
        return [], "нет файла"
    try:
        df = pl.read_parquet(cache_file)
    except Exception as e:
        return [], f"не читается ({e})"
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return [], f"старый формат (нет колонок: {', '.join(missing)})"
    rows = df.to_dicts()
    ids = {r.get("match_id") for r in rows}
    if len(rows) != len(ids):
        return [], f"дубли ({len(rows)} строк на {len(ids)} матчей)"
    expected = FULL_SEASON_MATCHES.get(season)
    if expected is not None and len(rows) != expected:
        return [], f"неполно ({len(rows)} матчей вместо {expected})"
    return rows, None


def _merge_season(played: list[dict], schedule: list[dict],
                  cached: list[dict]) -> tuple[list[dict], int]:
    """Сводит три источника в строки сезона.

    Приоритеты:
      * кэш — основа (он переживает сбой сети);
      * календарь — кто дома, кто в гостях, время начала, названия;
      * статистика — счёт сыгранных матчей (главнее данных календаря:
        в календаре счёт иногда отстаёт на очко).

    Матч, который в календаре помечен как сыгранный, но в статистике
    ещё не появился, в данные не попадает: счёта у него всё равно нет,
    а ждать его — дело следующего прогона. Такие считаются и печатаются.
    """
    rows: dict[str, dict] = {}
    for row in cached:
        match_id = row.get("match_id")
        if match_id:
            rows[str(match_id)] = dict(row)

    played_ids = {str(g["match_id"]) for g in played}
    missing_stats = 0
    for sched in schedule:
        match_id = str(sched["match_id"])
        old = rows.get(match_id)
        if old is None:
            if sched.get("is_played"):
                # Матч помечен сыгранным в календаре, но в box score его
                # ещё нет: счёта у него всё равно нет, а ждать — дело
                # следующего прогона. Считаем такие, чтобы видеть, что
                # статистика отстаёт.
                if match_id not in played_ids:
                    missing_stats += 1
                continue
            rows[match_id] = dict(sched)
            continue
        merged = dict(old)
        merged["kickoff_utc"] = sched.get("kickoff_utc") or old.get("kickoff_utc")
        merged["match_date"] = sched.get("match_date") or old.get("match_date")
        merged["home_id"] = sched.get("home_id") or old.get("home_id")
        merged["away_id"] = sched.get("away_id") or old.get("away_id")
        merged["home_name"] = sched.get("home_name") or old.get("home_name")
        merged["away_name"] = sched.get("away_name") or old.get("away_name")
        rows[match_id] = merged

    for game in played:
        match_id = str(game["match_id"])
        old = rows.get(match_id)
        if old is None:
            rows[match_id] = dict(game)
            continue
        merged = dict(old)
        for key, value in game.items():
            if value is not None:
                merged[key] = value
        rows[match_id] = merged

    return list(rows.values()), missing_stats


def _normalize(rows: list[dict]) -> pl.DataFrame:
    """Единая схема датасета.

    Собираем колонками, а не списком словарей: polars выводит типы по
    первым ста строкам, и если первыми идут будущие матчи (у них счёта
    нет), колонка счёта получает тип Null, а потом в неё не влезает ни
    одно число.
    """
    if not rows:
        return pl.DataFrame()
    data = {name: [row.get(name) for row in rows] for name in SCHEMA}
    df = pl.DataFrame(data)
    return df.with_columns([
        pl.col(name).cast(dtype, strict=False) for name, dtype in SCHEMA.items()
    ]).select(list(SCHEMA.keys()))


def fetch_season(season: str, refresh: bool = False) -> pl.DataFrame:
    """Собирает сезон NBA: история, расписание и время начала.

    Прошлые сезоны берутся из кэша как есть. Текущий — перезапрашивается
    каждый прогон: без этого список матчей замирал навсегда, сезон
    записывался один раз и больше не менялся (именно так в журнале NBA
    не было матчей за последний день).
    """
    cached_rows, why = _read_cache(season)
    if cached_rows and not refresh:
        print(f"  Сезон {season}: из кэша {_cache_path(season).name} "
              f"({len(cached_rows)} матчей)")
        return pl.DataFrame(cached_rows)
    if why and why != "нет файла":
        print(f"  Сезон {season}: кэш непригоден — {why}, пересобираю")

    schedule = _fetch_schedule(season)
    sides = {
        str(s["match_id"]): (str(s["home_id"]), str(s["away_id"]))
        for s in (schedule or [])
    }
    # Вежливость к API: между двумя запросами по сезону — пауза.
    time.sleep(RATE_LIMIT_DELAY)
    played = _fetch_played(season, sides)

    if played is None and schedule is None:
        if cached_rows:
            print(f"  Сезон {season}: сеть недоступна — отдаю кэш "
                  f"({len(cached_rows)} матчей)")
            return pl.DataFrame(cached_rows)
        print(f"  Сезон {season}: сеть недоступна и кэша нет")
        return pl.DataFrame()

    rows, missing_stats = _merge_season(played or [], schedule or [], cached_rows)
    if missing_stats:
        print(f"  Сезон {season}: {missing_stats} матчей помечены сыгранными "
              "в календаре, но статистики ещё нет — дождусь следующего прогона")

    if not rows:
        return pl.DataFrame()
    df = _normalize(rows)
    _cache_path(season).parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(_cache_path(season))
    print(f"  Сезон {season}: {df.height} матчей "
          f"(сыграно {df.filter(pl.col('is_played')).height})")
    return df


def fetch_all(today: date | None = None) -> pl.DataFrame:
    today = today or date.today()
    refresh = _refresh_seasons(today)
    frames = []
    for season in _seasons(today):
        df = fetch_season(season, refresh=season in refresh)
        if df.height:
            frames.append(df)
    if not frames:
        return pl.DataFrame()
    df = pl.concat(frames, how="diagonal_relaxed")
    df = df.sort(["match_date", "match_id"])

    # Страховка: один матч — одна строка. Дубли появляются, если кэш и
    # сеть отдают разные id для одного и того же матча, а модель тогда
    # обучалась бы на многократно взвешенных играх.
    before = df.height
    df = df.unique(subset=["match_id"], keep="last", maintain_order=True)
    if df.height != before:
        print(f"  убрано дублей: {before} → {df.height} строк")
    return df


def main() -> None:
    print("Загрузка данных NBA...")
    df = fetch_all()
    if df.is_empty():
        print("Нет данных NBA")
        return

    out_path = PROCESSED / "nba_matches.parquet"
    df.write_parquet(out_path)
    print(f"Сохранено: {out_path}")
    print(f"Всего матчей: {len(df)}")
    print(f"Уникальных id: {df['match_id'].n_unique()}")

    played = df.filter(pl.col("is_played"))
    upcoming = df.filter(~pl.col("is_played"))
    print(f"Сыгранных: {played.height}, будущих: {upcoming.height}")
    print(f"Сезоны: {sorted(df['season'].unique().to_list())}")
    print(f"Диапазон: {df['match_date'].min()} … {df['match_date'].max()}")

    # Самопроверка: число матчей в завершённом сезоне известно точно.
    # Несовпадение означает потерянный запрос.
    by_season = df.group_by("season").len().sort("season")
    season_counts = {r["season"]: r["len"] for r in by_season.to_dicts()}
    short = []
    for season, expected in FULL_SEASON_MATCHES.items():
        got = season_counts.get(season)
        if got is None:
            continue
        if got != expected:
            short.append(f"{season}: {got} матчей вместо {expected}")
    if short:
        print("ВНИМАНИЕ: число матчей в сезоне не совпадает с ожидаемым:")
        for line in short:
            print(f"  {line}")
    else:
        print("Число матчей по завершённым сезонам совпадает с ожидаемым.")

    # Самопроверка свежести: последний сыгранный матч должен быть
    # рядом с сегодняшним днём. Именно этот дефект и был виден на
    # сайте: в журнале NBA не было матчей за последний день.
    last_played = played["match_date"].max() if played.height else None
    stale_days = None
    if last_played is not None:
        stale_days = (date.today() - last_played).days
        print(f"Последний сыгранный матч: {last_played} "
              f"({stale_days} дн. назад)")
        if stale_days > STALE_DAYS and upcoming.height:
            print("ВНИМАНИЕ: в календаре есть будущие матчи, но последний "
                  "сыгранный матч давно — данные, похоже, не обновились.")

    # Матчи без времени начала: в журнале они не попадут в раздел
    # «ближайшие два дня», поэтому их число стоит видеть.
    no_kickoff = upcoming.filter(pl.col("kickoff_utc").is_null()).height
    print(f"Будущих матчей без времени начала: {no_kickoff}")

    stats = {
        "total_matches": len(df),
        "played_matches": played.height,
        "upcoming_matches": upcoming.height,
        "upcoming_without_kickoff": no_kickoff,
        "seasons": sorted(df["season"].unique().to_list()),
        "matches_by_season": season_counts,
        "season_counts_ok": not short,
        "date_range": [str(df["match_date"].min()), str(df["match_date"].max())],
        "last_played_date": str(last_played) if last_played else None,
        "days_since_last_played": stale_days,
        "teams": df["home_id"].n_unique(),
        "avg_points_per_game": (round(played["total_points"].mean(), 1)
                                if played.height else None),
        "home_win_share": (round(played["home_win"].mean(), 4)
                           if played.height else None),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    stats_path = REPORTS / "nba_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"Статистика: {stats_path}")


if __name__ == "__main__":
    main()
