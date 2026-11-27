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

Після успішного оновлення в `/opt/ip-media-tool/releases/` залишається каталог активного SHA. Попередній каталог потрібен під час перемикання і перевірки готовності сервісів для відкату в разі збою. Очищення охоплює лише розпізнані каталоги релізів. Якщо раніше встановлена версія знову знадобиться, завантаж її через updater із відповідним тегом.

## Що встановлюється

- Python, GStreamer/GI та OS packages з package list.
- FastAPI/Uvicorn/Argon2 з Ubuntu packages, без frontend build і server compiler toolchain.
- Root-owned code revision, systemd unit, окремий user ip-media-tool, admin CLI та updater.
- Захищений каталог accounts/sessions/audit; існуючі environment і акаунти зберігаються.

Драйвери DeckLink/NVIDIA встановлює користувач. Перший startup і admin bootstrap — явні дії за [інструкцією](WEB_AUTH.uk.md). Ручне оновлення відновлює сервіси, які були активні. Медіамаршрути запускаються окремо з графа.

## Журнали

Спільна тека `/opt/ip-media-tool/logs/` лежить поруч із `current` і зберігається під час оновлень.

Група `ip-media-tool-logs` має доступ до читання журналів. Інсталятор додає до неї користувача, який виконав оновлення через `sudo`. Щоб файловий менеджер або SFTP отримав нову групу, відкрий новий SSH-сеанс. Якщо оновлення запускалося безпосередньо з облікового запису root, групу можна додати командою `sudo usermod -aG ip-media-tool-logs ІМʼЯ_КОРИСТУВАЧА`.

| Файл | Вміст |
| --- | --- |
| `runtime.log` | Runtime, запуск воркерів і системні повідомлення GStreamer/DeckLink |
| `web.log` | Вебсервіс, API і перевірки обладнання |
| `nodes/<назва>--<UUID>.log` | Текстовий журнал ноди. Один рядок на подію з часом UTC, рівнем і повідомленням. UUID є в імені файла |
| `audit/web.jsonl`, `audit/admin.jsonl` | Події авторизації, адміністрування та керування з браузера чи консолі |
| `install.log`, `update.log` | Вивід повної інсталяції та ручного оновлення |

```bash
ls -l /opt/ip-media-tool/logs/nodes/
tail -F /opt/ip-media-tool/logs/runtime.log
```

У браузері кнопка **Журнал** показує ті самі рядки, що й файл. Під час старту runtime переводить JSON-історію наявних нод і її ротації у текстовий формат. Перейменування ноди переносить її журнал і резервні файли. Видалення ноди залишає її історію на диску. Файли нод і аудиту ротуються застосунком. Для файлів сервісів, інсталяції та оновлень застосовується системний logrotate.

## Старі каталоги на сервері

Поточні шляхи застосунку — `/opt/ip-media-tool`, `/etc/ip-media-tool` і `/var/lib/ip-media-tool`. При створенні сервісного користувача інсталятор задає домашній каталог `/var/lib/ip-media-tool`. Якщо на сервері є `/opt/converter` або `/home/ip-media-tool`, перевір їх окремо перед архівуванням чи видаленням:

```bash
getent passwd ip-media-tool
sudo grep -RIlF -- '/opt/converter' /etc/systemd/system /usr/lib/systemd/system
sudo ls -la /opt/converter /home/ip-media-tool
```

## Подальші вимоги до stable delivery

Це майбутні задачі, а не готові можливості поточного installer:

- Signed bundles, незалежна початкова довіра, dependency BOM та compatibility manifest.
- Перевірені release pins Python/GStreamer/plugins, відтворюваний Linux build.
- Backup accounts/node configs, versioned migrations і power-loss recovery journal.
- Bounded application health checks, узгоджений code/config rollback та попередній сумісний runtime.
- Окремий maintenance path для driver/kernel/несумісних shared-library змін.
- Install/update/rollback приймання на Linux користувачем; локальні tests/builds заборонені.

Зараз є лише спроба code-pointer rollback при помилці systemctl restart. OS packages і accounts не відкочуються, міграцій accounts ще немає. Успішне завантаження файлів не є підтвердженням працездатності програми; її перевіряє користувач на сервері.
