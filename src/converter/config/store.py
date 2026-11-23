"""One-file transactions: flock, revision CAS, last-good backup and fsync/replace."""

from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
from threading import RLock

from converter.config.validation import ConfigError, MAX_BYTES, canonical_id, issue, read_json


class NodeConfigStore:
    """Serialize all cooperating writers and retain invalid documents unchanged."""

    def __init__(self, directory, modules):
        """Configure paths without creating files or starting any workers."""
        self.directory = Path(directory)
        self.backups = self.directory / ".backups"
        self.modules = modules
        self.lock = RLock()

    def initialize(self):
        """Create private directories; refuse symlink roots before any transaction."""
        for directory in (self.directory, self.backups):
            if directory.is_symlink():
                raise ConfigError("unsafe_path", "Config storage directory must not be a symlink.", 503)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.transaction():
            pass

    @contextmanager
    def transaction(self):
        """Hold a thread and Linux process lock across read/check/commit."""
        with self.lock:
            descriptor = os.open(self.directory / ".writer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ConfigError("unsafe_path", "Config lock must be a regular file.", 503)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                os.close(descriptor)

    def _scan(self):
        """Read structural configs, isolating malformed files with safe diagnostics."""
        configs, diagnostics = {}, []
        for path in sorted(self.directory.glob("*.json")):
            try:
                node_id = canonical_id(path.stem)
                config = read_json(path)
                self.modules.schemas.validate("node-config", config)
                if config["id"] != node_id:
                    raise ConfigError("invalid_config", "Config ID does not match its filename.")
                for connection in config["connections"].values():
                    canonical_id(connection["source_node_id"])
                configs[node_id] = config
            except (ConfigError, OSError) as error:
                diagnostics.append(issue(getattr(error, "code", "invalid_file"), path.name, "Config file is isolated; original file retained."))
        return configs, diagnostics

    def snapshot(self):
        """Expose stored configs and readiness diagnostics without changing any files."""
        with self.transaction():
            configs, diagnostics = self._scan()
            for node_id, config in configs.items():
                try:
                    self.modules.validate_config(config)
                except ConfigError as error:
                    diagnostics.append(issue(error.code, node_id, "Stored config cannot currently be activated."))
            diagnostics.extend(self.modules.validate_graph(configs))
            return {"nodes": copy.deepcopy(list(configs.values())), "diagnostics": diagnostics}

    def get(self, node_id):
        """Read a config by canonical ID, preserving unavailable-module configurations."""
        canonical_id(node_id)
        with self.transaction():
            path = self.directory / f"{node_id}.json"
            try:
                config = read_json(path)
            except FileNotFoundError:
                raise ConfigError("not_found", "Node configuration not found.", 404) from None
            self.modules.schemas.validate("node-config", config)
            if config["id"] != node_id:
                raise ConfigError("invalid_config", "Config ID does not match its filename.")
            return config

    def save(self, config, expected_revision):
        """Create with expected_revision=0; update with an exact current revision."""
        config = copy.deepcopy(config)
        self.modules.validate_config(config)
        node_id = config["id"]
        if type(expected_revision) is not int or expected_revision < 0:
            raise ConfigError("revision_conflict", "An explicit expected revision is required.", 409)
        with self.transaction():
            path = self.directory / f"{node_id}.json"
            current = None
            found = True
            try:
                current = read_json(path)
            except FileNotFoundError:
                found = False
            if not found:
                if expected_revision != 0 or (self.backups / f"{node_id}.json").exists():
                    raise ConfigError("revision_conflict", "Config was removed or changed; reload before saving.", 409)
            else:
                self.modules.schemas.validate("node-config", current)
                if current["id"] != node_id or current["revision"] != expected_revision:
                    raise ConfigError("revision_conflict", "Config revision changed; reload before saving.", 409)
                if config["type"] != current["type"]:
                    raise ConfigError("invalid_config", "Changing the type of an existing node is forbidden.")
            if config["revision"] != expected_revision + 1:
                raise ConfigError("revision_conflict", "New config revision must equal expected_revision + 1.", 409)
            configs, isolated = self._scan()
            configs[node_id] = config
            graph_issues = self.modules.validate_graph(configs)
            blocking = [entry for entry in graph_issues if entry["severity"] == "error"]
            if blocking:
                raise ConfigError("invalid_graph", "Invalid node connections.", issues=blocking)
            if current is not None:
                self._atomic_write(self.backups, path.name, current)
            self._atomic_write(self.directory, path.name, config)
            return {"node": config, "diagnostics": isolated + graph_issues, "restart_required": current is not None and any(current[key] != config[key] for key in ("settings", "settings_version", "connections", "secret_refs", "enabled"))}

    def delete(self, node_id, expected_revision):
        """Back up and delete one exact revision; consumer references remain intact."""
        canonical_id(node_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ConfigError("revision_conflict", "An explicit current revision is required.", 409)
        with self.transaction():
            path = self.directory / f"{node_id}.json"
            try:
                current = read_json(path)
            except FileNotFoundError:
                raise ConfigError("not_found", "Node configuration not found.", 404) from None
            self.modules.schemas.validate("node-config", current)
            if current["id"] != node_id or current["revision"] != expected_revision:
                raise ConfigError("revision_conflict", "Config revision changed; reload before deleting.", 409)
            self._atomic_write(self.backups, path.name, current)
            try:
                path.unlink()
                self._fsync_directory(self.directory)
            except OSError:
                raise ConfigError("storage_uncertain", "Delete durability is uncertain; reload the configuration inventory.", 503) from None

    @staticmethod
    def _fsync_directory(directory):
        """Make a rename/unlink durable in the containing directory."""
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_write(self, directory, name, config):
        """Write a private same-filesystem temporary file then fsync/replace/fsync."""
        data = (json.dumps(config, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
        if len(data) > MAX_BYTES:
            raise ConfigError("invalid_config", "Serialized config exceeds 1 MiB.", 413)
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".node-", suffix=".tmp", dir=directory)
            with os.fdopen(descriptor, "wb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, directory / name)
            temporary = None
            self._fsync_directory(directory)
        except OSError:
            raise ConfigError("storage_uncertain", "Save durability is uncertain; reload before retrying.", 503) from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
