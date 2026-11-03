# Инфраструктура

Описание сервера и удалённого роутера. Не содержит секретов (ключи, пароли, токены).
При добавлении новых систем обновляйте этот файл.

## Сервер 89.125.54.41

Провайдер: Selectel (de-de2-srv-kvm-336). Белый IP. Ubuntu 24.04, ядро 6.8.

### Доступ
- SSH: root, ключ ed25519 (`jamm85@bk.ru`)
- Вход только по ключу (`PasswordAuthentication no`), пароль отключён
- Файрвол: ufw active, default deny
- fail2ban: активен на sshd

### Службы и порты
| Порт | Служба | Назначение |
|---|---|---|
| 22 | SSH | вход |
| 80/443 | nginx | сайт ( HTTPS ) |
| 8080 | Next.js (pm2 `liga-analytics`) | основной сайт |
| 3000 | Node.js (pm2) | игра tic-tac-toe |
| 8100 | Node.js (pm2) | игра arena |
| 44895/tcp | xray | VPN VLESS |
| 46173/udp | xray | VPN hysteria |
| 2096/tcp | x-ui | панель управления VPN |
| 61548/tcp | x-ui | резервный порт панели |
| 2200/tcp | обратный туннель | доступ к роутеру на даче (только localhost) |

### Пользователи
- root — полный доступ
- tunnel — доступ по ключу, без шелла. Фактически sshd разрешает ему и обратный туннель (-R 2200 к роутеру), и прямые пробросы (-L) — `permitopen any`, `allowtcpforwarding yes`. Проброс -L нужен прокси VLESS (см. «Прокси на роутере»).

### Важные файлы
- Конфиг сайта: `/opt/primeira-liga-analytics/`
- Секреты: `/opt/primeira-liga-analytics/.env` (600, в .gitignore)
- Ключ роутера (серверная сторона): `/root/tunnel_key_router` (600)
- Бэкап sshd: `/etc/ssh/sshd_config.bak.*`

### Обновление сервера
```bash
bash /opt/primeira-liga-analytics/deploy/update_server.sh
```

---

## Роутер на даче (Cudy WR3000S)

OpenWrt 24.10.5, ядро 6.6.119, mediatek/filogic (aarch64_cortex-a53).

### Сеть
- Белого IP нет (CGNAT провайдера, роутер получает внутренний адрес 10.x за NAT)
- WAN: PPPoE, динамический

### Удалённый доступ
- Обратный SSH-туннель: роутер → сервер, порт 2200 на сервере
- Клиент: dropbear (dbclient)
- Ключ: `/etc/tunnel/key` (ssh-rsa, комментарий root@OpenWrt)
- Автозапуск/keep-alive: `/etc/tunnel/start.sh` (цикл dbclient -R 2200) + cron `/etc/tunnel/check.sh` (каждые 5 мин). НЕ трогать — это единственный доступ к роутеру.

### Подключение к роутеру
```bash
ssh -J root@89.125.54.41 -p 2200 root@127.0.0.1
```

### Прокси на роутере (forkop + sing-box)
- forkop 1.0.5 (+ luci-app-forkop, i18n-ru), sing-box 1.12.22 (ставился через `opkg install --force-space` из-за нехватки места в opkg).
- Подписка с сервера (x-ui, порт 2096): два узла — VLESS (Reality, TCP 44895) и hysteria2 (UDP 46173).
- Выбранные сайты (секция `forkop.VLESS` → `community_lists`): youtube, telegram, google_play, cloudflare, tiktok, twitter, discord, meta, roblox, supercell, hdrezka + `domain_suffix` 2ip.ru. Остальное — напрямую (`final: direct-out`), маршрутизация через nft-tproxy (метка 0x04000000) + fakeip.
- `subscription_update` ОТКЛЮЧЁН (узел стабильный, свой сервер) — иначе forkop перезаписывает server обратно на домен.

**Проблема CGNAT → решение через туннель:**
- Провайдер пропускает TCP-handshake к `:44895`, но интермиттентно роняет payload (ESTABLISHED + залипший Send-Q → `context deadline exceeded`). Прямой VLESS работает «окнами» — ненадёжно.
- Решение: трафик идёт через SSH-туннель (порт 22, всегда жив). На роутере поднят проброс `dbclient -L 127.0.0.1:44895:127.0.0.1:44895` (`/etc/tunnel/proxy.sh`), а конфиг sing-box так отредактирован, что VLESS-утbound набирает `127.0.0.1:44895` вместо домена сервера.
- Персистентность: `/etc/tunnel/fix-proxy.sh` (cron `* * * * *`) — держит проброс живым и возвращает `server: 127.0.0.1`, если forkop перегенерировал конфиг (например, при reload/списках).
- DNAT-hairpin (output-chain → 127.0.0.1/192.168.1.1) пробовался и НЕ работает (ядро не доставляет reply) — поэтому прямой набор `127.0.0.1` в конфиге sing-box, а не DNAT.
- hysteria2 (UDP 46173) через SSH-туннель не идёт (TCP-only) — остаётся нерабочей (селектор по умолчанию VLESS).

### Службы на роутере
- forkop (генерит sing-box + nft), sing-box, dnsmasq
- cron: `check.sh` (доступ-туннель, */5), `fix-proxy.sh` (прокси-watchdog, * * * * *), forkop list_update (дневной), subscription/component update (отключён/дневной)

### Команды диагностики
```bash
/etc/init.d/forkop status
/usr/bin/forkop check_proxy       # вывод sing-box конфига (DNS/домены маскируются)
/usr/bin/forkop check_logs
logread | tail -30
# задержка узла через Clash API (sing-box слушает 192.168.1.1:9090):
#   /proxies/VLESS-RouterK2/delay?timeout=10000&url=http://www.gstatic.com/generate_204
ping -c 3 8.8.8.8
```
