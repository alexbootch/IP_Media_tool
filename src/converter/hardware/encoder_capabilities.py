"""Background, cached GPU capability inventory adapted from StreamMachine.

Only the child process loads CUDA/GStreamer. A probe failure never becomes an
empty successful inventory or blocks the authentication/control event loop.
"""

import copy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from threading import Event, Lock, Thread


LOGGER = logging.getLogger("ip-media-tool.encoder-capabilities")
CACHE_SCHEMA = 1
MAX_OUTPUT_BYTES = 2_000_000


class EncoderCapabilityManager:
    def __init__(self, cache_path):
        self.cache_path = Path(cache_path)
        self._lock = Lock()
        self._stop = Event()
        self._thread = None
        self._process = None
        self._state = {
            "query_running": False, "initialized": False, "last_error": None,
            "queried_at": None, "from_cache": False, "stale": False,
            "fingerprint": None, "gstreamer_version": None, "adapters": [],
        }

    def initialize(self):
        # Even discovery/nvidia-smi/cache validation run outside the web startup path.
        self.start_query(force=False)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._state)

    def start_query(self, force=True):
        with self._lock:
            if self._stop.is_set() or self._state["query_running"]:
                return False
            self._state.update(query_running=True, last_error=None)
            self._thread = Thread(target=self._run, args=(force,), name="encoder-capability-query", daemon=True)
            self._thread.start()
        return True

    def close(self):
        self._stop.set()
        with self._lock:
            process = self._process
            thread = self._thread
        if process is not None:
            self._kill(process)
        if thread is not None:
            thread.join(timeout=3)

    @staticmethod
    def _kill(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _probe(self, inventory=False):
        arguments = [sys.executable, "-m", "converter.hardware.gpu.probe"]
        if inventory:
            arguments.append("--inventory")
        environment = os.environ.copy()
        # Set explicitly: do not depend on the current working directory/module search side effects.
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(arguments, stdout=stdout, stderr=stderr, env=environment, start_new_session=True)
            with self._lock:
                self._process = process
            try:
                if self._stop.is_set():
                    self._kill(process)
                try:
                    process.wait(timeout=25 if inventory else 60)
                except subprocess.TimeoutExpired:
                    self._kill(process)
                    process.wait()
                    raise RuntimeError("GPU capability probe timed out; no media pipeline was started.") from None
                stderr.seek(0)
                details = stderr.read(2000).decode("utf-8", errors="replace").strip()
                if process.returncode:
                    raise RuntimeError("GPU probe failed: " + (details or f"exit {process.returncode}"))
                if stdout.tell() > MAX_OUTPUT_BYTES:
                    raise RuntimeError("GPU probe output exceeds the supported size.")
                stdout.seek(0)
                payload = json.load(stdout)
                if payload.get("schema_version") != 1 or not isinstance(payload.get("adapters"), list):
                    raise RuntimeError("GPU probe returned unsupported data.")
                if len(payload["adapters"]) > 64 or not isinstance(payload.get("fingerprint"), str):
                    raise RuntimeError("GPU probe returned invalid inventory.")
                if details:
                    LOGGER.debug("GPU probe diagnostics: %s", details)
                return payload
            finally:
                with self._lock:
                    self._process = None

    def _load_cache(self):
        try:
            if self.cache_path.stat().st_size > MAX_OUTPUT_BYTES:
                return None
            with self.cache_path.open(encoding="utf-8") as source:
                payload = json.load(source)
            if payload.get("cache_schema") != CACHE_SCHEMA or not isinstance(payload.get("state"), dict):
                return None
            state = payload["state"]
            if not isinstance(state.get("adapters"), list) or state.get("stale"):
                return None
            if any(adapter.get("query_status") in {"failed", "partial", "pending", "unavailable"} for adapter in state["adapters"]):
                return None
            return state
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, AttributeError):
            LOGGER.warning("GPU cache is unavailable or invalid; a new query will run.", exc_info=True)
            return None

    def _save_cache(self):
        state = self.snapshot()
        state["query_running"] = False
        state["from_cache"] = False
        self.cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".encoder-capabilities-", dir=self.cache_path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump({"cache_schema": CACHE_SCHEMA, "state": state}, target, ensure_ascii=False, indent=2)
                target.flush()
                os.fsync(target.fileno())
            os.replace(name, self.cache_path)
            directory_fd = os.open(self.cache_path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if name is not None and os.path.exists(name):
                os.unlink(name)

    def _run(self, force):
        try:
            inventory = self._probe(inventory=True)
            cached = None if force else self._load_cache()
            if cached and cached.get("fingerprint") == inventory["fingerprint"]:
                with self._lock:
                    self._state.update(cached, query_running=True, from_cache=True, last_error=None, stale=False, initialized=True)
                return
            with self._lock:
                previous = {adapter["adapter_id"]: adapter for adapter in self._state["adapters"]}
                retained = False
                for adapter in inventory["adapters"]:
                    old = previous.get(adapter["adapter_id"])
                    if old and old.get("codecs"):
                        adapter["codecs"] = old["codecs"]
                        retained = True
                self._state.update(adapters=inventory["adapters"], fingerprint=inventory["fingerprint"], from_cache=False, stale=retained)
            if self._stop.is_set():
                return
            payload = self._probe()
            if self._stop.is_set():
                return
            with self._lock:
                self._state.update(
                    adapters=payload["adapters"], fingerprint=payload["fingerprint"],
                    gstreamer_version=payload.get("gstreamer_version"),
                    queried_at=datetime.now(timezone.utc).isoformat(), initialized=True,
                    stale=False, from_cache=False,
                )
            try:
                self._save_cache()
            except OSError:
                LOGGER.warning("GPU capability result is available, but its cache could not be saved.", exc_info=True)
                with self._lock:
                    self._state["last_error"] = "Результат отримано, але запис кешу недоступний."
        except Exception as error:
            if not self._stop.is_set():
                LOGGER.exception("GPU capability query failed")
                with self._lock:
                    self._state.update(last_error=str(error)[:2000], stale=bool(self._state["adapters"]), initialized=True)
        finally:
            with self._lock:
                self._state["query_running"] = False
