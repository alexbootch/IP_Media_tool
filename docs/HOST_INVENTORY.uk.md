# Інвентаризація Linux хоста Converter

Стан за виводом користувача від 2026-11-17. Прямий доступ до сервера не використовується; перевірки виконує користувач. Джерело — прикріплений текст результатів інвентаризації та повідомлення користувача. IP/hostname цього хоста не підтверджені. Дані попереднього сервера StreamMachine не підставляються як поточні.

Користувач підтвердив, що раніше видаляв Kubernetes і GStreamer. Kubernetes не потрібен для Converter. Його залишки та встановлені контейнерні компоненти залишаємо без змін і виключаємо з подальшої інвентаризації; повертаємося до них лише за конкретного конфлікту ресурсів, портів або доступу до пристроїв. Встановлення Kubernetes CLI, пошук Device Plugin і очищення залишків не плануються.

## Підтверджені результати

| Компонент | Стан | Підстава |
| --- | --- | --- |
| ОС | Ubuntu 24.04.5 LTS, x86_64 | os-release, uname |
| Ядро | 6.8.0-147-generic | uname -r |
| DeckLink | Duo 2, PCI 01:00.0 | lspci; це не Quad 2 зі старого inventory |
| Desktop Video | Пакети та модулі 16.4a1 | dpkg, modinfo |
| DeckLink kernel binding | blackmagic-io, модуль blackmagic_io завантажений | lspci, lsmod |
| DeckLink DKMS | installed для 6.8.0-146 і 6.8.0-147 | dkms status; vermagic відповідає поточному ядру |
| DesktopVideoHelper | active/running | systemctl |
| GPU | NVIDIA Quadro P1000, PCI 02:00.0, 4096 MiB | lspci, nvidia-smi |
| NVIDIA driver | 580.178.04, модулі завантажені, nvidia-smi успішний | nvidia-smi, lsmod |
| NVIDIA DKMS | installed для 6.8.0-146 і 6.8.0-147 | dkms status |
| Encode/decode userspace | libnvidia-encode-580 і libnvidia-decode-580 встановлені | dpkg; фактична codec capability не перевірена |
| NVIDIA Container Toolkit | 1.20.1-1, nvidia-ctk та nvidia-container-cli доступні | dpkg, command -v |
| containerd | Пакет 2.2.1; system containerd.service inactive/dead | dpkg, systemctl; інший bundled runtime не виключений |
| Docker | Команда не знайдена в PATH, docker.service not-found | command -v, systemctl |
| Python | /usr/bin/python3 | command -v; версія поки невідома |
| gst-inspect-1.0 | Не знайдено в PATH користувача | command -v; бібліотеки та інші installation prefixes не перевірені |
| kubectl | Не знайдено в PATH користувача | повідомлення shell |
| helm | Не знайдено в PATH користувача | command -v; Helm release list не отриманий |
| Kubernetes traces | Є active kubepods slices | systemctl; це не доказ готовності control plane або конкретного NVIDIA workload |

## Межі висновків

Драйвери DeckLink і NVIDIA встановлені та підключені до відповідного заліза. Це ще не перевірка SDI capture/playback, доступу користувача до пристроїв або NVENC/NVDEC pipelines. nvidia-smi показав 0% GPU utilization, 3 MiB зайнятої пам'яті та відсутність процесів у своїй таблиці в момент перевірки; це не гарантія відсутності інших workloads чи резервування карти.

Показник CUDA Version 13.0 у nvidia-smi не приймається за доказ установленого CUDA Toolkit. Наявність NVIDIA Container Toolkit не доводить наявність NVIDIA Kubernetes Device Plugin чи GPU Operator. Стан залишків Kubernetes не досліджується далі за рішенням користувача. Відсутність впливу на Converter перевірятиметься під час звичайної перевірки ресурсів і запуску, якщо виникне конкретна проблема.

## Наступні перевірки без змін хоста

- CPU/RAM конкретного сервера не є умовою архітектури або завершення підготовки залежностей. Продукт універсальний; місткість маршрутів визначатиметься за доступними ресурсами під час роботи.
- [x] Версії драйверів перенесено у [чеклист ручного й автоматичного розгортання](DEPLOYMENT_CHECKLIST.uk.md).
- [ ] Скласти обов'язковий набір Python/GStreamer/GI/медіапакетів для інсталятора; наявність цих компонентів на поточній машині не є передумовою.
- [ ] Обрати NDI GStreamer plugin та потрібні runtime-бібліотеки; SDK/toolchain для build environment розглядати окремо. OMT відкладено, власний plugin не планується.
- GStreamer був видалений користувачем; точний майбутній набір пакетів визначається аналізом залежностей до встановлення.
- [ ] Не встановлювати kubectl, GStreamer, SDK або інші пакети тільки для інвентаризації.
- [ ] Не зупиняти, не видаляти й не переналаштовувати Kubernetes/container components під час цієї перевірки.

Повний етап 0 залишається відкритим через незавершений вибір версій компонентів і NDI runtime configuration. OMT не є блокером. Подальший збір CPU/RAM для визначення базової архітектури не потрібен.

## Довідкові джерела

- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/index.html) — компоненти доступу контейнерів до NVIDIA GPU.
- [NVIDIA Kubernetes Device Plugin](https://github.com/NVIDIA/k8s-device-plugin) — окремий Kubernetes-компонент; його наявність потрібно підтверджувати окремо від toolkit.
