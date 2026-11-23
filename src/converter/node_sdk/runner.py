"""Disposable plugin host; only this child imports the selected worker module."""

import argparse
import asyncio
from collections import deque
import importlib.util
import inspect
import logging
import os
from pathlib import Path
import signal
import socket

from converter.config.modules import ModuleRegistry
from converter.config.validation import ConfigError, SchemaRegistry, canonical_id
from converter.node_sdk.worker import NodeWorker, WorkerContext
from converter.runtime.protocol import peer_credentials, read_frame


async def initialize_worker(worker, context):
    """Validate plugin-specific settings and bound the asynchronous startup phase."""
    issues = await asyncio.wait_for(worker.validate_config(context.config), 5)
    if not isinstance(issues, list) or issues:
        raise ConfigError("invalid_config", "Worker-specific configuration validation failed.")
    await asyncio.wait_for(worker.start(), 20)
    if context.status_snapshot()["state"] == "starting":
        await context.set_state("waiting_source" if context.config["connections"] else "waiting_signal")


async def heartbeat(worker, context):
    """Prove event-loop responsiveness and propagate plugin-reported failures."""
    while not context.cancelled.is_set():
        await asyncio.sleep(2)
        await context.publish_status(await asyncio.wait_for(worker.get_status(), 3))


async def commands(reader, worker, context):
    """Handle bounded, validated session commands; duplicate requests never rerun work."""
    seen = deque(maxlen=128)
    while not context.cancelled.is_set():
        message = await read_frame(reader, timeout=None)
        context.schemas.validate("ipc-message", message)
        if message["node_id"] != context.node_id or message["instance_id"] != context.instance_id or message["kind"] != "command":
            raise ConfigError("invalid_message", "Worker session mismatch.")
        request_id = message["request_id"]
        command = message["payload"]["command"]
        if message["payload"]["config_revision"] != context.config["revision"]:
            await context.send("error", {"code": "invalid_config", "message": "Applied config revision mismatch.", "retryable": False}, request_id)
            continue
        await context.send("ack", {"accepted": True}, request_id)
        if request_id in seen:
            continue
        seen.append(request_id)
        if command == "stop":
            context.cancelled.set()
            return
        if command in {"get_status", "resync"}:
            await context.publish_status(await asyncio.wait_for(worker.get_status(), 3))
        # start is idempotent: the explicit supervisor spawn/bootstrap starts once.


def load_worker(root, node_type):
    """Load one trusted plugin as a package, enabling only its own relative helpers."""
    directory = (root / "plugins" / node_type).resolve(strict=True)
    if not directory.is_relative_to(root / "plugins"):
        raise ConfigError("invalid_config", "Plugin path escapes the release.")
    path = directory / "worker.py"
    if path.is_symlink():
        raise ConfigError("invalid_config", "Worker entry must not be a symlink.")
    name = "ip_media_plugin_" + node_type.replace("-", "_")
    specification = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(directory)])
    if specification is None or specification.loader is None:
        raise ConfigError("dependency_unavailable", "Plugin entry cannot be loaded.")
    import sys

    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    try:
        specification.loader.exec_module(module)
    except (ImportError, SyntaxError):
        raise ConfigError("dependency_unavailable", "Plugin import/dependency validation failed.") from None
    worker_class = getattr(module, "Worker", None)
    if not inspect.isclass(worker_class) or not issubclass(worker_class, NodeWorker) or inspect.isabstract(worker_class):
        raise ConfigError("incompatible_version", "Plugin Worker must inherit NodeWorker.")
    for method in ("validate_config", "start", "stop", "get_status", "on_contract"):
        if not inspect.iscoroutinefunction(getattr(worker_class, method, None)):
            raise ConfigError("incompatible_version", "Worker lifecycle methods must be async.")
    return worker_class


async def run(descriptor):
    """Authenticate the inherited channel and cleanly stop on EOF, failure or signal."""
    connection = socket.socket(fileno=descriptor)
    pid, uid, _ = peer_credentials(connection)
    if pid != os.getppid() or uid != os.getuid():
        connection.close()
        return 78
    reader, writer = await asyncio.open_connection(sock=connection)
    context, worker = None, None
    tasks = []
    exit_code = 0
    try:
        root = Path(__file__).resolve().parents[3]
        schemas = SchemaRegistry(root / "schemas" / "v1")
        bootstrap = await read_frame(reader, 20)
        schemas.validate("worker-bootstrap", bootstrap)
        canonical_id(bootstrap["instance_id"])
        modules = ModuleRegistry(root / "plugins", schemas)
        modules.scan()
        modules.validate_config(bootstrap["config"])
        context = WorkerContext(bootstrap["config"], bootstrap["instance_id"], bootstrap["leases"], writer, schemas)
        context.ports = modules.get(context.config["type"])["manifest"]["ports"]
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, context.cancelled.set)
        loop.add_signal_handler(signal.SIGINT, context.cancelled.set)
        await context.set_state("starting")
        worker = load_worker(root, context.config["type"])(context)
        startup = asyncio.create_task(initialize_worker(worker, context))
        beat = asyncio.create_task(heartbeat(worker, context))
        command_task = asyncio.create_task(commands(reader, worker, context))
        stopped = asyncio.create_task(context.cancelled.wait())
        tasks = [startup, beat, command_task, stopped]
        while True:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if stopped in done:
                break
            for task in done:
                task.result()
                if task is startup:
                    tasks.remove(startup)
                else:
                    raise ConnectionError("Worker control task ended")
            if context.status_snapshot()["state"] == "error":
                exit_code = 1 if context.status_snapshot()["error"]["retryable"] else 78
                break
    except Exception as error:
        exit_code = 78 if isinstance(error, ConfigError) else 1
        if context is not None:
            try:
                code = error.code if isinstance(error, ConfigError) and error.code in {"invalid_config", "incompatible_version", "dependency_unavailable"} else "pipeline_error"
                await context.set_state("error", {"code": code, "message": "Worker initialization or control failed; inspect node dependencies and configuration.", "retryable": exit_code != 78})
                context.logger.event("worker.failed", logging.ERROR)
            except Exception:
                pass
    finally:
        if context is not None:
            context.cancelled.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if worker is not None:
            try:
                if exit_code == 0:
                    await context.set_state("stopping")
                await asyncio.wait_for(worker.stop(), 5)
                if exit_code == 0:
                    await context.set_state("stopped")
            except Exception:
                exit_code = 1
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1)
        except Exception:
            pass
    return exit_code


def main():
    """Accept only an inherited FD; settings/secrets never appear in process argv."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipc-fd", required=True, type=int)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return asyncio.run(run(arguments.ipc_fd))


if __name__ == "__main__":
    raise SystemExit(main())
