# План реалізації Converter

Оновлено: 2026-11-20. Підготовлено структуру, web/auth і hardware detector code; стандарт незалежних нод та схеми v1 записані. Існування каталогу або схеми не означає готовність runtime. Виконані роботи й обмеження зазначені в кожному етапі.

Правило всіх етапів: Windows workspace — тільки код, документація й Git review. Локальні tests/builds/application runs заборонені. Усі виконувані перевірки в цьому плані стосуються Linux і запускаються користувачем вручну; release builds виконуються у визначеному Linux build environment за його дією. Git push не запускає deployment. [Локальний workflow](LOCAL_DEVELOPMENT.uk.md).

## Етап 0. Перевірка цільового середовища

Часткові результати від 2026-11-17: [інвентаризація хоста](HOST_INVENTORY.uk.md). Драйвери NVIDIA та DeckLink підтверджені за виводом користувача; повний етап ще не завершений. Команди на сервері виконує користувач, прямого доступу агента немає.

За уточненням користувача Kubernetes і GStreamer раніше видалялися. Kubernetes та NVIDIA Kubernetes Device Plugin не є залежностями Converter; перевірку й очищення їхніх залишків не продовжуємо без конкретного конфлікту. Для GStreamer готуємо потрібний набір залежностей перед майбутньою інсталяцією.

- [x] Зафіксувати поточні драйвери: Blackmagic Desktop Video 16.4a1 та NVIDIA 580.178.04; додати їх перевірку в [чеклист розгортання](DEPLOYMENT_CHECKLIST.uk.md). Це inventory baseline, медіасумісність ще не сертифікована.
- [ ] Визначити підтримуваний Linux runtime profile без прив'язки продукту до CPU/RAM конкретного хоста.
- [ ] Сформувати обов'язковий інсталяційний набір Python, GStreamer/GI та медіапакетів із версіями; перевірку factories виконувати після встановлення.
- [ ] Для NDI обрати версію gst-plugin-ndi і сумісної runtime-бібліотеки; перевірити groups, discovery server settings, ABI та постачання. Повний SDK на цільовому хості не є базовою вимогою. Окремо визначити NDI High Bandwidth/HX scope.
- OMT відкладено на майбутнє й виключено з поточних залежностей та критеріїв приймання. Власний OMT plugin не розробляємо; повернення до OMT можливе з придатним готовим plugin.
- [ ] Описати налаштування NDI runtime, groups та ізольовані worker contexts; Discovery Server вмикається незалежно для кожного входу й виходу. Вхід із сервером одночасно використовує mDNS + Discovery Server; подвійне оголошення виходу не потрібне. Деталі — в [описі NDI discovery](NDI_DISCOVERY.uk.md).
- [ ] Визначити початковий набір форматів, аудіоканалів, цільову кількість потоків, latency і допустимий час відновлення. Не вважати конкретний hardware обмеженням усієї архітектури.

Результат: host report, dependency matrix та список непідтверджених SDK можливостей. Це інвентаризація й аналіз: без встановлення пакетів, зміни хоста, збирання сторонніх SDK і запуску медіапрототипів. Недоступні дані позначаються як невідомі.

## Етап 1. Проєктування та схеми

Результат 2026-11-20: [стандарт ноди](NODE_STANDARD.uk.md), [API SDK v1](../node-sdk/README.md), [схеми v1](../schemas/v1/README.md) та [інертні приклади](../examples/nodes/reference/README.md). Правила незалежності, worker/CSS/UI файлів, naming, docstrings/JSDoc, спільного стилю, lifecycle/errors, ресурсів і незмінності аудіо закріплені також у AGENTS.md. Це завершений пакет специфікації; runtime validators, SDK implementation, transport ABI/clock mapping та виконуване приймання ще не завершені. Локальні tests/imports/schema validation не запускалися.

- [x] Зафіксувати погоджений стандарт модуля та єдині Python/JavaScript API names.
- [x] Написати JSON-схеми manifest, node config/connection, worker status, media contract та IPC envelope; додати статичні приклади.

