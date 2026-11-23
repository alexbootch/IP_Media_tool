"""Shared status lifecycle and explicit unsupported transport/secret operations."""

from abc import ABC, abstractmethod
import asyncio
import copy
from datetime import datetime, timezone
import json
import logging
from types import MappingProxyType

from converter.config.validation import ConfigError
from converter.runtime.protocol import MAX_STATUS, encode_frame, envelope, write_frame


TRANSITIONS = {
    "stopped": {"starting"},
    "starting": {"waiting_source", "waiting_signal", "running", "error", "stopping"},
    "waiting_source": {"waiting_signal", "running", "error", "stopping"},
    "waiting_signal": {"waiting_source", "running", "error", "stopping"},
    "running": {"waiting_source", "waiting_signal", "error", "stopping"},
    "error": {"stopping"},
    "stopping": {"stopped", "error"},
}


def utc_now():
    """Return timezone-aware RFC3339 timestamps for status snapshots."""
    return datetime.now(timezone.utc).isoformat()


class NodeLogger:
    """Log event codes and session identity without arbitrary settings or secret values."""

    def __init__(self, node_id, node_type, instance_id):
        """Bind stable audit fields to one worker process."""
        self.fields = {"node_id": node_id, "node_type": node_type, "instance_id": instance_id}
        self.logger = logging.getLogger("ip-media-tool.worker")

    def event(self, event_code, level=logging.INFO):
        """Write only a bounded identifier; callers cannot log secret-bearing dicts."""
        import re

        if not isinstance(event_code, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,100}", event_code):
            raise ValueError("A bounded event code is required")
        self.logger.log(level, json.dumps({**self.fields, "event_code": event_code}))


class WorkerContext:
    """Own immutable snapshot boundaries, cancellation, leases and IPC publication."""

    def __init__(self, config, instance_id, leases, writer, schemas):
        """Copy startup data; never retain a reference to the producer's config dict."""
        self._config = copy.deepcopy(config)
        self.node_id, self.instance_id = config["id"], instance_id
        self.leases = tuple(MappingProxyType(copy.deepcopy(lease)) for lease in leases)
        self.cancelled = asyncio.Event()
        self.logger = NodeLogger(self.node_id, config["type"], instance_id)
        self.writer, self.schemas = writer, schemas
        self._write_lock = asyncio.Lock()
        self._status = {"schema_version": 1, "node_id": self.node_id, "node_type": config["type"], "instance_id": instance_id, "config_revision": config["revision"], "status_revision": 1, "state": "stopped", "updated_at": utc_now(), "error": None, "metrics": {}}

    @property
    def config(self):
        """Return a detached copy so plugins cannot change the applied revision."""
        return copy.deepcopy(self._config)

    def status_snapshot(self):
        """Return a detached status without hardware access or live dict exposure."""
        return copy.deepcopy(self._status)

    async def publish_status(self, status):
        """Validate identity, revision and state before publishing a new snapshot."""
        async with self._write_lock:
            status = copy.deepcopy(status)
            for key in ("node_id", "node_type", "instance_id", "config_revision"):
                if status.get(key) != self._status[key]:
                    raise ConfigError("invalid_status", "Worker status identity is immutable.")
            previous, state = self._status["state"], status.get("state")
            if state != previous and state not in TRANSITIONS.get(previous, set()):
                raise ConfigError("invalid_status", "Invalid worker state transition.")
            if state == "running" and any(port["direction"] == "output" and port["required"] for port in getattr(self, "ports", [])):
                raise ConfigError("dependency_unavailable", "Required media publication is unavailable until transport validation is implemented.")
            status.update(status_revision=self._status["status_revision"] + 1, updated_at=utc_now())
            self.schemas.validate("node-status", status)
            if len(encode_frame(status)) > MAX_STATUS:
                raise ConfigError("invalid_status", "Worker status exceeds 48 KiB.")
            await write_frame(self.writer, envelope(self.node_id, self.instance_id, "status", status))
            self._status = status
            if state != previous:
                self.logger.event("worker.state." + state)

    async def set_state(self, state, error=None):
        """Convenience method shared by all node types; preserve current metrics."""
        status = self.status_snapshot()
        status.update(state=state, error=error)
        await self.publish_status(status)

    async def send(self, kind, payload, request_id):
        """Serialize command replies with status events on the same stream."""
        async with self._write_lock:
            await write_frame(self.writer, envelope(self.node_id, self.instance_id, kind, payload, request_id))

    async def publish_contract(self, source_port_id, contract):
        """Fail closed until a verified SHM transport and descriptor validator exist."""
        raise ConfigError("dependency_unavailable", "Media transport publication is not implemented.")

    async def withdraw_contract(self, source_port_id, reason):
        """Reject transport operations rather than pretending to revoke live buffers."""
        raise ConfigError("dependency_unavailable", "Media transport withdrawal is not implemented.")

    async def get_secret(self, secret_ref):
        """Expose no credentials until the scoped secret broker is implemented."""
        raise ConfigError("dependency_unavailable", "Worker secret broker is not implemented.")


class NodeWorker(ABC):
    """Standard plugin interface; the supervisor, not plugins, owns process restarts."""

    def __init__(self, context):
        """Bind a single worker instance to its validated startup context."""
        self.context = context

    async def validate_config(self, config):
        """Return node-specific issues without acquiring hardware; schemas ran earlier."""
        return []

    @abstractmethod
    async def start(self):
        """Install protocol handlers and publish waiting/readiness status promptly."""

    @abstractmethod
    async def stop(self):
        """Idempotently release only this instance's pipeline, handles and tasks."""

    async def get_status(self):
        """Return the SDK-owned snapshot without probing hardware."""
        return self.context.status_snapshot()

    async def on_contract(self, local_port_id, contract):
        """Provide a default no-op for producer-only plugins."""
        return None
