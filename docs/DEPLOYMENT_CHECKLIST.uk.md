# Чеклист розгортання Converter

Оновлено: 2026-11-17. Вимоги для майбутнього ручного й автоматичного інсталятора. Інсталятор ще не реалізовано; поточні дії обмежуються документацією та аналізом залежностей.

Запуск інсталяції й оновлення на сервері завжди ручний, користувачем. Автоматизація стосується кроків усередині явно викликаної команди. Передбачити --no-start; core services і медіамаршрути запускаються лише за вибором користувача. Жодного auto-deploy із VS Code чи GitHub. Локальний Windows workspace не виконує tests/builds або медіакод.

## Платформа

- [ ] Визначити підтримуваний Linux runtime profile та архітектуру пакета. Поточний перевірений на рівні inventory хост: Ubuntu 24.04.5 LTS, x86_64, ядро 6.8.0-147-generic.
- [ ] Не прив'язувати продукт до конкретних CPU, кількості RAM, моделі GPU чи DeckLink. Ресурсні обмеження впливають на доступні функції та місткість маршрутів; це не вимога повторити тестовий сервер.
- [ ] Не встановлювати Kubernetes, NVIDIA Device Plugin або контейнерний runtime як залежності нативного Converter. Існуючі залишки не змінювати без конкретного конфлікту.

## Драйвери для встановлених пристроїв

| Компонент | Зафіксована версія на поточному хості | Перевірка при розгортанні |
| --- | --- | --- |
| Blackmagic Desktop Video | 16.4a1 | Пакет, kernel module version/vermagic, DKMS для поточного ядра, binding пристрою та доступ користувача сервісу |
| NVIDIA driver | 580.178.04 | Узгоджені kernel/userspace versions, loaded module, nvidia-smi, encode/decode libraries та доступ користувача сервісу |

Це вихідні версії профілю сумісності, а не вже сертифікований медіастек: SDI/GStreamer та NVENC/NVDEC runtime tests ще попереду. Інсталятор звіряє версію з manifest конкретного релізу та показує невідповідність. Автоматичний downgrade або заміну драйверів не виконувати як побічну дію встановлення Python/GStreamer. Встановлення/оновлення драйверів має бути явним кроком розгортання з урахуванням пристрою й ядра. Відсутність конкретного пристрою вимикає відповідні можливості, а не весь продукт.

## Обов'язкові компоненти інсталяції

- [ ] Первинна інсталяція з GitHub Releases: stable або конкретна версія, перевірений installer/package/manifest; [вимоги до installer/updater](INSTALL_UPDATE.uk.md).
- [ ] Встановити converter-update: перевірка нового stable, оновлення, встановлення конкретної версії та rollback зі збереженням конфігів.

- [ ] Python потрібної версії, virtual environment та зафіксовані Python-залежності Converter.
- [ ] GStreamer runtime/tools, PyGObject/GI, typelibs і потрібні медіаплагіни відповідно до release manifest.
- [ ] SDI, SRT, WebRTC, SHM і video/audio processing factories, передбачені релізом; GPU factories відповідно до доступних пристроїв.
- [ ] NDI GStreamer plugin і сумісна NDI runtime-бібліотека. Runtime постачається/встановлюється згідно з його ліцензією; discovery і send/receive groups входять у критерії приймання інтеграції.
- OMT plugin та його бібліотеки відкладені й не входять у поточну інсталяцію. Власний plugin не розробляємо.
- [ ] Avahi runtime libraries і avahi-daemon для NDI на Linux; per-worker NDI_CONFIG_DIR до ініціалізації бібліотеки. Деталі: [NDI discovery](NDI_DISCOVERY.uk.md).
- [ ] Встановити systemd units, конфігураційні каталоги, права доступу й release manifest.
- [ ] Після інсталяції перевірити фактичні версії та наявність потрібних factories/library loading. Перевірка встановлення не запускає експериментальних медіатрактів.

Обов'язкова інсталяція означає, що інсталятор забезпечує потрібну версію кожного компонента на чистому або частково підготовленому хості. Уже встановлений сумісний компонент задовольняє вимогу; дублювати його чи примусово перевстановлювати без причини не потрібно.

## Розділення build та runtime

Збирання плагінів, compiler toolchains і SDK headers належать до build environment. На цільовий сервер потрапляють готові плагіни та потрібні їм shared libraries/runtime assets. Повний SDK або окремий .NET/Python media bridge не є базовою вимогою до сервера. Точний склад визначається обраною реалізацією плагіна; GStreamer wrapper не усуває його бібліотечні залежності.

## Відкриті рішення

- [ ] Зафіксувати точні Python/GStreamer/plugin versions у release manifest після перевірки сумісності.
- [ ] Для NDI перевірити runtime configuration discovery/groups, незалежне вмикання сервера для input/output, одночасний mDNS + server пошук входу при ввімкненому сервері та ізоляцію worker processes. Подвійне оголошення виходу не потрібне.
- OMT не є блокером поточного розгортання; повертатися до нього після появи/вибору придатного готового plugin.

Джерела: [NDI plugin README](https://github.com/GStreamer/gst-plugins-rs/blob/main/net/ndi/README.md), [NDI factories](https://gstreamer.freedesktop.org/documentation/ndi/), [обговорення OMT plugin](https://discourse.gstreamer.org/t/omt-open-media-transport-src-sink-plugin/5586). Перевірено 2026-11-17. Обговорення OMT доводить наявність розробки, але не готовність підтримуваного release package.
