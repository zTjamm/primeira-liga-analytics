#!/usr/bin/env bash
# Запуск витрины. Отдельный скрипт, а не набор флагов pm2: так аргументы
# не путаются с путём к скрипту, а лимит памяти виден в одном месте.
#
# Файл лежит в репозитории намеренно. Раньше он существовал только на
# сервере, и любое выравнивание сервера по GitHub (git reset --hard)
# удаляло его — а pm2 запускает витрину именно через этот файл. Сайт
# при этом падал не сразу: процесс уходил в статус «errored», и причина
# была видна только в /root/.pm2/logs/liga-analytics-error.log.
cd /opt/primeira-liga-analytics/web || exit 1
export NODE_ENV=production
# На сервере 1.9 Ги RAM, и рядом живут xray с двумя играми.
# Ограничиваем кучу, чтобы Next.js не утащил систему в OOM.
export NODE_OPTIONS="--max-old-space-size=320"
exec node_modules/next/dist/bin/next start -p 8080
