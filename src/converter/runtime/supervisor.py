"""Single-owner process supervision with bounded retries and per-instance leases."""

import asyncio
import copy
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import signal
import shutil
import socket
import sys
import tempfile
import time
from uuid import uuid4

from converter.config.validation import ConfigError, canonical_id, decode_json
from converter.node_sdk.worker import TRANSITIONS, utc_now
from converter.node_sdk.media import validate_contract
from converter.runtime.protocol import MAX_STATUS, encode_frame, envelope, read_frame, write_frame


RETRY_LIMIT = 3
HEARTBEAT_TIMEOUT = 15
START_TIMEOUT = 30
STOP_TIMEOUT = 8
LOGGER = logging.getLogger("ip-media-tool.runtime")


@dataclass
class WorkerEntry:
    """Track one logical launch and its bounded recovery attempts in memory only."""

    config: dict
    instance_id: str = ""
    status: dict | None = None
    process: object = None
    writer: object = None
    watcher: object = None
    restart_task: object = None
    desired_running: bool = True
    restart_count: int = 0
    last_worker_revision: int = 0
    last_seen: float = 0
    spawned_at: float = 0
    leases: list = field(default_factory=list)
    last_exit_code: int | None = None
    failure_reason: str | None = None
    media_pending: dict = field(default_factory=dict)
    media_changed: object = None
    contract_revisions: dict = field(default_factory=dict)


