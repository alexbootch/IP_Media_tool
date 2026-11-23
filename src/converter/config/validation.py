"""Bounded JSON loading and offline Draft 2020-12 validation for Ubuntu 24.04."""

import json
from datetime import datetime
import math
import os
from pathlib import Path
import stat
import re
from urllib.parse import urldefrag
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker, RefResolver
from jsonschema.exceptions import RefResolutionError, SchemaError


MAX_BYTES = 1_048_576
MAX_DEPTH = 48
DIALECT = "https://json-schema.org/draft/2020-12/schema"
FORMATS = FormatChecker()


@FORMATS.checks("uuid")
def is_uuid(value):
    """Validate standard UUID spelling without optional format packages."""
    if not isinstance(value, str):
        return True
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


@FORMATS.checks("date-time")
def is_date_time(value):
    """Require an explicit RFC3339 timezone and a valid calendar date/time."""
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", value):
        return False
    try:
        datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


class ConfigError(Exception):
    """Expose safe structured errors without echoing configuration values."""

    def __init__(self, code, message, status=422, issues=None):
        """Carry an API status and bounded diagnostics for callers."""
        super().__init__(message)
        self.code, self.status, self.issues = code, status, issues or []


def issue(code, path, message, severity="error"):
    """Build a diagnostic without embedding secret-bearing instance values."""
    return {"code": code, "path": path, "message": message, "severity": severity}


def check_tree(value, depth=0):
    """Reject non-JSON values, nonfinite numbers and excessive nesting."""
    if depth > MAX_DEPTH:
        raise ConfigError("invalid_json", "JSON nesting limit exceeded.")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigError("invalid_json", "JSON object keys must be strings.")
            check_tree(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            check_tree(item, depth + 1)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError("invalid_json", "Nonfinite JSON numbers are forbidden.")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise ConfigError("invalid_json", "Unsupported JSON value.")


def unique_pairs(pairs):
    """Reject duplicate keys while the JSON decoder still preserves them."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("invalid_json", "Duplicate JSON object key.")
        result[key] = value
    return result


def decode_json(data, max_bytes=MAX_BYTES):
    """Decode bounded UTF-8 JSON; never include raw input in an error."""
    if len(data) > max_bytes:
        raise ConfigError("invalid_json", "JSON document exceeds its supported size.", 413)
    try:
        value = json.loads(data, object_pairs_hook=unique_pairs)
        check_tree(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ConfigError("invalid_json", "Malformed or excessively nested JSON.") from None


def read_json(path):
    """Read a regular file without following its final symlink and bound its size."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ConfigError("invalid_file", "A regular JSON file is required.")
        return decode_json(source.read(MAX_BYTES + 1))


def canonical_id(value):
    """Require canonical UUID spelling for consistent filenames and graph keys."""
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise ConfigError("invalid_config", "A canonical lowercase UUID is required.") from None
    return value


class OfflineResolver(RefResolver):
    """Support distro jsonschema 4.10 without any network/filesystem retrieval."""

    def resolve_remote(self, uri):
        """Fail closed when a reference was not preloaded in the schema store."""
        raise ConfigError("invalid_schema", "Schema reference is outside the preloaded registry.")


class SchemaRegistry:
    """Load a fixed trusted schema set and create per-call offline validators."""

    names = ("common", "node-manifest", "node-config", "node-status", "media-contract", "ipc-message", "worker-bootstrap")

    def __init__(self, directory):
        """Load version-one schemas only; caller controls the trusted directory."""
        self.directory = Path(directory).resolve(strict=True)
        self.schemas = {}
        self.store = {}
        for name in self.names:
            path = self.directory / f"{name}.schema.json"
            schema = read_json(path)
            if not isinstance(schema, dict) or schema.get("$schema") != DIALECT:
                raise ConfigError("invalid_schema", "Unsupported bundled schema dialect.")
            Draft202012Validator.check_schema(schema)
            self.schemas[name] = schema
            self.store[path.as_uri()] = schema

    def validate(self, name, value):
        """Validate against a known schema, ignoring instance-provided $schema hints."""
        check_tree(value)
        path = self.directory / f"{name}.schema.json"
        self.validate_schema(self.schemas[name], path.as_uri(), self.store, value)

    def validate_schema(self, schema, uri, store, value):
        """Use fresh resolver state per call and redact jsonschema error messages."""
        check_tree(value)
        resolver = OfflineResolver(base_uri=uri, referrer=schema, store=store)
        validator = Draft202012Validator(schema, resolver=resolver, format_checker=FORMATS)
        issues = []
        try:
            for error in validator.iter_errors(value):
                path = "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in error.absolute_path)
                issues.append(issue("invalid_config", path, f"Schema constraint failed: {error.validator}."))
                if len(issues) >= 40:
                    break
        except (RefResolutionError, RecursionError):
            raise ConfigError("invalid_schema", "Schema reference could not be resolved safely.") from None
        if issues:
            raise ConfigError("invalid_config", "JSON schema validation failed.", issues=issues)

    def plugin_schema(self, directory):
        """Preload plugin-local refs; reject remote refs, nested IDs and dynamic refs."""
        root = Path(directory).resolve(strict=True)
        store = dict(self.store)
        pending = [root / "config.schema.json"]
        loaded = {}
        while pending:
            path = pending.pop()
            if path in loaded:
                continue
            if len(loaded) >= 32:
                raise ConfigError("invalid_schema", "Too many plugin schema resources.")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or path.is_symlink():
                raise ConfigError("invalid_schema", "Plugin schema escapes its module directory.")
            schema = read_json(resolved)
            if not isinstance(schema, (dict, bool)):
                raise ConfigError("invalid_schema", "Plugin settings schema must be a schema object or boolean.")
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError:
                raise ConfigError("invalid_schema", "Plugin settings schema is invalid.") from None
            loaded[path] = schema
            store[path.as_uri()] = schema
            self._schema_refs(schema, path, root, pending)
        entry = root / "config.schema.json"
        return loaded[entry], entry.as_uri(), store

    def _schema_refs(self, value, path, root, pending):
        """Inspect schema keywords only, leaving properties named '$id' untouched."""
        if not isinstance(value, dict):
            return
        if any(key in value for key in ("$id", "$dynamicRef", "$recursiveRef")):
            raise ConfigError("invalid_schema", "Plugin schemas cannot redefine base IDs or dynamic references.")
        if "$schema" in value and value["$schema"] != DIALECT:
            raise ConfigError("invalid_schema", "Plugin schemas must use Draft 2020-12.")
        if "$ref" in value:
            reference = value["$ref"]
            if not isinstance(reference, str):
                raise ConfigError("invalid_schema", "Invalid schema reference.")
            filename, _ = urldefrag(reference)
            if filename:
                if any(char in filename for char in (":", "\\", "%", "?")) or filename.startswith("/"):
                    raise ConfigError("invalid_schema", "Only plugin-local schema references are allowed.")
                target = path.parent / filename
                if not target.resolve(strict=True).is_relative_to(root):
                    raise ConfigError("invalid_schema", "Plugin schema reference escapes its module.")
                pending.append(target.resolve(strict=True))
        for key in ("properties", "patternProperties", "$defs", "dependentSchemas"):
            for child in value.get(key, {}).values():
                self._schema_refs(child, path, root, pending)
        for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
            for child in value.get(key, []):
                self._schema_refs(child, path, root, pending)
        for key in ("items", "contains", "additionalProperties", "unevaluatedProperties", "unevaluatedItems", "propertyNames", "not", "if", "then", "else"):
            self._schema_refs(value.get(key), path, root, pending)
