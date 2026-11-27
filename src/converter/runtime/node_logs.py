"""Persistent, per-node JSON Lines journals owned by the supervisor process."""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import unicodedata


LOGGER = logging.getLogger("ip-media-tool.runtime")
BACKUPS = 3
TAIL_BYTES = 64 * 1024
PUBLIC_FIELDS = {"at", "event_code", "node_id", "node_type", "instance_id",
                 "config_revision", "restart_count", "exit_code", "error_code", "message", "stage"}


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

    def write(self, config, event):
        """Append one structured event without holding file descriptors between events."""
        try:
            path = self._path(config)
            handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=BACKUPS, encoding="utf-8", delay=True)
            try:
                record = logging.LogRecord(LOGGER.name, logging.INFO, "", 0, json.dumps(event, ensure_ascii=False), (), None)
                handler.handle(record)
            finally:
                handler.close()
        except OSError as error:
            LOGGER.warning("node.log_io_failed node_id=%s errno=%s", config["id"], error.errno)

    def prepare(self, config):
        """Create a stable log file even for an existing node that has never run."""
        try:
            with self._path(config).open("a", encoding="utf-8"):
                pass
        except OSError as error:
            LOGGER.warning("node.log_prepare_failed node_id=%s errno=%s", config["id"], error.errno)

    def read(self, node_id, limit):
        """Read the most recent events across rotation using bounded file tails."""
        path = self.paths.get(node_id) or next(self.directory.glob(f"*--{node_id}.log"), None)
        if path is None:
            return []
        events = []
        for index in range(BACKUPS + 1):
            current = path if index == 0 else path.with_suffix(f".log.{index}")
            try:
                with current.open("rb") as source:
                    start = max(0, source.seek(0, 2) - TAIL_BYTES)
                    source.seek(start)
                    if start:
                        source.readline(TAIL_BYTES)
                    lines = source.read(TAIL_BYTES).splitlines()
            except FileNotFoundError:
                continue
            for line in reversed(lines):
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if (not isinstance(event, dict) or event.get("node_id") != node_id
                        or not all(isinstance(event.get(key), str) for key in ("at", "event_code"))):
                    continue
                events.append({key: value for key, value in event.items() if key in PUBLIC_FIELDS})
                if len(events) == limit:
                    return list(reversed(events))
        return list(reversed(events))
