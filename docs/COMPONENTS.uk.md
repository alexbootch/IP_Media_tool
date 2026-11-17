# Компоненти та залежності Converter

Оновлено: 2026-11-17. Це перелік для вибору версій та обов'язкової інсталяції залежностей. Python, GStreamer/GI і медіапакети забезпечуються інсталятором незалежно від їх початкової наявності на сервері. CPU/RAM поточного хоста не визначають архітектуру продукту. Драйвери та перевірки зафіксовано у [чеклисті розгортання](DEPLOYMENT_CHECKLIST.uk.md).

## Платформа

| Елемент | Призначення та умова |
| --- | --- |
| Linux LTS + systemd | Нативний runtime, service lifecycle, watchdog, journald; дистрибутив і версію зафіксувати після SDK compatibility check |
| Python + virtual environment | Control/runtime, конфіги, API, SDK воркерів; minor version фіксується |
| GStreamer + PyGObject/GI | Pipeline orchestration без передачі кадрів через Python за замовчуванням |
| GStreamer core/base/good/bad/ugly/libav | Вибір конкретних пакетів за переліком потрібних factories та ліцензіями |
| React + TypeScript + React Flow | Кандидат для браузерного графа; Node потрібен на build host, не обов'язково на сервері |
| FastAPI + ASGI + validation library | Кандидат control API; точні версії та file-backed session design визначити на етапі core |
| TLS reverse proxy | HTTPS та WebSocket/event forwarding; кандидат Caddy/nginx |
| Unix domain sockets | Кандидат локального control IPC без обов'язкової DB чи message broker |
| JSON Schema | Node config, manifests, media contract, IPC, release manifest |

## Медіаелементи

Factories нижче — кандидати для gst-inspect на цільовому хості, не гарантія доступності чи сумісності властивостей.

| Група | Кандидати / роботи |
| --- | --- |
| Test source | videotestsrc, audiotestsrc; багатоканальні ідентифіковані тестові сигнали |
| SHM | shmsink, shmsrc; перевірка metadata/time/late join; GDP як окремий дослід, якщо потрібно |
| Video conversion | videoconvert, videoscale, videorate, deinterlace, interlace, capsfilter, queue; field order і fractional fps перевіряються на реальних режимах |
| Audio | queues і timing; audioconvert/audioresample лише за вимогою виходу, без неявного remix |
| SDI | decklinkvideosrc/decklinkaudiosrc/decklinkvideosink/decklinkaudiosink, Desktop Video driver і SDK за потреби |
| SRT | srtsrc/srtsink, MPEG-TS demux/mux, parsers, decode/encode; caller/listener, latency, streamid та secret references |
| WebRTC | webrtcbin або перевірений wrapper, codec/payloader support, signaling, ICE/STUN/TURN; input ingestion і output viewing окремі сценарії |
| NDI | gst-plugin-ndi: ndisrc, ndisrcdemux, ndisinkcombiner, ndisink і discovery provider; потрібна динамічно завантажувана NDI runtime-бібліотека. Повний SDK на сервері не є базовою вимогою; перевірити groups/discovery та ABI |
| OMT | Відкладено. Не входить у поточну інсталяцію; власний plugin не розробляємо. Майбутня інтеграція лише з придатним готовим plugin |
| GPU | NVIDIA driver + nvcodec factories та native NVENC probe; NVDEC й інші vendors окремими задачами |

Не вважати NDI HX і всі його кодеки включеними автоматично: підтримувані NDI режими зафіксувати після SDK/plugin тесту. Для WebRTC визначити standalone signaling чи WHIP/WHEP після перевірки потрібних клієнтів. Для SRT перший кандидат payload — MPEG-TS, інші payloads не обіцяються автоматично.

## Необхідні програмні модулі

- Config store: один writer, schema/revision checks, atomic replace, backup, migration, import/export, multi-file recovery.
- Graph resolver: читання node configs, перевірка портів, unresolved references, fan-out та відновлення UI.
- Worker SDK: readiness, commands/events, heartbeat, metrics, contract publication/subscription, graceful shutdown.
- Supervisor: resource ownership, restart backoff/limit, process isolation, stale endpoint cleanup, startup ordering.
- Media contract registry: snapshot + events, revision/generation checks, lost signal та reconnect.
- SHM transport: bounded fan-out, timing metadata, consistent format transition, memory accounting.
- Hardware inventory: DeckLink/GPU/NIC, provenance capabilities, cache fingerprint, rescan, reservations.
- Discovery: NDI runtime contexts/groups/servers, спільний mDNS + server пошук, catalogue deduplication, stale entries і retry. Рівень налаштувань і обмеження sender описано у [NDI discovery](NDI_DISCOVERY.uk.md). OMT discovery відкладено.
- Web API/UI: graph, node settings, source browser, capabilities-driven controls, telemetry, logs, users.
- Security: bootstrap admin, password hashing, sessions, RBAC, CSRF defense за обраною session model, secret storage.
- Observability: input frame age/rate, drops, queue levels, A/V drift, worker restarts, device/GPU/RAM/NIC load, audit і log rotation.
- Release tools: lockfile/runtime manifest, signed release, offline diagnostics bundle, update/rollback CLI.

## Офіційні джерела та наслідки

- [NDI plugin README](https://github.com/GStreamer/gst-plugins-rs/blob/main/net/ndi/README.md): plugin підтримує receive/send/discovery та завантажує NDI library під час роботи. Потрібний runtime не усувається наявністю GStreamer plugin.
- [OMT plugin development](https://discourse.gstreamer.org/t/omt-open-media-transport-src-sink-plugin/5586): розробник описує src/sink plugin і source proof of concept; це не підтверджує готовий стабільний Linux package.

- [NDI configuration](https://docs.ndi.video/all/developing-with-ndi/sdk/configuration-files): є send/recv groups і список discovery servers; sender та receiver поводяться по-різному щодо mDNS. Per-instance settings документовано для Advanced SDK, тому доступність способу налаштування у вибраному SDK треба перевірити.
- [OMT Discovery Server](https://github.com/openmediatransport/OMTDiscoveryServer): окремий TCP discovery; документовано process configuration до викликів API. Налаштований sender публікується на сервері, receiver використовує також DNS-SD. Підтвердити це для обраного Linux binding.
- [OMT libomtnet](https://github.com/openmediatransport/libomtnet): cross-platform .NET library залежить від native libvmx для конкретної платформи. Наявність документації Linux не означає готового перевіреного GStreamer-пакета.
- [GStreamer SHM](https://gstreamer.freedesktop.org/documentation/shm/index.html), [shmsrc](https://gstreamer.freedesktop.org/documentation/shm/shmsrc.html): socket-path визначає керувальний socket SHM transport.
- [GStreamer synchronisation](https://gstreamer.freedesktop.org/documentation/additional/design/synchronisation.html): синхронізація залежить від clock, buffer timestamps і SEGMENT; їх передачу між процесами треба довести.
- [systemd service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html): restart/watchdog для core services; медіапрогрес перевіряється логікою Converter.

У release BOM фіксувати version, source URL/commit, license, build flags, architecture, hashes і спосіб доставки кожного SDK/плагіна. Точні pin versions визначити після host audit, не підміняти їх словами latest.
