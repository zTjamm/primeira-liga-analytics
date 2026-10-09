"""Точка входа пайплайна: скачать → нормализовать → проверить → сохранить.

Запуск:
    python -m etl.run

Артефакты:
    data/processed/matches.parquet     все матчи, единая схема
    data/processed/fixtures.parquet    будущие матчи
    data/processed/standings.parquet   текущая таблица
    data/processed/teams.parquet       справочник клубов
    data/reports/validation.json       отчёт по качеству
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import polars as pl

from . import fetch_api, fetch_csv, normalize, validate
from .config import CURRENT_SEASON, PROCESSED, REPORTS, SEASON_DIRS


def run(skip_api: bool = False) -> None:
    print("→ скачиваю CSV с football-data.co.uk …")
    raw_csv = fetch_csv.fetch_all()
    print(f"  строк в сырье: {raw_csv.height}")

    matches = normalize.normalize_csv(raw_csv)
    print(f"  нормализовано матчей: {matches.height}")

    # Инициализируем всё, что приходит из API, ДО try. Иначе ветка отказа
    # оставила бы переменные неопределёнными и пайплайн падал бы уже после
    # успешно загруженных данных — ровно это случилось на первом запуске в CI.
    fixtures = standings = teams = fixtures_out = pl.DataFrame()
    api_ok = False

    if not skip_api:
        try:
            csv_max = matches["match_date"].max()
            api_hist = fetch_api.fetch_results_since(csv_max - timedelta(days=3))
            fixtures = fetch_api.fetch_fixtures()
            standings = fetch_api.fetch_standings()
            teams = fetch_api.fetch_teams()
            print(f"  API: догружено матчей {api_hist.height}, будущих {fixtures.height}")

            api_matches = normalize.normalize_api(
                pl.concat([api_hist, fixtures.filter(pl.col("ftr").is_null())], how="diagonal_relaxed"),
                CURRENT_SEASON,
            )
            matches = normalize.merge_sources(matches, api_matches)
            print(f"  всего матчей после слияния: {matches.height}")

            # На витрину расписания отдаём нормализованные club id + служебные поля API
            fixtures_out = _build_fixture_view(fixtures, CURRENT_SEASON)
            api_ok = True
        except Exception as exc:  # noqa: BLE001 — не роняем пайплайн из-за API
            print(f"  ! API недоступен ({exc}); продолжаю только на CSV")
    else:
        print("  API пропущен по флагу --skip-api")

    if not api_ok:
        print("  ! без API не будет расписания будущих матчей: "
              "проверь FD_ORG_TOKEN в секретах репозитория")

    print("→ проверяю качество данных …")
    checks = validate.report(matches)
    failed = [c for c in checks if not c["ok"]]
    for c in checks:
        mark = "ok  " if c["ok"] else "ФЕЙЛ"
        print(f"  [{mark}] {c['check']}" + (f" — {c['detail']}" if c["detail"] else ""))
    if failed:
        print(f"\n  Провалено проверок: {len(failed)}. Останавливаюсь.")
        REPORTS.joinpath("validation.json").write_text(
            json.dumps({"checks": checks, "coverage": coverage_dict(matches)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        sys.exit(1)

    print("→ сохраняю …")
    matches.write_parquet(PROCESSED / "matches.parquet")
    if fixtures_out.height:
        fixtures_out.write_parquet(PROCESSED / "fixtures.parquet")
    if standings.height:
        standings.write_parquet(PROCESSED / "standings.parquet")
    if teams.height:
        teams.write_parquet(PROCESSED / "teams.parquet")

    REPORTS.joinpath("validation.json").write_text(
        json.dumps({"checks": checks, "coverage": coverage_dict(matches)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    cov = validate.coverage(matches)
    print("\nПокрытие по сезонам (матчи / xG / удары / закр.коэфф / из API):")
    for r in cov.iter_rows(named=True):
        print(f"  {r['season']}  {r['matches']:>4}  {r['xg']:>4}  {r['shots']:>4}  {r['closing']:>4}  {r['from_api']:>3}")
    print(f"\nГотово. Сезонов: {len(SEASON_DIRS)}, матчей: {matches.height}, "
          f"диапазон: {matches['match_date'].min()} … {matches['match_date'].max()}")


def coverage_dict(matches: pl.DataFrame) -> list[dict]:
    return validate.coverage(matches).to_dicts()


def _build_fixture_view(fixtures: pl.DataFrame, season: str) -> pl.DataFrame:
    """Расписание с нашими club id вместо названий API.

    Склеиваем по дате и названиям: нормализованная часть даёт home_id/away_id,
    исходная — время матча, тур и статус. Дубликатов быть не должно, но
    если появятся, оставляем первый — лишний матч лучше пропустить, чем
    задублировать в витрине.
    """
    if fixtures.height == 0:
        return fixtures
    ids = normalize.resolve_ids(fixtures).select(
        ["match_date", "home_name", "away_name", "home_id", "away_id"]
    )

    return (
        fixtures.join(ids, on=["match_date", "home_name", "away_name"], how="left")
        .unique(subset=["fd_match_id"], keep="first", maintain_order=True)
        .sort("match_date")
    )


if __name__ == "__main__":
    run(skip_api="--skip-api" in sys.argv)
