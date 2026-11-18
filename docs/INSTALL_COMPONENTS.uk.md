# Встановлення системних компонентів

Дата: 2026-11-18. Підготовлено `tools/install/install-components.sh` та перелік пакетів `packaging/ubuntu-24.04.packages`. Скрипт не запускався локально й ще не перевірений користувачем на Linux. Це підготовка залежностей, а не інсталяція самого застосунку Converter чи реалізація converter-update.

## Запуск користувачем на сервері

Спочатку користувач виконує commit/push нових файлів із Windows у GitHub. До публікації tools/install/install-components.sh і packaging/ubuntu-24.04.packages команди нижче не зможуть запустити інсталяцію. Один лише раніше опублікований репозиторій не містить нових локальних файлів.

Джерело: [репозиторій](https://github.com/alexbootch/IP_Media_tool). Після commit/push: [скрипт](https://github.com/alexbootch/IP_Media_tool/blob/main/tools/install/install-components.sh), [список пакетів](https://github.com/alexbootch/IP_Media_tool/blob/main/packaging/ubuntu-24.04.packages). Це поки код гілки main, не підписаний stable release installer.

На Ubuntu, якщо git ще не встановлений:

```bash
sudo apt-get update && sudo apt-get install --no-install-recommends git ca-certificates
```

Перше завантаження та ручний запуск із домашнього каталогу користувача:

```bash
cd "$HOME" &&
git clone https://github.com/alexbootch/IP_Media_tool.git &&
cd IP_Media_tool &&
sudo bash tools/install/install-components.sh
```

Якщо checkout уже є, спочатку перевірити його стан, потім отримати зміни та виконати скрипт:

```bash
cd "$HOME/IP_Media_tool" &&
git pull --ff-only &&
sudo bash tools/install/install-components.sh
```

Git не перезаписуватиме несумісні локальні зміни через force/reset. Для приватного repository потрібна GitHub authentication; секрети не включати в URL команди або tracked files. Завантажується checkout, бо скрипту потрібен сусідній файл зі списком пакетів; окремий raw shell-файл не є самодостатнім installer.

Дозволу executable не потрібно, бо використовується bash. Для окремих режимів із кореня checkout:

```bash
# Перевірка без APT install/update та змін системних конфігів:
bash tools/install/install-components.sh --check

# Встановлення відсутніх системних компонентів з підтвердженням APT:
sudo bash tools/install/install-components.sh

# Те саме з явним прийняттям запропонованої пакетної транзакції:
sudo bash tools/install/install-components.sh --yes
```

Поточний профіль — Ubuntu 24.04 LTS; інші дистрибутиви зупиняються з поясненням. Скрипт не прив'язаний до CPU, RAM чи конкретної моделі GPU/DeckLink. Перелік потрібних пакетів — штатний файл проєкту, без завантаження випадкових install scripts.

## Поведінка

1. Перевірити OS profile та наявність базових OS-команд. У режимі встановлення перевірити root і взяти installer lock.
2. За PCI vendor/class виявити NVIDIA GPU та DeckLink, перевірити kernel binding і loaded module. Для NVIDIA також перевірити nvidia-smi з timeout та записи libcuda/libnvidia-encode/libnvcuvid у linker cache. Версії порівнюються з inventory baseline Desktop Video 16.4a1 / NVIDIA 580.178.04; інша версія дає попередження, не автоматичну заміну.
3. Якщо пристрій є, а драйвер відсутній/не працює — показати інструкцію ручного встановлення й продовжити підготовку компонентів. Якщо пристрою немає, відповідний драйвер не є блокером.
4. Перевірити dpkg status кожного потрібного пакета. Для відсутніх — apt-get update, simulation, потім install без removals, без upgrade вже наявних цільових пакетів і без recommends. Якщо симуляція пропонує зміну driver/kernel/container packages — зупинитися до install.
5. Перевірити встановлені пакети, наявність потрібних GStreamer factories і доступ Python GI до Gst/GstWebRTC. Це metadata/import checks на сервері, не media pipelines.
6. Показати NDI readiness і підсумок; невирішені вимоги не маскуються повідомленням про повну готовність.

Пакети: Python/venv/GI, GStreamer tools/base/good/bad/ugly/libav/nice/gl та typelibs, Avahi daemon/libraries, базові curl/CA/pciutils/kmod. Нові репозиторії/PPA, Docker/Kubernetes, compiler toolchains, SDK і pip application packages не додаються. Оскільки application dependency lock ще не створений, Python-залежності майбутнього API цим скриптом не встановлюються.

Точні distro package versions беруться з налаштованих APT repositories і показуються у виводі; це bootstrap profile, а не вже зафіксований production release manifest. Скрипт не виконує apt upgrade, autoremove, зміну драйверів, modprobe, reboot, Git push, запуск Converter або media routes. Власні maintainer scripts пакетів Ubuntu можуть запускати відповідні системні служби, зокрема Avahi. Converter services ще не встановлюються, тому --no-start для цього скрипта не потрібний.

Перевірки gst-inspect використовують тимчасовий окремий registry у /tmp, щоб не змінювати кеш користувача/сервісу. Тимчасові файли видаляються при звичайному виході; --check не встановлює пакети й не змінює конфіги. Після SIGKILL або втрати живлення може залишитися лише цей temporary directory. Активні SDI/GPU encode pipelines скрипт не створює. Результат під root не доводить права майбутнього service account на пристрої.

## NDI та обсяг цього кроку

NDI runtime і gst-plugin-ndi не підміняються пакетами стандартного GStreamer. Перевіряються factories ndisrc/ndisrcdemux/ndisinkcombiner/ndisink та наявність libndi у linker cache або NDI_RUNTIME_DIR_V5/V6. Це не перевірка ABI чи фактичної роботи discovery/groups. Передавання цих environment variables через sudo потребує налаштування користувачем; скрипт не змінює sudoers.

Готові перевірені пакети NDI для Converter ще не підготовлені; їх автоматичне встановлення залишається наступним кроком packaging. Скрипт не збирає плагіни на сервері й не встановлює повний SDK. За відсутності NDI стандартний запуск повертає 2 навіть після успішного встановлення системних пакетів.

Для явної підготовки лише базових залежностей без вимоги NDI:

```bash
sudo bash tools/install/install-components.sh --base-only
bash tools/install/install-components.sh --check --base-only
```

У цьому режимі NDI залишається видимим як незавершений компонент, але не впливає на код завершення. Невирішені драйвери наявного заліза та відсутні базові компоненти продовжують впливати. OMT відкладено й не перевіряється.

## Коди завершення та ручне приймання

- 0 — вимоги обраного scope задоволені; це не заява про встановлений застосунок.
- 1 — unsupported profile, неправильні аргументи, недостатні права, APT/command failure або заборонена пакетна транзакція.
- 2 — після перевірки/інсталяції залишилися невирішені вимоги; їх перелік є у виводі.

На Linux користувач перевіряє: початковий --check, встановлення, повторний ідемпотентний запуск, відсутність драйвера, помилку APT та явний NDI incomplete result. Локальні тести чи виконання інсталятора не проводилися. У разі помилки перервати розгортання й надіслати вивід; скрипт не виконує небезпечне автоматичне видалення вже встановлених OS-пакетів як rollback.

Джерела для package profile: [Ubuntu GStreamer Bad packages](https://packages.ubuntu.com/en/noble/gir1.2-gst-plugins-bad-1.0), [GStreamer Bad file list](https://packages.ubuntu.com/noble/all/gstreamer1.0-plugins-bad/filelist), [NDI runtime loading](https://github.com/GStreamer/gst-plugins-rs/blob/main/net/ndi/README.md).
