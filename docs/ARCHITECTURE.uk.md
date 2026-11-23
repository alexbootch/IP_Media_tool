# Архітектура Converter

Дата: 2026-10-30. Базові вимоги погоджені; позначені пропозиції та відкриті питання потребують практичної перевірки.

## Погоджені принципи

- Один фізичний Linux-сервер без віртуальних машин.
- GStreamer — основний медіарушій. Інтеграційний адаптер може використовувати SDK протоколу, якщо потрібного GStreamer-елемента немає.
- Вхід зберігає фактичний відеостандарт та структуру аудіо; виконує demux/decode для публікації raw media в SHM.
- Вихід читає SHM і виконує власне масштабування, зміну частоти кадрів, progressive/interlaced, кодування та передавання.
- Звук без автоматичного remix/remap за замовчуванням. Адаптація до вимог протоколу та компенсація затримки відео явні; непідтримувана кількість каналів не скорочується мовчки.
- Граф із нодами у браузері. DB та graph.json відсутні.
- Координати — у конфігу кожної ноди. З'єднання — лише у конфігу споживача як local input port -> source node ID + source output port ID.
- Стабільні ID нод не залежать від назви або координат. Endpoint SHM, PID, поточні caps та стани не є постійним конфігом.
- NDI підтримує Discovery Server і send/receive groups на рівні runtime; для пошуку входів потрібні одночасні mDNS + server. Обмеження sender та конфігурація — у [NDI discovery](NDI_DISCOVERY.uk.md). OMT відкладено; власний plugin не розробляємо.
- GPU і DeckLink можливості виявляються на хості, а доступні параметри нод визначаються за результатами детекції.
- Стандарт незалежних модулів, naming, CSS scope та документація кожної функції погоджені 2026-11-20: [NODE_STANDARD](NODE_STANDARD.uk.md), [SDK v1](../node-sdk/README.md), [схеми](../schemas/v1/README.md). Один тип має власні worker/UI/CSS; екземпляри працюють у різних процесах і не імпортують код інших нод.

## Рекомендований поділ процесів

```text
Browser graph + node UI
          | HTTPS / REST / events
Control service: config, graph, auth, audit, discovery catalogue
          | versioned local IPC
Runtime service: supervision, contracts, resource ownership
          |                        |
Input worker -- raw media SHM --> Output worker
          |                        |
          +-- contract events -----+
```

API не передає медіабуфери та не імпортує GI/SDK пристроїв для виконання сканувань. Один екземпляр ноди — один worker process. Worker створює власні GStreamer pipelines. Виходи незалежні: їх перезапуск не зупиняє джерело чи інші виходи.

systemd контролює core-сервіси, runtime контролює worker lifecycle. Лише один власник перезапуску кожного процесу. Перезапуск control service не повинен зупиняти активні воркери; відновлення runtime service та долю воркерів при його аварії потрібно визначити й перевірити окремо. Обраний IPC — пропозиція Unix domain sockets з versioned messages; NATS не є обов'язковою залежністю.

## Конфіги без DB

```text
/etc/ip-media-tool/system.json
/etc/ip-media-tool/nodes/<node-id>.json
/var/lib/ip-media-tool/security/           users, protected credentials
/run/ip-media-tool/                        sockets, reconstructed runtime state
/dev/shm/ip-media-tool/                    media allocation, if required by chosen transport
/var/lib/ip-media-tool/                    capability cache, backups, update state
/var/log/ip-media-tool/                    rotated audit and diagnostic files if used
/opt/ip-media-tool/releases/<revision>/    immutable application runtime
/opt/ip-media-tool/current                active revision pointer
```

Остаточне розташування media allocation залежить від реалізації SHM; socket-path не є адресою буфера.

Кожен конфіг ноди містить schema_version, id, type, revision, settings_version, name, enabled, position, settings, connections та secret_refs за схемою v1. Enabled дозволяє ручний запуск, а не autostart. Зміни проходять через одного writer service: schema validation -> revision check -> запис тимчасового файла -> fsync -> atomic replace -> fsync каталогу. Конкурентні зміни відхиляються за revision. Для multi-node операцій потрібні transaction journal і recovery або інший перевірений протокол фіксації; атомарність окремого файла не гарантує атомарність кількох файлів.

Невалідний конфіг ізолюється з діагностикою. Відсутнє джерело зберігається як unresolved connection. Видалення джерела не повинно мовчки стирати посилання у споживачів. Цикли та несумісні порти перевіряються перед запуском. Експорт графа — пакет конфігів, імпорт перевіряє колізії ID.

## Медіаконтракт і SHM

Реалізація й експерименти з транспортом починаються після підготовки схем, node-sdk та каркаса керування за планом. Тимчасові досліди виконуються в окремому тестовому середовищі поза деревом вихідного коду, з прибиранням власних процесів, sockets і SHM allocations після запуску. У проєкті залишаються штатна реалізація, відтворювані integration tests та потрібні звіти.

Один логічний media output port описує відео та список аудіопотоків. Немає вимоги заздалегідь ділити всі аудіодоріжки на mono SHM: формат банків обрати після тесту синхронізації та багатоканальних входів.

