# Інсталяція та оновлення через GitHub

Оновлено: 2026-11-19. Написано installer/updater для web application, auth і user management. Локальні виконання й тести не проводилися.

## Поточний інтерфейс

Основний сценарій — [публічний main installer](PUBLIC_INSTALL.uk.md):

```bash
curl -fL https://raw.githubusercontent.com/alexbootch/IP_Media_tool/main/install.sh -o /tmp/ip-media-tool-install.sh && sudo bash /tmp/ip-media-tool-install.sh
```

Подальші оновлення:

```bash
ip-media-tool-update
```

Windows/VS Code -> ручний commit/push -> public main -> ручний updater на Ubuntu. На сервері немає Git login, PAT чи deploy keys. Новий release після кожної зміни не потрібний. Updater фіксує SHA, завантажує installer і archive саме цього commit. Stable/version — опціональні режими.

## Що встановлюється

- Python, GStreamer/GI та OS packages з package list.
- FastAPI/Uvicorn/Argon2 з Ubuntu packages, без frontend build і server compiler toolchain.
- Root-owned code revision, systemd unit, окремий user ip-media-tool, admin CLI та updater.
- Захищений каталог accounts/sessions/audit; існуючі environment і акаунти зберігаються.

Драйвери DeckLink/NVIDIA не встановлюються й не замінюються. Невирішені NDI/media checks не блокують web control plane. Перший startup і admin bootstrap — явні дії користувача за [інструкцією](WEB_AUTH.uk.md). Наступний ручний update перезапускає лише вже активний web service. Фонові updates/media startup відсутні.

## Журнали

Спільна тека `/opt/ip-media-tool/logs/` лежить поруч із `current` і зберігається під час оновлень.

| Файл | Вміст |
| --- | --- |
| `runtime.log` | Runtime, запуск воркерів і системні повідомлення GStreamer/DeckLink |
| `web.log` | Вебсервіс, API і перевірки обладнання |
| `nodes/<назва>--<UUID>.log` | Події конкретної ноди у форматі JSON Lines. Перейменування ноди переносить її журнал і резервні файли |
| `audit/web.jsonl`, `audit/admin.jsonl` | Події авторизації, адміністрування та керування з браузера чи консолі |
| `install.log`, `update.log` | Вивід повної інсталяції та ручного оновлення |

```bash
sudo ls -l /opt/ip-media-tool/logs/nodes/
sudo tail -F /opt/ip-media-tool/logs/runtime.log
```

У браузері кнопка **Журнал** показує останні події ноди з її файла. Видалення ноди залишає її історію на диску. Файли нод і аудиту ротуються застосунком. Для файлів сервісів, інсталяції та оновлень застосовується системний logrotate. Попередні записи залишаються в системному журналі та у старому файлі аудиту `/var/lib/ip-media-tool/security/audit.jsonl`.

## Подальші вимоги до stable delivery

Це майбутні задачі, а не готові можливості поточного installer:

- Signed bundles, незалежна початкова довіра, dependency BOM та compatibility manifest.
- Перевірені release pins Python/GStreamer/plugins, відтворюваний Linux build.
- Backup accounts/node configs, versioned migrations і power-loss recovery journal.
- Bounded application health checks, узгоджений code/config rollback та попередній сумісний runtime.
- Окремий maintenance path для driver/kernel/несумісних shared-library змін.
- Install/update/rollback приймання на Linux користувачем; локальні tests/builds заборонені.

Зараз є лише спроба code-pointer rollback при помилці systemctl restart. OS packages і accounts не відкочуються, міграцій accounts ще немає. Успішне завантаження файлів не є підтвердженням працездатності програми; її перевіряє користувач на сервері.
