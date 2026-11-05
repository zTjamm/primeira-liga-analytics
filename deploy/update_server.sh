#!/usr/bin/env bash
# Обновление данных витрины на самом сервере.
#
# Зачем это вообще: расписание GitHub Actions работает по принципу
# «best effort» и может пропускать большую часть слотов — измеренный
# пример: за восемь часов из ~24 запланированных сработал один. Пока
# обновление зависело от cron, сайт показывал устаревшие данные часами.
#
# Теперь код тянется с GitHub, а прогнозы считаются локально. Полный
# цикл занимает около пятнадцати секунд и пик памяти 183 МБ, поэтому
# запускается каждые пять минут и не мешает VPN: xray, x-ui и обе игры
# не трогаются.
#
# Тяжёлые отчёты (бэктесты, диагностики) здесь НЕ считаются — они
# остаются в GitHub Actions и приезжают с git pull раз в шесть часов.
# Разделение намеренное: быстрый путь нужен часто, тяжёлый — редко.
set -uo pipefail

REPO=/opt/primeira-liga-analytics
LOG=/var/log/liga-analytics-update.log
LOCK=/run/liga-analytics-update.lock
PY="$REPO/.venv/bin/python"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

# Файлы, которые считаем локально. Их нельзя брать с сервера GitHub:
# там они старше на то, как давно отработал последний прогон Actions.
#
# rpl_predictions.json и rpl_teams.json здесь НЕТ намеренно: они пишутся
# models.rpl_predict сразу в web/public/data, копировать файл на самого
# себя не на чем.
LOCAL=(
  data/processed/predictions.json
  data/processed/football_journal.json
  data/processed/vtb_predictions.json
  data/processed/vtb_strength.json
  data/processed/basketball_journal.json
  data/processed/wnba_predictions.json
)

