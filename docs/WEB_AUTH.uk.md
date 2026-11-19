# Вхід і керування користувачами

Дата: 2026-11-19. Написано першу реалізацію вебзастосунку. Локально на Windows не запускалися код, тести, сервер чи браузерний preview. Серверне та візуальне приймання виконує користувач після commit/push.

## Що доступно

- /login — форма входу з показом/приховуванням пароля, помилками та поясненням, якщо початковий admin ще не створений.
- /app — робоча сторінка, доступна лише після входу. Наразі це оболонка майбутнього графа, без медіапайплайнів.
- Користувачі — екран адміністратора: пошук, створення, зміна імені/ролі/активності, видалення, скидання пароля. Логін після створення незмінний.
- Кожен користувач може змінити власний пароль, вказавши поточний. Після зміни всі його сесії завершуються.
- Ролі admin/operator/viewer. Керування користувачами вже перевіряється backend за роллю admin. Operator/viewer поки відкривають ту саму оболонку; майбутні media APIs отримають окремі перевірки прав.
- Перший admin і відновлення пароля — лише через ручну команду на сервері, без публічного setup endpoint і без default credentials.

## Отримання нової реалізації на Ubuntu

Після ручного commit/push цих файлів у публічну main гілку один раз виконати новий bootstrap, щоб замінити старий updater, який ще працює тільки з releases:

```bash
curl -fL https://raw.githubusercontent.com/alexbootch/IP_Media_tool/main/install.sh -o /tmp/ip-media-tool-install.sh && sudo bash /tmp/ip-media-tool-install.sh
```

Installer отримує конкретний SHA гілки main, завантажує source archive з GitHub без авторизації, встановлює системні залежності й web service files. Версії коду зберігаються в /opt/ip-media-tool/releases/<sha>, активна — /opt/ip-media-tool/current. Пакети Python беруться з Ubuntu: python3-fastapi, python3-uvicorn, python3-argon2. Node/npm, frontend build, компілятори й pip install на сервері не потрібні. Статичний frontend не має зовнішніх CDN-залежностей.

Недоступні media driver/NDI checks зі статусом 2 не блокують окремо встановлений вебінтерфейс. Помилка APT або відсутні FastAPI/Uvicorn/Argon2 блокують встановлення. Медіапайплайни не запускаються.

## Перший запуск

```bash
ip-media-tool-admin bootstrap
sudo systemctl start ip-media-tool
```

CLI запитує логін, ім'я та пароль двічі; пароль не передається як аргумент команди й не друкується. Довжина пароля — 12–128 символів; логін — 3–32 символи a-z, 0-9, крапка, дефіс або підкреслення. Команда використовує sudo локально, потім записує стан від користувача ip-media-tool.

У браузері відкрити http://SERVER-IP:8080. Installer не змінює firewall. Для автозапуску після reboot користувач може окремо виконати sudo systemctl enable ip-media-tool; сам installer enable не виконує.

## Подальші оновлення

```bash
ip-media-tool-update
```

Default — main, без нового release після кожної зміни. Updater завантажує й застосовує конкретний commit, зберігаючи акаунти/сесії. Вже активний web service перезапускається в межах цього ручного виклику; зупинений не запускається. Окремо доступні --check, --channel stable, --version vX.Y.Z та --base-only. Старі stable tags без web application installer не є придатними для цієї реалізації. Stable — опціональний шлях на майбутнє; підписаний production update ще не реалізований.

## Файли та доступ

- /etc/ip-media-tool/environment — bind/port/security directory і cookie settings; існуючий файл installer не переписує.
- /var/lib/ip-media-tool/security/accounts.json — користувачі, Argon2id hashes, server-side sessions і login throttling; без DB. Одна атомарна JSON-транзакція, fsync, process/thread lock.
- accounts.lock — Linux flock; lock не застосовується як постійна ознака зайнятості.
- audit.jsonl — входи/відмови/виходи й адміністративні дії; ротація до п'яти резервних файлів по 2 MB. Паролі, cookie tokens і CSRF values до журналу не записуються.
- Обліковий запис ip-media-tool запускає один Uvicorn worker. Code directories root-owned/read-only для сервісу; security directory доступний лише сервісу/root.

Сесія — випадковий HttpOnly cookie, на сервері зберігається SHA-256 token, lifetime 8 годин; до 5 активних сесій користувача. Права перевіряються в API, а не тільки через приховування кнопок. Зміни ролі/активності, reset password і видалення відкликають сесії цільового користувача. Власний обліковий запис адміністратора не можна видалити/вимкнути/понизити через UI; останній активний admin захищений транзакційною перевіркою. Конкурентне редагування перевіряє revision і повертає 409 замість перезапису.

Login throttling обмежує невдалі спроби за IP і логіном на 15 хвилин. Mutating API requests потребують same-origin та session CSRF token. Cookie не зберігається в localStorage; user content відображається через textContent. Static assets і захищені сторінки мають CSP/no-store headers. CORS не відкритий.

## HTTP і HTTPS

Початковий LAN-профіль слухає HTTP на 0.0.0.0:8080 з IP_MEDIA_TOOL_COOKIE_SECURE=false. HTTP не шифрує логін/пароль у мережі. Для експлуатації з реальними обліковими даними потрібно налаштувати HTTPS reverse proxy й IP_MEDIA_TOOL_COOKIE_SECURE=true; TLS automation у цей етап не входить. Forwarded headers приймаються тільки від proxy на 127.0.0.1. На reverse proxy необхідно зберегти Host та передавати X-Forwarded-Proto. За зміни environment користувач перезапускає сервіс.

## Відновлення та діагностика

```bash
ip-media-tool-admin reset-password --username admin
sudo systemctl status ip-media-tool --no-pager
sudo journalctl -u ip-media-tool -n 80 --no-pager
```

Корумпований/невідомий accounts.json не скидається автоматично. Перед ручним відновленням state потрібно зупинити сервіс і зберегти захищену резервну копію. Відкат current code pointer не відкочує OS-пакети чи акаунти. На цьому етапі схема accounts.json одна, автоматичні міграції ще не додані.

## Ручне серверне приймання

Після встановлення користувач перевіряє login/logout, неправильний пароль, два браузери, user creation/edit/disable/delete/password reset, відмову operator/viewer у /api/users, втрату доступу після відкликання сесій, збереження accounts після restart/update та захист власного/останнього admin. Окремо перевіряються вигляд форм і таблиці на desktop/mobile. Ці перевірки тут не запускалися; працездатність до серверного приймання не підтверджена.
