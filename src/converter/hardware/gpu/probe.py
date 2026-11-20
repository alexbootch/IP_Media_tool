"""Disposable Linux GPU probe, adapted from StreamMachine encoder_capabilities.

GStreamer/device initialization is kept out of the web process. All stdout is JSON.
No element is transitioned out of NULL and no frames are encoded.
"""

import csv
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import uuid


NVIDIA_VENDOR_ID = "0x10de"
CODECS = {"video/x-h264": "h264", "video/x-h265": "hevc", "video/x-av1": "av1"}
DEEP_FORMATS = {
    "P010_10LE", "P010_10BE", "P012_LE", "P016_LE", "P210_10LE", "P216_LE",
    "I420_10LE", "I420_10BE", "I422_10LE", "I422_10BE", "I422_12LE",
    "Y444_10LE", "Y444_10BE", "Y444_12LE", "Y444_16LE", "GBR_10LE", "GBR_12LE",
    "GBR_16LE", "RGB10A2_LE", "BGR10A2_LE",
}
FORMATS_422 = {"NV16", "Y42B", "YUY2", "UYVY", "P210_10LE", "P216_LE", "I422_10LE", "I422_10BE", "I422_12LE"}
FORMATS_444 = {"Y444", "Y444_10LE", "Y444_10BE", "Y444_12LE", "Y444_16LE", "VUYA", "AYUV"}


def command(arguments, timeout=8):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, str(error)[:400]
    if result.returncode:
        return None, (result.stderr.strip() or "Command exited with code " + str(result.returncode))[:400]
    return result.stdout, None


def pci_id(value):
    try:
        domain, bus, slot = value.lower().split(":")
        return f"{int(domain, 16):04x}:{bus}:{slot}"
    except (ValueError, AttributeError):
        return None