mkdir -p "$REPO/web/public/data"

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  # Замок: если цикл не уложился в пять минут, следующий запуск не должен
  # начаться поверх него. Раньше это не было нужно, потому что тянули
  # только git, а теперь считаем.
  exec 9>"$LOCK" || exit 1
  if ! flock -n 9; then
    echo "предыдущий запуск ещё идёт, пропускаем"
    exit 0
  fi

  cd "$REPO" || { echo "нет каталога $REPO"; exit 1; }

  if [ ! -x "$PY" ]; then
    echo "НЕТ ОКРУЖЕНИЯ $PY — считать нечем. См. requirements-server.txt"
    exit 1
  fi

  # Обновление кода. Локальные данные (data/, web/public/data/) этот
  # цикл пишет каждую итерацию, и git не даст ничего тянуть, пока они
  # изменены.
  #
  # Раньше здесь стоял откат `git checkout -- data web/public/data`,
  # который сбрасывал ВСЕ локальные данные, и только потом шёл pull.
  # Он порождал две беды, обе измерены на живом сервере:
  #
  #  * Сайт в окне между откатом и пересчётом отдавал статику от
  #    последнего коммита. Замер опросом журнала баскетбола каждые
  #    0.25 с: 18:28:25 → 17 сыгранных, 18:28:26 → 0, 18:28:35 → 17.
  #    Раздел «Сыгранные матчи» пропадал целиком — выглядело как
  #    потеря всех матчей.
  #  * CI перестал коммитить данные 11 октября, поэтому откат каждый
  #    раз уводил data/forecast_log_*.jsonl на эту дату. Потерянного
  #    было больше, чем кажется: у сыгранных матчей исчезали срезы
  #    T-24h/T-6h/T-4h — пайплайн их не пересоздаёт, ведь матч уже
  #    сыгран. 13 из 17 сыгранных матчей показывали один срез вместо
  #    трёх, счётчик «мнение менялось» врал (0 вместо 4). Ещё опаснее
  #    матч, попавший в расписание позже 11 октября: после отката его
  #    единственная запись стёрлась бы насовсем, а леджер append-only —
  #    позаписанные строки не вернуть ни откуда.
  #
  # Теперь: fetch → смотрим, какие файлы реально меняет upstream →
  # откатываем только их → merge. CI данные не коммитит, значит
  # откатывать обычно нечего, окно закрывается вовсе, а локальные
  # изменения лежат до самого пересчёта. На всякий случай оба набора
  # всё равно забираем в tmp и возвращаем — функция restore_bak ниже.
  #
  # Леджер возвращаем слиянием, а не перезаписью: он append-only, и
  # версия из git (если CI снова начнёт коммитить) не должна выбрасывать
  # наши записи. Для web/public/data достаточно копии — там каждый прогон
  # пишет полный срез заново, а до пересчёта сайт показывает данные
  # прошлого успешного прогона, что и требовалось по замыслу.

  # Шаг цикла: имя + команда. Падение любого шага останавливает
  # публикацию — см. комментарий ниже у блока расчёта.
  run_step() {
    local name="$1"; shift
    if ! "$@" 2>&1 | tail -2; then
      echo "СБОЙ на шаге «$name» — публикацию пропускаем, чтобы старые"
      echo "данные не выдали за свежие"
      return 1
    fi
  }

  BAK=$(mktemp -d)
  cp -a data/forecast_log_*.jsonl "$BAK"/ 2>/dev/null || true
  if [ -d web/public/data ]; then
    cp -a web/public/data "$BAK"/web_public_data 2>/dev/null || true
  fi

  # Возврат откаченного. Нужен и на пути отказа: если уйти из скрипта,
  # не вернув леджер, откат останется насовсем — а это ровно та потеря,
  # ради которой затеяна вся эта схема.
  restore_bak() {
    if [ -d "$BAK" ]; then
      # Не run_step: тот режет вывод до двух строк, а здесь важно видеть
      # каждую из трёх спортивных веток — иначе пропажа леджера одного
      # вида спорта осталась бы незамеченной в логе.
      if ! $PY - "$BAK" 2>&1 <<'RESTORE_LEDGER'
import glob
import json
import os
import sys

sys.path.insert(0, os.getcwd())
from models import forecast  # noqa: E402  — путь подставляем выше

bak = sys.argv[1]
bad = 0
for f in sorted(glob.glob(os.path.join(bak, "forecast_log_*.jsonl"))):
    sport = os.path.basename(f)[len("forecast_log_"):-len(".jsonl")]
    recs = []
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            # Оборванная строка в бэкапе не должна ронять восстановление
            bad += 1
            continue
    if recs:
        added = forecast.append(recs, sport=sport)
        print(f"  леджер {sport}: возвращено {len(recs)} записей, "
              f"новых относительно git {added}")
if bad:
    print(f"  повреждённых строк в бэкапе пропущено: {bad}")
RESTORE_LEDGER
      then
        echo "СБОЙ: леджер вернуть не удалось — дальше идти нельзя,"
        echo "иначе откат перед pull уничтожит записи насовсем"
        rm -rf "$BAK"
        return 1
      fi
      if [ -d "$BAK/web_public_data" ]; then
        mkdir -p web/public/data
        cp -a "$BAK/web_public_data/." web/public/data/ 2>/dev/null || true
        echo "  опубликованные данные возвращены до пересчёта"
      fi
      # Файлы, которых у нас не было (upstream добавляет новые), мы перед
      # merge убирали, чтобы git не спотыкался об untracked. Если merge
      # не состоялся, они просто исчезли бы — возвращаем те, которых
      # сейчас нет. -n (без перезаписи) принципиален: после успешного
      # merge такие файлы уже пришли из git, и наши старые копии поверх
      # них класть нельзя. Леджер сюда же не попадает: после merge он
      # существует и был восстановлен слиянием выше.
      if [ -d "$BAK/upstream_new" ]; then
        cp -an "$BAK/upstream_new/." . 2>/dev/null || true
      fi
      rm -rf "$BAK"
    fi
  }

  # Раньше здесь стояло `git checkout -- data web/public/data`, которое
  # откатывало ВСЕ локальные данные, а потом шёл pull. Сайт в окне между
  # откатом и восстановлением (~9 секунд каждые пять минут) отдавал
  # статику от последнего коммита — замер на живом журнале баскетбола:
  # 18:28:25 → 17 сыгранных, 18:28:26 → 0, 18:28:35 → 17. Раздел
  # «Сыгранные матчи» пропадал целиком и выглядел как потеря данных.
  #
  # Отказываться от отката нельзя вовсе: git merge требует чистых тех
  # файлов, которые меняет upstream. Но чистыми нужны только они —
  # поэтому сначала fetch, затем смотрим разницу и откатываем лишь её.
  # CI перестал коммитить данные 11 октября, так что обычно откатывать
  # нечего и окна не возникает вовсе: merge идёт по не тронутым локально
  # файлам, а изменённые остаются лежать до пересчёта.
  if ! git fetch origin main 2>&1 | tail -1; then
    echo "fetch не удался — код не обновляем, данные не трогаем"
    restore_bak
    exit 1
  fi

  # Что именно upstream меняет — по всему дереву, а не только по данным.
  #
  # Раньше список ограничивался путями data/ и web/public/data/, и из-за
  # этого merge упирался в файлы кода: 5 ноября 2026 сервер вёл
  # update.sh и web/run.sh как неотслеживаемые (они жили только на
  # сервере), а коммит с ними в GitHub давал ровно ту ошибку, что и
  # 31 октября с data/forecast_log_rpl.jsonl:
  #
  #     error: The following untracked working tree files would be
  #     overwritten by merge: update.sh, web/run.sh
  #
  # Цикл уходил в abort и молча переставал обновлять сайт. Поэтому
  # теперь разбираемся со ВСЕМ, что upstream трогает, а не только с
  # данными. Разница в том, что с данными делается, отдельно:
  #
  #   * данные (data/, web/public/data/) сервер считает сам — их версия
  #     важнее версии из git, и restore_bak вернёт её после merge;
  #   * код берётся из GitHub — локальные правки кода откатываются.
  #
  # TOUCHED — всё, что upstream меняет или удаляет.
  # NEWFILES — то, чего в HEAD нет, а upstream добавляет.
  TOUCHED=$(git diff --name-only HEAD FETCH_HEAD)
  NEWFILES=$(git diff --name-only --diff-filter=A HEAD FETCH_HEAD)

  if [ -n "$TOUCHED" ]; then
    echo "upstream меняет файлы — готовим рабочий каталог:"
    echo "$TOUCHED"

    # Шаг 1: файлы, которых в HEAD нет, а upstream их добавляет.
    #
    # Если такой файл уже лежит в рабочем каталоге как неотслеживаемый,
    # merge обязан на нём споткнуться: «untracked working tree files
    # would be overwritten». Копию убираем в бэкап — вернётся в
    # restore_bak, если файла после merge не окажется.
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      [ -e "$f" ] || continue
      if git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
        continue
      fi
      mkdir -p "$BAK/upstream_new/$(dirname "$f")"
      cp -a "$f" "$BAK/upstream_new/$f" 2>/dev/null || true
      rm -f "$f"
      echo "  убран неотслеживаемый $f (вернётся в restore_bak)"
    done <<EOF
