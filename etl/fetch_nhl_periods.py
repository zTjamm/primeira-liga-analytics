"""Сбор разбивки матчей НХЛ по периодам (основное время / овертайм / буллиты).

Зачем это нужно. Итоговый счёт матча НХЛ не позволяет разделить три
разные вещи:

  * игру, решённую в основное время;
  * игру, ушедшую в овертайм и решённую там;
  * игру, дошедшую до буллитной серии.

А модель, которая считает хоккей, обязана это различать: доля ничьих
в хоккее — это не 15% (как выходит из нормального приближения), а
ровно столько, сколько матчей ушло в буллиты. По данным API по
выборке сезона 2025/26: ничья после основного времени 21.7%, буллитная
серия 7.7%.

Поэтому тянем разбивку по периодам из gamecenter/{id}/landing: в ней
есть счёт после каждого периода. Один запрос на матч, ключ не нужен.

Запуск:
    python -m etl.fetch_nhl_periods

Результат: data/processed/nhl_periods.parquet
Кэш:      data/raw/nhl_periods.jsonl (по строке на матч, докачивается)
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import requests

from etl.config import PROCESSED, RAW, REPORTS

API_BASE = "https://api-web.nhle.com/v1"
CACHE = RAW / "nhl_periods.jsonl"

# Сколько запросов делаем одновременно. API — публичный CDN без
# заявленных лимитов, но и забивать его нельзя: четыре потока держат
# уважительную нагрузку.
WORKERS = 4
TIMEOUT = 25


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "primeira-liga-analytics/1.0"})
    return s


def _period_types(payload: dict) -> set[str]:
    """Типы периодов, которые реально были в матче.

    В summary.scoring лежит по блоку на период, и у блока есть
    periodType: REG (основное время), OT (овертайм), SO (буллиты).
    Списка голов при этом может не быть вовсе — у части старых матчей
    API отдаёт только структуру периодов. Поэтому разбор построен на
    типах периодов, а не на голах.
    """
    types: set[str] = set()
    for blk in payload.get("summary", {}).get("scoring", []) or []:
        pd = blk.get("periodDescriptor", {}) or {}
        ptype = str(pd.get("periodType") or "").upper()
        if ptype:
            types.add(ptype)
    return types


def _reg_score_from_goals(payload: dict) -> tuple[int, int] | None:
    """Счёт после основного времени по данным о голах (если они есть)."""
    score = None
    for blk in payload.get("summary", {}).get("scoring", []) or []:
        pd = blk.get("periodDescriptor", {}) or {}
        ptype = str(pd.get("periodType") or "").upper()
        if ptype and ptype != "REG":
            continue
        goals = blk.get("goals") or []
        if not goals:
            continue
        last = goals[-1]
        try:
            score = (int(last.get("homeScore", 0)), int(last.get("awayScore", 0)))
        except (TypeError, ValueError):
            return None
    return score


def _parse(payload: dict) -> dict | None:
    """Достаёт из ответа gamecenter: счёт основного времени и структуру ОТ.

    Правило, на котором держится весь разбор: овертайм заканчивается
    на первой шайбе, поэтому победитель овертайма (или буллитной
    серии) получает ровно ОДНУ дополнительную шайбу. Значит:

      * если блоков OT/SO нет — матч решён в основное время, и счёт
        после основного времени равен итоговому;
      * если блок OT или SO есть — основное время закончилось вничью,
        а счёт после него = итоговый минус одна шайба победителя.

    Это работает даже тогда, когда API не отдаёт список голов.
    """
    home = payload.get("homeTeam", {}) or {}
    away = payload.get("awayTeam", {}) or {}
    hs = home.get("score")
    as_ = away.get("score")
    if hs is None or as_ is None:
        return None
    final = (int(hs), int(as_))

    types = _period_types(payload)
    ot_played = bool(types & {"OT", "SO"})
    shootout = "SO" in types

    if ot_played:
        if final[0] == final[1]:
            return None  # противоречие: победитель должен получить +1
        winner = "H" if final[0] > final[1] else "A"
        reg = (final[0] - 1, final[1]) if winner == "H" else (final[0], final[1] - 1)
    else:
        winner = None
        reg = final

    # Сверка с данными о голах, когда они есть.
    by_goals = _reg_score_from_goals(payload)
    if by_goals is not None and by_goals != reg:
        return None

    return {
        "match_id": str(payload.get("id")),
        "reg_home": reg[0],
        "reg_away": reg[1],
        "ot_played": ot_played,
        "shootout": shootout,
        "ot_winner": winner,
        "final_home": final[0],
        "final_away": final[1],
    }


def _fetch(match_id: str) -> dict | None:
    s = _session()
    try:
        r = s.get(f"{API_BASE}/gamecenter/{match_id}/landing", timeout=TIMEOUT)
        r.raise_for_status()
        return _parse(r.json())
    except Exception:
        return None


def _is_consistent(row: dict) -> bool:
    """Внутренняя непротиворечивость строки разбивки.

    В НХЛ ничья после основного времени ВСЕГДА означает овертайм.
    Поэтому строка «ничья, но овертайма не было» физически невозможна
    и означает, что строка разобрана неправильно (так получилось у
    части матчей пузыря 2019/20, где API отдаёт блоки периодов без
    списка голов). Такие строки выбрасываются из кэша и качаются
    заново.
    """
    if row.get("reg_home") is None or row.get("reg_away") is None:
        return False
    if row["reg_home"] == row["reg_away"] and not row.get("ot_played"):
        return False
    if row.get("ot_played"):
        fh = row.get("final_home")
        fa = row.get("final_away")
        rh = row["reg_home"]
        ra = row["reg_away"]
        if fh is None or fa is None:
            return False
        delta = (fh - rh, fa - ra)
        if delta not in ((1, 0), (0, 1)):
            return False
    return True


def _seed_from_parquet() -> dict[str, dict]:
    """Разбивка из ранее сохранённого parquet — если кэша нет.

    Зачем: полная пересборка разбивки — это десять тысяч запросов к
    gamecenter, по одному на матч. В CI рабочий каталог каждый раз
    чистый, и без посева job скачивал бы всё заново (десятки минут и
    десять тысяч запросов к чужому API).

    parquet лежит в репозитории (data/processed/nhl_periods.parquet,
    35 КБ) — это снимок того же разбора, и он переживает перезапуск.
    Докачиваются только те матчи, которых в снимке нет.

    Раскладка колонок совпадает с кэшем: parquet пишется из кэша, и
    наоборот — из parquet можно восстановить кэш как есть.
    """
    path = PROCESSED / "nhl_periods.parquet"
    if not path.exists():
        return {}
    try:
        df = pl.read_parquet(path)
    except Exception as e:
        print(f"  снимок разбивки не читается ({e}), качаю с нуля")
        return {}
    out: dict[str, dict] = {}
    for row in df.to_dicts():
        mid = row.get("match_id")
        if mid is None:
            continue
        out[str(mid)] = {
            "match_id": str(mid),
            "reg_home": row.get("reg_home"),
            "reg_away": row.get("reg_away"),
            "ot_played": bool(row.get("ot_played")),
            "shootout": bool(row.get("shootout")),
            "ot_winner": row.get("ot_winner"),
            "final_home": row.get("final_home"),
            "final_away": row.get("final_away"),
        }
    return out


def _load_cache() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("match_id"):
                out[row["match_id"]] = row
    # Непротиворечивые строки оставляем, противоречивые — перекачиваем.
    out = {k: v for k, v in out.items() if _is_consistent(v)}
    if not out:
        print("  кэша нет — беру снимок разбивки из репозитория")
        return _seed_from_parquet()
    return out


def main() -> None:
    matches_path = PROCESSED / "nhl_matches.parquet"
    if not matches_path.exists():
        print(f"Нет {matches_path} — запусти: python -m etl.fetch_nhl")
        return

    played = pl.read_parquet(matches_path).filter(pl.col("home_score").is_not_null())
    ids = [str(m) for m in played["match_id"].to_list()]
    cache = _load_cache()
    todo = [i for i in ids if i not in cache]
    print(f"Матчей сыграно: {len(ids)}, в кэше: {len(cache)}, к докачке: {len(todo)}")

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    if todo:
        done = 0
        with open(CACHE, "a", encoding="utf-8") as fh:
            def work(mid: str) -> tuple[str, dict | None]:
                return mid, _fetch(mid)

            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for mid, row in pool.map(work, todo):
                    if row is not None:
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                        cache[mid] = row
                    done += 1
                    if done % 500 == 0:
                        fh.flush()
                        print(f"  {done}/{len(todo)}")

    rows = [cache[i] for i in ids if i in cache]
    if not rows:
        print("Не удалось получить ни одного матча.")
        return

    # Кэш переписывается начисто: одна строка на матч, без дублей и
    # без записей, которые оказались неразборчивыми.
    CACHE.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n"
                for r in (cache[i] for i in ids if i in cache)),
        encoding="utf-8")

    df = pl.DataFrame(rows)
    out_path = PROCESSED / "nhl_periods.parquet"
    df.write_parquet(out_path)
    print(f"Сохранено: {out_path} ({df.height} матчей)")

    reg_tie = int((df["reg_home"] == df["reg_away"]).sum())
    so = int(df["shootout"].sum())
    ot_played = int(df["ot_played"].sum())
    n = df.height
    # Проверка согласованности: овертайм бывает только при ничьей в
    # основное время, и победитель всегда получает ровно +1.
    ot_rows = df.filter(pl.col("ot_played"))
    bad_tie = int((ot_rows["reg_home"] != ot_rows["reg_away"]).sum())
    bad_delta = int(((
        (ot_rows["final_home"] - ot_rows["reg_home"]) * (ot_rows["final_home"] > ot_rows["reg_home"])
        + (ot_rows["final_away"] - ot_rows["reg_away"]) * (ot_rows["final_away"] > ot_rows["reg_away"])
    ) != 1).sum())
    # Кто выигрывает овертайм — отдельный измеренный факт, а не
    # предположение. Модель не вводит преимущества хозяев в овертайме:
    # если бы оно было, его можно было бы оценить из данных.
    ot_home_wins = int((ot_rows["ot_winner"] == "H").sum())
    stats = {
        "matches": n,
        "reg_tie_share": round(reg_tie / n, 4),
        "shootout_share": round(so / n, 4),
        "ot_played_share": round(ot_played / n, 4),
        "ot_decided_share": round((ot_played - so) / n, 4),
        "shootout_of_ties": round(so / max(reg_tie, 1), 4),
        "ot_home_win_share": round(ot_home_wins / max(ot_played, 1), 4),
        "ot_home_wins": ot_home_wins,
        "consistency_ot_without_tie": bad_tie,
        "consistency_winner_delta_not_1": bad_delta,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    stats_path = REPORTS / "nhl_periods_ingest.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Статистика: {stats_path}")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
