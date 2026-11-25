# Node SDK · public interface v1

Python lifecycle API реалізований у `src/converter/node_sdk/`; supervisor — окремий `converter.runtime` service. Форми SDI використовують JavaScript API нижче. [Реалізація, межі й ручний запуск](../docs/WORKER_RUNTIME.uk.md) · [NODE_STANDARD](../docs/NODE_STANDARD.uk.md) · [schemas/v1](../schemas/v1/README.md)

## Python worker

Клас `Worker` наслідує SDK `NodeWorker`. Всі lifecycle methods async; локальні обчислювальні helpers можуть бути sync. SDK створює worker через `Worker(context)`; context передає перевірений config, node/instance IDs, logger, IPC, contract/resource services і cancellation. Worker не керує supervisor.

| Метод | Семантика |
| --- | --- |
| `validate_config(self, config)` | Без доступу до заліза й запуску pipeline; повертає список структурних validation issues. SDK уже перевірив JSON schemas. Не змінює config. |
| `start(self)` | Починає налаштування протоколу/pipeline, повертається після встановлення обробників подій. Не чекає сигнал нескінченно; готовність публікується через status окремо. |
| `stop(self)` | Ідемпотентно звільняє власні handles/subscriptions/pipeline до stop timeout; cancellation завжди поважається. |
| `get_status(self)` | Повертає поточний NodeStatus snapshot без hardware rescan чи блокування на мережі. Побічних дій немає. |
| `on_contract(self, local_port_id, contract)` | Для consumer: отримує snapshot або новий контракт від SDK. `None` означає відкликання джерела. SDK відсіює старі instances/revisions; worker перевіряє підтримку формату. Producer без consumer ports використовує стандартний no-op SDK. |

SDK context надає `publish_status(status)`, `set_state(state, error=None)`, `create_shm_generation(...)` та `open_shm(contract)`. Публікація й відкликання контракту виконуються через покоління кільця. Producer із required output оголошує `running` після підтвердженої публікації. Consumer отримує `on_contract(local_port_id, contract)` або `None` при відкликанні. `config` та `status_snapshot()` повертають detached копії; `cancelled` — asyncio Event, `logger.event(event_code)` — журнал подій. `get_secret(secret_ref)` повертає `dependency_unavailable`, доки broker не реалізовано. [Опис SHM та ручна перевірка](../docs/SHM_TRANSPORT.uk.md).

Resource leases надає runtime через context після центральної перевірки; worker не має власної глобальної таблиці бронювань. Контракт та status — immutable snapshots для споживачів. Worker не змінює вкладені об’єкти вже опублікованого snapshot.

## Стани

```text
stopped → starting → waiting_source / waiting_signal / running
starting / waiting_source / waiting_signal / running → error
running → waiting_source / waiting_signal
waiting_source / waiting_signal → running
starting / waiting_source / waiting_signal / running / error → stopping → stopped
```

Перехід між waiting_source і waiting_signal допустимий. `waiting_source` — немає готового upstream contract/transport; `waiting_signal` — зовнішнє джерело не дає узгодженого медіасигналу. Очікування не означає restart loop. Прямий `error → starting` не застосовується: supervisor завершує старий instance, потім запускає новий. Кожне оновлення status збільшує `status_revision` у межах instance.

Команди runtime: `start`, `stop`, `get_status`, `resync`. Зміна налаштувань — окрема control-plane операція з config revision і погодженим restart, не команда довільного Python/shell виконання. IPC envelope: [ipc-message.schema.json](../schemas/v1/ipc-message.schema.json). `ack` підтверджує прийняття команди, а не готовий медіатракт. Runtime валідує peer credentials, session/instance identity, порядок подій і ownership, не лише JSON.

## JavaScript UI

`ui.js` експортує `mount(container, context)`. Повернене значення — controller з методами `updateConfig(config)`, `updateStatus(status)`, `updateCapabilities(capabilities)` та `unmount()`. Кожна функція/метод має JSDoc. Context містить node ID і API оболонки; жодного імпорту UI іншої ноди.

`mount` повертає controller синхронно, а асинхронні запити обробляються з явним станом завантаження/помилки. `unmount` ідемпотентно знімає listeners, скасовує власні запити й таймери. Оновлення старого request не змінює новий/видалений instance. Методи update не переписують незбережені поля форми.

У діалозі графа controller реалізує також `readSettings()`, який повертає поточні поля модуля для перевірки та збереження оболонкою. `updateConfig(null)` задає початкові значення нової ноди. Активні конфи відкриваються з `context.readOnly`; зміни status і capabilities надходять окремо від draft.

Спільна оболонка зберігає config через versioned API, перевіряє права і надає кнопки lifecycle. Plugin UI визначає протокольні поля та їхній виклад. CSS підключає оболонка з manifest, один раз на тип; instance isolation забезпечують локальні DOM queries, root scope і IDs з UUID.
