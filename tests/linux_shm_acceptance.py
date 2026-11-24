"""Manual Linux integration scenario. Uses a private release, runtime and /dev/shm."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from converter.runtime.client import RuntimeClient
from converter.config.validation import ConfigError


ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    """Write an inert fixture configuration inside the isolated project copy."""
    path.write_text(json.dumps(data), encoding="utf-8")


def install_fixture(release, direction):
    """Register exactly one acceptance module in the temporary release only."""
    input_node = direction == "input"
    name = "acceptance-" + direction
    plugin = release / "plugins" / name
    plugin.mkdir(parents=True)
    shutil.copyfile(ROOT / "tests" / "fixtures" / "shm" / ("source.py" if input_node else "sink.py"), plugin / "worker.py")
    (plugin / "ui.js").write_text("// Headless Linux acceptance fixture.\n", encoding="utf-8")
    (plugin / "styles.css").write_text("/* Headless Linux acceptance fixture. */\n", encoding="utf-8")
    (plugin / "README.md").write_text("Linux SHM acceptance only.\n", encoding="utf-8")
    write_json(plugin / "config.schema.json", {"type": "object", "additionalProperties": False, "properties": {} if input_node else {"delay": {"type": "number", "minimum": 0.001, "maximum": 1}}})
    write_json(plugin / "manifest.json", {"schema_version": 1, "sdk_version": 1, "settings_version": 1, "type": name, "version": "1.0.0", "name": name, "kind": direction, "worker": {"path": "worker.py", "class_name": "Worker"}, "ui": {"path": "ui.js", "stylesheet": "styles.css"}, "config_schema": "config.schema.json", "requirements": {"capabilities": [], "gstreamer_factories": []}, "ports": [{"id": "out" if input_node else "in", "name": "Media", "direction": "output" if input_node else "input", "contract_type": "raw_av", "required": True}]})


def config(node_id, direction, source=None, delay=None):
    """Construct a schema-valid config using normal consumer-owned edges."""
    return {"schema_version": 1, "id": node_id, "type": "acceptance-" + direction, "revision": 1, "settings_version": 1, "name": direction, "enabled": True, "position": {"x": 0, "y": 0}, "settings": {} if delay is None else {"delay": delay}, "connections": {} if source is None else {"in": {"source_node_id": source, "source_port_id": "out"}}, "secret_refs": {}}


def wait_for(label, check, seconds=20):
    """Wait for a real observation with a deadline, preserving runtime errors."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.15)
    raise AssertionError(f"Timed out: {label}")


def exercise(client):
    """Verify late attach, slow-reader isolation, independent stops and source restart."""
    source, fast, slow = (str(uuid4()) for _ in range(3))
    for data in (config(source, "input"), config(fast, "output", source, 0.003), config(slow, "output", source, 0.25)):
        client.call("save_config", config=data, expected_revision=0)

    def status(node):
        """Read and fail promptly on an exhausted or permanent worker error."""
        value = client.call("status", node_id=node)
        if value["state"] == "error" and not value["restart_pending"]:
            raise AssertionError(value)
        return value

    def state(node, wanted):
        """Return the observed snapshot only when the state matches."""
        value = status(node)
        return value if value["state"] == wanted else None

    def metrics(node):
        """Read the latest measured values using normal status RPC."""
        return (status(node)["status"] or {}).get("metrics", {})

    client.call("start", node_id=fast, expected_revision=1)
    early = wait_for("consumer waiting for source", lambda: state(fast, "waiting_source"))
    client.call("start", node_id=source, expected_revision=1)
    wait_for("fast reader receives aligned A/V", lambda: metrics(fast).get("pairs", 0) >= 20)
    client.call("start", node_id=slow, expected_revision=1)
    wait_for("late and slow reader receives data and reports overflow", lambda: metrics(slow).get("pairs", 0) >= 3 and metrics(slow).get("skipped", 0) > 0)
    first = metrics(fast)
    slow_before = metrics(slow)
    assert first["generation"] == slow_before["generation"]
    assert first["bad_samples"] == slow_before["bad_samples"] == 0
    assert first["max_skew_ns"] <= 160_000_000 and slow_before["max_skew_ns"] <= 160_000_000
    wait_for("fast reader continues while slow reader falls behind", lambda: metrics(fast).get("pairs", 0) >= first["pairs"] + 20)
    before_stop = metrics(fast)["pairs"]
    client.call("stop", node_id=slow)
    wait_for("only the slow reader stops", lambda: state(slow, "stopped"))
    wait_for("fast reader progresses with the other reader stopped", lambda: metrics(fast).get("pairs", 0) >= before_stop + 20)
    client.call("start", node_id=slow, expected_revision=1)
    wait_for("slow reader reattaches independently", lambda: metrics(slow).get("pairs", 0) >= 3)
    fast_before, slow_before = metrics(fast), metrics(slow)
    client.call("stop", node_id=source)
    wait_for("both readers observe withdrawal", lambda: state(fast, "waiting_source") and state(slow, "waiting_source"))
    client.call("start", node_id=source, expected_revision=1)
    wait_for("both readers receive new generation", lambda: metrics(fast).get("generation") not in {None, first["generation"]} and metrics(slow).get("generation") == metrics(fast).get("generation"))
    wait_for("both readers progress after producer restart", lambda: metrics(fast).get("pairs", 0) > fast_before["pairs"] and metrics(slow).get("pairs", 0) > slow_before["pairs"])
    assert status(fast)["status"]["instance_id"] == early["status"]["instance_id"]
    results = {"fast": metrics(fast), "slow": metrics(slow), "source": metrics(source)}
    assert results["fast"]["bad_samples"] == results["slow"]["bad_samples"] == 0
    for node in (slow, fast, source):
        client.call("stop", node_id=node)
        wait_for("clean worker stop", lambda: state(node, "stopped"))
    print(json.dumps(results, indent=2))


def main():
    """Start a temporary runtime and tear it down even on a failing assertion."""
    if sys.platform != "linux":
        raise SystemExit("Run manually on Linux only.")
    with tempfile.TemporaryDirectory(prefix="ipm-shm-", dir="/dev/shm") as workspace:
        directory = Path(workspace)
        release = directory / "release"
        shutil.copytree(ROOT / "src", release / "src", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "schemas", release / "schemas")
        install_fixture(release, "input")
        install_fixture(release, "output")
        sock = directory / "runtime.sock"
        environment = {**os.environ, "PYTHONPATH": str(release / "src"), "PYTHONDONTWRITEBYTECODE": "1", "IP_MEDIA_TOOL_RUNTIME_SOCKET": str(sock), "IP_MEDIA_TOOL_NODE_DIR": str(directory / "nodes"), "GST_REGISTRY": str(directory / "gst-registry.bin")}
        client = RuntimeClient(sock, release.name)
        with (directory / "runtime.log").open("w+") as log:
            runtime = subprocess.Popen([sys.executable, "-m", "converter.runtime.service"], cwd=release, env=environment, stdout=log, stderr=log)
            try:
                wait_for("runtime socket", lambda: sock.is_socket() and runtime.poll() is None)
                exercise(client)
            except (AssertionError, ConfigError, OSError):
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                if runtime.poll() is None:
                    runtime.send_signal(signal.SIGTERM)
                    try:
                        runtime.wait(timeout=25)
                    except subprocess.TimeoutExpired:
                        runtime.kill()
                        runtime.wait()
                        raise AssertionError("Isolated runtime failed to shut down") from None
        assert not list(directory.glob("media-*")), "Runtime left SHM generation files behind"
        print("SHM fan-out acceptance passed")


if __name__ == "__main__":
    main()
