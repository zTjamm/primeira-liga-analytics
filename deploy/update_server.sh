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
LOCAL=(
  data/processed/predictions.json
  data/processed/football_journal.json
  data/processed/vtb_predictions.json
  data/processed/vtb_strength.json
  data/processed/basketball_journal.json
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

  # Локальные данные ниже будут пересчитаны, поэтому перед обновлением
  # кода откатываем их: иначе git откажется тянуть код из-за правок в
  # отслеживаемых файлах web/public/data.
  git checkout -- web/public/data 2>/dev/null

  if git pull --ff-only origin main 2>&1 | tail -1; then
    echo "код обновлён"
  else
    echo "git pull не удался — считаем на текущей версии"
  fi

  echo "-- сбор данных"
  $PY -m etl.run 2>&1 | tail -1
  $PY -m etl.fetch_vtb 2>&1 | tail -1

  echo "-- прогнозы"
  $PY -m models.predict 2>&1 | tail -2
  $PY -m models.bt_predict 2>&1 | tail -1

  echo "-- публикация"
  for f in "${LOCAL[@]}"; do
    if [ -f "$f" ]; then
      cp "$f" web/public/data/
    else
      echo "  НЕТ ФАЙЛА $f"
    fi
  done

  # Сборку Next.js не трогаем: страницы читают JSON при каждом запросе
  # (проверено — правка файла видна сразу). Пересобирать и перезапускать
  # pm2 не нужно, а лишний рестарт дёргал бы процесс на ровном месте.
  if [ ! -f web/.next/BUILD_ID ]; then
    echo "ВНИМАНИЕ: нет web/.next/BUILD_ID — требуется ручная перезаливка сборки"
  fi
} >>"$LOG" 2>&1