Контракт містить schema_version, source_node_id, source_port_id, instance_id, contract_revision, transport_generation, стан, raw-video caps, raw-audio caps кожного потоку, channel labels/order, transport endpoints і time model. Відеоопис включає точні дробові fps/PAR, interlace mode, field order, colorimetry та наявні HDR metadata; невідомі значення не вигадуються. Передаються ідентифікатори endpoint'ів, не адреси процесної пам'яті. Для власного SHM layout додатково потрібні узгоджені ABI, strides, plane offsets, buffer ownership та bounds.

Джерело публікує контракт після узгодження caps і готовності endpoint'ів. До цього — waiting_signal. Контракт передається споживачу control plane через підписку edge; startup snapshot та наступні події мають узгоджені ревізії. Controller/runtime тримає реконструйований реєстр у пам'яті. Доставка має підтримувати resync після reconnect, а споживач відкидає застарілі revision/instance_id.

Зміна формату публікується узгоджено з новими буферами. Якщо транспорт не підтримує безпечне in-band перемикання, створюється нове transport_generation з іншими endpoint'ами. Старе покоління закривається за визначеним handshake/timeout. Процесна аварія, loss of signal та втрата discovery — різні стани.

SHM-прототип повинен перевірити caps, PTS/DTS/duration, SEGMENT, discontinuity, clock/base-time mapping, start/reconnect, A/V sync і late join. Статичний JSON не замінює timestamps кожного буфера. Стандартні shmsink/shmsrc — перший кандидат, а не вже доведене рішення. GDP або власний transport envelope розглядати лише після вимірювань і перевірки late join. Zero-copy не обіцяється; рахуються фактичні копії й RAM bandwidth.

Один producer endpoint може мати кілька consumers. Повільний або відключений consumer не повинен блокувати ingest: bounded buffers, явна drop/disconnect policy, лічильники втрат і окреме тестування fan-out.

## Discovery

Discovery contexts прив'язані до ефективних мережевих налаштувань ноди. Каталог не повинен показувати джерела іншої групи чи discovery-контексту як доступні для поточного входу. Дедуплікація та відновлення після перезапуску сервера обов'язкові. Discovery server не є медіареле.

NDI: список discovery servers, receive groups для input finder, send groups для output sender, local discovery згідно з SDK, additional source addresses і NIC selection за наявності підтримки. Перевірити реальний спосіб ізоляції per-worker settings до SDK initialization: API або окремий SDK config context. Зміна глобального SDK-файла однією нодою не може змінювати поведінку інших нод. Функції Advanced SDK не вважати доступними без перевірки обраної ліцензії/runtime.

OMT відкладено й виключено з поточної реалізації, інсталяції та критеріїв приймання. Власний OMT plugin не розробляємо. Раніше створені порожні каталоги OMT залишаються лише позначенням майбутнього розширення. У базовому scope Discovery Server зовнішній; запуск локального сервера — окрема опційна функція.

NDI groups — спосіб керування видимістю, не заміна автентифікації вебінтерфейсу.

## Hardware capabilities

DeckLink: окремий subprocess із timeout, persistent identity, реальні input/output modes. Каталог назв режимів допоміжний, не доказ підтримки залізом. Виявляти board/subdevice/profile/duplex, audio limits і format support настільки, наскільки це підтверджує API. Наявність input і output у discovery не означає одночасний full-duplex. Зміна board profile не виконується під активними маршрутами автоматично.

GPU: adapter UUID/PCI identity, driver/runtime versions, native codec caps, Gst element caps, перевірка конкретного device binding. Ефективні можливості — перетин hardware, driver, SDK, plugin і перевіреної конфігурації pipeline. Невідомий результат залишається unknown, а не supported. Окремо обліковуються підтримка формату та поточна місткість GPU. Перший кандидат перенесення — NVIDIA NVENC; NVDEC/VAAPI/QSV потребують власних probes.

Reserve/release ресурсів централізовані: SDI endpoint exclusive за перевіреною політикою плати; GPU допускає багато worker sessions за перевіреною місткістю. Read-only discovery за замовчуванням; активні probes, які захоплюють пристрої, запускаються до роботи маршрутів або явно в maintenance mode.

## Безпека і оновлення

Вхід, сесії, права viewer/operator/admin, protected user files з password hashes, обмежені файлові права, audit trail та rotation входять у перший керований вертикальний зріз. Секрети протоколів зберігаються окремо від експортованих конфігів нод; конфіг містить secret reference. Web API не приймає довільні shell commands чи неперевірені launch strings.

ip-media-tool-update завантажує конкретний stable release asset із GitHub Releases, перевіряє довіру підпису й digest, compatibility manifest і доступний диск; створює backup конфігів, готує нову версію поруч із чинною, проводить preflight, контрольовано зупиняє потрібні workloads, перемикає release та перевіряє здоров'я. Rollback повертає попередній runtime і сумісний snapshot конфігів. Migration failure, interrupted update та power loss — обов'язкові сценарії.

Оновлення драйверів, kernel і базового OS stack — окреме maintenance завдання. Безперервність сигналу під час update не є гарантією першого релізу.
