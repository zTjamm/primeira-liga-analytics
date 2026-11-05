"""Общие части прогнозов для баскетбольных и хоккейных лиг.

НХЛ, NBA и WNBA отличаются источником данных и тем, возможна ли ничья.
Всё остальное — обучение модели, отсев слабых прогнозов, экспорт
рейтингов команд, фильтрация «будущих» матчей — у них одинаково.
Дублировать это в трёх файлах означает со временем получить три разные
версии правил (и уже получалось: в nhl_predict.py появились дубли
ключей, в wnba_predict.py — повтор строки). Поэтому общие куски
вынесены сюда, а различающееся осталось в скриптах видов спорта.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

# Сколько последних отложенных матчей показывать в веб-журнале.
#
# Полная история журнала — это тысячи матчей, и выгружать их все в JSON
# для страницы бессмысленно: таблица не читается, а файл весит
# мегабайты. Итоги (точность, log-loss) считаются по всей истории, а
# таблица показывает свежие матчи.
WEB_JOURNAL_ROWS = 600


def load_names(matches: pl.DataFrame) -> dict[str, str]:
    """Названия команд по id: самое частое написание из данных."""
    pairs = pl.concat([
        matches.select(["home_id", "home_name"]).rename({"home_id": "id", "home_name": "name"}),
        matches.select(["away_id", "away_name"]).rename({"away_id": "id", "away_name": "name"}),
    ])
    grouped = pairs.group_by("id").agg(pl.col("name").mode().first().alias("name"))
    return {r["id"]: r["name"] for r in grouped.to_dicts()}


def team_games(matches: pl.DataFrame) -> dict[str, int]:
    """Сколько матчей у каждой команды в обучающей выборке.

    Нужно для правила отсева: у новичка параметры выведены из данных,
    которых у нас нет, поэтому его прогноз формально есть, а по сути
    нет (models/verdict.py).
    """
    counts: dict[str, int] = {}
    for m in matches.to_dicts():
        for side in ("home_id", "away_id"):
            counts[m[side]] = counts.get(m[side], 0) + 1
    return counts


def remove_draw(probs) -> list[float]:
    """В баскетболе ничьей не бывает.

    Модель выдаёт крошечную вероятность ничьей из-за дискретности счёта
    (около 0.5%), и она перераспределяется на П1 и П2 пропорционально.

    Возвращает ровно ДВА значения — [P1, P2]. Раньше здесь возвращалась
    тройка [P1, 0.0, P2], и в коде выбора исхода `max(range(2), ...)`
    второй элемент тройки — это ничья с вероятностью 0.0. Из-за этого
    сильнейшая гостевая команда никогда не выбиралась: победа гостей
    просто не участвовала в сравнении.
    """
    p_home, p_draw, p_away = probs
    decisive = p_home + p_away
    if decisive > 0:
        return [p_home / decisive, p_away / decisive]
    return [0.5, 0.5]


def future_fixtures(fixtures: pl.DataFrame, today: str | None = None) -> pl.DataFrame:
    """Оставляет только матчи, которые ещё не начались.

    Фильтр по дате, а не по колонке is_future: у НХЛ в официальном API
    есть отложенные матчи прошлых сезонов со статусом FUT, и по статусу
    они попадали в «будущие» — на сайте они выглядели как матчи
    будущего сезона, хотя на самом деле это перенесённые игры 2024 года.
    """
    if today is None:
        today = date.today().isoformat()
    if fixtures.height == 0:
        return fixtures
    return fixtures.filter(pl.col("match_date").cast(pl.Utf8) >= today)


def export_strength(model, names: dict[str, str], out: Path) -> None:
    """Рейтинг-листа: атака, оборона и итоговая сила.

    Модель намеренно не типизирована: у баскетбола это BasketballModel,
    у хоккея — HockeyModel (models/hockey.py). Обе отдают одинаковый
    список словарей с полями team/attack/defence/overall, и жёсткая
    аннотация здесь только мешала бы второй модели попасть в общий
    конвейер.
    """
    rows = []
    for r in model.strength():
        rows.append({
            "team": r["team"],
            "name": names.get(r["team"], r["team"]),
            "attack": r["attack"],
            "defence": r["defence"],
            "overall": r["overall"],
        })
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def write_json(out: Path, payload: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def upcoming_matches(matches: pl.DataFrame, now: datetime) -> list[dict]:
    """Матчи, которые ещё не начались.

    Отбор по времени старта, а не по дате матча. Дата у матча местная
    (так её и показывает сайт), а «сейчас» считается в UTC, и вечерний
    матч по местному времени оказывается то «сегодня», то «завтра» по
    UTC. Время старта в UTC — единственная величина, сравнимая с текущим
    моментом, поэтому сравниваем с ним.

    Матч без времени старта (так бывает у архивных сезонов) сравниваем
    по дате: если дата прошла, матч не показываем.

    Отдельно важно не показывать начавшийся матч: у него счёт появится
    через два с половиной часа, а до этого он «несыгранный», и в
    прогнозах он смотрелся бы как матч впереди.
    """
    today = now.date().isoformat()
    out: list[dict] = []
    for m in matches.filter(pl.col("home_score").is_null()).to_dicts():
        kickoff = m.get("kickoff_utc")
        if kickoff:
            try:
                ts = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
            except ValueError:
                ts = None
            if ts is not None:
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts <= now:
                    continue
        elif str(m.get("match_date")) < today:
            continue
        row = dict(m)
        row["match_date"] = str(m["match_date"])
        out.append(row)
    out.sort(key=lambda r: (r["match_date"], r.get("kickoff_utc") or "",
                            r["match_id"]))
    return out


def season_label(matches: pl.DataFrame, fixtures: list[dict]) -> str:
    """Сезон, к которому относятся будущие матчи.

    Не вписываем руками: в апреле следующий сезон уже расписан, и
    вписанная строка врала бы на полгода. Берём сезон будущих матчей,
    а если их нет — последний сезон в данных.
    """
    seasons = [str(f.get("season")) for f in fixtures if f.get("season")]
    if seasons:
        return max(seasons)
    all_seasons = [str(s) for s in matches["season"].unique().to_list() if s]
    return max(all_seasons) if all_seasons else ""


def write_web_journal(hist: dict, path: Path,
                      rows: int = WEB_JOURNAL_ROWS) -> None:
    """Веб-копия журнала: полные итоги, но только свежие строки в таблице.

    Общая функция для backtest'а и прогнозов: иначе прогнозы, копируя
    полный журнал в web/public/data, затирали бы урезанную версию —
    и страница грузила бы мегабайты вместо полутора.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    trimmed = dict(hist)
    trimmed["resolved"] = hist["resolved"][:rows]
    trimmed["resolved_total"] = len(hist["resolved"])
    trimmed["resolved_note"] = (
        f"в таблице показаны последние {rows} матчей из "
        f"{len(hist['resolved'])}; итоги посчитаны по всем"
    )
    path.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2),
                    encoding="utf-8")
