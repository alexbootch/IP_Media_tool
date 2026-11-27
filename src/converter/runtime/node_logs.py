"""Persistent, readable per-node journals owned by the supervisor process."""

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import stat
import tempfile
import unicodedata


LOGGER = logging.getLogger("ip-media-tool.runtime")
BACKUPS = 3
TAIL_BYTES = 64 * 1024
LINE_BYTES = 400
EVENT_LABELS = {
    "node.config_created": "Ноду створено",
    "node.config_updated": "Конфігурацію збережено",
    "worker.start_requested": "Запит запуску",
    "worker.start_failed": "Запуск відхилено",
    "worker.spawned": "Воркер запущено",
    "worker.exited": "Воркер завершився",
    "worker.recovery_scheduled": "Заплановано повторний запуск",
    "worker.recovery_failed": "Повторний запуск відхилено",
    "worker.stop_requested": "Запит зупинки",
    "worker.channel_failed": "Помилка зв’язку з воркером",
    "worker.state.starting": "Запуск",
    "worker.state.waiting_source": "Очікує джерело",
    "worker.state.waiting_signal": "Очікує сигнал",
    "worker.state.running": "Працює",
    "worker.state.stopping": "Зупиняється",
    "worker.state.stopped": "Зупинено",
    "worker.state.error": "Помилка воркера",
}


class NodeLogFormatter(logging.Formatter):
    """Use an unambiguous UTC timestamp independent of the server's time zone."""

    def formatTime(self, record, datefmt=None):
        """Render UTC consistently for live events and migrated history."""
        return datetime.fromtimestamp(record.created, timezone.utc).isoformat(
            sep=" ", timespec="milliseconds").replace("+00:00", "Z")


FORMATTER = NodeLogFormatter("%(asctime)s  %(levelname)-7s  %(message)s")


def node_log_record(event):
    """Turn a runtime event into a logging record with relevant context only."""
    code = event["event_code"]
    level = logging.ERROR if event.get("error_code") else logging.INFO
    description = EVENT_LABELS.get(code, code)
    attempts = event.get("restart_count", 0)
    if code == "worker.recovery_scheduled":
        description += f" · спроба {attempts + 1}"
    elif code in {"worker.spawned", "worker.recovery_failed"} and attempts:
        description += f" · спроба {attempts}"
    if code == "worker.exited" and event.get("exit_code") is not None:
        exit_code = event["exit_code"]
        description += f" · код виходу {exit_code}"
        if exit_code:
            level = logging.WARNING
    error_fields = [event[key] for key in ("error_code", "stage") if event.get(key)]
    if error_fields:
        description += f" [{', '.join(error_fields)}]"
    if event.get("message"):
        description += " — " + " ".join(event["message"].splitlines())
    record = logging.LogRecord(LOGGER.name, level, "", 0, description, (), None)
    record.created = datetime.fromisoformat(event["at"]).timestamp()
    return record


def text_line(raw, node_id):
    """Read plain text or render a legacy JSON entry without exposing its identifiers."""
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith(b"{"):
        try:
            event = json.loads(raw)
            if not isinstance(event, dict) or event.get("node_id") != node_id:
                return ""
            return FORMATTER.format(node_log_record(event))
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
            return ""
    return raw.decode("utf-8", errors="replace")


def preview_line(line):
    """Bound a journal line before sending it through the runtime socket."""
    return line.encode("utf-8")[:LINE_BYTES].decode("utf-8", errors="ignore")


class NodeLogs:
    """Keep one journal family per UUID, including across renames and service restarts."""

    def __init__(self, directory):
        """Bind to the installer-owned private log directory."""
        self.directory = Path(directory)
        self.paths = {}

    def _path(self, config):
        """Resolve a safe, readable name and move the entire journal when it changes."""
        node_id = config["id"]
        name = unicodedata.normalize("NFKC", config["name"])
        slug = re.sub(r"[^\w-]+", "-", name).strip("-_")[:48] or "node"
        target = self.directory / f"{slug}--{node_id}.log"
        previous = self.paths.get(node_id)
        if previous != target:
            if previous is None:
                previous = next(self.directory.glob(f"*--{node_id}.log"), None)
            if previous is not None and previous != target:
                for index in range(1, BACKUPS + 1):
                    backup = previous.with_suffix(f".log.{index}")
                    if backup.exists():
                        backup.rename(target.with_suffix(f".log.{index}"))
                previous.rename(target)
            self.paths[node_id] = target
        return target

    def write(self, config, record):
        """Append one text record with the standard rotating file handler."""
        try:
            path = self._path(config)
            handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=BACKUPS, encoding="utf-8", delay=True)
            try:
                handler.setFormatter(FORMATTER)
                handler.handle(record)
            finally:
                handler.close()
        except OSError as error:
            LOGGER.warning("node.log_io_failed node_id=%s errno=%s", config["id"], error.errno)

    def prepare(self, config):
        """Create a stable log file even for an existing node that has never run."""
        try:
            path = self._path(config)
            with path.open("a", encoding="utf-8"):
                pass
            for current in self._files(path):
                self._migrate(current, config["id"])
        except OSError as error:
            LOGGER.warning("node.log_prepare_failed node_id=%s errno=%s", config["id"], error.errno)

    def _files(self, path):
        """Yield the active journal and backups in order from newest to oldest."""
        yield path
        for index in range(1, BACKUPS + 1):
            yield path.with_suffix(f".log.{index}")

    def _migrate(self, path, node_id):
        """Replace legacy JSON records atomically while retaining their events and mode."""
        try:
            source = path.open("rb")
        except FileNotFoundError:
            return
        temporary = None
        try:
            with source:
                if source.read(1) != b"{":
                    return
                source.seek(0)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                                 prefix=".node-log-", delete=False) as destination:
                    temporary = Path(destination.name)
                    os.fchmod(destination.fileno(), stat.S_IMODE(os.fstat(source.fileno()).st_mode))
                    for raw in source:
                        line = text_line(raw, node_id)
                        if line:
                            destination.write(line + "\n")
                    destination.flush()
                    os.fsync(destination.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def read(self, node_id, limit):
        """Read the most recent lines across rotation using bounded file tails."""
        path = self.paths.get(node_id) or next(self.directory.glob(f"*--{node_id}.log"), None)
        if path is None:
            return []
        recent = []
        for current in self._files(path):
            try:
                with current.open("rb") as source:
                    start = max(0, source.seek(0, 2) - TAIL_BYTES)
                    source.seek(start)
                    if start:
                        source.readline(TAIL_BYTES)
                    lines = source.read(TAIL_BYTES).splitlines()
            except FileNotFoundError:
                continue
            for raw in reversed(lines):
                line = text_line(raw, node_id)
                if line:
                    recent.append(preview_line(line))
                if len(recent) == limit:
                    return list(reversed(recent))
        return list(reversed(recent))
