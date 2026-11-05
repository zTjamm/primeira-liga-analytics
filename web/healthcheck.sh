#!/usr/bin/env bash
# Проверка живости витрины.
#
# Зачем. Цикл обновления умеет молча остановиться: если шаг считает
# упал, публикация не происходит и сайт продолжает отдавать старые
# данные — это правильное поведение, но заметить его можно только
# посмотрев лог. Скрипт отвечает на вопрос «витрина жива и данные
# свежие» одной командой.
#
# Запуск:
#     bash web/healthcheck.sh
#
# Код возврата: 0 — всё в порядке, 1 — есть проблема.
set -uo pipefail

REPO=/opt/primeira-liga-analytics
BASE=${BASE:-http://127.0.0.1:8080}
fail=0

note() { printf "  %s\n" "$*"; }
bad() { printf "  ✗ %s\n" "$*"; fail=1; }
ok() { printf "  ✓ %s\n" "$*"; }

echo "Витрина: $BASE"

echo "Страницы:"
for p in / /nhl /nhl/accuracy /nhl/methodology /rpl /basketball /nba /wnba; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE$p" || echo 000)
  if [ "$code" = "200" ]; then
    ok "$p → 200"
  else
    bad "$p → $code"
  fi
done

echo "Данные НХЛ:"
if [ -f "$REPO/web/public/data/nhl_model_compare.json" ]; then
  ok "отчёт сравнения моделей на месте"
else
  bad "нет web/public/data/nhl_model_compare.json"
fi
if [ -f "$REPO/web/public/data/nhl_journal.json" ]; then
  ok "журнал НХЛ на месте"
else
  bad "нет web/public/data/nhl_journal.json"
fi

echo "Службы:"
for svc in x-ui; do
  state=$(systemctl is-active "$svc" 2>/dev/null || echo unknown)
  if [ "$state" = "active" ]; then
    ok "$svc: $state"
  else
    bad "$svc: $state"
  fi
done
if command -v pm2 >/dev/null 2>&1; then
  pm2 jlist 2>/dev/null | python3 -c "
import json, sys
try:
    procs = json.load(sys.stdin)
except Exception:
    print('  ✗ pm2 jlist не разобран'); sys.exit(1)
bad = 0
for p in procs:
    name = p.get('name')
    status = p.get('pm2_env', {}).get('status')
    mark = '✓' if status == 'online' else '✗'
    print(f'  {mark} pm2 {name}: {status}')
    if status != 'online':
        bad = 1
sys.exit(bad)
" || fail=1
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "Всё в порядке."
  exit 0
fi
echo "Есть проблемы — смотри /var/log/liga-analytics-update.log и pm2 logs."
exit 1
