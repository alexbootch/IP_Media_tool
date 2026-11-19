# Локальна розробка та ручне розгортання Converter

Оновлено: 2026-11-17. Windows використовується для коду, документації та Git. Користувач сам контролює commit, push, release, інсталяцію, оновлення й запуск на Linux. Локальні тести, збирання та виконання застосунку не проводяться.

## Редактор

Рекомендація для цього проєкту — VS Code: один folder workspace для Python, TypeScript/React, JSON, shell і systemd-файлів; вбудований Git interface. Visual Studio Community підтримує Python і теж може редагувати проєкт, але її IDE/workload setup зараз не дає потрібної переваги: Windows build/debug не входить у workflow.

Виявлено локально: VS Code 1.146.0 (`C:/Microsoft VS Code/Code.exe`), Git 2.54.0.windows.1. Git-репозиторій ініціалізовано в `C:/Converter` з початковою гілкою main, без комітів. Origin: https://github.com/alexbootch/IP_Media_tool.git. Для роботи Admin з checkout, створеним sandbox, у Git user config додано safe.directory лише для C:/Converter. Локальні параметри: core.autocrlf=false, core.safecrlf=warn, pull.ff=only. Список установлених editor extensions не підтверджений: CLI отримав відмову доступу до каталогу профілю. Project settings підготовлені незалежно від user/global settings; інші встановлені extensions потребують ручного огляду на automatic execution.

Відкрити VS Code -> File -> Open Folder -> C:/Converter. Рекомендовані extensions перелічені в `.vscode/extensions.json`: Python, Pylance, EditorConfig. Їх встановлення виконується окремою дією користувача. JavaScript/TypeScript/JSON і Git підтримуються редактором без додаткового GitHub extension. GitHub Pull Requests можна додати пізніше для review.

`.vscode/settings.json` вимикає Python pytest/unittest і auto discovery, automatic tasks, automatic environment activation та Git autofetch. Smart Commit вимкнений, Sync вимагає підтвердження. Format on Save й Auto Save вимкнені, щоб зміни залишалися видимими й керованими. Це налаштування проєкту, а не sandbox: сторонні extensions можуть мати свої тригери; не вмикати test runners, Code Runner, watch mode, automatic build/deploy.

Локальний Python, GStreamer, NDI runtime, Node/npm dependencies, compiler toolchains, Docker і WSL для роботи з цим workspace не потрібні. Без локального interpreter/runtime частина підказок щодо Linux/GI imports може бути недоступна; це не привід встановлювати медіастек у Windows. Pylance використовується лише для editor assistance, без виконання вихідного коду.

## Що дозволено локально

- Редагування source/config/schema/docs та написання test source для майбутнього виконання на сервері.
- Підсвічування синтаксису, навігація, читання й ручний review diff.
- Явні Git stage/commit/push/pull у Source Control або терміналі за рішенням користувача.
- Немає локальних pytest, import/compile перевірок, build, dev server, smoke tests, media pipelines чи прототипів. Review коду не позначається як пройдені runtime tests.

## Git із редактора

1. Відкрити Source Control (`Ctrl+Shift+G`), переглянути кожен файл і його diff. На першому коміті всі файли untracked, тому звичайний git diff ще не показує їхнього вмісту.
2. Stage лише перевірені файли. Переконатися, що немає credentials, runtime configs, logs, build artifacts чи локальних SDK.
3. Написати змістовне повідомлення і виконати Commit. Git identity вже задана у користувацькій конфігурації; за потреби користувач змінює її лише для цього репозиторію.
4. Підключити наданий GitHub URL як origin та вибрати потрібну автентифікацію. GitHub token не зберігати у remote URL чи tracked config.
5. Перший Push/Publish Branch виконати вручну. Створення нового remote repository, його visibility та публікація коду не здійснюються автоматично.
6. Подальші зміни: edit -> review -> stage -> commit -> push. Push не встановлює код на сервер і не публікує stable release автоматично.

Робоча гілка main; для окремих змін користувач може створювати feature branches і PR. Stable release має окрему версію/tag і відповідає конкретному commit. Відсутність локальних тестів не означає, що кожен commit придатний для stable.

Доступні лише ручні VS Code Tasks: Git status, Git diff і Git staged diff. Launch configurations, build/test/deploy tasks та Git hooks не додані.

## GitHub та Linux

Актуальна реалізація 2026-11-19: [WEB_AUTH.uk.md](WEB_AUTH.uk.md). Публічний main bootstrap встановлює web app, auth/users, admin CLI і systemd unit. Після першої інсталяції сервіс стартує лише за командою користувача; надалі його явний виклик ip-media-tool-update перезапускає вже активний web service для застосування нового коду. Main не потребує release після кожного push. Попередні припущення про обов'язковий build/release для кожного update не застосовуються до поточного frontend без build step.

Уточнення 2026-11-18: на Linux не використовуються Git clone/pull, токени або SSH deploy keys для інсталяції. Основний шлях — [публічні release assets та ip-media-tool-update](PUBLIC_INSTALL.uk.md). Git залишається тільки на Windows для публікації розробником; публічний release workflow запускається вручну. Старі рекомендації щодо Git-доступу сервера не є вимогою нового workflow.

```text
Windows / VS Code: код -> review -> commit -> push
                              |
                           GitHub
                   source / versioned release
                              |
Linux: користувач вручну встановлює версію, запускає перевірки
       і після приймання вручну запускає потрібні маршрути
```

Linux packages збираються у визначеному Linux build environment, не у локальному Windows workspace. Де саме виконується build (GitHub Actions чи виділене Linux build environment) визначається при реалізації packaging. Якщо використовуються GitHub Actions, release workflow запускається користувачем і не має SSH/secrets для доступу до production server. Автоматичне розгортання не створюється.

Installer/updater можуть автоматизувати кроки після ручного виклику команди, але не самостійно запускати серверні перевірки чи media routes. Первинну інсталяцію й update виконує користувач; перед запуском сервісів передбачити явний вибір. Після першої інсталяції маршрути не запускаються. При update стан маршрутів зберігається, але запуск медіаворкерів після maintenance також потребує явної дії користувача. Це вимога до майбутньої реалізації, installer/updater ще відсутні.

Користувач надав репозиторій [alexbootch/IP_Media_tool](https://github.com/alexbootch/IP_Media_tool). Origin підключений локально для fetch і push. Доступ, visibility та вміст віддаленого repository ще не перевірялися; fetch, commit і push не виконувалися. Перед першим push користувач переглядає файли, виконує stage/commit і перевіряє стан remote. Для приватного repository метод доступу server installer до Releases буде визначений окремо без додавання секретів у код.

## Джерела

- [VS Code source control](https://code.visualstudio.com/docs/sourcecontrol/overview).
- [Python testing settings](https://code.visualstudio.com/docs/python/settings-reference).
- [Visual Studio Python support](https://learn.microsoft.com/en-us/visualstudio/python/overview-of-python-tools-for-visual-studio).
