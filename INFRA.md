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
- tunnel — только обратный туннель (ключ ограничен, permitopen=2200, без шелла)

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
- Белого IP нет (CGNAT провайдера)
- WAN IP: 195.191.174.28 (динамический)

### Удалённый доступ
- Обратный SSH-туннель: роутер → сервер, порт 2200 на сервере
- Клиент: dropbear (dbclient)
- Ключ: `/etc/tunnel/key` (формат dropbear, ed25519)
- Автозапуск: `/etc/tunnel/start.sh` + `/etc/rc.local` + `/etc/hotplug.d/iface/99-tunnel`

### Подключение к роутеру
```bash
ssh -J root@89.125.54.41 -p 2200 root@127.0.0.1
```

### Службы на роутере
- podkop-plus — проксирование через sing-box (подписка VLESS)
- Подписка: раздаётся через x-ui на сервере (порт 2096)

### Команды диагностики
```bash
/etc/init.d/podkop-plus status
logread | tail -30
ping -c 3 8.8.8.8
```
