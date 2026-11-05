"""Сбор данных НХЛ из официального API api-web.nhle.com.
Ключ не нужен, лимитов не заявлено.
Источник: https://api-web.nhle.com/v1

Запуск:
    python -m etl.fetch_nhl
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import requests

from etl.config import PROCESSED, RAW, REPORTS

API_BASE = "https://api-web.nhle.com/v1"

# Формат сезонов NHL: 20242025 = сезон 2024/25.
#
# Сколько сезонов тянуть. Изначально было три (2024/25, 2025/26 и
# текущий 2026/27), но этого мало: на команду выходит около 80 матчей
# за сезон, и сила команды, оценённая по одному сезону, почти целиком
# состоит из шума. Изучение на данных показало: перенос силы команд
# между сезонами держится на уровне r ≈ 0.3-0.5, и на одном сезоне
# подгонки это шум, а не сигнал. Чем больше истории, тем увереннее
# оценка силы — поэтому тянем восемь полных сезонов плюс текущий.
SEASONS = [
    "20182019", "20192020", "20202021", "20212022", "20222023",
    "20232024", "20242025", "20252026", "20262027",
]

# Тип игры, который берём: 2 = регулярный чемпионат.
#
# В API есть и другие типы, и все они попадали в данные:
#   1 — предсезон (17 матчей в 2024/25),
#   3 — плей-офф (16-24 матча за сезон),
#   9 — Олимпиада-2026 (30 матчей со сборными: Франция, Италия, …),
#   19/20 — «4 Nations Face-Off».
#
# Плей-офф — это другой турнир: другая тактика, больше овертаймов,
# другие команды. Сборные — вообще не клубы НХЛ, и в модели они
# выглядят как команды с одной игрой, у которых параметры выводятся
# из шума. Оставим только регулярный сезон.
GAME_TYPE_REGULAR = 2

# Примерные границы регулярных сезонов (месяц-день начала и конца).
# Нужны только для обхода API по датам; season матча определяется по
# префиксу id, а не по дате (см. _season_for_id).
SEASON_DATE_RANGES = {
    "20182019": ("2018-10-03", "2019-04-06"),
    "20192020": ("2019-10-02", "2020-03-11"),
    "20202021": ("2021-01-13", "2021-05-10"),
    "20212022": ("2021-10-12", "2022-04-29"),
    "20222023": ("2022-10-07", "2023-04-14"),
    "20232024": ("2023-10-10", "2024-04-18"),
    "20242025": ("2024-10-04", "2025-04-18"),
    "20252026": ("2025-10-09", "2026-04-17"),
    "20262027": ("2026-10-08", "2027-04-17"),
}

# Окно докачки текущего сезона, в днях назад и вперёд.
#
# Полный обход сезона — это около тридцати запросов (неделя за запрос).
# Цикл обновления на сервере запускается каждые пять минут, и при полном
# обходе это 30 запросов * 288 запусков = около восьми тысяч запросов в
# сутки ради данных, которые за сутки не изменились и десять раз.
#
# Поэтому для текущего сезона обход идёт не по всему сезону, а по
# окну вокруг сегодняшнего дня:
#
#   * назад — чтобы переписать счёт вчерашних и позавчерашних матчей
#     (API обновляет их по мере окончания) и поймать переносы;
#   * вперёд — чтобы подхватить свежие пары в календаре.
#
# Всё, что старше окна, давно сыграно и больше не меняется: счёт
# сыгранного матча не пересматривается, а календарь на весь сезон
# публикуется заранее. Окно намеренно с запасом — перенос матча
# меняет его дату, и такой матч надо переписать.
REFRESH_PAST_DAYS = 21
REFRESH_FUTURE_DAYS = 14

# Сколько дней в начале сезона считаются «началом».
#
# Проверка нужна для полноты кэша: частичный кэш (сезон начался, а
# первые недели в него не попали) точечной докачкой не лечится — эти
# матчи уже никогда не попадут в окно «после последнего сыгранного».
# Если начало сезона в кэше есть, кэш считается полным.
SEASON_OPEN_WINDOW_DAYS = 14

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "primeira-liga-analytics/1.0"})


def _cache_path(season: str) -> Path:
    return RAW / f"nhl_{season}.json"


def _season_for_id(match_id: str) -> str | None:
    """Сезон по id матча.

    id устроен так: YYYY02NNNN, где YYYY — год начала сезона, а 02 —
    тип игры (регулярный чемпионат). То есть сезон определяется
    однозначно и не зависит от даты: матч, «зависший» на границе
    календаря (например, перенесённый на январь), попадёт в свой
    сезон, а не в чужой.

    Раньше сезон определялся по дате, и матчи плей-офф (апрель-май)
    и Олимпиады (февраль) приписывались к регулярному сезону.
    """
    if not match_id or len(match_id) < 6 or not match_id[:4].isdigit():
        return None
    year = int(match_id[:4])
    return f"{year}{year + 1}"


def _is_regular_game(game_id: str) -> bool:
    """Регулярный чемпионат — по id, а не по полю gameType в ответе.

    На границе сезона API отдаёт в недельном ответе первые игры
    плей-офф, помечая их gameType=2. В выборку они попадали как
    регулярные матчи: в сезоне 2024/25 это 40 лишних игр (1 352 вместо
    1 312), причём пары команд в них повторялись — плей-офф играют
    те же команды в разных сериях.

    В id НХЛ тип игры зашит: YYYY + 0 + тип + номер. У регулярных
    игр пятый-шестой разряды — «02» (2024021307), у плей-офф — «03»
    (2024030163). Поэтому сверяемся с id, а не с полем ответа.
    """
    return len(game_id) >= 6 and game_id[4:6] == "02"


def _fetch_date(target_date: date, season: str) -> list[dict]:
    """Загружает матчи недели, которая начинается в target_date.

    ВАЖНО: /v1/schedule/{date} отдаёт НЕ один день, а неделю целиком
    (в payload семь объектов gameWeek — по одному на день). Если брать из
    ответа все матчи, то при итерации по дням каждый матч попадет в
    выборку семь раз: по разу на каждый день своей недели.

    Поэтому дата матча берётся НЕ из даты запроса, а из самого ответа:
    у каждого gameWeek есть поле date — местная дата этого дня. Раньше
    дата бралась из startTimeUTC, а если его не было (архивные сезоны
    отдают часть игр без времени начала) — подставлялась дата запроса.
    Тогда один и тот же матч получал семь разных дат: по одной на
    каждый день недели, в которую он попал. Дата матча не должна
    зависеть от того, каким запросом его увидели.

    Шаг обхода. Неделя перекрывается с соседней на один день (ответ на
    дату D покрывает D…D+6), поэтому обход с шагом в семь дней покрывает
    календарь без пропусков. Проверено на реальных данных: обход сезона
    с шагом 1 и с шагом 7 дают ОДИНАКОВЫЙ набор матчей (1 336 для
    2026/27), при семикратном меньшем числе запросов.
    """
    url = f"{API_BASE}/schedule/{target_date.isoformat()}"
    try:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        print(f"    Ошибка {target_date}: {e}")
        return []

    rows = []
    for week in payload.get("gameWeek", []):
        # Местная дата дня из ответа. Это единственная дата, которая не
        # зависит от того, каким запросом увидели матч.
        local_date = week.get("date")
        for game in week.get("games", []):
            if game.get("gameType") != GAME_TYPE_REGULAR:
                continue
            game_id = str(game.get("id"))
            if not _is_regular_game(game_id):
                continue
            home = game.get("homeTeam", {})
            away = game.get("awayTeam", {})
            start = game.get("startTimeUTC")
            game_date = local_date or (start[:10] if start else target_date.isoformat())
            rows.append({
                "season": _season_for_id(game_id) or season,
                "match_id": game_id,
                "match_date": game_date,
                "kickoff_utc": start,
                "home_id": str(home.get("id")),
                "home_name": home.get("commonName", {}).get("default", ""),
                "away_id": str(away.get("id")),
                "away_name": away.get("commonName", {}).get("default", ""),
                "home_score": home.get("score"),
                "away_score": away.get("score"),
                "status": game.get("gameState", ""),
                "is_played": game.get("gameState", "") in ("OFF", "FINAL"),
            })
    return rows


def _row_is_better(old: dict, new: dict) -> bool:
    """Стоит ли заменить сохранённую строку новой.

    Матч приходит из семи недельных ответов, и в свежих ответах он
    выглядит полнее: у сыгранного есть счёт и статус OFF. Поэтому
    более «сыгранная» версия главнее, а при равенстве — свежая.
    """
    if bool(old.get("is_played")) != bool(new.get("is_played")):
        return bool(new.get("is_played"))
    if old.get("home_score") is None and new.get("home_score") is not None:
        return True
    if not old.get("kickoff_utc") and new.get("kickoff_utc"):
        return True
    return True


def _current_season(today: date | None = None) -> str | None:
    """Сезон, который сейчас идёт.

    Нужен, чтобы понимать, какой сезон надо дообновлять, а какой можно
    взять из кэша как есть. Даты сезонов в SEASON_DATE_RANGES —
    примерные, но для выбора «текущего» этого достаточно.
    """
    today = today or date.today()
    for season, (start_str, end_str) in SEASON_DATE_RANGES.items():
        if date.fromisoformat(start_str) <= today <= date.fromisoformat(end_str):
            return season
    return None


def _covers_season_open(rows: list[dict], season_start: date) -> bool:
    """Есть ли в кэше матчи начала сезона.

    От этого зависит, можно ли доверять кэшу и докачивать его точечно.
    Пустой кэш или кэш без начала сезона означает, что он неполон,
    и такой сезон перебирается целиком.
    """
    limit = season_start + timedelta(days=SEASON_OPEN_WINDOW_DAYS)
    for r in rows:
        raw = r.get("match_date")
        if not raw:
            continue
        try:
            d = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if season_start <= d <= limit:
            return True
    return False


def _refresh_window(rows: list[dict], season_start: date,
                    season_end: date, today: date) -> tuple[date, date]:
    """Какой кусок сезона нужно перебрать при обновлении.

    Полный сезон — если кэш неполон (нет начала сезона). Иначе —
    окно вокруг сегодняшнего дня: счёт недавних матчей в API ещё
    уточняется, календарь на будущее может пополниться, а всё
    остальное давно зафиксировано.
    """
    if not _covers_season_open(rows, season_start):
        return season_start, season_end
    start = today - timedelta(days=REFRESH_PAST_DAYS)
    end = today + timedelta(days=REFRESH_FUTURE_DAYS)
    return max(start, season_start), min(end, season_end)


def fetch_season(season: str, refresh: bool = False) -> pl.DataFrame:
    """Загружает матчи сезона НХЛ, итерируясь по датам.

    Кэш используется для прошлых сезонов как есть. Для текущего сезона
    кэш — только база: он обновляется по окну вокруг сегодняшнего дня
    (см. _refresh_window). Без этого список матчей замирал навсегда:
    сезон начался, кэш записался — и новые матчи в него больше не
    попадали.

    Кэш всегда перезаписывается результатом обхода. Это важно: старые
    версии кода складывали в кэш сырые строки из семи недельных ответов
    без дедупликации, и кэш раздувался в семь раз (9 284 строки на
    1 352 матча). Ранний выход по существующему кэшу сохранял эту
    поломку навсегда, а датаматча в таких строках была датой запроса —
    один матч получал семь разных дат. Теперь грязный кэш
    обнаруживается и пересобирается.
    """
    cache_file = _cache_path(season)
    cached_rows: list[dict] = []
    dirty = False
    if cache_file.exists():
        try:
            cached_rows = pl.read_json(cache_file).to_dicts()
        except Exception as e:
            print(f"  Сезон {season}: кэш не читается ({e}), качаю заново")
            cached_rows = []
        else:
            ids = {r.get("match_id") for r in cached_rows}
            if len(cached_rows) != len(ids):
                dirty = True
                print(f"  Сезон {season}: кэш грязный — {len(cached_rows)} строк "
                      f"на {len(ids)} матчей, пересобираю")
            elif any(mid and not _is_regular_game(mid) for mid in ids):
                # В кэше есть игры плей-офф, помеченные API как
                # регулярные. Такие кэши писались до фильтра по id.
                dirty = True
                print(f"  Сезон {season}: в кэше есть плей-офф, помеченный "
                      f"как регулярный, пересобираю")

    if cached_rows and not refresh and not dirty:
        print(f"  Сезон {season}: из кэша {cache_file.name}")
        return pl.DataFrame(cached_rows)

    start_str, end_str = SEASON_DATE_RANGES[season]
    season_start = date.fromisoformat(start_str) - timedelta(days=7)
    season_end = date.fromisoformat(end_str) + timedelta(days=7)

    if cached_rows and not dirty:
        # Текущий сезон обновляется точечно: перебирается окно вокруг
        # сегодняшнего дня, а не весь сезон. Обход недели отдаёт семь
        # дней за запрос, поэтому весь сезон — это около тридцати
        # запросов, и тянуть их каждые пять минут (цикл обновления на
        # сервере) незачем: всё, что дальше окна, давно сыграно и не
        # меняется.
        #
        # Полнота при этом гарантирована: если кэш не покрывает начало
        # сезона, перебирается сезон целиком. Иначе частичный кэш
        # (скажем, сезон начался, а первые недели в него не попали)
        # остался бы неполным навсегда — сыгранные матчи начала сезона
        # уже никогда не попадут в окно «после последнего сыгранного».
        start, end = _refresh_window(cached_rows, season_start,
                                     season_end, date.today())
        print(f"  Сезон {season}: обновляю окно {start} … {end}")
    else:
        start = season_start
        end = season_end
        print(f"  Сезон {season}: пересобираю {start} … {end}")

    # Кэш — основа, новые данные главнее: перенесённый матч или
    # изменившийся счёт должны обновить старую запись. Игры плей-офф,
    # попавшие в кэш под видом регулярных, отбрасываются: иначе они
    # вернулись бы в датасет из кэша, минуя фильтр обхода.
    seen: dict[str, dict] = {}
    for r in cached_rows:
        mid = r.get("match_id")
        if mid is None or not _is_regular_game(mid):
            continue
        seen[mid] = r

    current = start
    while current <= end:
        for r in _fetch_date(current, season):
            mid = r["match_id"]
            old = seen.get(mid)
            if old is None or _row_is_better(old, r):
                seen[mid] = r
        # Шаг в неделю: ответ покрывает семь дней, шаг 1 дал бы тот же
        # результат в семь раз большим числом запросов.
        current += timedelta(days=7)
        time.sleep(0.12)  # вежливость к API

    all_rows = list(seen.values())

    # Сохраняем кэш — всегда, даже если обновляли: так грязный кэш
    # заменяется чистым, а устаревший не переживает следующий запуск.
    if all_rows:
        df = pl.DataFrame(all_rows)
        df.write_json(cache_file)
    else:
        df = pl.DataFrame()

    print(f"  Сезон {season}: {df.height} матчей")
    return df


def fetch_all() -> pl.DataFrame:
    current = _current_season()
    frames = [fetch_season(s, refresh=(s == current)) for s in SEASONS]
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame()
    df = pl.concat(frames, how="diagonal_relaxed").sort("match_date")
    return df


def main():
    print("Загрузка данных NHL...")
    df = fetch_all()
    if df.is_empty():
        print("Нет данных NHL")
        return

    df = df.with_columns(
        pl.col("match_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
        pl.col("home_score").cast(pl.Int32, strict=False),
        pl.col("away_score").cast(pl.Int32, strict=False),
        pl.col("season").cast(pl.Utf8),
    )

    # Страховка: один матч — одна строка. Обход недели отдаёт каждый
    # матч в семи ответах, и если дедупликация по id где-то не сработает,
    # датасет раздуется. Проверено на старых грязных кэшах: 9 284 строки
    # на 1 352 матча, и модель обучалась бы на многократно
    # перевзвешенных играх. Сортируем так, чтобы «полная» версия
    # (сыгранная, со счётом и временем начала) осталась последней.
    df = df.sort([
        "match_id",
        pl.col("home_score").is_null(),
        pl.col("kickoff_utc").is_null(),
    ])
    before = df.height
    df = df.unique(subset=["match_id"], keep="last", maintain_order=True)
    if df.height != before:
        print(f"  убрано дублей: {before} → {df.height} строк")
    df = df.sort("match_date")

    out_path = PROCESSED / "nhl_matches.parquet"
    df.write_parquet(out_path)
    print(f"Сохранено: {out_path}")
    print(f"Всего матчей: {len(df)}")
    print(f"Уникальных id: {df['match_id'].n_unique()}")
    print(f"Сезоны: {sorted(df['season'].unique().to_list())}")
    print(f"Диапазон: {df['match_date'].min()} … {df['match_date'].max()}")

    played = df.filter(pl.col("is_played"))
    stats = {
        "total_matches": len(df),
        "played_matches": len(played),
        "seasons": sorted(df['season'].unique().to_list()),
        "date_range": [str(df['match_date'].min()), str(df['match_date'].max())],
        "teams": df['home_id'].n_unique(),
        "home_win_share": round(played["home_score"].gt(played["away_score"]).mean(), 4),
        "draws": int(played["home_score"].eq(played["away_score"]).sum()),
    }
    stats_path = REPORTS / "nhl_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats_path}")


if __name__ == "__main__":
    main()
