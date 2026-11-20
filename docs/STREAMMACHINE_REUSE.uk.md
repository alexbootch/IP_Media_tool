# Перенесення компонентів StreamMachine

Дата аудиту: 2026-10-30. Джерело: C:/StreamMachine. Виконано огляд коду; runtime на Linux у межах цієї підготовки не перевірявся. Код ще не скопійований.

| Файли у StreamMachine | Що використати | Що змінити або перевірити |
| --- | --- | --- |
| hardware/detector.py, decklink_scan_process.py | Disposable subprocess, timeout, stdout JSON, stderr diagnostics | Новий namespace/path/env; bounded output; crash, timeout і malformed payload tests |
| hardware/decklink.py, models.py, manager.py | Gst.DeviceMonitor, input/output mode inventory, snapshot model | Стабільна identity замість decklink:index; unknown fallback identity явно позначати; subdevices/profile/duplex не виводити лише з наявності двох напрямків |
| hardware/decklink_mode_catalog.py, decklink_modes.py | Нормалізація назв і exact fractional modes | Перевіряти реальні device caps; статичний список не є переліком можливостей карти |
| hardware/encoder_capabilities.py | Adapter identity, background query, cache fingerprint, native/Gst caps merge | Прибрати DMF paths; перевірити cache invalidation при зміні plugin/runtime, per-device Gst binding і unknown states; textual caps matching замінити/перевірити структурним |
| hardware/native/nvenc_capabilities.cpp, build_nvenc_capabilities.sh | Native NVENC capability query | Відтворювана Linux-збірка, ABI/driver compatibility, licenses, bounded subprocess і malformed results |
| workers/sdi_source | Capture/pipeline напрацювання | Контракт нового SDK, actual caps, loss/recovery, багатоканальний звук і часові мітки |
| workers/srt_receiver, workers/srt_sender | SRT session lifecycle та pipeline experience | Відокремити від NATS/SHM старого graph model; новий output conversion profile, secret handling |
| workers/webrtc_monitor_v2 | Signaling, session lifecycle, audio/video monitoring | Розмежувати output media і preview; ізоляція сесій, нові auth/contract/time model; це не готовий WebRTC input |
| shared/media_contract.py | Ідеї опису caps/endpoints/generation | Новий transport-neutral versioned contract, instance identity, stream list і resync |
| frontend/src | Досвід React Flow та спеціальних node controls | Зберігання у node configs, нові auth/API, capabilities-driven forms |

## Обмеження перенесення

Стан переносу DeckLink 2026-11-20: адаптовано підхід decklink.py і decklink_scan_process.py у hardware/decklink/probe.py та hardware/decklink_capabilities.py. Опитується лише DeckLink provider в окремому процесі; є API, header panel, кеш попереднього результату й роздільні списки режимів. Кілька однакових моделей не об'єднуються за назвою, невідомий serial не замінюється вигаданою ідентичністю плати. Статичний каталог режимів не використовується як доказ підтримки. Див. DECKLINK_CAPABILITIES.uk.md; локально код не виконувався.

Стан переносу 2026-11-19: у Converter написано hardware/encoder_capabilities.py і hardware/gpu/probe.py та panel у шапці. Використано background/cache/per-adapter підхід із перевіркою UUID/PCI/CUDA identity; GI перенесений у disposable subprocess. Native helper не є runtime-залежністю: effective flags/limits беруться зі структурованих GStreamer caps/properties. Hardware-only NVENC flags без GStreamer exposure не оголошуються як перевірені. Див. GPU_CAPABILITIES.uk.md; локально код не виконувався.

- Не переносити SQLite graph store, старі NATS contracts, MXL/NMOS/Kubernetes як обов'язкові залежності нового ядра.
- Не переносити весь controller/main.py як основу: виділити потрібні механізми зі стабільними межами.
- Не називати NVIDIA encoder probe універсальним GPU-detector: decode caps і AMD/Intel providers потребують окремої реалізації.
- Перевірити provenance і ліцензії перед включенням у release; зафіксувати вихідну ревізію або hashes, оскільки checkout може містити локальні зміни.
- Unit/fixture тести доводять парсинг і політики; реальні SDI modes, GPU encode та синхронізація приймаються лише після Linux hardware tests.

## Умови приймання hardware шару

1. Без GPU/DeckLink система запускається й показує пояснення; probe failure відрізняється від порожнього inventory.
2. Crash/hang сканера не завершує API/runtime; останній snapshot позначений stale, а не підтверджений як актуальний.
3. Перестановка enumeration indices не змінює прив'язку нод до реального пристрою.
4. UI не пропонує непідтримувані codec/profile/pixel format/mode; невідомі можливості мають пояснення.
5. Перевірено два concurrent GPU workers, exclusive SDI ownership та profile conflict handling.
6. Cache інвалідується за зміни driver/runtime/helper/plugin fingerprint; probes не порушують активні потоки.
