# Реєстр модулів і сховище конфігів нод

Завершення перерваної роботи: 2026-11-23. Код підключений до web API; локально імпорти, tests та schema validation не запускалися. Штатний worker runtime і graph UI ще не реалізовані.

## Що реалізовано

`src/converter/config/validation.py`: UTF-8 JSON з лімітом 1 MiB та глибиною 48, відхилення повторних ключів, NaN/Infinity, offline schemas v1, явна UUID/date-time validation. Помилки не містять значень settings. На Ubuntu 24.04 використовується distribution package `python3-jsonschema` (4.10.3); RefResolver забороняє будь-який непідготовлений зовнішній ресурс. Нова pip-залежність чи build на сервері не потрібні.

`modules.py`: startup scan `plugins/`, перевірка manifest/settings schema, SDK/settings versions, потрібних worker/UI/CSS/README файлів, унікальних портів та напрямків. Worker не імпортується, hardware/GStreamer не опитується. Пошкоджений plugin ізолюється у diagnostics; порожні каталоги-позначення ігноруються. Registry snapshot позначає `capabilities_verified=false`: запис конфігу не доводить готовність обладнання або transport.

Plugin schema refs дозволені лише до файлів у тому самому plugin; мережа, вихід із каталогу, власні `$id` і dynamic refs відхиляються. Невалідний fragment також відхиляється під час validation. Schemas та module registry перечитуються при рестарті сервісу, не змінюються довільним HTTP-запитом.

`store.py`: `/etc/ip-media-tool/nodes/<uuid>.json`, thread lock + Linux flock для всіх cooperating writers. POST створює revision 1, update потребує точний expected revision та наступну revision у документі. Тип існуючого UUID змінити не можна. Невідомий module або settings_version не дають записати новий несумісний конфіг, але вже збережений структурно валідний конфіг залишається доступним з діагностикою.

Зв’язки перевіряються за manifest: локальний input, віддалений output, однаковий contract type, відсутність self-link та циклів. Невідоме джерело і незаповнений required input дозволяють зберегти чернетку зі warning; запуск має бути заблокований майбутнім runtime readiness validator. Незалежні consumer-owned посилання не стираються при видаленні джерела.

## Надійність запису

Оновлення: прочитати поточний config під lock → перевірити revision і граф → атомарно записати останню попередню версію в `.backups/<uuid>.json` → записати новий тимчасовий файл у тому самому каталозі → file fsync → os.replace → directory fsync. Temp/backup/config мають приватні permissions; остаточні file symlinks не читаються. Оригінал пошкодженого JSON, у тому числі `null`, не перезаписується автоматично.

Файли `.tmp`, що могли залишитися після примусового завершення процесу/живлення, не вважаються конфігами й не активуються. Їх автоматично не підміняють замість основного документа. Пошкоджені документи ізолюються логічно, не переміщуються й не видаляються при GET. Відновлення з backup — ручне, після аналізу; механізм не зменшує revision автоматично.

Backup містить лише останню попередню версію, не історію. Delete спершу робить backup, потім unlink+directory fsync. Повторне створення видаленого UUID заборонене за наявності backup, щоб застарілий клієнт не воскресив його. Створіть новий UUID. Якщо після replace/unlink fsync не підтверджено, API повертає `storage_uncertain` (503): перечитати inventory, не повторювати запис наосліп.

Атомарність гарантується лише для одного config, не для multi-node batch. Всі записувачі мають використовувати це сховище; ручне редагування файла під час роботи не бере flock і не є підтримуваною конкурентною операцією. Майбутні import/export transactions потребують окремого journal/recovery.

## API

Оновлення lifecycle 2026-11-23: production POST/PUT/DELETE тепер передаються в окремий runtime через owner-only Unix RPC. Для запису потрібно вручну запустити ip-media-tool-runtime; web не робить цього сам. Активний worker/recovery треба зупинити перед будь-яким edit/delete цієї ноди. GET configs працює й без runtime. Докладніше: [WORKER_RUNTIME](WORKER_RUNTIME.uk.md). Файловий атомарний writer і revision CAS не змінені.

| Метод | Адреса | Дія |
| --- | --- | --- |
| GET | `/api/node-modules` | Manifest-и доступних типів та diagnostics |
| GET | `/api/nodes` | Конфіги й проблеми файлів/зв’язків |
| GET | `/api/nodes/{uuid}` | Один структурно валідний config |
| POST | `/api/nodes` | Новий повний config з revision=1 |
| PUT | `/api/nodes/{uuid}?expected_revision=N` | Повний config з revision=N+1 |
| DELETE | `/api/nodes/{uuid}?expected_revision=N` | Видалити саме revision N, з backup |

GET потребує входу; записи дозволені admin/operator зі стандартними Origin/CSRF перевірками. POST/PUT приймають raw JSON і відхиляють duplicate keys; ліміт web request поки 16 KiB, навіть якщо файловий loader допускає 1 MiB. DELETE також підпорядковується поточній middleware: передати Content-Length, наприклад JSON `{}`. Аудит зберігає node ID/revision/action, не settings.

`enabled=true` не запускає процес. `restart_required` у відповіді update вказує, що змінилися settings/connections/secrets/enabled; зміна координат/назви restart не потребує. Сам restart тут не виконується. Після появи runtime видалення/зміна активної ноди мають проходити погоджений lifecycle, а не безпосередній доступ до файла.

Поточні каталоги plugins містять лише порожні позначення, тому порожній registry очікуваний. Приклади з examples не реєструються як робочі ноди. Серверне приймання запису виконується після додавання першого штатного модуля або окремо через контрольовані Linux fixtures поза production configs.

## Межі валідаторів

Status/media/IPC документи мають структурну JSON Schema validation, але transport descriptors, fixed caps, A/V timing, апаратні leases, IPC peer identity/revision ordering та secrets access поки не є реалізованими runtime validators. Збережений config і JSON schema pass не надають дозволу запустити маршрут. Цей етап реалізує config/module/graph validation, не media readiness.

Installer додає `python3-jsonschema`, створює приватний каталог nodes і дозволяє systemd service записувати лише в нього, додатково до попередніх security/hardware paths. Змінна `IP_MEDIA_TOOL_NODE_DIR` може змінити шлях, але systemd ReadWritePaths слід налаштувати відповідно вручну.

Офіційні джерела сумісності: [Ubuntu Noble python3-jsonschema](https://packages.ubuntu.com/noble/python3-jsonschema), [jsonschema 4.10 RefResolver](https://python-jsonschema.readthedocs.io/en/v4.10.3/references/).
