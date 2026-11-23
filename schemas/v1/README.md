# Schemas v1

JSON Schema Draft 2020-12. Offline registry і config/module/graph validation підключені до web API 2026-11-23; див. [сховище конфігів](../../docs/NODE_CONFIG_STORAGE.uk.md). Media readiness та IPC session validators ще не реалізовані. `$ref` резолвиться з підготовленого registry без завантаження довільних remote URLs. Метасхема `$schema` вказує стандарт, не потребу звертатись у мережу при кожній перевірці.

| Файл | Об’єкт |
| --- | --- |
| common.schema.json | ID, versions, revisions, структурні errors |
| node-manifest.schema.json | Модуль типу ноди, entry points, ports, requirements |
| node-config.schema.json | Постійний конфіг екземпляра, координати, consumer-owned connections |
| node-status.schema.json | Runtime snapshot конкретного worker instance |
| media-contract.schema.json | Готовий raw video/audio producer port і opaque endpoint descriptors |
| ipc-message.schema.json | Runtime-worker команди, ack, status, contract та withdrawal |
| worker-bootstrap.schema.json | Приватний inherited-FD startup config, instance ID і leases |

Версії не взаємозамінні: `schema_version` — структура документа; manifest `version` — версія коду модуля; `sdk_version` — major API SDK; `settings_version` — схема settings конкретного plugin. `revision`, `status_revision`, `contract_revision` — порядкові зміни відповідних об’єктів, а не версії схем. `transport_generation` — UUID покоління transport allocations.

Валідатор зобов’язаний перевіряти format assertions (`uuid`, `date-time`), відхиляти нечислові NaN/Infinity, дубльовані JSON keys, невідомі schema versions і не виконувати довільні schema URLs із конфігів. Максимальні розміри файлів, IPC frames і вкладеність задає loader до schema validation. Manifest/config читаються як дані, без import worker.

Після JSON Schema перевіряються семантичні умови, які вона не доводить:

1. Унікальні port IDs усередині manifest; тип config відповідає manifest і settings_version. Для kind=input немає input ports, для kind=output — output ports; processor має власні правила. Port `required` для input означає необхідне підключення, для output — обов’язкову публікацію при readiness, а не вимогу мати споживача.
2. Settings проходять plugin config.schema.json. Required dependencies і capabilities доступні на поточному host; unknown не перетворюється на supported.
3. Local connection key є input port; source port є output. UUID колізії, self-links, цикли й incompatible contract types відхиляються перед запуском. Unresolved source зберігається для подальшого відновлення.
4. Шляхи plugin entries після resolve лишаються в межах plugin; symlinks не дають виходу з нього. Worker class/type/SDK version перевіряє loader у worker process.
5. У контракті stream IDs та endpoint IDs унікальні у своїх namespaces; кожен stream endpoint існує, зайві endpoints відхиляються. Спільний endpoint кількох stream дозволяє лише відповідний transport descriptor validator.
6. Caps — fixed negotiated caps, не ANY/list/range; їхні значення узгоджені зі структурними полями. NULL fps означає невідомий/змінний rate, а не нульову частоту. Довжина channel_labels, якщо відома, дорівнює channels; порядок масиву відповідає канальному порядку. Однакові labels допустимі. Не підставляти stereo для невідомих 2-channel labels.
7. Transport-specific descriptor, memory features, ABI, bounds, socket/SHM ownership, timestamp/segment mapping та HDR metadata encoding потребують окремої перевірки. Порожній descriptor, навіть якщо проходить загальну схему, не дає дозволу запустити route.
8. Runtime перевіряє envelope node_id/instance_id за авторизованою worker session. Для status вони збігаються з payload. При forwarding contract до consumer envelope належить consumer session, payload зберігає source identity. Contract accepted лише від producer-власника; routing відбувається за connections.
9. Request IDs корелюються в межах session. Command повторно з тим самим request_id не виконується вдруге; ack — лише прийнято. Error envelope — відмова на запит, unsolicited failure передається status=error. Старий instance відхиляється; revision порівнюється тільки в межах відповідного source/port/instance. Withdrawal має нову contract_revision, щоб запізнілий ready event не відновив відкликаний transport.
10. State transition і config revision застосовуються за SDK lifecycle; збережений enabled не дозволяє autostart. Schema validation сама по собі не підтверджує hardware ownership чи readiness.

Приклади: [examples/nodes/reference](../../examples/nodes/reference/README.md). Це інертні приклади специфікації, не встановлені plugins і не transport prototype. Локальна валідація/запуск не виконувалися.

Стан lifecycle 2026-11-23: статуси, identity/revision/state checks, command framing і bootstrap підключені до node-sdk/runtime. Manifest resource_bindings задає DeckLink settings key і напрямок для централізованого lease. Media contract routing/transport-specific validators та secret broker ще не реалізовані; див. [WORKER_RUNTIME](../../docs/WORKER_RUNTIME.uk.md).