- [ ] Доповнити capability snapshot schema та transport-specific descriptor/ABI schemas; загальні node config, manifest, contract і IPC v1 вже написані.
- [ ] Уточнити handshake/generation switch і resync semantics на рівні transport; публічні назви методів SDK та lifecycle вже описані.
- [ ] Описати часову модель, вимоги до SHM і критерії майбутніх транспортних тестів; відкриті рішення позначити явно.
- [ ] Уточнити пакування плагінів і міграції settings; інертні приклади конфігів, межі модулів та runtime paths вже описані.
- [x] Написати offline schema registry, config/module/graph validators та атомарний writer з revision CAS, backup, API й installer dependencies. Завершено після переривання 2026-11-23; [обсяг та межі](NODE_CONFIG_STORAGE.uk.md). Код локально не виконувався.
- [ ] Прийняти config store на Linux: stale revision, malformed/duplicate JSON, цикли, unavailable module, symlink escape та збій atomic replace/fsync. Media readiness й IPC session validation — наступні етапи.
- [ ] Визначити правила експериментів: окреме середовище, відокремлені результати та прибирання ресурсів.

Умова завершення: узгоджені межі модулів, схеми та контракти, визначені відкриті технічні питання й критерії їх перевірки. Медіапрототип на цьому етапі не створюється.

## Етап 2. Каркас системи та керування

Lifecycle 2026-11-23: написано Python node-sdk, inherited-FD worker host, незалежний runtime service, Unix RPC/client, start/stop/status API, deadlines і bounded crash recovery (3 retries). Config mutations координує runtime, активні ноди треба зупинити перед edit/delete. Перший запуск systemd unit ручний. Updater зупиняє й відновлює раніше активні служби. [Обсяг, обмеження та серверне приймання](WORKER_RUNTIME.uk.md). Регресійні тести локально не запускалися. Медіатранспорт і маршрутизацію контрактів додано на етапі 3. Secret broker ще попереду.

- [x] Написати SDK lifecycle, окремий supervisor process, статуси, stop/start cancellation, deadlines і обмежене recovery без autostart.
- [ ] Прийняти SDK/runtime на Linux: subprocess crash/hang, systemd cleanup, незалежність web, version mismatch і активні config edits.

Реалізація 2026-11-19: написано FastAPI web shell, login/session/roles, file-backed users і browser user management; installer створює systemd unit, ip-media-tool-admin та main-channel updater. Локально не запускалося, [серверне приймання](WEB_AUTH.uk.md) відкрите. Graph/runtime/mock workers цього етапу ще не реалізовані.

- [ ] File config store: schema/revision validation, atomic writes, recovery, backup, конфіг пошкоджений/відсутній.
- [ ] Відновлення графа лише з конфігів нод; unresolved connections, перевірка циклів, зміна координат, import/export.
- [ ] node-sdk і plugin manifest: ports, config schema, worker entry point, UI entry point, capabilities requirements.
- [ ] Control/runtime separation, Unix socket IPC, lifecycle, readiness, bounded restart policy, resource ownership.
- [ ] systemd units та перевірка service lifecycle із тестовими воркерами без медіапайплайнів.
- [x] Написати браузерний редактор графа: створення нод із реєстру, переміщення, з’єднання портів, збереження конфігів, ручний запуск/зупинка та опитування статусів. [Робочий простір і серверне приймання](GRAPH_EDITOR.uk.md).
- [ ] Підтвердити graph UI та lifecycle на Linux із зареєстрованими модулями. Поточний UI читає статуси опитуванням, підписку на події залишено на майбутнє.
- [ ] Bootstrap admin, login/logout, захищені сесії, viewer/operator/admin, audit, secret references і log rotation.

Умова завершення: користувач після входу створює та з'єднує ноди, зберігає граф у їхніх конфігах і керує mock workers. Конфіги та бажаний стан відновлюються після перезапуску. Каркас ще не доводить працездатність медіатракту.

