"""One-shot DeckLink provider scan, adapted from StreamMachine hardware/decklink.py.

No capture/playout, element state changes or profile/duplex configuration.
Device-number is the provider's global index, never an index assigned after sorting.
"""

from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def command(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def driver_inventory():
    devices = []
    root = Path("/sys/bus/pci/devices")
    if not root.is_dir():
        raise RuntimeError("PCI inventory is unavailable at /sys/bus/pci/devices")
    for device in sorted(root.iterdir()):
        if (device / "vendor").read_text().strip().lower() != "0xbdbd":
            continue
        devices.append({
            "pci_bus_id": device.name,
            "device_id": (device / "device").read_text().strip(),
            "kernel_driver": (device / "driver").resolve().name if (device / "driver").exists() else None,
        })
    loaded_version = None
    for module in ("blackmagic_io", "blackmagic"):
        version = Path("/sys/module") / module / "version"
        if version.is_file():
            loaded_version = version.read_text().strip()
            break
    return {
        "pci_devices": devices, "loaded_driver_version": loaded_version,
        "installed_driver_version": command(["modinfo", "-F", "version", "blackmagic_io"]),
        "desktop_video_package": command(["dpkg-query", "-W", "-f=${Version}", "desktopvideo"]),
    }


def property_value(properties, name):
    return properties.get_value(name) if properties is not None and properties.has_field(name) else None


def parse_modes(caps):
    modes = {}
    skipped = 0
    if caps is None or caps.is_empty() or caps.is_any():
        return [], 0
    normalized = caps.copy().normalize()
    if normalized.get_size() > 8192:
        raise RuntimeError("DeckLink mode list exceeds the supported size")
    for index in range(normalized.get_size()):
        structure = normalized.get_structure(index)
        if structure.get_name() != "video/x-raw":
            continue
        width_ok, width = structure.get_int("width")
        height_ok, height = structure.get_int("height")
        fps_ok, numerator, denominator = structure.get_fraction("framerate")
        interlace = property_value(structure, "interlace-mode")
        if not (width_ok and height_ok and fps_ok and width > 0 and height > 0 and numerator > 0 and denominator > 0 and isinstance(interlace, str)):
            skipped += 1
            continue
        rate = Fraction(numerator, denominator)
        par_ok, par_num, par_den = structure.get_fraction("pixel-aspect-ratio")
        par = Fraction(par_num, par_den) if par_ok and par_den > 0 else None
        mode = {
            "width": width, "height": height, "fps_num": rate.numerator, "fps_den": rate.denominator,
            "interlace_mode": interlace, "field_order": property_value(structure, "field-order"),
            "pixel_aspect_ratio": f"{par.numerator}/{par.denominator}" if par else None,
            "format": property_value(structure, "format"),
        }
        key = json.dumps(mode, sort_keys=True)
        mode["mode_id"] = hashlib.sha256(key.encode()).hexdigest()[:20]
        modes[key] = mode
    return sorted(modes.values(), key=lambda item: (item["width"], item["height"], item["interlace_mode"], Fraction(item["fps_num"], item["fps_den"]), item["mode_id"])), skipped


def scan():
    driver = driver_inventory()
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    factory = Gst.DeviceProviderFactory.find("decklinkdeviceprovider")
    if factory is None:
        raise RuntimeError("GStreamer DeckLink Device Provider відсутній. Перевірте gstreamer1.0-plugins-bad і Desktop Video.")
    provider = factory.get()
    if provider is None:
        raise RuntimeError("Не вдалося створити GStreamer DeckLink Device Provider.")
    # A direct one-shot probe avoids unrelated cameras/providers and background monitoring.
    devices = provider.get_devices()
    ports = {}
    errors = []
    for device in devices:
        device_class = device.get_device_class().split("/")
        if "Video" not in device_class:
            continue
        direction = "input" if "Source" in device_class else "output" if "Sink" in device_class else None
        if direction is None:
            continue
        port = None
        try:
            properties = device.get_properties()
            number = property_value(properties, "device-number")
            if not isinstance(number, int) or number < 0:
                raise ValueError("Provider не надав device-number; порт не зіставлено за назвою.")
            persistent = property_value(properties, "persistent-id")
            persistent = str(persistent) if persistent is not None else None
            serial = property_value(properties, "serial-number")
            serial = str(serial).strip() if serial is not None else None
            if serial and (not serial.strip("0") or serial.lower() in {"unknown", "none", "n/a"}):
                serial = None
            model = property_value(properties, "model-name") or "DeckLink"
            display = property_value(properties, "display-name") or device.get_display_name()
            # Never merge same-model boards or different global device numbers.
            key = (persistent, number)
            port = ports.setdefault(key, {
                "port_id": f"decklink:{persistent}:device:{number}" if persistent is not None else f"decklink:device:{number}",
                "persistent_id": persistent, "device_number": number, "model_name": model,
                "display_name": display, "serial_number": serial, "board_id": f"serial:{serial}" if serial else None,
                "identity_status": "persistent" if persistent is not None else "session_index",
                "has_input": False, "has_output": False, "input_modes": [], "output_modes": [],
                "input_caps": None, "output_caps": None, "input_max_audio_channels": None,
                "output_max_audio_channels": None, "supports_format_detection": None,
                "query_status": "complete", "warnings": [],
            })
            if port[f"has_{direction}"]:
                raise ValueError(f"Дубль напрямку {direction} для device-number={number}")
            port[f"has_{direction}"] = True
            port[f"{direction}_max_audio_channels"] = property_value(properties, "max-channels")
            if direction == "input":
                port["supports_format_detection"] = property_value(properties, "supports-format-detection")
            caps = device.get_caps()
            port[f"{direction}_caps"] = caps.to_string() if caps is not None else None
            modes, skipped = parse_modes(caps)
            port[f"{direction}_modes"] = modes
            if not modes or skipped:
                port["query_status"] = "partial"
                port["warnings"].append(f"{direction}: режимів розібрано {len(modes)}, нерозібраних caps {skipped}; див. raw caps.")
        except Exception as error:
            errors.append(f"{device.get_display_name()}: {error}"[:500])
            if port is not None:
                port["query_status"] = "partial"
                port["warnings"].append(f"{direction}: {error}"[:500])
    result = sorted(ports.values(), key=lambda item: item["device_number"])
    persistent_counts = {}
    for port in result:
        if port["persistent_id"] is not None:
            persistent_counts[port["persistent_id"]] = persistent_counts.get(port["persistent_id"], 0) + 1
    for port in result:
        if persistent_counts.get(port["persistent_id"], 0) > 1:
            port["identity_status"] = "ambiguous_persistent_id"
            port["query_status"] = "partial"
            port["warnings"].append("Persistent ID повторюється; для цієї сесії порти розрізняються device-number.")
    boards = {}
    for port in result:
        if port["board_id"] is None:
            continue
        board = boards.setdefault(port["board_id"], {"board_id": port["board_id"], "serial_number": port["serial_number"], "model_name": port["model_name"], "port_ids": []})
        board["port_ids"].append(port["port_id"])
    if driver["pci_devices"] and not result:
        errors.append("PCI-пристрої Blackmagic присутні, але provider не надав відеопортів. Перевірте драйвер, профіль плати й доступ сервісу.")
    if any(not item["kernel_driver"] for item in driver["pci_devices"]):
        errors.append("Не всі PCI-пристрої Blackmagic прив’язані до драйвера.")
    plugin = factory.get_plugin()
    fingerprint = {"ports": result, "driver": driver, "gstreamer": Gst.version_string(), "plugin_version": plugin.get_version() if plugin else None, "probe_source": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return {
        "schema_version": 1, "ports": result, "boards": list(boards.values()), "driver": driver,
        "query_status": "partial" if errors or any(port["query_status"] != "complete" for port in result) else "complete",
        "scan_errors": errors, "gstreamer_version": Gst.version_string(),
        "plugin_version": plugin.get_version() if plugin else None,
        "fingerprint": hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest(),
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    with tempfile.TemporaryDirectory(prefix="ip-media-decklink-registry-") as directory:
        os.environ["GST_REGISTRY"] = str(Path(directory) / "registry.bin")
        print(json.dumps(scan()))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
