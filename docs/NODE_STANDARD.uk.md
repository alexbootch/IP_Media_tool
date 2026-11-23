# Стандарт модуля ноди · v1

Погоджено 2026-11-20. Обов’язковий стандарт для всіх нових нод. На цьому кроці визначені структура, схеми та API SDK; виконуваний runtime, graph UI і медіатранспорт ще не реалізовані.

## Модуль типу та екземпляр

```text
plugins/<node-type>/
  manifest.json
  config.schema.json
  worker.py
  ui.js
  styles.css
  README.md
```

Тип має стабільну назву, наприклад `sdi-input`. Екземпляр має окремий UUID, JSON-конфіг, worker process і ресурси. Налаштування двох екземплярів одного типу не зберігаються в каталозі модуля. Конфіги розміщуються в `/etc/ip-media-tool/nodes/<id>.json`; runtime IPC — у `/run/ip-media-tool/`, snapshot/cache — у `/var/lib/ip-media-tool/`. Ці каталоги не створюються самим фактом додавання стандарту.

Код ноди імпортує лише публічний SDK, дозволені системні бібліотеки й власні допоміжні файли. Заборонено імпорт worker/UI іншої ноди, доступ до її конфігів чи зміна глобального NDI runtime config. Manifest описує необхідні GStreamer factories і hardware capabilities; відсутня необов’язкова залежність не повинна ламати інші ноди чи login.

Загальний SDK розміщується у Python namespace `converter.node_sdk` під `src/`; каталог `node-sdk/` містить його публічну специфікацію. Це один SDK, не два незалежні пакети. Node-specific GI/SDK imports виконуються тільки у worker process. Control service не імпортує worker для читання manifest.

## Назви та документація

Python: `snake_case` для функцій, змінних і параметрів, `PascalCase` для класів, `UPPER_SNAKE_CASE` для констант. JSON: `snake_case`. JavaScript: `camelCase` для функцій/змінних, `PascalCase` для класів. Один публічний worker class має назву `Worker`. Однакові операції мають однакові сигнатури зі специфікації SDK.

Використовуються однозначні назви: `node_id`, `instance_id`, `config_revision`, `source_node_id`, `source_port_id`, `contract_revision`, `transport_generation`, `persistent_id`, `device_number`, `fps_num`, `fps_den`. `instance_id` — ID конкретного запуску воркера, а не ID ноди. `device_number` — поточний індекс GStreamer, а не стабільний hardware ID.

Кожна функція й метод, включно з приватними helpers та constructors, має Python docstring або JavaScript JSDoc. Опис пояснює призначення; нетривіальні параметри, результат, помилки й побічні дії документуються явно. Для складного callback також потрібен коментар або винесення в документовану функцію. Коментарі коду й API — англійською; інтерфейс і користувацька документація — українською. Коментарі не мають повторювати тіло функції чи описувати застарілу поведінку.

## CSS та UI

Кожен тип має власний `styles.css`. Спільна палітра, typography, spacing, controls, focus і кольори станів задаються централізованими design tokens/components. Нода не копіює їхні значення і не імпортує CSS іншої ноди. Власні правила обмежені контейнером `[data-node-type="sdi-input"]`; класи мають префікс `node-sdi-input__`. Обробники шукають елементи всередині переданого `container`, а не в усьому document. DOM IDs, якщо потрібні для label/aria, включають node UUID.

UI одержує config/status/capabilities через API оболонки. Він не запускає worker, не виконує shell commands, не резервує DeckLink самостійно і не змінює файл конфігурації напряму. Протокольні секрети передаються як references, не додаються до export/log. Оновлення status не повинно перезаписувати незбережену форму. Відповідь API зі старою revision ігнорується.

## Конфіг та валідація

Спільна схема: `schemas/v1/node-config.schema.json`. Manifest: `schemas/v1/node-manifest.schema.json`. Кожен plugin додає власну схему лише для `settings`.

Перевірка проходить послідовно: загальна схема → відповідність manifest/SDK version → plugin settings schema → перевірка connections/port directions → capabilities/resources. JSON Schema не замінює останні кроки. `format: uuid` і `format: date-time` мають перевірятися валідатором явно. Відносні entry-point paths мають залишатися в каталозі plugin після resolve; symlink escape заборонений.

Координати зберігаються у `position`; з’єднання лише у конфігу споживача: local input port → source node UUID + source output port. Один input port має одне джерело; кілька споживачів можуть посилатися на один output. Відсутнє джерело є unresolved connection, а не причиною стерти посилання. Один config writer застосовує optimistic revision check та atomic write; runtime не змінює config сам.

