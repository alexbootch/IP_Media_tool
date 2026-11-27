# Node SDK і керування воркерами

Реалізація від 2026-11-23: Python node-sdk, окремий Linux runtime daemon, захищений web API, systemd unit і ручний install/update workflow. Код, імпорти, тести, subprocesses та pipelines локально не запускалися. Це реалізація lifecycle; працездатність процесів на сервері ще потребує приймання.

## Межі процесів

Web service читає config/module metadata і надсилає явні команди до `/run/ip-media-tool/runtime.sock`. Він не створює supervisor у своєму lifespan і не імпортує worker. Runtime `converter.runtime.service` працює окремим systemd service. Worker host `converter.node_sdk.runner` — окремий процес і process group на екземпляр; тільки він імпортує відповідний `worker.py`.

Runtime закріплює Python imports і working directory за реальною release directory, не за змінним current symlink. Мутуючий запит від іншої версії web відхиляється. Перезапуск web не завершує воркери. Перезапуск/аварія runtime завершує попереднє дерево процесів через systemd і не відновлює ноди автоматично. Runtime також не має boot autostart для enabled-конфігів.

Дозволений лише довірений plugin code під спільним service UID. Process separation тут забезпечує lifecycle/failure isolation, а не security sandbox між зловмисними plugins. Plugin не запускає detached subprocess/new session: його helpers мають залишатися у worker process group і звільнятися в stop(). Непідконтрольні forks не є підтримуваним plugin API. Systemd `KillMode=control-group` завершує залишки всього дерева при зупинці runtime.

## Python SDK

Публічний import: `from converter.node_sdk import NodeWorker, WorkerContext`. `Worker` наслідує NodeWorker і реалізує async start()/stop(); validate_config(), get_status() та producer-only on_contract() мають спільні defaults. Loader перевіряє subclass, відсутність abstract methods і async signatures. Власні helper files можуть імпортуватися відносно пакета своєї ноди.

Context надає detached config/status snapshots, node_id/instance_id, immutable lease descriptors, cancellation Event, logger.event(event_code), publish_status(status), set_state(state, error=None). Status identity/config revision незмінні. SDK сам збільшує status_revision, задає updated_at та перевіряє схему й переходи станів. Heartbeat не повинен бути сигналом апаратної готовності.

