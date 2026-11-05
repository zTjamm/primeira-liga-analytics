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
from datetime import date
from pathlib import Path

import polars as pl


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