`enabled` дозволяє ручний запуск, але не означає autostart. Збережений config, bootstrap та update не запускають медіамаршрут. Незбережені PID, live caps, SHM endpoints, counters і стан процесу належать snapshot, не config. Secrets у `settings` заборонені: поля протоколу посилаються на ключі `secret_refs`, секрет дозволяє SDK лише авторизованому worker.

## Lifecycle, ресурси та помилки

Один worker process на екземпляр; runtime — єдиний власник запуску/перезапуску. Стани: `stopped`, `starting`, `waiting_source`, `waiting_signal`, `running`, `stopping`, `error`. Переходи й точні сигнатури — у `node-sdk/README.md`. Живий процес не дорівнює готовому медіатракту. Readiness підтверджується runtime status та узгодженими caps/endpoint generation.

SDK забезпечує структурні повідомлення IPC, status, logging, contract subscription, timeout/cancellation і cleanup. Worker визначає протокольні параметри, GStreamer pipeline і реакцію на втрату сигналу. Зміна config v1 застосовується контрольованим restart лише відповідної ноди; довільний hot reload не входить у v1. Новий worker отримує новий `instance_id`.

Стан реалізації lifecycle 2026-11-23: [WORKER_RUNTIME](WORKER_RUNTIME.uk.md). Реалізовані процеси, status IPC, cancellation, bounded recovery і DeckLink leases. Contract subscription/transport та secrets поки fail-closed. Редагування активної ноди відхиляється до її ручного stop; автоматичний stop/restart при save не виконується. Detached subprocesses ноди заборонені: helpers мають залишатися у worker process group.

Reserve/release hardware виконує runtime перед використанням; worker не обирає «перший вільний» порт замість налаштованого persistent ID. Device-number повторно звіряється з ID перед запуском. Зміна board profile не виконується автоматично під маршрутами. Після аварії ОС звільняє handles, а runtime перевіряє/звільняє власні leases; не можна видаляти SHM або сокети інших нод.

Кожен stop має обмежений timeout; runtime після нього завершує process group і прибирає тільки ресурси цього instance. Restart policy має ліміт спроб/backoff; invalid config і постійна відсутність hardware не спричиняють нескінченний restart loop. Медійний backpressure виходу не блокує джерело/інші виходи: bounded buffers, явна drop/disconnect policy та лічильники.

Спільні error codes: `invalid_config`, `incompatible_version`, `dependency_unavailable`, `hardware_unavailable`, `resource_busy`, `source_unavailable`, `unsupported_format`, `transport_error`, `pipeline_error`, `timeout`, `internal_error`. Розширення мають namespace `<node-type>.<code>`. Структура error: code, message, retryable. Відсутність сигналу не дорівнює аварії процесу.

Кожна подія журналу має `node_id`, `node_type`, `instance_id`, `event_code`; за потреби `request_id` і `config_revision`. Параметри з credentials, URL tokens, паролі й secret values редагуються до запису. Не логувати кожен frame у звичайному режимі.

## Контракт і сумісність

`schemas/v1/media-contract.schema.json` описує runtime snapshot готового producer port. Video може бути null для audio-only джерела; якщо немає video, потрібен хоча б один audio stream. Зберігаються точні раціональні fps/PAR, фактичні caps та всі аудіопотоки/канали. Невідомі характеристики — null, не вигадані defaults. Звук не remap/remix автоматично.

Передаються opaque endpoint IDs і transport descriptors, не адреси пам’яті іншого процесу. Endpoint descriptor v1 навмисно не фіксує реалізацію SHM: її ABI, bounds, timestamps, ownership, handshake і clock mapping визначаються в транспортному етапі. Без transport-specific validator та приймання transport не вважається робочим. `clock_domain_id` лише ідентифікує часову область, не доводить спільний base-time.

Ревізія контракту монотонна в межах worker instance. Нова incompatible buffer layout/перепублікація endpoint потребує нової transport generation та погодженого переключення. Status `waiting_signal`/`error` відкликає readiness; останній контракт не є доказом чинного сигналу. Події старого instance або generation відкидаються. Після reconnect потрібен snapshot/resync.

## Межі поточного етапу

Додані стандарт, специфікація SDK, JSON schemas та інертні приклади. Вони не реєструють ноди в UI, не створюють runtime resources і не є медіапрототипом. Стан 2026-11-23: штатні config/module/graph validators, manifest registry і атомарний config writer підключені до захищеного API; див. [сховище конфігів](NODE_CONFIG_STORAGE.uk.md). Далі SDK/runtime, graph UI та транспорт за погодженим планом. Локальні виконувані перевірки не запускалися.
