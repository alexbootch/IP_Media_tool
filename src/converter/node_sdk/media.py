"""SHM contract validation and generation ownership for the worker SDK."""

import copy
from contextlib import ExitStack
from pathlib import Path
import shutil
from uuid import uuid4

from converter.config.validation import ConfigError
from converter.node_sdk.gst_shm import validate_caps
from converter.node_sdk.shm import MAX_BYTES, Ring, ring_path, ring_size


class GenerationRetired(Exception):
    """A valid queued snapshot was superseded before its consumer opened the files."""


def validate_sample(layout, frame):
    """Reject noncanonical frame sizes and incomplete interleaved PCM samples."""
    exact, alignment = layout
    size = len(frame.data)
    if (exact and size != alignment) or (not exact and size % alignment):
        raise ConfigError("unsupported_format", "Sample bytes do not match the canonical raw layout.")


def validate_contract(schemas, root, contract, *, inspect_rings=False):
    """Validate stream topology and ABI; optionally inspect owned ring headers."""
    schemas.validate("media-contract", contract)
    streams = ([contract["video"]] if contract["video"] else []) + contract["audio"]
    endpoints = contract["endpoints"]
    expected_ids = {stream["endpoint_id"] for stream in streams}
    if len({stream["stream_id"] for stream in streams}) != len(streams) or len(expected_ids) != len(streams) or len(endpoints) != len(streams) or {item["endpoint_id"] for item in endpoints} != expected_ids:
        raise ConfigError("invalid_config", "SHM v1 requires unique stream IDs and one endpoint per stream.")
    for stream in contract["audio"]:
        labels = stream["channel_labels"]
        if labels is not None and len(labels) != stream["channels"]:
            raise ConfigError("invalid_config", "Audio label count must match channel count.")
    if contract["timing"]["clock_domain_id"] != contract["transport_generation"]:
        raise ConfigError("invalid_config", "SHM v1 uses its generation as the running-time domain ID.")
    size = 0
    for item in endpoints:
        descriptor = item["descriptor"]
        if item["descriptor_version"] != 1 or descriptor.keys() != {"abi", "slots", "slot_bytes"} or descriptor["abi"] != "mmap_ring_v1":
            raise ConfigError("unsupported_format", "Unsupported SHM descriptor.")
        size += ring_size(descriptor["slots"], descriptor["slot_bytes"])
        if size > MAX_BYTES:
            raise ConfigError("unsupported_format", "SHM generation exceeds 128 MiB.")
        path = ring_path(root, contract["instance_id"], contract["transport_generation"], item["endpoint_id"])
        if inspect_rings:
            if path.parent.is_symlink() or path.parent.parent.is_symlink():
                raise ConfigError("transport_error", "Invalid SHM directory.")
            ring = Ring(path, descriptor["slots"], descriptor["slot_bytes"])
            ring.close()


class ShmGeneration:
    """Producer-owned set of rings. Publish only after actual caps and allocation exist."""

    def __init__(self, context, source_port_id, video, audio, buffers):
        """Allocate one ring per stream using descriptors and a fresh generation UUID."""
        self.context = context
        self.port_id = source_port_id
        self.generation = str(uuid4())
        self.directory = Path(context.media_root) / context.instance_id / self.generation
        self.rings = {}
        self.closed = False
        self.published = False
        self._contract = {
            "schema_version": 1, "source_node_id": context.node_id, "source_port_id": source_port_id,
            "instance_id": context.instance_id, "contract_revision": 1, "transport_generation": self.generation,
            "state": "ready", "video": copy.deepcopy(video), "audio": copy.deepcopy(audio),
            "endpoints": [{"endpoint_id": key, "transport": "shm", "descriptor_version": 1,
                           "descriptor": {"abi": "mmap_ring_v1", "slots": values[0], "slot_bytes": values[1]}} for key, values in buffers.items()],
            "timing": {"model_version": 1, "clock_domain_id": self.generation, "timestamp_unit": "ns"},
        }
        validate_contract(context.schemas, context.media_root, self._contract)
        self.layouts = validate_caps(self._contract["video"], self._contract["audio"])
        total = sum(ring_size(ring.slots, ring.slot_bytes) for item in context.generations.values() for ring in item.rings.values())
        total += sum(ring_size(*dimensions) for dimensions in buffers.values())
        if total > 2 * MAX_BYTES:
            raise ConfigError("unsupported_format", "Worker SHM allocation exceeds 256 MiB.")
        self.directory.mkdir(mode=0o700)
        try:
            for key, (slots, slot_bytes) in buffers.items():
                if slot_bytes < self.layouts[key][1]:
                    raise ConfigError("unsupported_format", "SHM slot is smaller than one canonical media frame.")
                path = ring_path(context.media_root, context.instance_id, self.generation, key)
                self.rings[key] = Ring(path, slots, slot_bytes, create=True)
        except BaseException:
            self._close()
            raise

    def write(self, endpoint_id, frame):
        """Commit one normalized frame; return false if a reader is copying a slot."""
        validate_sample(self.layouts[endpoint_id], frame)
        return self.rings[endpoint_id].write(frame)

    async def publish(self):
        """Announce this ready generation via the authenticated worker channel."""
        await self.context.publish_contract(self.port_id, self._contract)

    async def close(self):
        """Withdraw a live generation before closing its files, or discard a draft."""
        if self.context.cancelled.is_set():
            # Runtime revokes this worker's routes on stop/exit and reaps its files.
            return
        if self.context.live_generations.get(self.port_id) is self:
            await self.context.withdraw_contract(self.port_id, "generation_closed")
        else:
            self._close()

    def _close(self):
        """Unlink only this generation; already mapped readers retain their inode."""
        if self.closed:
            return
        self.closed = True
        for ring in self.rings.values():
            ring.close()
        shutil.rmtree(self.directory, ignore_errors=True)
        self.context.generations.pop(self.generation, None)


class ShmReader:
    """Independent read-only cursors for all streams in a received contract."""

    def __init__(self, context, contract):
        """Open each ring and roll back all opened descriptors on partial failure."""
        validate_contract(context.schemas, context.media_root, contract)
        self.layouts = validate_caps(contract["video"], contract["audio"])
        self.rings = {}
        with ExitStack() as pending:
            for item in contract["endpoints"]:
                endpoint = item["endpoint_id"]
                path = ring_path(context.media_root, contract["instance_id"], contract["transport_generation"], endpoint)
                descriptor = item["descriptor"]
                try:
                    ring = Ring(path, descriptor["slots"], descriptor["slot_bytes"])
                except FileNotFoundError:
                    raise GenerationRetired from None
                pending.callback(ring.close)
                self.rings[endpoint] = ring
            self.files = pending.pop_all()

    def read(self, endpoint_id, *, newest=False):
        """Copy one sample, optionally skipping history when joining a live feed."""
        frame = self.rings[endpoint_id].read(newest=newest)
        if frame is not None:
            validate_sample(self.layouts[endpoint_id], frame)
        return frame

    def close(self):
        """Release the reader mappings without affecting other subscribers."""
        self.files.close()
