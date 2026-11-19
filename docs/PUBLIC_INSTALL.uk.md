# Публічна інсталяція та ip-media-tool-update

Оновлено: 2026-11-19. Основний канал розробки — main. На Ubuntu не потрібні Git, GitHub account, PAT або SSH key. Вихідна main гілка має бути публічною.

## Перше встановлення або перехід зі старого release-only updater

Після ручного commit/push розробником:

```bash
curl -fL https://raw.githubusercontent.com/alexbootch/IP_Media_tool/main/install.sh -o /tmp/ip-media-tool-install.sh && sudo bash /tmp/ip-media-tool-install.sh
```

Installer отримує конкретний commit SHA з GitHub API і завантажує його archive. Склад файлів одного запуску не змішується з наступними push у main. Python/GStreamer/Avahi встановлюються через Ubuntu APT, драйвери перевіряються без їх заміни.

Встановлюються web application code, systemd unit, ip-media-tool-admin і ip-media-tool-update. Перший admin створюється вручну, web service спочатку не запускається й не вмикається на reboot. Команди першого запуску — у [WEB_AUTH.uk.md](WEB_AUTH.uk.md).

## Наступні оновлення

```bash
ip-media-tool-update
```

Default — актуальний commit main після push, без створення release. За потреби запитується локальний sudo password, не GitHub authentication. Уже запущений web service перезапускається в межах ручного update, зупинений залишається зупиненим. Акаунти, сесії та серверні налаштування зберігаються поза code directory.

Додаткові режими: --check (лише порівняння SHA), --channel stable, --version vX.Y.Z, --base-only (OS prerequisites/updater без встановлення web app). Stable tag має містити новий application installer; старі prerequisite-only tags не підходять.

## Зберігання й межі

Code revisions: /opt/ip-media-tool/releases/<sha>; active pointer: /opt/ip-media-tool/current; встановлений SHA: /var/lib/ip-media-tool/installed-revision. Користувачі: /var/lib/ip-media-tool/security; environment: /etc/ip-media-tool/environment.

Перед перемиканням code pointer перевіряється наявність web packages. Якщо явний restart повертає помилку, installer намагається повернути попередній code pointer. Повні application health checks, config migrations, rollback OS packages і підписані production bundles ще не реалізовані. Локально код не виконувався; runtime приймання проводить користувач на Linux.

Main доставка покладається на GitHub HTTPS та контроль commit/push. Source archive не має незалежного підпису. NDI packages і медіаноди ще не готові; web installation незалежна від них. Жодних медіапайплайнів installer не запускає.

## Опціональні релізи

Власник може вручну запускати workflow Publish IP Media Tool release після серверного приймання. Це не потрібно для звичайного main update й не запускає deployment на Ubuntu. Visibility, commit, push і release publication залишаються діями власника.
