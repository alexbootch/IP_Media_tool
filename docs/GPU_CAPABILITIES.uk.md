# GPU та можливості апаратних енкодерів

Дата: 2026-11-19. Написано backend discovery і GPU-панель у шапці. Джерело підходу — C:/StreamMachine/hardware/encoder_capabilities.py та frontend/src/components/EncoderCapabilitiesPanel.tsx. Код, detector, browser preview і тести на Windows не запускалися. Серверне приймання проводить користувач.

## Інтерфейс

Після входу шапка показує модель GPU та кодеки, оголошені GStreamer для цієї карти. Натискання розгортає панель із картами, драйвером, PCI, VRAM і CUDA ID, профілями енкодерів, форматами, межами caps, 10-bit, 4:2:2/4:4:4, interlace, Temporal AQ, B-frame/lookahead limits та rate-control enums.

Так/ні/невідомо розрізняються. «Не надано GStreamer» не означає «чип не підтримує». Глобальні межі width/height — підсумок caps, не гарантія будь-якого їх поєднання для кожного формату. Сирі per-format/per-memory caps доступні в деталях і в API для подальшої перевірки media routes. Максимальні fps, вільні NVENC sessions і реальна пропускна здатність не виводяться як вигадані гарантії. NVDEC/decode ще не перевіряється.

Перегляд доступний усім авторизованим ролям. Кнопка «Перевірити знову» — admin/operator; POST захищений CSRF і перевіркою ролі, записується в audit. Viewer не запускає probes.

## Детекція

- hardware/encoder_capabilities.py — фоновий single-flight manager, cache, snapshot API, timeout і shutdown cleanup.
- hardware/gpu/probe.py — disposable child process для PCI/NVML/CUDA/GStreamer. Web process не імпортує GI й не ініціалізує CUDA.
- PCI inventory включає лише display/3D controllers NVIDIA (PCI vendor ID 0x10de). ASPEED, Intel, AMD та інші виробники не потрапляють до результатів детектора, API та GPU-панелі. Encoder-перевірка реалізована для NVIDIA NVENC. Зміна коду детектора змінює fingerprint, тому попередній кеш із GPU інших виробників автоматично замінюється після оновлення.
- nvidia-smi надає UUID, модель, PCI, драйвер і total VRAM. CUDA Driver API зіставляє CUDA ordinal із UUID та PCI. Nvidia-smi index не прирівнюється до CUDA ordinal, навіть на multi-GPU хості.
- Параметри зчитуються зі структурованих caps/properties конкретних nvcodec encoder factories з read-only cuda-device-id. Auto-GPU та selectable-device factories не використовуються як доказ per-device можливостей. Не виконується кодування тестових кадрів і не створюється media pipeline.
- GStreamer/plugin initialization може короткочасно створювати driver contexts/session probes для отримання caps. Наявність енкодера не доводить, що в момент запуску нового потоку є вільні ресурси.

Цей перенос не потребує C++ compiler або стороннього executable на сервері: NVIDIA runtime і встановлений GStreamer nvcodec самі відкривають effective capabilities. На відміну від повного StreamMachine native helper, окремі hardware-only NVENC flags, які GStreamer не відкриває, тут залишаються unknown (native_hardware_only=null). Підтримку raw hardware-only capabilities можна додати окремим provider пізніше, без фальшивих false у поточному API.

## Кеш і відмови

Кеш: /var/lib/ip-media-tool/hardware/encoder-capabilities.json, доступний лише service user/root. Запис через temporary file, fsync та atomic replace. Fingerprint містить GPU identity/driver, OS/Python/packages, nvcodec plugin і driver library file signatures, relevant CUDA/GStreamer environment, supplementary groups, device permissions та hash probe source.

На старті manager асинхронно перевіряє fingerprint і повторно використовує лише сумісний успішний кеш. Query failures не кешуються як підтверджена відсутність GPU. Force refresh повторно отримує параметри. При помилці збережені попередні результати позначаються stale. Нові route consumers у майбутньому не повинні використовувати stale/failed snapshots для admission.

Inventory timeout — 25 s, full query — 60 s. Child process має окрему process group; timeout/shutdown прибирає її. JSON output обмежено до 2 MB при прийманні, stderr діагностика обмежена. Registry тимчасовий і не змінює GStreamer cache користувача. UI опитує швидкий snapshot API лише доки query_running=true; GET не запускає нові probes.

## API і розгортання

- GET /api/hardware/encoder-capabilities — snapshot для авторизованого користувача.
- POST /api/hardware/encoder-capabilities/refresh — background refresh для admin/operator.
- Installer створює writable hardware cache directory і додає service user до video/render тільки якщо такі groups уже існують. Device permissions, драйвери та їхні налаштування не переписуються.
- systemd дозволяє write до security та hardware directories; GStreamer/CUDA query виконується як ip-media-tool, не root.

Після manual commit/push на Windows:

```bash
ip-media-tool-update
```

Для вже активного сервісу updater застосує code/unit/group changes через restart. Для зупиненого користувач окремо виконує sudo systemctl start ip-media-tool. Перезапуск ОС не потрібний для самого оновлення коду; driver problems діагностуються окремо.

## Ручне серверне приймання

Перевірити назву GPU, PCI/UUID association, codecs/profiles і формати фактичного nvcodec, cache hit після restart, force refresh, service-user permissions, absent/broken driver, unavailable GI, timeout, multi-GPU mapping та відмову viewer на refresh. Окремо прийняти layout шапки desktop/mobile. Серверні результати й візуальний preview ще не отримані.

Першоджерела: [GStreamer nvh264enc](https://gstreamer.freedesktop.org/documentation/nvcodec/nvh264enc.html), [nvh265enc](https://gstreamer.freedesktop.org/documentation/nvcodec/nvh265enc.html), [GStreamer 1.24 NVENC device property](https://github.com/GStreamer/gstreamer/blob/1.24/subprojects/gst-plugins-bad/sys/nvcodec/gstnvbaseenc.c), [hardware-derived class properties](https://github.com/GStreamer/gstreamer/blob/1.24/subprojects/gst-plugins-bad/sys/nvcodec/gstnvh264enc.c).