def inventory():
    smi, smi_error = command([
        "nvidia-smi", "--query-gpu=index,uuid,name,pci.bus_id,driver_version,memory.total",
        "--format=csv,noheader,nounits",
    ])
    nvidia = {}
    for row in csv.reader((smi or "").splitlines(), skipinitialspace=True):
        if len(row) != 6:
            continue
        index, gpu_uuid, name, bus, driver, memory = [value.strip() for value in row]
        bus = pci_id(bus)
        if not bus or not index.isdecimal() or not gpu_uuid.startswith("GPU-"):
            continue
        nvidia[bus] = {
            "adapter_id": "nvidia:" + gpu_uuid, "uuid": gpu_uuid, "display_name": name,
            "nvidia_index": int(index), "driver_version": driver,
            "memory_mib": int(memory) if memory.isdecimal() else None,
        }
    pci_output, _ = command(["lspci", "-D", "-nn"])
    names = {}
    for line in (pci_output or "").splitlines():
        bus, _, name = line.partition(" ")
        names[pci_id(bus)] = name
    adapters = []
    root = Path("/sys/bus/pci/devices")
    if not root.is_dir():
        raise RuntimeError("PCI inventory is unavailable at /sys/bus/pci/devices")
    for device in sorted(root.iterdir()):
        try:
            if not (device / "class").read_text().strip().startswith("0x03"):
                continue
            vendor_id = (device / "vendor").read_text().strip()
            if vendor_id != NVIDIA_VENDOR_ID:
                continue
            vendor = "NVIDIA"
            bus = pci_id(device.name)
            driver = (device / "driver").resolve().name if (device / "driver").exists() else None
            item = {
                "adapter_id": f"pci:{bus}", "pci_bus_id": bus, "vendor": vendor,
                "display_name": names.get(bus, f"{vendor} GPU {bus}"), "driver_version": None,
                "kernel_driver": driver, "memory_mib": None, "uuid": None,
                "nvidia_index": None, "cuda_device_id": None,
                "backend": "nvenc", "query_status": "pending", "message": None,
                "codecs": [],
            }
            item.update(nvidia.pop(bus, {}))
            if item["uuid"] is None:
                item["query_status"] = "unavailable"
                item["message"] = "nvidia-smi не надав ідентифікатор GPU. " + (smi_error or "Перевірте драйвер і доступ сервісу.")
            adapters.append(item)
        except OSError as error:
            raise RuntimeError(f"Cannot read PCI device {device.name}: {error}") from error
    # Preserve devices reported by NVML even if their PCI entry is not exposed in this namespace.
    for bus, details in nvidia.items():
        adapters.append({
            **details, "pci_bus_id": bus, "vendor": "NVIDIA", "kernel_driver": "nvidia",
            "cuda_device_id": None, "backend": "nvenc", "query_status": "pending", "message": None, "codecs": [],
        })
    runtime_packages, _ = command([
        "dpkg-query", "-W", "-f=${binary:Package}=${Version}\n", "gstreamer1.0-plugins-bad",
        "libgstreamer1.0-0", "python3-gi", "python3-gst-1.0",
    ])
    files = []
    directories = [Path("/usr/lib/gstreamer-1.0"), Path("/usr/local/lib/gstreamer-1.0")]
    directories += list(Path("/usr/lib").glob("*/gstreamer-1.0"))
    for key in ("GST_PLUGIN_PATH", "GST_PLUGIN_PATH_1_0", "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_SYSTEM_PATH_1_0"):
        directories += [Path(value) for value in os.environ.get(key, "").split(":") if value]
    for directory in directories:
        for candidate in directory.glob("*nvcodec*"):
            try:
                stat = candidate.stat()
                files.append((str(candidate), stat.st_size, stat.st_mtime_ns))
            except OSError:
                continue
    driver_files = []
    for directory in [Path("/usr/lib"), *Path("/usr/lib").glob("*-linux-gnu")]:
        for pattern in ("libcuda.so*", "libnvidia-encode.so*"):
            for candidate in directory.glob(pattern):
                try:
                    stat = candidate.stat()
                    driver_files.append((str(candidate), stat.st_size, stat.st_mtime_ns))
                except OSError:
                    continue
    device_permissions = []
    for candidate in Path("/dev").glob("nvidia*"):
        try:
            stat = candidate.stat()
            device_permissions.append((str(candidate), stat.st_mode, stat.st_uid, stat.st_gid))
        except OSError:
            continue
    signature = {
        "adapters": adapters, "packages": runtime_packages, "plugins": sorted(set(files)),
        "driver_libraries": sorted(set(driver_files)), "devices": device_permissions,
        "kernel": platform.release(), "groups": sorted(os.getgroups()), "python": sys.version,
        "probe_source": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": {key: os.environ.get(key) for key in (
            "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "NVIDIA_VISIBLE_DEVICES", "LD_LIBRARY_PATH",
            "GST_PLUGIN_PATH", "GST_PLUGIN_PATH_1_0", "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_SYSTEM_PATH_1_0",
        )},
    }
    return {"schema_version": 1, "adapters": adapters, "fingerprint": hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()}


def cuda_devices():
    """Map CUDA ordinals to UUID/PCI, never assume that nvidia-smi index is a CUDA ID."""
    library = ctypes.CDLL("libcuda.so.1")
    library.cuInit.argtypes = [ctypes.c_uint]
    library.cuInit.restype = ctypes.c_int
    library.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
    library.cuDeviceGetCount.restype = ctypes.c_int
    library.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
    library.cuDeviceGet.restype = ctypes.c_int
    library.cuDeviceGetPCIBusId.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    library.cuDeviceGetPCIBusId.restype = ctypes.c_int
    get_uuid = getattr(library, "cuDeviceGetUuid_v2", None) or library.cuDeviceGetUuid
    get_uuid.argtypes = [ctypes.c_void_p, ctypes.c_int]
    get_uuid.restype = ctypes.c_int
    status = library.cuInit(0)
    if status != 0:
        raise RuntimeError(f"cuInit failed ({status}); check service permissions and driver.")
    count = ctypes.c_int()
    if library.cuDeviceGetCount(ctypes.byref(count)) != 0:
        raise RuntimeError("cuDeviceGetCount failed")
    result = {}
    for ordinal in range(min(count.value, 64)):
        device = ctypes.c_int()
        bus = ctypes.create_string_buffer(32)
        identifier = ctypes.create_string_buffer(16)
        if library.cuDeviceGet(ctypes.byref(device), ordinal) != 0:
            continue
        if library.cuDeviceGetPCIBusId(bus, len(bus), device.value) != 0 or get_uuid(identifier, device.value) != 0:
            continue
        result[ordinal] = {"uuid": "GPU-" + str(uuid.UUID(bytes=identifier.raw)), "pci_bus_id": pci_id(bus.value.decode("ascii"))}
    return result


