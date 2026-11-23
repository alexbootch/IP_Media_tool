"""Read-only plugin discovery; worker modules are never imported by control."""

import copy
from pathlib import Path

from converter.config.validation import ConfigError, canonical_id, issue, read_json


class ModuleRegistry:
    """Keep validated module metadata and plugin-local settings schemas in memory."""

    def __init__(self, directory, schemas):
        """Initialize an empty registry; scan explicitly during application startup."""
        self.directory = Path(directory).resolve(strict=True)
        self.schemas = schemas
        self.modules = {}
        self.diagnostics = []

    def scan(self):
        """Load complete plugins, isolating invalid modules and ignoring empty placeholders."""
        modules, diagnostics = {}, []
        for directory in sorted(self.directory.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            if not (directory / "manifest.json").exists() and not any(directory.glob("*.py")):
                continue
            try:
                if directory.is_symlink() or not directory.resolve().is_relative_to(self.directory):
                    raise ConfigError("invalid_module", "Plugin directory must not be a symlink.")
                manifest = read_json(directory / "manifest.json")
                self.schemas.validate("node-manifest", manifest)
                if manifest["type"] != directory.name:
                    raise ConfigError("invalid_module", "Plugin type must match its directory name.")
                for name in ("worker.py", "ui.js", "styles.css", "README.md", "config.schema.json"):
                    path = directory / name
                    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory):
                        raise ConfigError("invalid_module", "Required plugin file is absent or unsafe.")
                ports = {port["id"]: port for port in manifest["ports"]}
                if len(ports) != len(manifest["ports"]):
                    raise ConfigError("invalid_module", "Duplicate plugin port ID.")
                directions = {port["direction"] for port in ports.values()}
                required_directions = {"input": {"output"}, "output": {"input"}, "processor": {"input", "output"}}
                if directions != required_directions[manifest["kind"]]:
                    raise ConfigError("invalid_module", "Port directions do not match plugin kind.")
                schema, uri, store = self.schemas.plugin_schema(directory)
                modules[manifest["type"]] = {"manifest": manifest, "ports": ports, "schema": schema, "uri": uri, "store": store}
            except Exception as error:
                diagnostics.append(issue(getattr(error, "code", "invalid_module"), directory.name, "Plugin could not be registered."))
        self.modules, self.diagnostics = modules, diagnostics

    def snapshot(self):
        """Return detached metadata, never schema internals or executable workers."""
        return {"modules": [copy.deepcopy(module["manifest"]) for module in self.modules.values()], "diagnostics": copy.deepcopy(self.diagnostics), "capabilities_verified": False}

    def get(self, node_type):
        """Look up an available type without importing its worker or probing hardware."""
        if node_type not in self.modules:
            raise ConfigError("module_unavailable", "Node module is not registered.", 409)
        return self.modules[node_type]

    def validate_config(self, config):
        """Check the base config, canonical IDs and plugin-specific settings schema."""
        self.schemas.validate("node-config", config)
        canonical_id(config["id"])
        for connection in config["connections"].values():
            canonical_id(connection["source_node_id"])
        module = self.get(config["type"])
        if config["settings_version"] != module["manifest"]["settings_version"]:
            raise ConfigError("incompatible_version", "Plugin settings require an explicit migration.", 409)
        self.schemas.validate_schema(module["schema"], module["uri"], module["store"], config["settings"])
        for port_id in config["connections"]:
            if module["ports"].get(port_id, {}).get("direction") != "input":
                raise ConfigError("invalid_connection", "Connection key is not a declared input port.")

    def validate_graph(self, configs):
        """Report unresolved sources and missing inputs; reject invalid edges and cycles."""
        issues, edges = [], {node_id: set() for node_id in configs}
        for node_id, config in configs.items():
            module = self.modules.get(config["type"])
            if module is not None:
                for port in module["ports"].values():
                    if port["direction"] == "input" and port["required"] and port["id"] not in config["connections"]:
                        issues.append(issue("missing_connection", f"/{node_id}/connections/{port['id']}", "Required input is not connected; start is blocked.", "warning"))
            for port_id, connection in config["connections"].items():
                path = f"/{node_id}/connections/{port_id}"
                source_id = connection["source_node_id"]
                if source_id == node_id:
                    issues.append(issue("invalid_connection", path, "A node cannot connect to itself."))
                    continue
                if source_id not in configs:
                    issues.append(issue("unresolved_connection", path, "Source node is unavailable; reference retained.", "warning"))
                    continue
                edges[source_id].add(node_id)
                source_module = self.modules.get(configs[source_id]["type"])
                if module is None or source_module is None:
                    issues.append(issue("module_unavailable", path, "Endpoint module is unavailable; start is blocked.", "warning"))
                    continue
                local = module["ports"].get(port_id)
                source = source_module["ports"].get(connection["source_port_id"])
                if not local or local["direction"] != "input" or not source or source["direction"] != "output" or local["contract_type"] != source["contract_type"]:
                    issues.append(issue("invalid_connection", path, "Ports or contract types are incompatible."))
        degrees = {node_id: 0 for node_id in edges}
        for targets in edges.values():
            for target in targets:
                degrees[target] += 1
        pending = [node_id for node_id, degree in degrees.items() if degree == 0]
        visited = 0
        while pending:
            node_id = pending.pop()
            visited += 1
            for target in edges[node_id]:
                degrees[target] -= 1
                if degrees[target] == 0:
                    pending.append(target)
        if visited != len(edges):
            issues.append(issue("graph_cycle", "/connections", "Node connections contain a cycle."))
        return issues
