"""Synchronous web-to-runtime RPC; no supervisor is created in the web process."""

import socket
import struct
from uuid import uuid4

from converter.config.validation import ConfigError, decode_json
from converter.runtime.protocol import MAX_FRAME, encode_frame, peer_credentials


def receive_exact(connection, length):
    """Read exactly one frame segment or fail when runtime closes the channel."""
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise ConnectionError("Runtime connection ended")
        data.extend(chunk)
    return bytes(data)


class RuntimeClient:
    """Send explicit commands to a matching release over an owner-only local socket."""

    def __init__(self, path, application_revision):
        """Configure the socket; do not start or connect to the service automatically."""
        self.path, self.application_revision = str(path), application_revision

    def call(self, operation, **arguments):
        """Return a correlated reply; never silently retry a possibly committed command."""
        import os

        request_id = str(uuid4())
        request = {"schema_version": 1, "request_id": request_id, "application_revision": self.application_revision, "operation": operation, "arguments": arguments}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(90)
                connection.connect(self.path)
                if peer_credentials(connection)[1] != os.getuid():
                    raise ConfigError("runtime_unavailable", "Runtime socket owner is invalid.", 503)
                connection.sendall(encode_frame(request))
                length = struct.unpack("!I", receive_exact(connection, 4))[0]
                if not 0 < length <= MAX_FRAME:
                    raise ConfigError("invalid_message", "Invalid runtime response size.", 503)
                response = decode_json(receive_exact(connection, length))
        except OSError:
            raise ConfigError("runtime_unavailable", "Runtime is unavailable or command completion is unknown. Check node status/config before retrying; start ip-media-tool-runtime manually if stopped.", 503) from None
        if not isinstance(response, dict) or response.get("schema_version") != 1 or response.get("request_id") != request_id:
            raise ConfigError("invalid_message", "Runtime response did not match the request.", 503)
        if not response.get("ok"):
            error = response.get("error", {})
            raise ConfigError(error.get("code", "internal_error"), error.get("message", "Runtime command failed."), error.get("status", 503), error.get("issues", []))
        return response["result"]