def probe_encoders(payload):
    targets = [adapter for adapter in payload["adapters"] if adapter["vendor"] == "NVIDIA" and adapter["uuid"]]
    if not targets:
        return payload
    try:
        mapping = cuda_devices()
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst, GObject

        Gst.init(None)
    except Exception as error:
        for adapter in targets:
            adapter.update(query_status="failed", message=str(error)[:500])
        return payload

    def values(value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        try:
            return [str(item) for item in value]
        except TypeError:
            return [str(value)]

    def bounds(value):
        if isinstance(value, int):
            return [value, value]
        try:
            serialized = Gst.value_serialize(value)
            numbers = re.findall(r"-?\d+", serialized or "")
            return [int(numbers[0]), int(numbers[-1])] if numbers else None
        except Exception:
            return None

    def caps_data(caps):
        result = []
        for index in range(caps.get_size()):
            structure = caps.get_structure(index)
            result.append({
                "media_type": structure.get_name(), "features": caps.get_features(index).to_string(),
                "formats": values(structure.get_value("format")) if structure.has_field("format") else [],
                "profiles": values(structure.get_value("profile")) if structure.has_field("profile") else [],
                "interlace_modes": values(structure.get_value("interlace-mode")) if structure.has_field("interlace-mode") else [],
                "width": bounds(structure.get_value("width")) if structure.has_field("width") else None,
                "height": bounds(structure.get_value("height")) if structure.has_field("height") else None,
            })
        return result

    for adapter in targets:
        matching = [index for index, value in mapping.items() if value["uuid"].lower() == adapter["uuid"].lower() and value["pci_bus_id"] == adapter["pci_bus_id"]]
        if len(matching) != 1:
            adapter.update(query_status="unavailable", message="Не вдалося однозначно зіставити GPU UUID/PCI з CUDA. Можливості не приписуються іншій карті.")
        else:
            adapter["cuda_device_id"] = matching[0]
    factories = Gst.Registry.get().get_feature_list(Gst.ElementFactory)
    errors = []
    for factory in sorted(factories, key=lambda item: item.get_name()):
        name = factory.get_name()
        if factory.get_plugin_name() != "nvcodec" or "auto" in name or "Encoder" not in (factory.get_metadata("klass") or ""):
            continue
        try:
            element = factory.create(None)
            if element is None:
                continue
            device_property = element.find_property("cuda-device-id")
            if device_property is None:
                continue
            if device_property.flags & GObject.ParamFlags.WRITABLE:
                # A selectable/default ordinal does not prove per-device static caps.
                errors.append(f"{name}: CUDA device is selectable; per-device caps are not confirmed.")
                continue
            cuda_id = int(element.get_property("cuda-device-id"))
            adapter = next((item for item in targets if item["cuda_device_id"] == cuda_id), None)
            if adapter is None:
                continue
            sink, source = [], []
            sink_strings, source_strings = [], []
            for template in factory.get_static_pad_templates():
                caps = template.get_caps()
                if template.direction == Gst.PadDirection.SINK:
                    sink.extend(caps_data(caps))
                    sink_strings.append(caps.to_string())
                elif template.direction == Gst.PadDirection.SRC:
                    source.extend(caps_data(caps))
                    source_strings.append(caps.to_string())
            codec = next((CODECS[item["media_type"]] for item in source if item["media_type"] in CODECS), None)
            if codec is None:
                continue
            formats = sorted({value for item in sink for value in item["formats"]})
            profiles = sorted({value for item in source for value in item["profiles"]})
            # AV1 profiles encode depth/chroma in their semantics, not their names.
            # Keep both raw-input and encoded-profile evidence for these summaries.
            deep_profile = any("10" in value or "12" in value for value in profiles)
            if codec == "h264":
                deep_profile = deep_profile or bool(set(profiles) & {"high-4:2:2", "high-4:4:4", "high-4:4:4-predictive"})
            elif codec == "av1":
                deep_profile = bool(set(profiles) & {"main", "high", "professional"})
            profile_422 = any("422" in value or "4:2:2" in value for value in profiles) or (codec == "av1" and "professional" in profiles)
            profile_444 = any("444" in value or "4:4:4" in value for value in profiles) or (codec == "av1" and bool(set(profiles) & {"high", "professional"}))
            interlace = sorted({value for item in sink for value in item["interlace_modes"]})
            widths = [item["width"][1] for item in sink if item["width"]]
            heights = [item["height"][1] for item in sink if item["height"]]
            bframes = element.find_property("bframes")
            lookahead = element.find_property("rc-lookahead")
            rc_modes = []
            rc_property = element.find_property("rc-mode")
            if rc_property is not None:
                enum_class = getattr(rc_property, "enum_class", None)
                rc_modes = sorted({value.value_nick for value in getattr(enum_class, "__enum_values__", {}).values()})
            plugin = factory.get_plugin()
            # Retain exact per-format/per-memory caps for later route validation; global maxima are UI summaries only.
            adapter["codecs"].append({
                "codec": codec, "element": name, "plugin_version": plugin.get_version() if plugin else None,
                "profiles": profiles, "input_formats": formats, "interlace_modes": interlace,
                "max_width": max(widths) if widths else None, "max_height": max(heights) if heights else None,
                "bit_depth_10": bool(set(formats) & DEEP_FORMATS) and deep_profile if formats and profiles else None,
                "chroma_422": bool(set(formats) & FORMATS_422) and profile_422 if formats and profiles else None,
                "chroma_444": bool(set(formats) & FORMATS_444) and profile_444 if formats and profiles else None,
                "field_encoding": bool(set(interlace) & {"interleaved", "mixed", "alternate"}) if interlace else None,
                "max_b_frames": int(bframes.maximum) if bframes is not None else None,
                "lookahead_frames": int(lookahead.maximum) if lookahead is not None else None,
                "temporal_aq": element.find_property("temporal-aq") is not None,
                "rate_control_modes": rc_modes,
                "sink_caps": sink_strings, "src_caps": source_strings, "format_modes": sink,
                "provenance": "device-bound GStreamer nvcodec caps/properties", "native_hardware_only": None,
            })
            element = None  # Remains in NULL; never open a streaming/encoding pipeline.
        except Exception as error:
            errors.append(f"{name}: {error}"[:400])
    for adapter in targets:
        if adapter["cuda_device_id"] is None:
            continue
        adapter["query_status"] = "complete" if adapter["codecs"] else "unavailable"
        adapter["message"] = None if adapter["codecs"] else "GStreamer не надав прив’язаних до цієї GPU NVENC-енкодерів. Це не доказ відсутності можливостей у самого чипа."
        if errors:
            adapter["query_status"] = "partial" if adapter["codecs"] else "failed"
            adapter["message"] = "; ".join(errors)[:1200]
    payload["gstreamer_version"] = Gst.version_string()
    return payload


def main():
    payload = inventory()
    if "--inventory" not in sys.argv[1:]:
        with tempfile.TemporaryDirectory(prefix="ip-media-gpu-registry-") as directory:
            os.environ["GST_REGISTRY"] = str(Path(directory) / "registry.bin")
            payload = probe_encoders(payload)
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