## Етап 3. Перевірка SDK і реалізація медіатранспорту

Починається після завершення схем і каркаса системи. Досліди виконуються в окремому Linux test environment з визначеним каталогом за межами дерева вихідного коду. Тимчасові pipelines та сторонні SDK builds не розміщуються у корені проєкту чи production-модулях.

- [ ] Перевірити Linux NDI runtime/plugin, спільний пошук mDNS + Discovery Server, groups та ізоляцію worker contexts за сценаріями етапу 0.
- [x] Додати SHM-контракти, `mmap`-кільця, fan-out та ізольований Linux-сценарій: джерело → два незалежні виходи. [SHM і приймання](SHM_TRANSPORT.uk.md).
- [ ] Виконати сценарій на Linux і зафіксувати результат до приймання транспорту.
- [ ] Перевірити caps, timestamps/SEGMENT/discontinuity, clock/base-time mapping, A/V drift і затримку.
- [ ] Перевірити late join, повільного consumer, full SHM, disconnect, producer restart і format change.
- [ ] Обрати транспортну реалізацію за результатами metadata correctness, copies, RAM bandwidth і fan-out; визначити формат audio banks.
- [ ] Після кожного запуску прибрати лише ресурси цього тесту: процеси, sockets, SHM allocations і тимчасові файли; зберегти потрібний звіт окремо.
- [ ] Перенести перевірений код у штатні модулі, а відтворювані сценарії — в integration tests; зафіксувати результат експерименту та прибрати тимчасову реалізацію.

Умова завершення: два виходи працюють незалежно, A/V sync проходить погоджений бюджет, stale endpoints не використовуються, а експеримент не залишає тимчасових ресурсів у робочому дереві та на хості.

## Етап 4. Hardware і перший зовнішній тракт

GPU-частина 2026-11-19: написано background manager, disposable PCI/CUDA/GStreamer probe, versioned cache і header panel за підходом StreamMachine encoder_capabilities. Параметри effective per-device NVENC беруться зі встановленого GStreamer; hardware-only flags без native provider залишаються unknown. Локально не запускалося, [серверне приймання](GPU_CAPABILITIES.uk.md) відкрите.

DeckLink-частина 2026-11-20: написано disposable provider scan, startup/manual background manager із кешем попереднього результату, API та панель у шапці. Кілька плат розрізняються за ID/serial, режими входу/виходу читаються окремо з device caps. Непідтверджене групування плат і фізичне BNC mapping не припускаються. [Серверне приймання](DECKLINK_CAPABILITIES.uk.md) відкрите; локально не запускалося. Media routes ще не реалізовані.

- [ ] Перенести й адаптувати DeckLink subprocess scanner та NVIDIA/NVENC capability helpers за reuse audit.
- [ ] Додати stable identity, capability provenance, cache invalidation і діагностику unavailable/unknown/stale.
- [ ] Перевірити board/subdevice/profile/duplex, точні video modes та audio limits; resource locking.
- [ ] Зробити форми UI залежними від effective capabilities; GPU support відокремити від вільної місткості.
- [ ] SDI input/output: actual signal caps, source loss/recovery, audio preservation, field order і AV timing.
- [ ] SRT input/output: caller/listener, latency, streamid/secrets, reconnect, demux/decode/encode/mux.
- [ ] Output conversion profile: resolution, rational fps, progressive/interlaced, field order, codec/pixel format; audit зміни.

Умова завершення: SDI -> SRT і SRT -> SDI на фізичному Linux host, незалежні виходи з різними профілями, жодного мовчазного скорочення аудіоканалів.

## Етап 5. NDI

- [ ] NDI input/output; receive/send groups, discovery server list, source selection і потрібні мережеві параметри.
- [ ] Окремий discovery context для кожного ефективного набору налаштувань; SDK context isolation до init.
- OMT input/output не входять у поточний етап.
- [ ] Спільний UI каталогу джерел з протокольними можливостями; дедуплікація, stale status та diagnostics.
- [ ] Перевірити server outage, restart, зміну груп NDI та два workers з різними discovery settings без взаємного впливу.
- [ ] Прогнати конверсії між SDI/SRT/NDI, збереження аудіодоріжок і незалежний fan-out.

