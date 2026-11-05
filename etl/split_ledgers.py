"""Разделение журналов на живые и walk-forward.

Зачем. До 5 ноября 2026 бэктест (models/*_backtest.py) писал свои
walk-forward записи в тот же файл, что и живые прогнозы, а записи в
журнале различаются по паре (матч, этап). Walk-forward запись всегда
имеет этап T-24h — тот же, что у живого прогноза за сутки до матча, —
и ключ у неё тот же. В итоге бэктест затирал живой прогноз: у него
нет времени начала матча, а вероятность он подставляет свою.

Замерено на живых данных перед разделением:

  * NBA — 1560 из 2662 записей walk-forward, живых по сыгранным
    матчам 14 (и только этап T-4h);
  * НХЛ — 8752 из 9863 записей walk-forward, живых по сыгранным
    матчам 4 (и только этап T-4h);
  * WNBA — все 700 записей walk-forward.

Живые T-24h по сыгранным матчам утрачены: git-история их не содержит
(первая версия леджера целиком walk-forward), а серверные копии
перезаписывались каждым прогоном. Разделение не воскресит их — оно
остановит дальнейшую потерю и разложит историю по источнику.

Как различать. Сначала по явной метке extra.source ("live" или
"backtest") — её ставят живые прогнозы и бэктест. У старых записей
метки нет, и там работает признак бэктеста: этап T-24h и отсутствие
ключа "kickoff" (живые прогнозы время начала пишут всегда, кроме
старого сборщика NBA — его шестнадцать записей различаются по
"kickoff": null и остаются живыми).

Скрипт идемпотентен: запускать можно сколько угодно раз, записи не
теряются и не дублируются. Оригиналы копируются в data/raw/.

Запуск:
    python -m etl.split_ledgers            # все виды спорта
    python -m etl.split_ledgers nba nhl    # выборочно
    python -m etl.split_ledgers --dry-run  # только показать, что изменится
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl.config import RAW  # noqa: E402
from models import forecast  # noqa: E402

# Виды спорта, у которых есть walk-forward журнал.
SPORTS = ("nba", "nhl", "wnba")


def is_backtest_record(rec: dict) -> bool:
    """Похоже ли это на walk-forward запись бэктеста."""
    source = (rec.get("extra") or {}).get("source")
    if source == forecast.SOURCE_BACKTEST:
        return True
    if source == forecast.SOURCE_LIVE:
        return False
    # Старые записи без метки: у бэктеста нет ключа "kickoff",
    # а живой прогноз время начала пишет (пусть и null).
    return rec.get("stage") == "T-24h" and "kickoff" not in rec


def _mark(rec: dict, source: str) -> dict:
    out = dict(rec)
    extra = dict(out.get("extra") or {})
    extra["source"] = source
    out["extra"] = extra
    return out


def split_sport(sport: str, dry_run: bool = False) -> dict:
    live_path = forecast.ledger_path(sport)
    bt_path = forecast.backtest_ledger_path(sport)

    live_records = forecast.load(sport)
    bt_records = forecast.load_backtest(sport)

    to_bt = [_mark(r, forecast.SOURCE_BACKTEST)
             for r in live_records if is_backtest_record(r)]
    to_live = [_mark(r, forecast.SOURCE_LIVE)
               for r in live_records if not is_backtest_record(r)]

    # Дубли по (матч, этап) — последняя побеждает, как и в forecast.append.
    def dedup(rows: list[dict]) -> list[dict]:
        merged = {(r["key"], r["stage"]): r for r in rows}
        return [merged[k] for k in sorted(merged)]

    to_bt = dedup(to_bt)
    to_live = dedup(to_live)

    report = {
        "sport": sport,
        "live_before": len(live_records),
        "backtest_before": len(bt_records),
        "live_after": len(to_live),
        "backtest_after": len({**{(r["key"], r["stage"]): r
                                  for r in bt_records},
                               **{(r["key"], r["stage"]): r for r in to_bt}}),
        "moved_to_backtest": len(to_bt),
        "kept_live": len(to_live),
    }

    if dry_run:
        return report

    if not live_records and not bt_records:
        report["skipped"] = "леджер не найден"
        return report

    # Оригиналы — в data/raw (в git не попадает).
    RAW.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if live_path.exists():
        shutil.copy2(live_path, RAW / f"forecast_log_{sport}.pre-split.{stamp}.jsonl")
    if bt_path.exists():
        shutil.copy2(bt_path, RAW / f"forecast_log_{sport}_backtest.pre-split.{stamp}.jsonl")

    # Сначала walk-forward в свой файл — и только потом чистим живой,
    # чтобы при падении между шагами данные не потерялись.
    if to_bt:
        forecast.append_backtest(to_bt, sport)
    forecast.replace(to_live, sport)

    return report


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    sports = [a for a in argv if not a.startswith("--")]
    if not sports:
        sports = list(SPORTS)

    unknown = [s for s in sports if s not in SPORTS]
    if unknown:
        print(f"неизвестные виды спорта: {', '.join(unknown)}")
        print(f"доступны: {', '.join(SPORTS)}")
        return 1

    for sport in sports:
        report = split_sport(sport, dry_run=dry_run)
        print(f"=== {sport} ===")
        if report.get("skipped"):
            print(f"  {report['skipped']}")
            continue
        print(f"  записей в живом леджере: было {report['live_before']}, "
              f"стало {report['live_after']}")
        print(f"  в walk-forward ушло: {report['moved_to_backtest']}")
        print(f"  записей в walk-forward журнале: было {report['backtest_before']}, "
              f"стало {report['backtest_after']}")
        if dry_run:
            print("  ( dry-run: файлы не тронуты )")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
