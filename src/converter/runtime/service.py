"""Private Unix RPC daemon and systemd-owned worker process tree."""

import asyncio
import fcntl
import logging
import os
from pathlib import Path
import signal
import stat
import tempfile

from converter.config.modules import ModuleRegistry
from converter.config.store import NodeConfigStore
from converter.config.validation import ConfigError, SchemaRegistry, canonical_id
from converter.runtime.protocol import peer_credentials, read_frame, write_frame
from converter.runtime.supervisor import Supervisor


LOGGER = logging.getLogger("ip-media-tool.runtime")
OPERATIONS = {"start", "stop", "status", "list", "logs", "save_config", "delete_config"}


class RuntimeServer:
    """Authenticate local peers and isolate malformed requests from worker supervision."""

    def __init__(self, supervisor):
        """Track bounded concurrent handlers for cancellation before service shutdown."""
        self.supervisor = supervisor
        self.handlers = set()

    async def handle(self, reader, writer):
        """Serve one framed request from root or the runtime service UID."""
        task = asyncio.current_task()
        if len(self.handlers) >= 32:
            writer.close()
            return
        self.handlers.add(task)
        request_id = None
        try:
            if peer_credentials(writer.get_extra_info("socket"))[1] not in {0, os.getuid()}:
                raise ConfigError("forbidden", "Runtime peer is not authorized.", 403)
            request = await read_frame(reader)
            request_id = canonical_id(request.get("request_id"))
            if request.get("schema_version") != 1 or request.get("operation") not in OPERATIONS or not isinstance(request.get("arguments"), dict):
                raise ConfigError("invalid_message", "Unsupported runtime request.")
            operation = request["operation"]
            if operation not in {"list", "status", "logs"} and request.get("application_revision") != self.supervisor.root.name:
                raise ConfigError("incompatible_version", "Web and runtime releases differ; stop runtime before updating and start the new service manually.", 409)
            result = await asyncio.wait_for(self.supervisor.dispatch(operation, request["arguments"]), 85)
            await write_frame(writer, {"schema_version": 1, "request_id": request_id, "ok": True, "result": result})
            if operation != "logs":
                LOGGER.info("runtime.command operation=%s request_id=%s", operation, request_id)
        except ConfigError as error:
            await self._error(writer, request_id, error)
        except (KeyError, TypeError, ValueError):
            await self._error(writer, request_id, ConfigError("invalid_message", "Runtime arguments are invalid."))
        except Exception:
            LOGGER.error("runtime.command_failed request_id=%s", request_id)
            await self._error(writer, request_id, ConfigError("runtime_unavailable", "Runtime operation failed or completion is unknown; inspect status before retrying.", 503))
        finally:
            self.handlers.discard(task)
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except Exception:
                pass

    async def _error(self, writer, request_id, error):
        """Return only safe diagnostics; never echo configs, argv or secret values."""
        try:
            await write_frame(writer, {"schema_version": 1, "request_id": request_id, "ok": False, "error": {"code": error.code, "message": str(error), "status": error.status, "issues": error.issues[:40]}})
        except Exception:
            pass


async def serve():
    """Hold a singleton lock, remove only a stale owned socket and start no nodes."""
    root = Path(__file__).resolve().parents[3]
    path = Path(os.environ.get("IP_MEDIA_TOOL_RUNTIME_SOCKET", "/run/ip-media-tool/runtime.sock"))
    if path.parent.is_symlink():
        raise RuntimeError("Runtime socket directory must not be a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(path.parent / ".runtime.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    server, supervisor, endpoint, media = None, None, None, None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists() or path.is_symlink():
            metadata = path.lstat()
            if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise RuntimeError("Refusing to replace an unowned/non-socket runtime path")
            path.unlink()
        schemas = SchemaRegistry(root / "schemas" / "v1")
        modules = ModuleRegistry(root / "plugins", schemas)
        modules.scan()
        nodes = NodeConfigStore(os.environ.get("IP_MEDIA_TOOL_NODE_DIR", "/etc/ip-media-tool/nodes"), modules)
        nodes.initialize()
        media = tempfile.TemporaryDirectory(prefix="media-", dir=path.parent)
        supervisor = Supervisor(root, modules, nodes, media.name)
        endpoint = RuntimeServer(supervisor)
        server = await asyncio.start_unix_server(endpoint.handle, path=path, limit=65540, backlog=32)
        os.chmod(path, 0o600)
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()

        def request_shutdown():
            """Disable all recovery before systemd terminates the worker process tree."""
            supervisor.closing = True
            for entry in supervisor.entries.values():
                entry.desired_running = False
            stopped.set()

        loop.add_signal_handler(signal.SIGTERM, request_shutdown)
        loop.add_signal_handler(signal.SIGINT, request_shutdown)
        await stopped.wait()
    finally:
        if supervisor is not None:
            supervisor.closing = True
        if server is not None:
            server.close()
        if endpoint is not None:
            handlers = list(endpoint.handlers)
            for task in handlers:
                task.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)
        if server is not None:
            await server.wait_closed()
        if supervisor is not None:
            await supervisor.close()
        if media is not None:
            media.cleanup()
        if server is not None and path.exists() and stat.S_ISSOCK(path.lstat().st_mode):
            path.unlink()
        os.close(lock)


def main():
    """Run only when explicitly started by systemd/operator, with no config autostart."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(serve())


if __name__ == "__main__":
    main()
