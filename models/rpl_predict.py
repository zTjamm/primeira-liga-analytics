"""Модель для Российской Премьер-Лиги.

Полностью изолирована от португальского футбола и баскетбола:
- своё пространство команд (keys — названия команд РФПЛ)
- свои параметры Dixon-Coles
- свой файл прогнозов и журнала

Использует ту же математику (Dixon-Coles), что и португальский футбол,
но обучается исключительно на матчах РФПЛ. Никакого смешивания лиг.

Запуск:
    python -m models.rpl_predict
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from etl.config import PROCESSED
from .dixon_coles import DixonColesModel
from .elo import EloModel
from .poisson import over_prob

# Окно обучения в годах (только последние N сезонов)
TRAIN_WINDOW_YEARS = 6


def load_matches() -> list[dict]:
    """Загружает матчи РФПЛ из parquet.

    Источник результатов — два, и это принципиально:
    CSV (football-data.co.uk) даёт историю с 2000 года и коэффициенты,
    но обновляется с огромной задержкой — файл сезона 2026/27 обрывался
    на 02.08, пока лига уже доигрывала 11-й тур. Официальный API РФПЛ
    отдаёт счёт сразу, но без истории и без коэффициентов.
    По одному CSV модель не видела шести туров текущего сезона — и
    «не замечала» форму, которую и так видно невооружённым взглядом
    (Спартак дома против Рубина). Поэтому дозагружаем из API всё, чего
    нет в CSV: дедуплицируем по (дата, хозяева, гости), CSV при
    совпадении остаётся приоритетным — у него есть коэффициенты.
    """
    from datetime import date as date_cls

    path = PROCESSED / "rpl_matches.parquet"
    if not path.exists():
        raise SystemExit(f"Нет {path}. Запусти: python -m etl.fetch_rpl")
    df = pl.read_parquet(path)
    # Фильтруем только сыгранные матчи
    df = df.filter(pl.col("fthg").is_not_null() & pl.col("ftag").is_not_null())
    records = df.to_dicts()
    # Приводим к формату, ожидаемому моделями
    out = []
    for r in records:
        if r["ftr"] == "H":
            ftr = "H"
        elif r["ftr"] == "D":
            ftr = "D"
        elif r["ftr"] == "A":
            ftr = "A"
        else:
            continue
        # match_date должен быть объектом date, не строкой
        md = r["match_date"]
        if isinstance(md, str):
            try:
                md = date_cls.fromisoformat(md)
            except ValueError:
                continue
        out.append({
            "season": r["season"],
            "match_date": md,
            "home_id": r["home_name"],
            "away_id": r["away_name"],
            "home_name": r["home_name"],
            "away_name": r["away_name"],
            "fthg": int(r["fthg"]),
            "ftag": int(r["ftag"]),
            "ftr": ftr,
            "odds_h": float(r["odds_h"]) if r.get("odds_h") else None,
            "odds_d": float(r["odds_d"]) if r.get("odds_d") else None,
            "odds_a": float(r["odds_a"]) if r.get("odds_a") else None,
        })

    n_csv = len(out)
    n_api = _merge_api_results(out, date_cls)
    if n_api:
        print(f"  Результаты из API РФПЛ дозагружены: +{n_api} (CSV дал {n_csv})")
    # Elo в fit_models идёт по порядку и следит за сменой сезона, поэтому
    # после слияния двух источников сортировка обязательна.
    out.sort(key=lambda m: m["match_date"])
    return out


def _merge_api_results(
    out: list[dict], date_cls, fx_path: Path | None = None
) -> int:
    """Дополняет список сыгранными матчами из rpl_fixtures.parquet.

    Возвращает число добавленных. CSV сюда уже положен — при совпадении
    ключа (дата, хозяева, гости) строка API не нужна.
    fx_path нужен тестам; по умолчанию — рабочий файл.
    """
    if fx_path is None:
        fx_path = PROCESSED / "rpl_fixtures.parquet"
    if not fx_path.exists():
        return 0
    fx = pl.read_parquet(fx_path).filter(pl.col("fthg").is_not_null())
    if fx.is_empty():
        return 0

    have = {(m["match_date"], m["home_name"], m["away_name"]) for m in out}
    added = 0
    for r in fx.to_dicts():
        if r.get("ftr") not in ("H", "D", "A"):
            continue
        md = r["match_date"]
        if isinstance(md, str):
            try:
                md = date_cls.fromisoformat(md)
            except ValueError:
                continue
        # Сезон считаем той же формулой, что и в ETL (РФПЛ: июль-май)
        if md.month >= 7:
            season = f"{md.year}/{str(md.year + 1)[2:]}"
        else:
            season = f"{md.year - 1}/{str(md.year)[2:]}"
        key = (md, r["home_name"], r["away_name"])
        if key in have:
            continue
        have.add(key)
        out.append({
            "season": season,
            "match_date": md,
            "home_id": r["home_name"],
            "away_id": r["away_name"],
            "home_name": r["home_name"],
            "away_name": r["away_name"],
            "fthg": int(r["fthg"]),
            "ftag": int(r["ftag"]),
            "ftr": r["ftr"],
            "odds_h": None,
            "odds_d": None,
            "odds_a": None,
        })
        added += 1
    return added


def fit_models(matches: list[dict], verbose: bool = True) -> tuple[EloModel, DixonColesModel]:
    """Обучает модели Elo и Dixon-Coles на матчах РФПЛ."""
    elo = EloModel()
    season = None
    for m in matches:
        if m["season"] != season:
            if season is not None:
                elo.start_season(m["season"])
            season = m["season"]
        elo.update(m["home_id"], m["away_id"], m["fthg"], m["ftag"])

    teams = sorted({m["home_id"] for m in matches} | {m["away_id"] for m in matches})
    dc = DixonColesModel().fit(matches, teams)
    if verbose:
        print(f"  РФПЛ: {len(matches)} матчей, {len(teams)} команд")
        print(f"  Elo: обучен")
        print(f"  DC: rho={dc.rho:+.4f}, gamma={dc.home_adv:+.3f}, сошёлся={dc._converged}")
        top = dc.strength()[:5]
        print("  сильнейшие: " + ", ".join(f"{t['team']} {t['overall']:+.2f}" for t in top))
    return elo, dc


def predict_match(dc: DixonColesModel, home: str, away: str) -> dict:
    """Строит прогноз на матч РФПЛ."""
    p_dc = dc.predict_1x2(home, away)
    matrix = dc.score_matrix(home, away)
    over, under = over_prob(matrix, 2.5)
    lam_h, lam_a = dc.lambdas(home, away)
    return {
        "p_home": round(float(p_dc[0]), 4),
        "p_draw": round(float(p_dc[1]), 4),
        "p_away": round(float(p_dc[2]), 4),
        "over25": round(float(over), 4),
        "under25": round(float(under), 4),
        "xg_home": round(float(lam_h), 3),
        "xg_away": round(float(lam_a), 3),
    }


def main() -> int:
    matches = load_matches()
    if not matches:
        print("  Нет сыгранных матчей для обучения")
        return 1

    elo, dc = fit_models(matches)

    # Сохраняем рейтинг команд
    teams = dc.strength()
    rating_path = PROCESSED / "rpl_rating.json"
    rating_path.write_text(
        json.dumps({"teams": teams, "n_matches": len(matches)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  Рейтинг: {rating_path}")

    # Сохраняем параметры модели (для отладки)
    params = {
        "n_matches": len(matches),
        "n_teams": len(dc.teams),
        "rho": dc.rho,
        "gamma": dc.home_adv,
        "converged": dc._converged,
        "mean_home_goals": dc.mean_home_goals,
        "mean_away_goals": dc.mean_away_goals,
    }
    params_path = PROCESSED / "rpl_params.json"
    params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Параметры: {params_path}")

    # Прогнозы на предстоящие матчи — отдельный шаг со своим файлом
    write_forecast(dc, matches)
    return 0


def team_games(matches: list[dict], team: str, cutoff) -> int:
    """Сколько матчей у команды было до cutoff — данные для порога.

    Тот же смысл, что в португальском пайплайне: у новичка мало матчей,
    и прогноз по его параметрам условен.
    """
    n = 0
    for m in matches:
        if m["match_date"] >= cutoff:
            continue
        if m["home_id"] == team or m["away_id"] == team:
            n += 1
    return n


def write_forecast(dc: DixonColesModel, matches: list[dict]) -> None:
    """Прогноз на каждый предстоящий матч РФПЛ -> web/public/data/rpl_predictions.json.

    Вердикт по тому же правилу, что и в португальском футболе: argmax
    П1/Х/П2 показывается только при вероятности >= порога, иначе — отсев
    с объяснением. Порог для РФПЛ теперь СВОЙ, а не перенесённый:
    models/rpl_selective.py подбирает его на валидационном сезоне 2023/24
    и проверяет на тестовых 2024/25 и 2025/26 тем же walk-forward, что и
    в португальском разделе. Значения читаются из отчёта подбора, чтобы
    сайт и модель не разъезжались: в файле лежит тот порог, по которому
    реально принималось решение.

    Если отчёта ещё нет (свежий сервер) — работают константы
    rpl_selective.RPL_*_THRESHOLD, и в шапке файла это подписано как
    запасной путь, а не как результат подбора.

    Помимо файла прогнозов дописывает записи в общий журнал (леджер
    sport="rpl") и пересобирает рпl_journal.json. Леджер обязателен:
    прогноз, который каждый раз перезаписывается, нельзя потом проверить.
    """
    from datetime import datetime, timezone

    from . import forecast
    from .verdict import total_verdict, verdict
    from .rpl_selective import RPL_TOTAL_THRESHOLD, load_thresholds

    out_th, total_th = load_thresholds()

    fixtures_path = PROCESSED / "rpl_fixtures.parquet"
    if not fixtures_path.exists():
        print("  Нет рpl_fixtures.parquet — пропускаю прогнозы. "
              "Запусти: python -m etl.fetch_rpl")
        return
    fx = pl.read_parquet(fixtures_path)
    # В parquet теперь лежат и сыгранные матчи (для резолва журнала),
    # а прогноз строим только на незакрытые.
    upcoming = fx.filter(pl.col("fthg").is_null())

    now = datetime.now(timezone.utc)
    rows = []
    ledger: list[dict] = []
    skipped = 0
    for r in upcoming.to_dicts():
        home, away = r["home_name"], r["away_name"]
        # Команда вне обучающей выборки — прогноза для неё не существует
        if home not in dc.teams or away not in dc.teams:
            skipped += 1
            continue

        p = predict_match(dc, home, away)
        conf = max(p["p_home"], p["p_draw"], p["p_away"])
        probs = {"H": p["p_home"], "D": p["p_draw"], "A": p["p_away"]}
        picked = max(probs, key=probs.get)

        # Сколько матчей у команд было до этой игры
        md = r["match_date"]
        cutoff = md
        games_h = team_games(matches, home, cutoff)
        games_a = team_games(matches, away, cutoff)
        team_games_min = min(games_h, games_a)

        ok, reason = verdict(conf, team_games=team_games_min,
                             threshold=out_th)
        if total_th is None:
            # Подбор порога по тоталу не дал преимущества над игрой без
            # отсева (models/rpl_selective.py, chosen=None). Вердикт по
            # тоталу не выдаём вовсе — порог, который ничего не отсекает,
            # показывал бы «уверенность», которой нет.
            t_ok, t_reason = False, (
                "на РФПЛ ни один порог отсева не поднял точность по тоталу "
                "над игрой без отсева — вердикт не выдаём")
        else:
            t_ok, t_reason = total_verdict(p["over25"], threshold=total_th)

        # Часы до матча и стадия — через общую функцию журнала, чтобы
        # границы этапов не разъезжались с португальским разделом.
        kickoff = datetime.fromisoformat(r["kickoff_utc"].replace("Z", "+00:00"))
        hours_before = (kickoff - now).total_seconds() / 3600.0
        stage = forecast.stage_for(hours_before)

        rows.append({
            "match_id": r["match_id"],
            "date": str(md),
            "kickoff_utc": r["kickoff_utc"],
            "matchday": r["matchday"],
            "home_name": home,
            "away_name": away,
            "stadium": r["stadium"],
            "city": r["city"],
            "hours_before": round(hours_before, 2),
            "stage": stage,
            **p,
            "prediction": picked,
            "confidence": round(conf, 4),
            "verdict_given": ok,
            "verdict_reason": reason,
            "team_games": team_games_min,
            "total_line": 2.5,
            "total_picked": "over" if p["over25"] >= 0.5 else "under",
            "total_given": t_ok,
            "total_reason": t_reason,
            "threshold": out_th,
            "total_threshold": total_th,
        })

        # Запись в леджер — тот же формат, что у остальных видов спорта:
        # один ключ на матч, этапы перезаписываются свежей версией.
        ledger.append({
            "key": r["match_id"],
            "date": str(md),
            "kickoff": r["kickoff_utc"],
            "matchday": r["matchday"],
            "home_id": home,
            "away_id": away,
            "home_name": home,
            "away_name": away,
            "generated_at": now.isoformat(timespec="seconds"),
            "hours_before": round(hours_before, 2),
            "stage": stage,
            "p_home": p["p_home"], "p_draw": p["p_draw"], "p_away": p["p_away"],
            "extra": {
                "over25": p["over25"],
                "xg_home": p["xg_home"], "xg_away": p["xg_away"],
                "team_games": team_games_min,
                # Метки вердиктов обязательны: точность считается только
                # по выданным, иначе отсев был бы незаметен.
                "verdict_given": ok,
                "verdict_reason": reason,
                "total_verdict_given": t_ok,
                "total_verdict_reason": t_reason,
            },
        })

    rows.sort(key=lambda x: x["kickoff_utc"])

    # Откуда взялся порог. Если отчёта подбора ещё нет, load_thresholds()
    # вернул константу по умолчанию — и это должно быть видно в файле,
    # а не спрятано за словом «подобран».
    from etl.config import REPORTS
    report_exists = (REPORTS / "rpl_selective.json").exists()
    threshold_source = (
        "подобран на РФПЛ: walk-forward подбор на валидационном сезоне "
        "2023/24, проверка на тестовых 2024/25 и 2025/26 "
        "(models/rpl_selective.py)"
        if report_exists else
        "запасной путь: отчёт models/rpl_selective.json ещё не сформирован, "
        "значение по умолчанию"
    )
    total_source = (
        "подобран на РФПЛ отдельно от исхода — тотал у РФПЛ шумливее "
        "(models/rpl_selective.py)"
        if report_exists and total_th is not None else
        ("подбор не дал преимущества над игрой без отсева — вердикт "
         "по тоталу не выдаётся" if report_exists else "значение по умолчанию")
    )

    out = {
        "sport": "rpl",
        "league": "Российская Премьер-Лига",
        "generated_at": now.isoformat(timespec="seconds"),
        "n_matches_history": len(matches),
        "threshold": out_th,
        "threshold_source": threshold_source,
        "total_threshold": total_th,
        "total_threshold_source": total_source,
        "model": "Dixon-Coles, обученный только на матчах РФПЛ (изолирован от других лиг)",
        "upcoming": rows,
    }

    from etl.config import ROOT
    out_path = ROOT / "web" / "public" / "data" / "rpl_predictions.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    given = sum(1 for x in rows if x["verdict_given"])
    print(f"  Прогнозов: {len(rows)} (с вердиктом {given}), пропущено неизвестных команд: {skipped}")
    print(f"  Файл: {out_path}")

    # Журнал: леджер + резолв по результатам API. Результаты берутся из
    # того же API, что и расписание, а не из CSV — CSV отстаёт на недели.
    added = forecast.append(ledger, sport="rpl")
    print(f"  В леджер добавлено новых записей: {added} (этапов всего: {len(ledger)})")

    results_df = fx.select([
        pl.col("match_date"),
        pl.col("home_name").alias("home_id"),
        pl.col("away_name").alias("away_id"),
        "ftr", "fthg", "ftag",
    ])
    hist = forecast.build_history(results_df, sport="rpl")
    journal_path = ROOT / "web" / "public" / "data" / "rpl_journal.json"
    journal_path.write_text(
        json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    s = hist["summary"]
    print(f"  Журнал: {journal_path} "
          f"(сыграно прогнозов: {s['total']}, в ожидании: {len(hist['pending'])})")

    # Рейтинг команд тоже кладём в витрину — страница «Команды» читает оттуда
    teams_path = ROOT / "web" / "public" / "data" / "rpl_teams.json"
    teams_path.write_text(
        json.dumps({
            "n_matches": len(matches),
            "teams": dc.strength(),
        }, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"  Рейтинг для сайта: {teams_path}")


if __name__ == "__main__":
    raise SystemExit(main())
