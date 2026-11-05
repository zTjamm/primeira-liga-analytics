"""Журнал прогнозов — общий для обоих видов спорта.

Идея журнала: прогноз, который каждый раз перезаписывается, бесполезен.
Нельзя ни понять, насколько модель ошибается, ни оценить, помогает ли
обновление ближе к матчу. Поэтому ведём append-only журнал: одна запись
на пару (матч, этап), где этап — расстояние до начала игры.

Спорта два, и различия между ними только в источнике результатов:
матчи футбола лежат в matches.parquet, баскетбола — в vtb_matches.parquet.
Всё остальное — этапы, разрешение вердиктов, метрики по этапам — общее,
поэтому один модуль на оба. Дублировать эту логику ради двух строк
разницы было бы хуже: правки в одном месте разъедутся.

Проверено тестами: tests/test_forecast.py покрывает football-путь,
поскольку именно он вызывается по умолчанию.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from etl.config import ROOT

# Границы этапов в часах до начала матча. Повторные прогоны внутри одного
# слота обновляют запись, а не плодят дубликаты.
STAGES: list[tuple[str, float]] = [
    ("T-72h+", 72.0),   # от 72 часов и дальше
    ("T-24h", 12.0),    # 12–72 часа
    ("T-6h", 4.0),      # 4–12 часов
    ("T-4h", 0.0),      # меньше 4 часов — финальный срез
]
STAGE_ORDER = {name: i for i, (name, _) in enumerate(STAGES)}

ACTUAL = {"H": 0, "D": 1, "A": 2}


def ledger_path(sport: str) -> Path:
    return ROOT / "data" / f"forecast_log_{sport}.jsonl"


def stage_for(hours_before: float) -> str:
    for name, upper in STAGES:
        if hours_before >= upper:
            return name
    return STAGES[-1][0]


def load(sport: str) -> list[dict]:
    path = ledger_path(sport)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # Строка может оборваться при обрыве связи во время коммита
            # в CI. Пропускаем её, а не роняем весь разбор журнала.
            continue
    return out


def append(records: list[dict], sport: str = "football") -> int:
    """Дописывает прогнозы в журнал. Запись с тем же (матч, этап)
    заменяется более свежей — иначе десять прогонов подряд дадут
    десять одинаковых строк."""
    path = ledger_path(sport)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {(r["key"], r["stage"]): r for r in load(sport)}
    added = 0
    for r in records:
        k = (r["key"], r["stage"])
        if k not in merged:
            added += 1
        merged[k] = r

    payload = [merged[k] for k in sorted(merged, key=lambda t: (t[0], STAGE_ORDER.get(t[1], 99)))]
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in payload) + "\n",
        encoding="utf-8",
    )
    return added


def _date_key(d) -> str:
    """Дату приводим к ISO-строке с обеих сторон.

    В parquet match_date лежит типом date, а в журнале он сохранён строкой.
    Если не привести к общему виду, ключи не совпадут никогда: страница
    будет годами показывать «не угадано» вместо реального результата.
    """
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _normalize_result(m: dict) -> dict | None:
    """Приводит сыгранный матч к общему виду (ftr/fthg/ftag) или None.

    Футбол и баскетбол отдают результат по-разному: у футбола из
    football-data.co.uk есть готовые колонки ftr/fthg/ftag, а официальный
    API ВТБ отдаёт только is_played и счёт по очкам. Раньше индекс
    результатов требовал именно ftr, из-за чего в баскетбольном журнале
    НИ ОДИН матч не резолвился — страница годами показывала «сыгранных
    прогнозов пока нет», хотя API возвращало результаты штатно.

    Ничья в баскетболе = равный счёт. Модель это учитывает: у неё три
    исхода, ничья около 0.5% (models/basketball.py, predict_1x2).
    """
    if "ftr" in m:
        return m if m["ftr"] is not None else None

    hs, as_ = m.get("home_score"), m.get("away_score")
    if hs is None or as_ is None:
        return None
    # is_played может отсутствовать (WNBA, VTM — там есть только счёт),
    # тогда считаем_match сыгранным по наличию счёта.
    if m.get("is_played", True) is not True:
        return None
    return {
        **m,
        "ftr": "H" if hs > as_ else ("A" if as_ > hs else "D"),
        "fthg": hs,
        "ftag": as_,
    }


def _results_index(matches: pl.DataFrame) -> dict[tuple, dict]:
    """Индекс результатов по (дата, домашний, гостевой).

    Формат результата у видов спорта разный, и принимать надо оба:
    футбол и РФПЛ дают готовые ftr/fthg/ftag, а НХЛ, НБА, ВТБ и WNBA —
    счёт в home_score/away_score. Раньше здесь стояла проверка
    «если нет home_score/away_score —.index пуст», и футбол с РФПЛ
    перестали резолвиться совсем: страницы точности показывали ноль
    сыгранных прогнозов, хотя данные и прогнозы были на месте.
    Проверка ловит только случай, когда нет ни того, ни другого.
    """
    if matches.height == 0:
        return {}
    has_ftr = "ftr" in matches.columns
    has_scores = ("home_score" in matches.columns
                  and "away_score" in matches.columns)
    if not (has_ftr or has_scores):
        return {}
    idx = {}
    for m in matches.to_dicts():
        r = _normalize_result(m)
        if r is None:
            continue
        idx[(_date_key(m["match_date"]), m["home_id"], m["away_id"])] = r
    return idx


def _find_result(results: dict[tuple, dict], version: dict) -> dict | None:
    """Ищет результат матча для среза журнала.

    Сначала — точное совпадение (дата, домашний, гостевой). Если его
    нет, ищем с обратным порядком команд.

    Обратный порядок нужен из-за старого сборщика фикстур NBA
    (etl/fetch_nba_fixtures_v3*.py): он путал дома и гостей, из-за чего
    записи журнала сохраняли пары команд наоборот. Такие матчи иначе
    навсегда оставались бы «незавершёнными»: сыграны, а в журнале — в
    ожидании, и точность на странице считалась по меньшему числу
    матчей, чем на самом деле.
    """
    day = _date_key(version["date"])
    home = version.get("home_id")
    away = version.get("away_id")
    found = results.get((day, home, away))
    if found is not None:
        return found
    if home is None or away is None or home == away:
        return None
    return results.get((day, away, home))


def _oriented(version: dict, result: dict | None) -> dict:
    """Версия среза в той же перспективе, что и результат матча.

    Если в данных команды стоят в обратном порядке, чем в записи
    журнала, меняем местами дома и гостей вместе с вероятностями и
    коэффициентами. Иначе в таблице «П1» означала бы чужую команду, а
    счёт не совпадал бы с названиями: матч Detroit — Philadelphia со
    счётом 106:112 был бы показан как Philadelphia — Detroit 112:106, и
    читатель решил бы, что перепутаны команды.
    """
    if result is None:
        return version
    rh = result.get("home_id")
    ra = result.get("away_id")
    if rh is None or ra is None:
        return version
    if (str(rh) == str(version.get("home_id"))
            and str(ra) == str(version.get("away_id"))):
        return version
    out = dict(version)
    out["home_id"] = version.get("away_id")
    out["away_id"] = version.get("home_id")
    out["home_name"] = version.get("away_name")
    out["away_name"] = version.get("home_name")
    out["p_home"] = version.get("p_away")
    out["p_away"] = version.get("p_home")
    out["odds_home"] = version.get("odds_away")
    out["odds_away"] = version.get("odds_home")
    return out


def _pick_latest(versions: list[dict]) -> dict:
    """Какой срез показывать для незавершённого матча.

    Обычно это самый поздний этап — он ближе всех к началу матча. Но
    срез без времени начала матча не может быть финальным: в нём «часов
    до матча» посчитано от полуночи дня матча, то есть выдумано. И
    из-за этого матч выпадал из раздела «ближайшие двое суток», хотя
    время начала было известно в других срезах той же записи: старый
    сборщик фикстур NBA не знал времени старта для шестнадцати матчей
    и оставлял им «ноль часов до матча».

    Поэтому: если для матча есть срезы с известным временем начала,
    показываем ближайший из них.
    """
    known = [v for v in versions if v.get("kickoff")]
    pool = known if known else versions
    return pool[-1]


def build_history(matches: pl.DataFrame, sport: str = "football") -> dict:
    """Сводит журнал в отчёт: сыгранные прогнозы с вердиктом и незавершённые."""
    log = load(sport)
    results = _results_index(matches)

    grouped: dict[str, list[dict]] = {}
    for r in log:
        grouped.setdefault(r["key"], []).append(r)

    resolved: list[dict] = []
    pending: list[dict] = []

    for key, versions in grouped.items():
        versions.sort(key=lambda r: STAGE_ORDER.get(r["stage"], 99))

        # Результат ищем по всем срезам записи, а не только по последнему.
        #
        # Срез — это запись журнала, и дата в ней может разойтись с датой
        # матча. Так вышло с НХЛ: отдельный сборщик фикстур писал дату в
        # UTC, а календарь — в местном времени, и для вечерних матчей
        # это разные дни. Тогда последний срез не находил свой матч, и
        # прогноз навсегда оставался «незавершённым», хотя матч давно
        # сыгран: разрешение шло только по последнему срезу.
        #
        # Поэтому ищем от свежего среза к старому и берём тот, который
        # сошёлся с результатом. Срез выбирается целиком: его вероятности
        # и его подпись этапа — из одного и того же места.
        latest = versions[-1]
        res = None
        for v in reversed(versions):
            found = _find_result(results, v)
            if found is not None:
                res = found
                latest = v
                break

        if res is None:
            # Матч ещё не сыгран. Показываем ближайший срез с известным
            # временем начала: срез без времени старта не может быть
            # финальным (см. _pick_latest).
            latest = _pick_latest(versions)

        # Показать срез нужно в той же перспективе, что и результат: если
        # команды в данных стоят наоборот, «дома» и «в гостях» меняются
        # местами вместе с вероятностями (см. _oriented).
        shown = _oriented(latest, res)
        shown_versions = [_oriented(v, res) for v in versions]

        base = {
            "key": key,
            "date": shown["date"],
            "kickoff": shown.get("kickoff"),
            "matchday": shown.get("matchday"),
            "home_id": shown["home_id"],
            "away_id": shown["away_id"],
            "home_name": shown.get("home_name"),
            "away_name": shown.get("away_name"),
            "generated_at": shown["generated_at"],
            "hours_before": shown["hours_before"],
            "stage": shown["stage"],
            "p_home": shown["p_home"],
            "p_draw": shown["p_draw"],
            "p_away": shown["p_away"],
            "extra": shown.get("extra"),
            # ROI-tracking: коэффициенты и ставка передаются из скрипта прогноза
            # (models/wnba_predict.py, models/bt_predict.py и т.д.).
            "odds_home": shown.get("odds_home"),
            "odds_draw": shown.get("odds_draw"),
            "odds_away": shown.get("odds_away"),
            "stake": shown.get("stake"),
            "versions": [
                {
                    "stage": v["stage"], "p_home": v["p_home"], "p_draw": v["p_draw"],
                    "p_away": v["p_away"], "hours_before": v["hours_before"],
                    "generated_at": v["generated_at"],
                }
                for v in shown_versions
            ],
        }

        if res is None:
            pending.append(base)
            continue

        probs = [shown["p_home"], shown["p_draw"], shown["p_away"]]
        ai = ACTUAL.get(res["ftr"])
        total = sum(probs) or 1.0
        probs = [p / total for p in probs]

        hit = ai is not None and max(range(3), key=lambda i: probs[i]) == ai
        p_actual = probs[ai] if ai is not None else None

        # Какой исход мы назвали и с какой вероятностью. Раньше в журнал
        # попадали только hit и p_actual, где p_actual — вероятность на
        # ФАКТИЧЕСКИЙ исход. При промахе это вероятность того, что
        # произошло вопреки прогнозу, и рядом со словом «мимо» она
        # читалась как «мы были уверены». Поэтому храним и названный
        # исход, и его вероятность — отдельно от фактического.
        top_i = max(range(3), key=lambda i: probs[i])
        picked = ["H", "D", "A"][top_i]
        picked_p = probs[top_i]

        # Вердикт по тоталу 2.5 — отдельным рынком. Складывать его с
        # исходом через «и» нельзя: ошибиться хотя бы на одном рынке
        # означает потерять всё сразу, и точность падает с 55.4% до 30.9%
        # (замерено на 918 матчах). Поэтому это вторая параллельная
        # строка, а не дополнение к первой.
        # Вердикт по тоталу — отдельным рынком. Складывать его с исходом
        # через «и» нельзя: ошибиться хотя бы на одном рынке означает
        # потерять всё сразу, и точность падает с 55.4% до 30.9%
        # (замерено на 918 матчах). Поэтому это вторая параллельная
        # строка, а не дополнение к первой.
        #
        # Линия и ключ разные: у футбола это 2.5 гола (over25), у
        # баскетбола 155 очков (over_155). Раньше код искал только
        # over25, поэтому в баскетболе вердикт по тоталу был всегда
        # пустым, хотя вероятность в журнале лежала.
        total_hit = total_p = total_label = total_line = None
        total_given = total_reason = None
        extra = shown.get("extra") or {}
        # Ключ и линия зависят от вида спорта. Раньше здесь была двойка
        # «football → over25, иначе over_155», из-за чего у третьего вида
        # (РФПЛ, тотал 2.5) вердикт по тоталу был бы всегда пустым.
        key, line = (("over25", 2.5) if sport in ("football", "rpl")
                     else ("over_155", 155.0))
        o = extra.get(key)
        if o is not None:
            o = float(o)
            pred_over = o >= 0.5
            fmt = "ТБ {:.1f}" if line < 10 else "ТБ {:.0f}"
            total_label = (fmt if pred_over else fmt.replace("ТБ", "ТМ")).format(line)
            total_line = line
            # Порог целый у баскетбола, и модель считает «больше 155»
            # с непрерывной поправкой 155.5 (predict_total_2h), то есть
            # фактически ТБ 155 = тотал от 156. Строгое «>» это и значит.
            total_hit = pred_over == (res["fthg"] + res["ftag"] > line)
            total_p = max(o, 1.0 - o)

            # Учитываем, выдавался ли вердикт по тоталу. Если модель
            # воздержалась (тотал около линии), вердикт в таблице не
            # показывается — и точность должна считаться только по
            # выданным, иначе она завысится так же, как завышала по
            # исходу до введения отсева.
            total_given = extra.get("total_verdict_given")
            total_reason = extra.get("total_verdict_reason")

        first = shown_versions[0]
        shift = round(sum(abs(a - b) for a, b in
                          zip([first["p_home"], first["p_draw"], first["p_away"]], probs)), 4)
        first_top = max(range(3), key=lambda i: [first["p_home"], first["p_draw"], first["p_away"]][i])

        # --- ROI-tracking -------------------------------------------------
        # Если в прогнозе были переданы коэффициенты и ставка,
        # считаем прибыль по фактическому исходу.
        odds_home = shown.get("odds_home")
        odds_draw = shown.get("odds_draw")
        odds_away = shown.get("odds_away")
        stake = shown.get("stake")

        profit = None
        roi = None
        if stake is not None:
            if res["ftr"] == "H" and odds_home:
                profit = round(stake * odds_home - stake, 2)
            elif res["ftr"] == "D" and odds_draw:
                profit = round(stake * odds_draw - stake, 2)
            elif res["ftr"] == "A" and odds_away:
                profit = round(stake * odds_away - stake, 2)
            else:
                # Исход не совпал ни с одним коэффициентом — проигрыш ставки.
                profit = round(-stake, 2)
            if profit is not None and stake:
                roi = round(profit / stake, 4)

        base.update({
            "actual": res["ftr"],
            "score": f"{res['fthg']}-{res['ftag']}",
            "hit": hit,
            "picked": picked,
            "picked_p": round(picked_p, 4),
            "verdict_given": extra.get("verdict_given"),
            "verdict_reason": extra.get("verdict_reason"),
            "total_hit": total_hit,
            "total_label": total_label,
            "total_line": total_line,
            "total_given": total_given,
            "total_no_reason": total_reason,
            "total_p": round(total_p, 4) if total_p is not None else None,
            "p_actual": round(p_actual, 4) if p_actual is not None else None,
            "logloss": round(-math.log(max(p_actual, 1e-12)), 4) if p_actual is not None else None,
            "n_versions": len(versions),
            "shift": shift,
            "flipped": shift > 0.01 and first_top != max(range(3), key=lambda i: probs[i]),
            # ROI-поля (None, если коэффициенты/ставка не передавались).
            "profit": profit,
            "roi": roi,
        })
        resolved.append(base)

    resolved.sort(key=lambda r: (r["date"], r["kickoff"] or ""), reverse=True)
    pending.sort(key=lambda r: (r["kickoff"] or "", r["date"]))

    return {
        "sport": sport,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resolved": resolved,
        "pending": pending,
        "summary": summary(resolved),
    }


def summary(resolved: list[dict]) -> dict:
    """Итоги и разбивка по этапам: помогает ли обновление ближе к матчу."""
    if not resolved:
        return {"total": 0}

    def agg(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0}
        losses = [r["logloss"] for r in rows if r["logloss"] is not None]
        # ROI-агрегат: сумма прибыли / сумма ставок.
        stakes = [r["stake"] for r in rows if r.get("stake") is not None]
        profits = [r["profit"] for r in rows if r.get("profit") is not None]
        roi = (
            round(sum(profits) / sum(stakes), 4)
            if profits and stakes
            else None
        )
        return {
            "n": len(rows),
            "accuracy": round(sum(1 for r in rows if r["hit"]) / len(rows), 4),
            "logloss": round(sum(losses) / len(losses), 5) if losses else None,
            "avg_p_actual": round(
                sum(r["p_actual"] for r in rows if r["p_actual"] is not None) / len(rows), 4
            ),
            "roi": roi,
            "total_staked": round(sum(stakes), 2) if stakes else None,
            "total_profit": round(sum(profits), 2) if profits else None,
        }

    by_stage: dict[str, dict] = {}
    for stage, _ in STAGES:
        rows = [r for r in resolved if r["stage"] == stage]
        if rows:
            by_stage[stage] = agg(rows)

    multi = [r for r in resolved if r["n_versions"] > 1]
    # Точность по тоталу считаем отдельно и по тем же матчам, что и по
    # исходу. Складывать их через «и» нельзя — это пересечение, а не
    # сумма: ошибиться хотя бы на одном рынке означает потерять всё сразу,
    # и точность падает с 55.4% до 30.9%. Два независимых числа полезнее
    # одного составного.
    # Точность по тоталу — только по матчам, где вердикт ВЫДАВАЛСЯ.
    #
    # Отсев по тоталу появился тем же порогом 0.55, что и по исходу
    # (models/verdict.py, обоснование в models/total_gate.py). Если бы мы
    # считали точность по всем матчам, отсев был бы незаметен: невыданные
    # вердикты попали бы в знаменатель и занизили бы результат, а вердикт
    # на экране всё равно не показывается. Считаем по тем, что показаны, и
    # отдельно отдаём число отсева, чтобы его нельзя было спрятать.
    # По исходу — та же логика «только по выданным», что и по тоталу.
    # Раньше сводка делила hit'ы на все матчи, включая те, где модель
    # отказалась выдавать вердикт: в таблице такая строка показана как
    # «без вердикта», а в проценте она участвовала — цифра выходила
    # непонятной смесью отсева и попаданий.
    out_given = [r for r in resolved if r.get("verdict_given") is not False]
    out_hits = sum(1 for r in out_given if r["hit"])
    outcome_agg = {
        "n": len(out_given),
        "hits": out_hits,
        "withheld": len(resolved) - len(out_given),
        "accuracy": round(out_hits / len(out_given), 4) if out_given else None,
        # Сколько записей сделано до появления отсева (флаг вердикта
        # в extra отсутствует). Не прятать: без этого числа «0 отсеяных»
        # читается как «порог сработал везде», хотя флага тогда ещё не было.
        "no_flag": sum(1 for r in resolved if r.get("verdict_given") is None),
    }

    with_total = [r for r in resolved if r.get("total_hit") is not None]
    given = [r for r in with_total if r.get("total_given") is not False]
    lines = {r.get("total_line") for r in given if r.get("total_line") is not None}
    total_agg = {
        "n": len(given),
        "hits": sum(1 for r in given if r["total_hit"]),
        # Сколько матчей было отсеяно: показываем рядом с точностью,
        # иначе 100% на двух матчах выглядит как открытие.
        "withheld": len(with_total) - len(given),
        # Записи без флага: сделаны до появления порога по тоталу
        # (977d30a, 11 октября). Они считаются как «вердикт выдан» —
        # тогда его и показывали бы, — но число отдельно выводится,
        # чтобы честно сказать: отсев в этих матчах не применялся.
        "no_flag": sum(1 for r in with_total if r.get("total_given") is None),
        # Линия попадает в отчёт, а не зашивается названием ключа: у
        # футбола это 2.5 гола, у баскетбола 155 очков, и сводка не
        # должна врать про баскетбол, называя его «тотал 2.5».
        "line": sorted(lines)[0] if len(lines) == 1 else None,
    }
    total_agg["accuracy"] = (
        round(total_agg["hits"] / total_agg["n"], 4) if total_agg["n"] else None
    )

    return {
        "total": len(resolved),
        "overall": agg(resolved),
        # Точность по исходу — только по выданным вердиктам, как и по
        # тоталу. Старое поле overall оставлено (по всем матчам): на нём
        # построены тесты и лог-loss, и менять смысл старого числа на
        # месте — верный способ заставить всю страницу врать молча.
        "outcome_market": outcome_agg,
        "total_market": total_agg,
        "by_stage": by_stage,
        "multi_stage": len(multi),
        "flipped": sum(1 for r in multi if r.get("flipped")),
        "avg_shift": round(sum(r["shift"] for r in multi) / len(multi), 4) if multi else None,
    }


def save(matches: pl.DataFrame, sport: str = "football", out: Path | None = None) -> dict:
    hist = build_history(matches, sport)
    if out is None:
        from etl.config import PROCESSED
        out = PROCESSED / f"{sport}_journal.json"
    out.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    return hist