Умова завершення: знаходження й приймання джерел із потрібних discovery contexts, публікація виходів, перевірений media round-trip. Власний локальний Discovery Server не є умовою етапу.

## Етап 6. WebRTC

- [ ] Узгодити потрібні клієнти та signaling: browser input/output і за потреби WHIP/WHEP.
- [ ] WebRTC input -> SHM та SHM -> WebRTC output; ICE/STUN/TURN, codec negotiation, audio limits.
- [ ] Авторизація signaling/viewing, session cleanup, browser reconnect, slow client і кілька одночасних глядачів.
- [ ] Повна матриця чотирьох поточних типів входів/виходів (SDI, SRT, NDI, WebRTC): кожна підтримувана пара перевіряється; обмеження форматів показуються явно.

Умова завершення: WebRTC працює як повноцінний input/output, а не лише preview; per-session failures не зупиняють джерело.

## Етап 7. Пакування і надійність

- [x] Написати public release bootstrap install.sh, ip-media-tool-update і workflow_dispatch для ручної публікації системних компонентів. Це не повний application installer; див. docs/PUBLIC_INSTALL.uk.md.
- [ ] Забезпечити публічне розповсюдження та вручну опублікувати перший release. На Ubuntu — один URL для install, далі ip-media-tool-update; Git, PAT і deploy keys не потрібні.
- [x] Написати скрипт перевірки драйверів і встановлення відсутніх базових Ubuntu-компонентів: tools/install/install-components.sh. Локально не виконувався; серверне приймання й NDI packages залишаються відкритими.
- [ ] Користувач перевіряє installer на Linux: --check, install, повторний запуск, missing driver, APT failure, NDI incomplete; без локальних тестів.
- [ ] GitHub release pipeline: versioned build, validation, signed assets, dependency BOM, публікація stable та конкретні release manifests за [вимогами](INSTALL_UPDATE.uk.md).
- [ ] converter-install: первинна й повторна інсталяція з GitHub Releases, stable/explicit version, обов'язкові runtime-залежності, driver checks, service account/systemd/bootstrap admin.
- [ ] Versioned application/runtime bundle, dependency locks/BOM, compatibility manifest, systemd install/uninstall policy.
- [ ] ip-media-tool-update: GitHub stable release, signature/digest checks, prepare/preflight, backup, controlled switch, health check і rollback.
- [ ] Перевірити update failure, config migration rollback, disk full, interruption/power loss та offline installed runtime.
- [ ] Fault injection: worker crash/hang, device removal, discovery loss, network interruption, API restart, memory exhaustion.
- [ ] Узгоджений soak test (початковий кандидат 72 години), кількість маршрутів/формати та бюджети CPU/GPU/RAM/latency/drift.
- [ ] Документація install, operation, recovery, diagnostics bundle та hardware compatibility matrix.

Умова завершення: повторювана інсталяція на чистий host, перевірений rollback і звіт випробувань. Безперервність сигналу при update не обіцяється у першому релізі.

## Найближчі конкретні результати

1. Перелік обов'язкових компонентів інсталяції та відкритих питань NDI runtime/plugin; OMT відкладено.
2. Узгоджені межі модулів, API node-sdk і правила життєвого циклу ноди.
3. Версійні схеми node config і media contract, приклади connections усередині споживача.
4. Каркас config/control/runtime та правила збирання й перевірки проєкту.

Ранні медіапрототипи не входять до підготовки проєкту. SDK runtime-досліди й SHM-перевірки виконуються на етапі 3 у визначеному тестовому середовищі після підготовки контрактів і каркаса. Реалізацію production media transport не затверджувати до завершення його timing/fan-out перевірки.
