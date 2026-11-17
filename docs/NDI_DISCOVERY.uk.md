# NDI discovery та groups

Оновлено: 2026-11-17. Медіатракт працює через gst-plugin-ndi. Discovery Server, groups та взаємодія з mDNS належать до NDI runtime, який використовує плагін.

## Конфігурація процесу

На Linux NDI читає `$HOME/.ndi/ndi-config.v1.json`. Змінна `NDI_CONFIG_DIR`, задана до ініціалізації бібліотеки, дозволяє вибрати інший каталог конфігурації. Linux runtime також потребує Avahi libraries та avahi-daemon для відповідного discovery шляху. [NDI platform considerations](https://docs.ndi.video/all/developing-with-ndi/sdk/platform-considerations).

Для Converter постійні discovery_server_enabled, discovery_servers та відповідні receive_groups/send_groups зберігаються у конфігу кожної ноди. Вмикання сервера для входу і виходу незалежне. Перед запуском worker runtime service генерує службовий ndi-config.v1.json у `/run/converter/nodes/<node-id>/ndi/` та задає NDI_CONFIG_DIR лише цьому процесу. Службовий файл відновлюється з node config; окремим джерелом налаштувань він не є. Це спосіб ізоляції звичайних процесів, який ще потрібно перевірити з обраною версією NDI runtime/plugin.

Глобальний NDI конфіг користувача сервісу не переписується нодами. Зміна discovery/groups передбачає контрольований перезапуск відповідного worker і його discovery context; hot reload не обіцяється.

Розглянутий GStreamer device provider створює стандартний NDI finder, а sink — NDI sender. Отже, базовий шлях — налаштувати runtime перед стартом плагіна. [Device provider source](https://github.com/GStreamer/gst-plugins-rs/blob/main/net/ndi/src/device_provider/imp.rs), [sink source](https://github.com/GStreamer/gst-plugins-rs/blob/main/net/ndi/src/ndisink/imp.rs). Зафіксувати точну ревізію при виборі plugin release; main не є pin версії.

## Одночасний mDNS і Discovery Server

Вимога користувача: одночасні mDNS і Discovery Server потрібні для пошуку входів, коли сервер увімкнений у відповідній ноді. Для входу без сервера залишається локальний mDNS. NDI документує об'єднання результатів для receiver; sender з налаштованим сервером уникає mDNS. [NDI configuration](https://docs.ndi.video/all/developing-with-ndi/sdk/configuration-files).

Вихід використовує Discovery Server або mDNS відповідно до власного discovery_server_enabled. Подвійне оголошення виходу не потрібне. Якщо сервер вимкнено, у згенерованому runtime config явно задається порожній networks.discovery, щоб не успадкувати налаштування іншого контексту. Якщо сервер увімкнено, список адрес обов'язковий. Збережений список адрес у node config можна залишити для повторного вмикання.

Каталог UI для входу має використовувати той самий ефективний NDI config, що й відповідна нода. Якщо discovery виконує окремий helper process, йому також задається цей NDI_CONFIG_DIR. Один глобальний finder не підходить для незалежних конфігурацій нод. Ця модель не потребує двох різних NDI-конфігурацій усередині одного процесу.

## Критерії приймання

- При ввімкненому сервері джерело, доступне лише через mDNS, і джерело, доступне лише через сервер, присутні в одному контексті пошуку входу; при вимкненому сервері контекст його не використовує.
- Те саме джерело не дублюється в UI; походження результату не вигадується, якщо finder його не повідомляє.
- Receive groups фільтрують пошук, send groups задають належність виходу; різні ноди не змінюють налаштування одна одної.
- Відмова сервера не вимикає доступний локальний discovery; каталог оновлюється після відновлення сервера.
- Перевірено всі чотири комбінації вмикання сервера на input/output; зміна конфігурації однієї ноди не впливає на іншу. Подвійне оголошення виходу не входить у вимоги.

Ці сценарії виконуються пізніше за планом, після каркаса системи. Поточна робота — аналіз і документування.
