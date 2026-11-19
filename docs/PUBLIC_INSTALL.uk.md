# Публічна інсталяція та application-update

Оновлено: 2026-11-18. Це основний спосіб інсталяції. Git clone/pull, GitHub login, PAT та deploy keys на Ubuntu більше не потрібні. Windows Git залишається інструментом розробки й публікації коду під контролем користувача.

## Інтерфейс для користувача Ubuntu

Після публікації першого релізу в публічному alexbootch/IP_Media_tool:

```bash
curl -fL https://github.com/alexbootch/IP_Media_tool/releases/latest/download/install.sh -o /tmp/ip-media-install.sh && sudo bash /tmp/ip-media-install.sh
```

Файл завантажується повністю до запуску; при помилці HTTP виконання не починається. На поточному хості curl уже входив до встановлених компонентів. Якщо його немає на чистому Ubuntu, інсталятор можна завантажити на ПК через браузер і передати файлом; після запуску він забезпечує curl/CA certificates. Не додавати token до URL.

Далі достатньо:

```bash
application-update
```

Для встановлення OS-пакетів може знадобитися локальний sudo password. Це підвищення прав на Ubuntu, а не GitHub authentication. Окремий запуск на сервері користувачем залишається обов'язковим; фонових update jobs немає.

Додатково доступні application-update --check, --version vX.Y.Z та --base-only. Останній прапорець явно обирає тільки системні залежності й не приховує, що NDI ще не готовий.

## Що реалізовано зараз

- install.sh — standalone release bootstrap. Source template містить placeholders і не призначений для прямого виконання; workflow підставляє repository і конкретний release tag.
- tools/install/application-update.sh — updater без Git, після інсталяції розміщується в /usr/local/sbin/application-update.
- tools/install/install-components.sh — перевірка драйверів і встановлення системних Python/GStreamer/Avahi packages.
- .github/workflows/publish-installer.yml — тільки ручний workflow_dispatch; складає й публікує assets, але не запускає installer/tests чи server deployment.

Bootstrap і updater завантажують останній release installer, а він прив'язаний до конкретного tag та завантажує решту файлів саме з цього релізу. SHA256SUMS перевіряє узгодженість файлів; це не незалежний цифровий підпис. Початкова довіра — GitHub HTTPS і контроль доступу до публікації. Підписані application bundles та повний application rollback залишаються майбутнім етапом, їх наявність не заявляється.

Версійні bootstrap-файли зберігаються у /opt/converter/bootstrap/releases/<tag>, результат останньої підготовки — /var/lib/converter/bootstrap-release. Updater встановлюється навіть якщо потім driver/NDI checks повернули 2: після ручного усунення проблеми користувач повторює application-update. Новий реліз обробляє новий список системних залежностей; скрипти не встановлюють і не замінюють драйвери. Відкат змін OS-пакетів не реалізований.

Поки це bootstrap системних компонентів, а не готовий медіаконвертор. Програма Converter, її systemd units, готові NDI packages, конфіг-міграції й application health checks ще не реалізовані. Сервіси Converter і media routes не запускаються. Неповна готовність повертає код 2, а не маскується успішною інсталяцією всього продукту.

## Одноразова публікація розробником

1. Публічні assets — обов'язкова умова завантаження без авторизації. Якщо source repository приватний, або зробити його Public за рішенням власника, або тримати installer/release files у окремому публічному distribution repository. Налаштування visibility агент не змінює. Releases приватного repository не стають публічними самі по собі.
2. Переглянути зміни у VS Code, вручну виконати commit/push. До GitHub мають потрапити install.sh, workflow, application-update.sh, install-components.sh і ubuntu-24.04.packages.
3. Відкрити Actions -> Publish prerequisite installer -> Run workflow, указати новий tag, наприклад v0.1.0. Workflow відмовляється публікувати з приватного repository, перевикористовувати наявний tag або замінювати існуючий реліз.
4. Переконатися у Release assets: install.sh, application-update.sh, install-components.sh, ubuntu-24.04.packages, SHA256SUMS. Для іншого публічного repository URL у команді відповідно змінюється.
5. Перевірити доступність install.sh без GitHub login і виконати команду на Ubuntu вручну. Runtime приймання здійснює користувач; локальні тести/скрипти не запускалися.

До публікації першого релізу вказаний URL поверне 404. Публікацію, push і зміну visibility агент не виконував. Публікація GitHub release не запускає нічого на Ubuntu.
