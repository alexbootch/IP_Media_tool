# Інсталяція та оновлення через GitHub

Актуальний спосіб доставки від 2026-11-18: [публічний install.sh та application-update](PUBLIC_INSTALL.uk.md). Код bootstrap/updater і ручного release workflow підготовлено, але реліз ще не опублікований. На сервері використовується public release download без Git login. Нижче описані також майбутні application-level вимоги: підписані bundles, конфіг-міграції, health checks і rollback; вони ще не реалізовані в bootstrap системних залежностей.

Оновлено: 2026-11-18. Репозиторій GitHub: [alexbootch/IP_Media_tool](https://github.com/alexbootch/IP_Media_tool), підключений локально як origin. Код installer/updater для системних залежностей та ручного release workflow підготовлений. Публікація й перевірка користувачем на Linux ще потрібні.

Режим роботи: код редагується на Windows без локальних тестів чи builds. Commit/push, запуск release build і публікація версії виконуються за дією користувача. На Linux користувач сам викликає installer/updater, обирає запуск сервісів і вручну запускає перевірки та маршрути. Пряме відвантаження на сервер із редактора, SSH-deploy pipeline та фонова інсталяція відсутні. Деталі — у [локальному workflow](LOCAL_DEVELOPMENT.uk.md).

## Канал постачання

GitHub Releases — джерело версійних пакетів Converter. Встановлюється готовий реліз з зафіксованим runtime profile; розробницька гілка не використовується як інсталяційний пакет. Канал stable містить лише опубліковані, перевірені релізи без draft/prerelease. Latest stable визначається release policy проєкту; перед завантаженням вибір фіксується в конкретний tag/version та digest, щоб один запуск не змішав різні версії.

Release assets: готовий application/runtime package для підтримуваного Linux profile/architecture, installer, manifest, checksums, signature та dependency BOM. Manifest описує версії Python/GStreamer/плагінів і бібліотек, driver compatibility, config schema та шлях міграції. Сторонні бібліотеки можуть постачатися з пакетом або встановлюватися з визначеного джерела згідно з ліцензією; GitHub не обов'язково є джерелом кожного OS-пакета.

Підпис перевіряється за довіреним ключем/ідентичністю, закріпленими незалежно від завантаженого пакета. Checksum із того самого релізу сам по собі не є перевіркою авторства. Bootstrap-доставку installer та початкової довіри описати в інструкції встановлення; не покладатися лише на підпис, який перевіряє неперевірений завантажений скрипт.

## Первинна інсталяція

Перший підготовлений крок — [скрипт системних компонентів](INSTALL_COMPONENTS.uk.md). Він встановлює базові Ubuntu-залежності та діагностує драйвери/NDI; повний release installer і автоматичне встановлення NDI-пакетів ще не готові.

Інтерфейс після публікації першого public release:

```text
curl -fL https://github.com/alexbootch/IP_Media_tool/releases/latest/download/install.sh -o /tmp/ip-media-install.sh && sudo bash /tmp/ip-media-install.sh
```

1. Перевірити Linux profile/architecture, права, диск, блокування паралельної інсталяції та поточний стан. CPU/RAM конкретного тестового сервера не є шаблоном вимог до машини.
2. Вибрати та зафіксувати release, завантажити й перевірити manifest/package.
3. Забезпечити обов'язкові Python, GStreamer/GI, медіаплагіни, NDI runtime і Avahi потрібних версій згідно з [чеклистом](DEPLOYMENT_CHECKLIST.uk.md). Build toolchain і повний SDK не встановлювати як runtime-залежності.
4. Перевірити драйвери наявних пристроїв за release manifest. Вихідні версії: Desktop Video 16.4a1, NVIDIA 580.178.04. Заміна драйверів і reboot — окремий явний крок, не прихована частина оновлення застосунку.
5. Розпакувати реліз у `/opt/converter/releases/<version>/`, створити service account, конфігураційні каталоги, права та systemd units. Зберегти вже наявні конфіги й секрети; повторний запуск installer має бути ідемпотентним.
6. Перевірити runtime/library loading і потрібні GStreamer factories; активувати реліз через current pointer. Запуск core services є явним вибором користувача; --no-start залишає їх зупиненими. Провести захищений bootstrap адміністратора при запуску control service, не розміщувати паролі в аргументах CLI чи release assets. Медіамаршрути після першої інсталяції автоматично не запускати.
7. Записати install report: версія, hashes, dependencies, driver checks, результат і відкладені дії. Якщо установка зупинилася, показати точний стан і спосіб продовження; не видаляти спільні OS-пакети автоматично як cleanup.

## Оновлення

Запланований інтерфейс:

```text
application-update --check
application-update
application-update --version v0.1.0
```

Виклик без version використовує останній опублікований release. Поточний updater готує системні компоненти; --rollback і --no-start для нього не реалізовані, застосунок і його сервіси не встановлюються. Нижче — майбутній application-level порядок. Автоматичний режим означає виконання процедури командою без ручного копіювання файлів; фонові оновлення за розкладом не входять до вимог.

Порядок: lock -> resolve release -> download/verify -> compatibility/preflight -> prepare version directory -> backup configs/secrets із захищеними правами -> контрольована зупинка потрібних services/workers -> config migration -> atomic current switch -> restart core services за явним вибором користувача -> bounded health checks -> commit installed state. Відновлення медіамаршрутів після maintenance виконується окремою дією користувача. При --no-start зберігається стан pending validation; runtime health checks і завершення приймання виконуються після ручного запуску, без неправдивої позначки успішного runtime validation.

До зупинки workloads підготувати всі можливі завантаження й перевірки. Якщо release потребує несумісної зміни спільних OS-залежностей, драйвера чи reboot, updater не починає звичайне перемикання: повертає maintenance requirement. Для залежностей, які змінюються в межах звичайного application update, потрібно забезпечити сумісне співіснування попереднього runtime для rollback.

При помилці повернути попередній release pointer, сумісний snapshot конфігів та попередній стан запуску. Вести recovery journal для обриву живлення або процесу між кроками. Зберігати принаймні один попередній робочий release; cleanup старих версій виконується після успішного commit. Відкат не повинен залишати старий код із новою несумісною схемою конфігів.

Конфіги нод, координати, connections, користувачі, секрети та налаштування NDI зберігаються між релізами. Оновлення може переривати сигнал; безперервність не є гарантією першої версії. Встановлена система працює без постійного доступу до GitHub.

## Критерії приймання

- Чистий підтримуваний Linux і повторна інсталяція на частково підготовлений хост.
- Latest stable та явно задана версія дають відтворюваний склад компонентів.
- Неправильний підпис/hash, непідтримуваний profile, недостатній диск і network failure не перемикають активний реліз.
- Успішне оновлення, помилка міграції, failed health check, interruption та явний rollback перевірені.
- Збереження node configs/secrets і незалежних NDI settings перевірено після update/rollback.
- Жодних змін Kubernetes-залишків, непомітних driver upgrades чи збирання експериментальних плагінів на production host.
