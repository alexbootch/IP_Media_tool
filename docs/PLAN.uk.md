# План реалізації Converter

Оновлено: 2026-11-17. Структура каталогів підготовлена. Усі задачі реалізації нижче ще відкриті; існування каталогу не означає готовність функції.

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

- [ ] Схеми node config, manifest, connection, media contract, IPC і capabilities; exact fractional rates та unknown states.
- [ ] Описати API node-sdk, lifecycle, публікацію контракту, handshake/generation switch і відновлення підписок.
- [ ] Описати часову модель, вимоги до SHM і критерії майбутніх транспортних тестів; відкриті рішення позначити явно.
- [ ] Підготувати приклади конфігів і опис меж модулів, runtime-каталогів, залежностей та пакування.
- [ ] Визначити правила експериментів: окреме середовище, відокремлені результати та прибирання ресурсів.

Умова завершення: узгоджені межі модулів, схеми та контракти, визначені відкриті технічні питання й критерії їх перевірки. Медіапрототип на цьому етапі не створюється.

## Етап 2. Каркас системи та керування

- [ ] File config store: schema/revision validation, atomic writes, recovery, backup, конфіг пошкоджений/відсутній.
- [ ] Відновлення графа лише з конфігів нод; unresolved connections, перевірка циклів, зміна координат, import/export.
- [ ] node-sdk і plugin manifest: ports, config schema, worker entry point, UI entry point, capabilities requirements.
- [ ] Control/runtime separation, Unix socket IPC, lifecycle, readiness, bounded restart policy, resource ownership.
- [ ] systemd units та перевірка service lifecycle із тестовими воркерами без медіапайплайнів.
- [ ] Браузерний граф, налаштування й актуальні стани через events; lifecycle перевіряється mock workers без GStreamer/SHM.
- [ ] Bootstrap admin, login/logout, захищені сесії, viewer/operator/admin, audit, secret references і log rotation.

Умова завершення: користувач після входу створює та з'єднує ноди, зберігає граф у їхніх конфігах і керує mock workers. Конфіги та бажаний стан відновлюються після перезапуску. Каркас ще не доводить працездатність медіатракту.

## Етап 3. Перевірка SDK і реалізація медіатранспорту

Починається після завершення схем і каркаса системи. Досліди виконуються в окремому Linux test environment з визначеним каталогом за межами дерева вихідного коду. Тимчасові pipelines та сторонні SDK builds не розміщуються у корені проєкту чи production-модулях.

- [ ] Перевірити Linux NDI runtime/plugin, спільний пошук mDNS + Discovery Server, groups та ізоляцію worker contexts за сценаріями етапу 0.
- [ ] Реалізувати SHM transport через визначений інтерфейс node-sdk: test input -> два незалежних test outputs.
- [ ] Перевірити caps, timestamps/SEGMENT/discontinuity, clock/base-time mapping, A/V drift і затримку.
- [ ] Перевірити late join, повільного consumer, full SHM, disconnect, producer restart і format change.
- [ ] Обрати транспортну реалізацію за результатами metadata correctness, copies, RAM bandwidth і fan-out; визначити формат audio banks.
- [ ] Після кожного запуску прибрати лише ресурси цього тесту: процеси, sockets, SHM allocations і тимчасові файли; зберегти потрібний звіт окремо.
- [ ] Перенести перевірений код у штатні модулі, а відтворювані сценарії — в integration tests; зафіксувати результат експерименту та прибрати тимчасову реалізацію.

Умова завершення: два виходи працюють незалежно, A/V sync проходить погоджений бюджет, stale endpoints не використовуються, а експеримент не залишає тимчасових ресурсів у робочому дереві та на хості.

## Етап 4. Hardware і перший зовнішній тракт

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
