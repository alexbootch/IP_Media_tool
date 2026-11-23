"""Linux-only SDK/lifecycle regression source; never executed in the Windows workspace."""

import asyncio
import copy
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

from converter.config.validation import ConfigError, SchemaRegistry
from converter.node_sdk.worker import WorkerContext
from converter.runtime.protocol import MAX_FRAME, encode_frame
from converter.runtime.supervisor import Supervisor, WorkerEntry


ROOT = Path(__file__).resolve().parents[1]
NODE_ID = "10000000-0000-4000-8000-000000000001"
INSTANCE_ID = "20000000-0000-4000-8000-000000000001"


def node_config():
    """Return inert config data; this fixture is never registered as a real plugin."""
    return {"schema_version": 1, "id": NODE_ID, "type": "fixture-input", "revision": 1, "settings_version": 1, "name": "Fixture", "enabled": True, "position": {"x": 0, "y": 0}, "settings": {}, "connections": {}, "secret_refs": {}}


class MemoryWriter:
    """Capture framed bytes without opening sockets or starting a subprocess."""

    def __init__(self):
        """Initialize an empty sequence of serialized writes."""
        self.frames = []

    def write(self, value):
        """Store a detached immutable frame."""
        self.frames.append(bytes(value))

    async def drain(self):
        """Complete immediately to isolate SDK lifecycle behavior."""
        return None


class MemoryStore:
    """Supply one config snapshot for cancellation tests, with no filesystem writes."""

    def get(self, node_id):
        """Return a detached fixture document for the expected node."""
        if node_id != NODE_ID:
            raise AssertionError("Unexpected fixture node")
        return node_config()


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Verify ownership, invalid transitions, transport gating and stop cancellation."""

    async def asyncSetUp(self):
        """Load the bundled schemas and a memory-only worker context."""
        self.schemas = SchemaRegistry(ROOT / "schemas" / "v1")
        self.writer = MemoryWriter()
        self.context = WorkerContext(node_config(), INSTANCE_ID, [], self.writer, self.schemas)

    async def test_context_config_is_detached(self):
        """Plugin edits cannot alter the SDK's applied config snapshot."""
        config = self.context.config
        config["settings"]["changed"] = True
        self.assertEqual(self.context.config["settings"], {})

    async def test_status_identity_is_immutable(self):
        """Reject a forged worker instance before emitting any bytes."""
        status = self.context.status_snapshot()
        status["instance_id"] = NODE_ID
        with self.assertRaises(ConfigError):
            await self.context.publish_status(status)
        self.assertEqual(self.writer.frames, [])

    async def test_invalid_transition_is_rejected(self):
        """A stopped worker cannot skip directly to running."""
        with self.assertRaises(ConfigError):
            await self.context.set_state("running")
        self.assertEqual(self.context.status_snapshot()["state"], "stopped")

    async def test_heartbeat_increases_revision_without_mutating_old_snapshot(self):
        """Published revisions advance even when the state does not change."""
        await self.context.set_state("starting")
        snapshot = self.context.status_snapshot()
        original = copy.deepcopy(snapshot)
        await self.context.publish_status(snapshot)
        self.assertEqual(snapshot, original)
        self.assertEqual(self.context.status_snapshot()["status_revision"], original["status_revision"] + 1)

    async def test_required_media_port_cannot_claim_readiness(self):
        """A producer cannot report running before a verified transport exists."""
        self.context.ports = [{"id": "media_out", "direction": "output", "required": True}]
        await self.context.set_state("starting")
        with self.assertRaises(ConfigError):
            await self.context.set_state("running")

    async def test_error_requires_a_structured_reason(self):
        """Reject an error status that lacks its required nonsecret error object."""
        await self.context.set_state("starting")
        with self.assertRaises(ConfigError):
            await self.context.set_state("error")

    async def test_stop_cancels_pending_start_without_a_process(self):
        """A long dependency preflight must not delay a manual stop until it times out."""
        supervisor = Supervisor(ROOT, None, MemoryStore())
        entered = asyncio.Event()

        async def pending_preflight(entry):
            """Wait until the stop operation cancels this startup attempt."""
            entered.set()
            await asyncio.Event().wait()

        supervisor._preflight = pending_preflight
        supervisor._spawn = AsyncMock()
        startup = asyncio.create_task(supervisor.dispatch("start", {"node_id": NODE_ID, "expected_revision": 1}))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            result = await asyncio.wait_for(supervisor.dispatch("stop", {"node_id": NODE_ID}), 1)
            self.assertEqual(result["state"], "stopped")
            self.assertFalse(result["process_alive"])
            supervisor._spawn.assert_not_awaited()
            with self.assertRaises(asyncio.CancelledError):
                await startup
        finally:
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)

    async def test_stop_cancels_recovery_and_releases_own_lease(self):
        """A queued retry cannot resurrect a manually stopped worker."""
        supervisor = Supervisor(ROOT, None, MemoryStore())
        await self.context.set_state("starting")
        await self.context.set_state("error", {"code": "pipeline_error", "message": "Fixture failure", "retryable": True})
        entry = WorkerEntry(config=node_config(), instance_id=INSTANCE_ID, status=self.context.status_snapshot(), leases=[{"provider": "decklink", "resource_id": "fixture", "direction": "input"}])
        entry.restart_task = asyncio.create_task(asyncio.sleep(60))
        supervisor.entries[NODE_ID] = entry
        supervisor.resource_owners["decklink:fixture"] = NODE_ID
        await supervisor.dispatch("stop", {"node_id": NODE_ID})
        self.assertTrue(entry.restart_task.cancelled())
        self.assertFalse(entry.desired_running)
        self.assertEqual(entry.status["state"], "stopped")
        self.assertEqual(supervisor.resource_owners, {})

    async def test_frame_size_is_bounded(self):
        """Refuse an oversize message before sending it to another process."""
        with self.assertRaises(ConfigError):
            encode_frame({"data": "x" * MAX_FRAME})