class Supervisor:
    """Own all starts/stops/config mutations; never restore desired-running at boot."""

    def __init__(self, root, modules, nodes, media_root=None):
        """Bind this daemon to one immutable release and the shared config store."""
        self.root, self.modules, self.nodes = Path(root).resolve(), modules, nodes
        self.entries = {}
        self.resource_owners = {}
        self.control_locks = {}
        self.launch_tasks = {}
        self.closing = False
        self.media_root = Path(media_root) if media_root is not None else None
        self.contracts = {}

    def snapshot(self, node_id=None):
        """Report absent workers as stopped without inventing an instance UUID."""
        if node_id is not None:
            canonical_id(node_id)
            entry = self.entries.get(node_id)
            if entry is None:
                return {"node_id": node_id, "state": "stopped", "status": None, "process_alive": False, "restart_pending": False, "restart_count": 0}
            alive = entry.process is not None and entry.process.returncode is None
            return {"node_id": node_id, "state": entry.status["state"], "status": copy.deepcopy(entry.status), "process_alive": alive, "restart_pending": entry.desired_running and entry.restart_task is not None and not entry.restart_task.done(), "restart_count": entry.restart_count, "last_exit_code": entry.last_exit_code}
        return {"workers": [self.snapshot(key) for key in self.entries], "application_revision": self.root.name, "autostart": False}

    def _lock_for(self, node_id):
        """Serialize one node without blocking stop commands for unrelated workers."""
        return self.control_locks.setdefault(node_id, asyncio.Lock())

    def _log(self, entry, event_code):
        """Record lifecycle identity and retry counts without configuration values."""
        LOGGER.info(json.dumps({"event_code": event_code, "node_id": entry.config["id"], "node_type": entry.config["type"], "instance_id": entry.instance_id, "config_revision": entry.config["revision"], "restart_count": entry.restart_count, "exit_code": entry.last_exit_code}))

    async def dispatch(self, operation, arguments):
        """Serialize lifecycle and writes so config edits cannot race a start."""
        if operation == "list":
            offset = arguments.get("offset", 0)
            if type(offset) is not int or offset < 0:
                raise ConfigError("invalid_message", "Worker page offset must be nonnegative.")
            keys = list(self.entries)
            workers = []
            for key in keys[offset:offset + 16]:
                summary = self.snapshot(key)
                status = summary.pop("status")
                summary.update({name: status[name] for name in ("instance_id", "config_revision", "status_revision", "updated_at", "error")})
                if summary["error"] is not None:
                    summary["error_message_truncated"] = len(summary["error"]["message"]) > 256
                    summary["error"] = {**summary["error"], "message": summary["error"]["message"][:256]}
                workers.append(summary)
            return {"workers": workers, "next_offset": offset + 16 if offset + 16 < len(keys) else None, "application_revision": self.root.name, "autostart": False}
        if operation == "status":
            return self.snapshot(arguments["node_id"])
        config_argument = arguments.get("config")
        node_id = canonical_id(config_argument.get("id") if operation == "save_config" and isinstance(config_argument, dict) else arguments.get("node_id"))
        if operation == "stop":
            pending = self.launch_tasks.get(node_id)
            recovering = self.entries.get(node_id)
            recovery = recovering.restart_task if recovering is not None else None
            cancellations = {task for task in (pending, recovery) if task is not None and task is not asyncio.current_task() and not task.done()}
            for task in cancellations:
                task.cancel()
            if cancellations:
                await asyncio.gather(*cancellations, return_exceptions=True)
        async with self._lock_for(node_id):
            if self.closing:
                raise ConfigError("runtime_unavailable", "Runtime is shutting down.", 503)
            if operation == "start":
                node_id = canonical_id(arguments["node_id"])
                config = await asyncio.to_thread(self.nodes.get, node_id)
                if config["revision"] != arguments.get("expected_revision"):
                    raise ConfigError("revision_conflict", "Reload the config before starting.", 409)
                existing = self.entries.get(node_id)
                if existing is not None and (existing.desired_running or existing.process is not None):
                    if existing.config["revision"] != config["revision"]:
                        raise ConfigError("revision_conflict", "Stop the applied revision before starting a new one.", 409)
                    return self.snapshot(node_id)
                if not config["enabled"]:
                    raise ConfigError("invalid_config", "Node is disabled; enable it explicitly before starting.", 409)
                entry = WorkerEntry(config=config)
                self.launch_tasks[node_id] = asyncio.current_task()
                try:
                    await asyncio.wait_for(self._preflight(entry), 75)
                    self.entries[node_id] = entry
                    await self._spawn(entry)
                except BaseException:
                    entry.desired_running = False
                    self._release(entry)
                    self.entries.pop(node_id, None)
                    raise
                finally:
                    self.launch_tasks.pop(node_id, None)
                return self.snapshot(node_id)
            if operation == "stop":
                node_id = canonical_id(arguments["node_id"])
                entry = self.entries.get(node_id)
                if entry is not None:
                    await self._stop(entry)
                return self.snapshot(node_id)
            if operation in {"save_config", "delete_config"}:
                config = arguments.get("config")
                node_id = config.get("id") if isinstance(config, dict) else arguments.get("node_id")
                canonical_id(node_id)
                entry = self.entries.get(node_id)
                if entry is not None and (entry.desired_running or entry.process is not None or (entry.restart_task is not None and not entry.restart_task.done())):
                    raise ConfigError("resource_busy", "Stop this node before editing or deleting its config.", 409)
                if operation == "save_config":
                    return await self._store_write(self.nodes.save, config, arguments["expected_revision"])
                await self._store_write(self.nodes.delete, node_id, arguments["expected_revision"])
                return {"deleted": node_id}
            raise ConfigError("invalid_message", "Unsupported runtime operation.")

    async def _store_write(self, method, *arguments):
        """Keep the node lock until a disk transaction ends, even after RPC cancellation."""
        transaction = asyncio.create_task(asyncio.to_thread(method, *arguments))
        try:
            return await asyncio.shield(transaction)
        except asyncio.CancelledError:
            await transaction
            raise

    async def _preflight(self, entry):
        """Validate graph/dependencies and reserve declared DeckLink identities."""
        config = entry.config
        self.modules.validate_config(config)
        inventory = await asyncio.to_thread(self.nodes.snapshot)
        configs = {node["id"]: node for node in inventory["nodes"]}
        related, pending = {}, [config["id"]]
        while pending:
            node_id = pending.pop()
            if node_id in related or node_id not in configs:
                continue
            related[node_id] = configs[node_id]
            self.modules.validate_config(configs[node_id])
            pending.extend(connection["source_node_id"] for connection in configs[node_id]["connections"].values())
        relevant = self.modules.validate_graph(related)
        if relevant:
            raise ConfigError("invalid_config", "Node graph is not ready to start.", issues=relevant)
        manifest = self.modules.get(config["type"])["manifest"]
        if config["secret_refs"]:
            raise ConfigError("dependency_unavailable", "Scoped worker secret broker is not implemented.", 409)
        requirements = manifest["requirements"]
        for capability in requirements["capabilities"]:
            if capability["required"] and capability["provider"] == "decklink" and capability["name"] not in {"input", "output"}:
                raise ConfigError("dependency_unavailable", "Unknown required DeckLink capability.", 409)
        if any(item["required"] and item["provider"] != "decklink" for item in requirements["capabilities"]):
            raise ConfigError("dependency_unavailable", "Required capabilities have no runtime readiness provider yet.", 409)
        with tempfile.TemporaryDirectory(prefix="ip-media-worker-preflight-") as temporary:
            environment = {**os.environ, "GST_REGISTRY": str(Path(temporary) / "registry.bin")}
            for factory in requirements["gstreamer_factories"]:
                await self._probe(["/usr/bin/gst-inspect-1.0", "--exists", factory], environment, 10, capture=False)
        bindings = manifest.get("resource_bindings", [])
        required_directions = {item["name"] for item in requirements["capabilities"] if item["required"] and item["provider"] == "decklink"}
        if not required_directions.issubset({binding["direction"] for binding in bindings}):
            raise ConfigError("invalid_config", "DeckLink capabilities need matching resource_bindings.")
        if any(item["required"] and item["provider"] == "decklink" for item in requirements["capabilities"]) and not bindings:
            raise ConfigError("invalid_config", "DeckLink plugins must declare resource_bindings.")
        leases = []
        if bindings:
            payload = await self._probe([sys.executable, "-m", "converter.hardware.decklink.probe"], self._environment(), 60)
            for binding in bindings:
                resource_id = config["settings"].get(binding["setting"])
                if not isinstance(resource_id, str) or not resource_id or len(resource_id) > 128:
                    raise ConfigError("invalid_config", "Resource setting must contain a persistent DeckLink ID.")
                matches = [port for port in payload.get("ports", []) if port["persistent_id"] == resource_id and port["identity_status"] == "persistent" and port[f"has_{binding['direction']}"]]
                if len(matches) != 1:
                    raise ConfigError("hardware_unavailable", "DeckLink identity/direction is missing or ambiguous.", 409)
                key = "decklink:" + resource_id
                if any(lease["resource_id"] == resource_id for lease in leases):
                    raise ConfigError("invalid_config", "One worker cannot request the same DeckLink endpoint twice.")
                if key in self.resource_owners and self.resource_owners[key] != config["id"]:
                    raise ConfigError("resource_busy", "DeckLink port is reserved by another node.", 409)
                leases.append({"provider": "decklink", "resource_id": resource_id, "direction": binding["direction"]})
        entry.leases = leases
        for lease in leases:
            self.resource_owners["decklink:" + lease["resource_id"]] = config["id"]

    def _environment(self):
        """Pin child imports to this release rather than the mutable current symlink."""
        return {**os.environ, "PYTHONPATH": str(self.root / "src"), "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}

    async def _probe(self, arguments, environment, timeout, capture=True):
        """Run bounded dependency discovery without starting a media pipeline."""
        with tempfile.TemporaryFile() as output:
            process = await asyncio.create_subprocess_exec(*arguments, env=environment, stdin=asyncio.subprocess.DEVNULL, stdout=output, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            try:
                await asyncio.wait_for(process.wait(), timeout)
                if process.returncode != 0:
                    raise ConfigError("dependency_unavailable", "Worker dependency probe failed.", 409)
                if not capture:
                    return None
                if output.tell() > 2_000_000:
                    raise ConfigError("dependency_unavailable", "Dependency inventory is too large.", 409)
                output.seek(0)
                return decode_json(output.read(2_000_001), max_bytes=2_000_000)
            finally:
                if process.returncode is None:
                    self._signal(process, signal.SIGKILL)
                    await process.wait()

    async def _spawn(self, entry):
        """Create a process group and private inherited FD, then send validated bootstrap."""
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        entry.instance_id = str(uuid4())
        entry.last_worker_revision = 0
        entry.spawned_at = entry.last_seen = time.monotonic()
        entry.failure_reason = None
        entry.media_pending.clear()
        entry.media_changed = asyncio.Event()
        entry.contract_revisions.clear()
        entry.status = {"schema_version": 1, "node_id": entry.config["id"], "node_type": entry.config["type"], "instance_id": entry.instance_id, "config_revision": entry.config["revision"], "status_revision": 1, "state": "starting", "updated_at": utc_now(), "error": None, "metrics": {}}
        try:
            (self.media_root / entry.instance_id).mkdir(mode=0o700)
            entry.process = await asyncio.create_subprocess_exec(sys.executable, "-m", "converter.node_sdk.runner", "--ipc-fd", str(child.fileno()), pass_fds=(child.fileno(),), env=self._environment(), cwd=self.root, stdin=asyncio.subprocess.DEVNULL, start_new_session=True)
            child.close()
            reader, entry.writer = await asyncio.open_connection(sock=parent)
            bootstrap = {"schema_version": 1, "config": entry.config, "instance_id": entry.instance_id, "leases": entry.leases, "media_root": str(self.media_root)}
            self.modules.schemas.validate("worker-bootstrap", bootstrap)
            await write_frame(entry.writer, bootstrap)
            await self._command(entry, "start")
            self._seed_subscriptions(entry)
            entry.watcher = asyncio.create_task(self._watch(entry, reader))
            self._log(entry, "worker.spawned")
        except BaseException:
            parent.close()
            child.close()
            if entry.writer is not None:
                entry.writer.close()
            if entry.process is not None:
                self._signal(entry.process, signal.SIGKILL)
                await entry.process.wait()
                entry.process = None
            self._cleanup_media(entry)
            raise

    def _queue_media(self, entry, local, contract):
        """Coalesce updates per input and wake only the affected consumer sender."""
        entry.media_pending[local] = contract
        entry.media_changed.set()

    def _seed_subscriptions(self, entry):
        """Populate the current snapshot for late joins and explicit resync."""
        for local, connection in entry.config["connections"].items():
            source = (connection["source_node_id"], connection["source_port_id"])
            self._queue_media(entry, local, self.contracts.get(source))

    def _fan_out(self, source, contract):
        """Publish a snapshot to matching configured edges without waiting on consumers."""
        for entry in self.entries.values():
            if entry.process is None or not entry.desired_running:
                continue
            for local, connection in entry.config["connections"].items():
                if source == (connection["source_node_id"], connection["source_port_id"]):
                    self._queue_media(entry, local, contract)

    def _revoke_media(self, entry):
        """Invalidate every live output of this exact producer instance."""
        for key, contract in list(self.contracts.items()):
            if contract["instance_id"] == entry.instance_id:
                del self.contracts[key]
                self._fan_out(key, None)

    def _cleanup_media(self, entry):
        """Delete only the runtime-created directory of a reaped worker instance."""
        if self.media_root is not None and entry.instance_id:
            canonical_id(entry.instance_id)
            shutil.rmtree(self.media_root / entry.instance_id, ignore_errors=True)

    async def _send_subscriptions(self, entry):
        """Deliver ordered changes per consumer with independent IPC deadlines."""
        while True:
            await entry.media_changed.wait()
            entry.media_changed.clear()
            while entry.media_pending:
                local = next(iter(entry.media_pending))
                contract = entry.media_pending.pop(local)
                message = envelope(entry.config["id"], entry.instance_id, "media_subscription", {"local_port_id": local, "contract": contract})
                await write_frame(entry.writer, message)

    async def _media_event(self, entry, message):
        """Register or withdraw a validated generation from its owning worker only."""
        payload = message["payload"]
        node_id, port_id = payload["source_node_id"], payload["source_port_id"]
        manifest = self.modules.get(entry.config["type"])["manifest"]
        if node_id != entry.config["id"] or payload["instance_id"] != entry.instance_id or not any(port["id"] == port_id and port["direction"] == "output" for port in manifest["ports"]):
            raise ConfigError("transport_error", "Contract publisher does not own this output.")
        if payload["contract_revision"] != entry.contract_revisions.get(port_id, 0) + 1:
            raise ConfigError("transport_error", "Out-of-order media contract revision.")
        source = (node_id, port_id)
        previous = self.contracts.get(source)
        publishing = message["kind"] == "contract"
        if publishing:
            if previous is not None and previous["transport_generation"] == payload["transport_generation"]:
                raise ConfigError("transport_error", "Media replacement requires a new generation.")
            validate_contract(self.modules.schemas, self.media_root, payload, inspect_rings=True)
            # Allow enough space for the subscriber wrapper as well as the publisher frame.
            if len(encode_frame(payload)) > 60 * 1024:
                raise ConfigError("transport_error", "Media contract exceeds its IPC budget.")
        elif previous is not None and previous["transport_generation"] != payload["transport_generation"]:
            raise ConfigError("transport_error", "Withdrawal refers to a different media generation.")
        entry.contract_revisions[port_id] = payload["contract_revision"]
        if entry.desired_running and publishing:
            self.contracts[source] = payload
            self._fan_out(source, payload)
        elif not publishing:
            self.contracts.pop(source, None)
            self._fan_out(source, None)
        await write_frame(entry.writer, envelope(node_id, entry.instance_id, "ack", {"accepted": True}, message["request_id"]))

    async def _command(self, entry, command):
        """Send a correlated lifecycle command for the applied config revision."""
        await write_frame(entry.writer, envelope(entry.config["id"], entry.instance_id, "command", {"command": command, "config_revision": entry.config["revision"]}, str(uuid4())))
        if command == "resync":
            self._seed_subscriptions(entry)

    async def _events(self, entry, reader):
        """Accept only monotonic status from the authenticated inherited worker channel."""
        while True:
            message = await read_frame(reader, HEARTBEAT_TIMEOUT)
            self.modules.schemas.validate("ipc-message", message)
            if message["node_id"] != entry.config["id"] or message["instance_id"] != entry.instance_id:
                raise ConfigError("invalid_status", "Worker session identity mismatch.")
            if message["kind"] == "ack":
                continue
            if message["kind"] in {"contract", "contract_withdrawn"}:
                await self._media_event(entry, message)
                continue
            if message["kind"] != "status":
                raise ConfigError("invalid_status", "Unsupported worker event.")
            status = message["payload"]
            if len(encode_frame(status)) > MAX_STATUS:
                raise ConfigError("invalid_status", "Worker status exceeds 48 KiB.")
            if any(status[key] != entry.status[key] for key in ("node_id", "node_type", "instance_id", "config_revision")):
                raise ConfigError("invalid_status", "Worker status identity mismatch.")
            if status["status_revision"] <= entry.last_worker_revision:
                raise ConfigError("invalid_status", "Worker status revision did not increase.")
            previous, state = entry.status["state"], status["state"]
            if state != previous and state not in TRANSITIONS.get(previous, set()):
                raise ConfigError("invalid_status", "Worker state transition is invalid.")
            if state == "running" and previous != "running" and entry.desired_running:
                ports = self.modules.get(entry.config["type"])["manifest"]["ports"]
                if any(port["direction"] == "output" and port["required"] and (entry.config["id"], port["id"]) not in self.contracts for port in ports):
                    raise ConfigError("transport_error", "Required output is not published.")
            if (previous == "running" and state in {"waiting_source", "waiting_signal"}) or state in {"error", "stopping", "stopped"}:
                self._revoke_media(entry)
            entry.status = status
            entry.last_worker_revision = status["status_revision"]
            entry.last_seen = time.monotonic()
            if state == "error":
                return

    async def _watchdog(self, entry):
        """Detect blocked startup even when a worker still emits starting heartbeats."""
        while True:
            await asyncio.sleep(1)
            if entry.status["state"] == "starting" and time.monotonic() - entry.spawned_at > START_TIMEOUT:
                raise TimeoutError("Worker startup deadline exceeded")
            if time.monotonic() - entry.last_seen > HEARTBEAT_TIMEOUT:
                raise TimeoutError("Worker heartbeat deadline exceeded")

    async def _watch(self, entry, reader):
        """Reap the process, release leases and schedule at most three recovery attempts."""
        events = asyncio.create_task(self._events(entry, reader))
        exited = asyncio.create_task(entry.process.wait())
        watchdog = asyncio.create_task(self._watchdog(entry))
        sender = asyncio.create_task(self._send_subscriptions(entry))
        try:
            done, _ = await asyncio.wait([events, exited, watchdog, sender], return_when=asyncio.FIRST_COMPLETED)
            if exited not in done:
                try:
                    for task in done:
                        task.result()
                except Exception as error:
                    entry.failure_reason = "timeout" if isinstance(error, TimeoutError) else "pipeline_error"
                await self._terminate(entry.process)
            await exited
        finally:
            self._revoke_media(entry)
            for task in (events, exited, watchdog, sender):
                task.cancel()
            await asyncio.gather(events, exited, watchdog, sender, return_exceptions=True)
            code = entry.process.returncode
            self._signal(entry.process, signal.SIGKILL)
            entry.last_exit_code = code
            self._log(entry, "worker.exited")
            entry.process = None
            entry.writer.close()
            self._cleanup_media(entry)
            self._release(entry)
            manual_stop = not entry.desired_running
            existing_error = entry.status.get("error")
            retryable = not manual_stop and code != 78 and (existing_error is None or existing_error["retryable"])
            if manual_stop and code == 0:
                self._terminal(entry, "stopped", None)
            else:
                error = existing_error or {"code": entry.failure_reason or "pipeline_error", "message": "Worker exited unexpectedly or exceeded a lifecycle deadline.", "retryable": retryable}
                self._terminal(entry, "error", error)
            if retryable and entry.restart_count < RETRY_LIMIT and not self.closing:
                entry.restart_task = asyncio.create_task(self._recover(entry))
                self._log(entry, "worker.recovery_scheduled")
            else:
                entry.desired_running = False

    def _terminal(self, entry, state, error):
        """Record a final supervisor observation after the worker channel has ended."""
        entry.status = {**entry.status, "state": state, "error": error, "status_revision": entry.status["status_revision"] + 1, "updated_at": utc_now()}

    async def _recover(self, entry):
        """Back off without resetting the retry budget; revalidate the exact config."""
        await asyncio.sleep(2 ** entry.restart_count)
        async with self._lock_for(entry.config["id"]):
            if self.closing or not entry.desired_running:
                return
            entry.restart_count += 1
            try:
                current = await asyncio.to_thread(self.nodes.get, entry.config["id"])
                if current != entry.config:
                    raise ConfigError("invalid_config", "Config changed during worker recovery.")
                await asyncio.wait_for(self._preflight(entry), 75)
                await self._spawn(entry)
            except asyncio.CancelledError:
                self._release(entry)
                entry.desired_running = False
                raise
            except Exception:
                self._release(entry)
                entry.desired_running = False
                self._terminal(entry, "error", {"code": "dependency_unavailable", "message": "Worker recovery failed preflight; manual action is required.", "retryable": False})

    @staticmethod
    def _signal(process, value):
        """Signal only this spawned worker/probe process group."""
        try:
            os.killpg(process.pid, value)
        except ProcessLookupError:
            pass

    async def _terminate(self, process):
        """Bound graceful termination, then kill and reap an unresponsive process group."""
        if process.returncode is not None:
            self._signal(process, signal.SIGKILL)
            return
        self._signal(process, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), 2)
        except TimeoutError:
            self._signal(process, signal.SIGKILL)
            await process.wait()

    def _release(self, entry):
        """Release only resource leases owned by this logical node."""
        for lease in entry.leases:
            key = "decklink:" + lease["resource_id"]
            if self.resource_owners.get(key) == entry.config["id"]:
                self.resource_owners.pop(key)
        entry.leases = []

    async def _stop(self, entry):
        """Cancel recovery before requesting an idempotent stop and waiting for reaping."""
        entry.desired_running = False
        self._revoke_media(entry)
        self._log(entry, "worker.stop_requested")
        if entry.restart_task is not None:
            entry.restart_task.cancel()
        if entry.process is None:
            self._release(entry)
            if entry.status["state"] == "error":
                self._terminal(entry, "stopped", None)
            return
        try:
            await self._command(entry, "stop")
        except Exception:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(entry.watcher), STOP_TIMEOUT)
        except TimeoutError:
            entry.failure_reason = "timeout"
            if entry.process is not None:
                await self._terminate(entry.process)
            await entry.watcher

    async def close(self):
        """Cancel recovery and stop all children; no state is persisted for autostart."""
        self.closing = True
        recoveries = [entry.restart_task for entry in self.entries.values() if entry.restart_task is not None and not entry.restart_task.done()]
        for task in recoveries:
            task.cancel()
        if recoveries:
            await asyncio.gather(*recoveries, return_exceptions=True)
        await asyncio.gather(*(self._stop(entry) for entry in list(self.entries.values())), return_exceptions=True)