$NEWFILES
EOF

    # Шаг 2: откатываем локальные правки по тем файлам, которых касается
    # upstream. Без этого merge не идёт: git требует, чтобы файл, который
    # он собирается записать, совпадал с HEAD.
    #
    # Для данных это откат к версии из git — версия сервера вернётся
    # в restore_bak слиянием (леджер append-only, перезапись недопустима).
    # Для кода это «код берётся из GitHub»: серверные правки отступают.
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      if git cat-file -e "HEAD:$f" 2>/dev/null; then
        git checkout HEAD -- "$f" 2>/dev/null || true
      fi
    done <<EOF
$TOUCHED
EOF
  fi

  if ! git merge --ff-only FETCH_HEAD 2>&1 | tail -1; then
    echo "merge не удался — НЕ считаем на старом коде, данные были бы"
    echo "показаны как свежие, хотя посчитаны прежней версией модели"
    restore_bak
    exit 1
  fi
  echo "код обновлён"

  # Если откат выше срабатывал — вернуть данные на место, чтобы сайт не
  # показывал журнальную статику от последнего коммита до пересчёта.
  restore_bak || exit 1

  # Дальше каждый шаг обязан довезти данные до публикации. Если сбор или
  # расчёт упали, публиковать нечего: на месте лежат файлы прошлого
  # успешного прогона, и сайт показал бы их как свежие. Для проекта, где
  # честность данных — основное требование, это хуже, чем просто не
  # обновиться. Поэтому падение шага останавливает цикл целиком.
  echo "-- сбор данных"
  run_step "etl.run" $PY -m etl.run || exit 1
  run_step "etl.fetch_vtb" $PY -m etl.fetch_vtb || exit 1

  echo "-- РФПЛ (изолированный пайплайн)"
  run_step "etl.fetch_rpl" $PY -m etl.fetch_rpl || exit 1

  echo "-- прогнозы"
  run_step "models.predict" $PY -m models.predict || exit 1
  run_step "models.bt_predict" $PY -m models.bt_predict || exit 1
  run_step "models.rpl_predict" $PY -m models.rpl_predict || exit 1

  echo "-- публикация"
  for f in "${LOCAL[@]}"; do
    if [ -f "$f" ]; then
      cp "$f" web/public/data/
    else
      echo "  НЕТ ФАЙЛА $f — публикую неполный набор, это лучше старого?"
      echo "  нет: старые данные честнее, помеченные как несвежие"
      exit 1
    fi
  done

  # Сборку Next.js не трогаем: страницы читают JSON при каждом запросе
  # (проверено — правка файла видна сразу). Пересобирать и перезапускать
  # pm2 не нужно, а лишний рестарт дёргал бы процесс на ровном месте.
  if [ ! -f web/.next/BUILD_ID ]; then
    echo "ВНИМАНИЕ: нет web/.next/BUILD_ID — требуется ручная перезаливка сборки"
  fi
} >>"$LOG" 2>&1