Події lifecycle містять node ID, тип, instance ID, revision, код виходу й кількість спроб. SDK logger приймає коди подій. Журнал сервісу — `/opt/ip-media-tool/logs/runtime.log`. Журнал конкретної ноди — `/opt/ip-media-tool/logs/nodes/<назва>--<UUID>.log`. [Перелік журналів](INSTALL_UPDATE.uk.md#журнали)

## IPC

Control RPC — owner-only Unix socket із SO_PEERCRED перевіркою UID. Кожен запит має version, UUID request ID, application revision, operation і arguments; відповідь корелюється з request ID. Root може адмініструвати socket. Мережевого listener runtime немає.

Канал worker — успадкований socketpair FD, не довільний socket path з конфігу. Worker перевіряє PID/UID батька. Bootstrap schema перевіряє config, instance і leases; settings не потрапляють у argv. Далі використовуються command/ack/status envelopes v1. Length prefix — 4 bytes network order, максимальний JSON frame 64 KiB, strict JSON без duplicate keys/NaN. Неправильні identities, status revisions, transitions або frame sizes завершують некоректний worker session.

Конфіг до 1 MiB може зберігатися файловим loader, але worker bootstrap обмежений 64 KiB; завеликий стартовий документ відхиляється. Web request як і раніше обмежений 16 KiB. Worker status обмежений 48 KiB, залишаючи місце для envelope/response metadata. GET workers пагінується по 16 стислих записів через offset/next_offset; повний status із metrics читається окремим GET ноди. Hardware inventory subprocess допускає до 2 MB JSON, як detector. Клієнт не повторює запити автоматично: при timeout треба перечитати status/config, бо виконання команди могло завершитися.

## Запуск і зупинка

Ручний start потребує enabled=true та точної expected_revision. Runtime перевіряє config/settings/manifest, локальний граф ноди й upstream references, доступність GStreamer factories та обов’язкові capability providers. Невалідний файл іншої незалежної ноди не повинен блокувати цей запуск. Start однієї ноди й stop іншої не мають спільної довгої operation lock.

Node-specific validate_config має завершитися за 5 секунд, start — за 20. Після завершення start без явного readiness воркер залишається waiting_signal/waiting_source. Supervisor вважає startup завислим після 30 секунд, навіть якщо надходять starting heartbeats. Перед spawn dependency preflight обмежений 75 секундами; stop скасовує ще не завершений preflight.

Heartbeat кожні 2 секунди публікує перевірений status; відсутність повідомлень або завислий status getter призводять до failure. Supervisor heartbeat deadline — 15 секунд. Процес може бути живим і водночас неготовим: starting/waiting_source/waiting_signal — нормальні окремі стани.

Stop ідемпотентний, скасовує pending start/recovery перед подальшими діями. Worker отримує cancellation та command stop. SDK очікує stop() до 5 секунд; supervisor чекає worker watcher до 8 секунд після відправки команди, потім TERM із 2-секундним grace і KILL за потреби. IPC write має власний максимум 5 секунд. Systemd обмежує загальну зупинку сервісу 20 секундами. Після завершення знищуються залишки worker process group і звільняються тільки його leases; чужі SHM/socket files не видаляються.

## Аварійне відновлення

Неочікуваний exit, обрив IPC, завислий heartbeat або невалідний статус дають error. Дозволено до трьох повторних запусків із backoff 1/2/4 секунди після першої спроби. Кожен має новий instance_id й нульову власну status revision history, новий dependency preflight та повторну перевірку незмінності config. Бюджет не скидається від короткого успішного запуску. Після вичерпання — error і лише ручний start відкриває новий бюджет.

Permanent SDK/config/import/version failures виходять із code 78 або retryable=false і не запускаються по колу. Recovery preflight failure зупиняє подальші спроби. Waiting_signal/waiting_source не перезапускають процес. Ручний stop відключає recovery незалежно від retryable поля старої помилки. Зупинка/рестарт самої runtime-служби ніколи не поновлює процеси нод.

Systemd може відновлювати сам daemon після аварії, з лімітом 3 starts за 120 секунд; daemon завжди починає без запущених нод. Це окрема політика від перезапусків worker.

## Ресурси та ще не реалізовані можливості

Manifest може містити resource_bindings: provider=decklink, setting=<ключ settings зі стабільним persistent ID>, direction=input/output. Обов’язкові DeckLink capability names на цьому етапі — input/output, з matching bindings. Runtime при явному start виконує одноразове свіже DeckLink discovery й перевіряє однозначний ID/напрямок. Це preflight ручного запуску, не постійний hardware monitor. Профіль плати не змінюється.

Один DeckLink persistent endpoint резервується виключно за однією нодою незалежно від напрямку; два незалежні порти різних ID можуть використовуватися різними workers. Запит одного endpoint двічі одним worker відхиляється: full-duplex не припускається. Бронювання контролює лише cooperating workers цього runtime. Вільний lease не доводить відсутність зовнішнього процесу; реальний pipeline має звірити device-number/ID і помилки драйвера перед використанням.

Runtime маршрутизує контракти SHM та надсилає відкликання споживачам. Споживач із валідним з’єднанням може запуститися до джерела й чекати на `on_contract`. [Опис SHM та ручна перевірка](SHM_TRANSPORT.uk.md). Обов’язкові непідтверджені capabilities та secret_refs блокують start. GPU capacity, додаткові readiness providers і secret broker залишаються окремими задачами.

## Конфіги під керуванням runtime

POST/PUT/DELETE configs тепер проходять через runtime, щоб start і edit/delete одного UUID не виконувалися конкурентно. Під час роботи/очікування/recovery ноди зміни відхиляються з 409: спочатку stop. На цьому етапі це стосується також координат і назви; live metadata edits можна додати з graph UI. Runtime тримає node lock до завершення disk transaction навіть після скасування RPC; файловий flock/revision CAS залишаються додатковим захистом.

При зупиненому runtime web login, users, hardware і GET configs продовжують працювати; запис конфігів та worker API повертають 503 з вказівкою запустити runtime вручну. Web не стартує runtime автоматично.

## API

| Метод | Адреса | Дія |
| --- | --- | --- |
| GET | `/api/workers?offset=0` | Порція status snapshots runtime |
| GET | `/api/nodes/{uuid}/status` | Поточний стан одного worker |
| POST | `/api/nodes/{uuid}/start?expected_revision=N` | Явний запуск саме збереженої revision N |
| POST | `/api/nodes/{uuid}/stop` | Ідемпотентна зупинка та скасування recovery |

Viewer читає; admin/operator змінюють. Стандартні session/Origin/CSRF перевірки зберігаються. Для POST передавати JSON `{}` із Content-Length. HTTP 202 означає прийняття команди, не running. До першого запуску state=stopped і status=null: instance не вигадується. Runtime й web повинні належати одній release revision для mutations.

## Ручне встановлення, запуск та update

Після commit/push користувач запускає ip-media-tool-update. Installer встановлює runtime unit, але не enable/start. Перший ручний запуск:

```bash
sudo systemctl start ip-media-tool-runtime
sudo systemctl status ip-media-tool-runtime --no-pager
```

Після цього start кожної ноди — окрема дія API/майбутнього graph UI. Наявність unit і enabled config не запускає медіапроцеси. Поки штатних plugins ще немає, runtime може показувати порожній список — це очікувано.

Під час ручного оновлення updater сам зупиняє активні служби перед перемиканням коміту. Після активації він відновлює раніше активні runtime і вебсервіс.

```bash
ip-media-tool-update
```

Зупинка runtime завершує всі потоки. Після update ноди потрібно запустити вручну.

## Приймання лише на Linux

Написано `tests/test_node_runtime.py`: memory-only regression cases для immutable snapshots, identities, state validation, transport readiness gate, heartbeat revisions, frame bounds і stop/start/recovery races. Вони не запускаються installer/update і не містять зареєстрованого тестового plugin. За бажанням користувача виконувати в окремому Linux checkout:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p test_node_runtime.py
```

Ці тести не доводять subprocess/systemd behavior. Окреме серверне приймання зі штатним worker або ізольованими fixtures поза production plugins має перевірити: два незалежні workers; web restart без їх зупинки; kill/hang одного worker; бюджет recovery; stop під час backoff/start; завислий stop; cleanup process groups/leases; заблоковані active edits; runtime crash без auto-resume; owner/version mismatch; update maintenance. Жоден із цих сценаріїв локально не виконувався.

Офіційні джерела: [systemd KillMode](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html), [GStreamer 1.24 gst-inspect --exists](https://github.com/GStreamer/gstreamer/blob/1.24/subprojects/gstreamer/tools/gst-inspect.c).
