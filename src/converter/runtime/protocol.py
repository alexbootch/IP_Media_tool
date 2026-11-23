"""Bounded, length-prefixed JSON over private Unix sockets."""

import asyncio
import json
import socket
import struct

from converter.config.validation import ConfigError, decode_json


MAX_FRAME = 65536
MAX_STATUS = 48 * 1024
IO_TIMEOUT = 5


def encode_frame(value):
    """Serialize a finite JSON frame without shell interpolation or pickle."""
    data = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if not 0 < len(data) <= MAX_FRAME:
        raise ConfigError("invalid_message", "IPC frame exceeds 64 KiB.", 413)
    return struct.pack("!I", len(data)) + data


async def read_frame(reader, timeout=IO_TIMEOUT):
    """Read one bounded frame; reject truncated, duplicate-key or non-object JSON."""
    header = await asyncio.wait_for(reader.readexactly(4), timeout)
    length = struct.unpack("!I", header)[0]
    if not 0 < length <= MAX_FRAME:
        raise ConfigError("invalid_message", "Invalid IPC frame size.")
    value = decode_json(await asyncio.wait_for(reader.readexactly(length), timeout))
    if not isinstance(value, dict):
        raise ConfigError("invalid_message", "IPC message must be an object.")
    return value


async def write_frame(writer, value):
    """Apply bounded backpressure rather than hanging on a silent peer."""
    writer.write(encode_frame(value))
    await asyncio.wait_for(writer.drain(), IO_TIMEOUT)


def peer_credentials(unix_socket):
    """Return Linux peer PID/UID/GID; filesystem permissions alone are insufficient."""
    return struct.unpack("3i", unix_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))


def envelope(node_id, instance_id, kind, payload, request_id=None):
    """Build an IPC v1 event tied to a single worker session."""
    from uuid import uuid4

    return {"schema_version": 1, "message_id": str(uuid4()), "request_id": request_id, "node_id": node_id, "instance_id": instance_id, "kind": kind, "payload": payload}
