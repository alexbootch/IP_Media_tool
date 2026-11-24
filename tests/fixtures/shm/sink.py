"""Linux acceptance sink with an independent cursor and optional slow reader."""

import asyncio

from converter.node_sdk import DISCONT, NodeWorker
from converter.node_sdk.gst_shm import gst, to_gst_buffer


class Worker(NodeWorker):
    """Consume the exact same A/V shape in fast and deliberately slow processes."""

    async def start(self):
        """Wait for the input contract without blocking the supervisor."""
        self.reader, self.task = None, None
        self.gst = gst()
        self.metrics = {"pairs": 0, "skipped": 0, "discontinuities": 0, "bad_samples": 0, "max_skew_ns": 0}
        await self.context.set_state("waiting_source")

    async def on_contract(self, local_port_id, contract):
        """Replace readers at generation boundaries and wait after a withdrawal."""
        await self.stop()
        if contract is None:
            await self.context.set_state("waiting_source")
            return
        self.reader = self.context.open_shm(contract)
        self.metrics["generation"] = contract["transport_generation"]
        self.task = asyncio.create_task(self.consume())

    async def consume(self):
        """Match timestamps and inspect sample bytes without retaining ring locks."""
        delay = self.context.config["settings"].get("delay", 0.003)
        pending = {"v": {}, "a": {}}
        last_pts = {"v": -1, "a": -1}
        while True:
            for endpoint in pending:
                frame = self.reader.read(endpoint)
                if frame is None:
                    continue
                self.metrics["skipped"] += frame.skipped
                self.metrics["discontinuities"] += bool(frame.flags & DISCONT)
                expected = (frame.pts // 40_000_000) % 100 + 1
                size = 1024 if endpoint == "v" else 7680
                buffer = to_gst_buffer(self.gst, frame)
                if len(frame.data) != size or frame.data[0] != expected or (endpoint == "a" and frame.data[2] != 255 - expected) or frame.pts <= last_pts[endpoint] or frame.pts % 40_000_000 or buffer.pts != frame.pts or buffer.duration != 40_000_000 or buffer.has_flags(self.gst.BufferFlags.DISCONT) != bool(frame.flags & DISCONT):
                    self.metrics["bad_samples"] += 1
                last_pts[endpoint] = frame.pts
                own, other = pending[endpoint], pending["a" if endpoint == "v" else "v"]
                if frame.pts in other:
                    other.pop(frame.pts)
                    self.metrics["pairs"] += 1
                    if self.context.status_snapshot()["state"] != "running":
                        await self.context.set_state("running")
                else:
                    own[frame.pts] = True
                    if len(own) > 16:
                        own.pop(next(iter(own)))
            if min(last_pts.values()) >= 0:
                self.metrics["max_skew_ns"] = max(self.metrics["max_skew_ns"], abs(last_pts["v"] - last_pts["a"]))
            await asyncio.sleep(delay)

    async def get_status(self):
        """Expose detached acceptance counters through the normal SDK heartbeat."""
        status = await super().get_status()
        if hasattr(self, "metrics"):
            status["metrics"] = dict(self.metrics)
        return status

    async def stop(self):
        """Close only this sink's task and mappings."""
        if getattr(self, "task", None) is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if getattr(self, "reader", None) is not None:
            self.reader.close()
            self.reader = None
