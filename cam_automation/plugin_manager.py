from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AppPlugin:
    schema_version: int
    plugin_id: str
    name: str
    version: str
    summary: str
    category: str
    order: int
    features: tuple[str, ...]
    dependencies: tuple[str, ...]
    permissions: tuple[str, ...]
    consent_required: bool
    auto_authorize: bool
    consent_reversible: bool
    icon: str
    source_dir: str

    @classmethod
    def load(cls, path: Path) -> "AppPlugin":
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain a JSON object.")
        plugin_id = str(value.get("id", "")).strip()
        if not plugin_id or plugin_id != path.parent.name:
            raise ValueError(f"{path} id must match its plugin directory.")
        required = ("name", "version", "summary", "category")
        missing = [key for key in required if not str(value.get(key, "")).strip()]
        if missing:
            raise ValueError(f"{path} is missing: {', '.join(missing)}")
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            plugin_id=plugin_id,
            name=str(value["name"]),
            version=str(value["version"]),
            summary=str(value["summary"]),
            category=str(value["category"]),
            order=int(value.get("order", 100)),
            features=tuple(str(item) for item in value.get("features", [])),
            dependencies=tuple(str(item) for item in value.get("dependencies", [])),
            permissions=tuple(str(item) for item in value.get("permissions", [])),
            consent_required=bool(value.get("consent_required", False)),
            auto_authorize=bool(value.get("auto_authorize", False)),
            consent_reversible=bool(value.get("consent_reversible", False)),
            icon=str(value.get("icon", "MOD"))[:4].upper(),
            source_dir=str(path.parent.resolve()),
        )

    def public_dict(
        self,
        *,
        installed: bool,
        installed_at: str | None,
    ) -> dict[str, Any]:
        value = asdict(self)
        value["id"] = value.pop("plugin_id")
        value.pop("source_dir")
        value["features"] = list(self.features)
        value["dependencies"] = list(self.dependencies)
        value["permissions"] = list(self.permissions)
        value["installed"] = installed
        value["enabled"] = installed
        value["installed_at"] = installed_at
        value["auto_authorize"] = self.auto_authorize
        value["consent_reversible"] = self.consent_reversible
        return value


class PluginManager:
    """Local plugin catalog and an explicit, empty-by-default install registry."""

    def __init__(self, data_dir: str | Path, catalog_root: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.catalog_root = Path(catalog_root)
        self.state_path = self.data_dir / "installed-plugins.json"
        self._lock = threading.RLock()
        self._catalog = self._load_catalog()
        self._installed = self._load_state()

    def _load_catalog(self) -> dict[str, AppPlugin]:
        catalog: dict[str, AppPlugin] = {}
        if not self.catalog_root.is_dir():
            return catalog
        for path in sorted(self.catalog_root.glob("*/app-plugin.json")):
            plugin = AppPlugin.load(path)
            if plugin.plugin_id in catalog:
                raise ValueError(f"Duplicate app plugin id: {plugin.plugin_id}")
            catalog[plugin.plugin_id] = plugin
        for plugin in catalog.values():
            missing = [item for item in plugin.dependencies if item not in catalog]
            if missing:
                raise ValueError(
                    f"{plugin.plugin_id} has unknown dependencies: {', '.join(missing)}"
                )
        return catalog

    def _load_state(self) -> dict[str, dict[str, str]]:
        if not self.state_path.is_file():
            return {}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        installed = value.get("installed", {}) if isinstance(value, dict) else {}
        if not isinstance(installed, dict):
            return {}
        return {
            plugin_id: {"installed_at": str(metadata.get("installed_at", ""))}
            for plugin_id, metadata in installed.items()
            if plugin_id in self._catalog and isinstance(metadata, dict)
        }

    def _save(self) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"schema_version": 1, "installed": self._installed},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def is_installed(self, plugin_id: str) -> bool:
        with self._lock:
            return plugin_id in self._installed

    def require(self, plugin_id: str) -> None:
        if plugin_id not in self._catalog:
            raise ValueError(f"Unknown plugin: {plugin_id}")
        if not self.is_installed(plugin_id):
            raise PluginNotInstalled(plugin_id)

    def install(self, plugin_id: str) -> dict[str, Any]:
        with self._lock:
            if plugin_id not in self._catalog:
                raise ValueError(f"Unknown plugin: {plugin_id}")
            changed: list[str] = []
            self._install_with_dependencies(plugin_id, changed, set())
            if changed:
                self._save()
            result = self.status()
            result["changed"] = changed
            return result

    def _install_with_dependencies(
        self,
        plugin_id: str,
        changed: list[str],
        visiting: set[str],
    ) -> None:
        if plugin_id in visiting:
            raise ValueError(f"Plugin dependency cycle includes {plugin_id}.")
        visiting.add(plugin_id)
        for dependency in self._catalog[plugin_id].dependencies:
            self._install_with_dependencies(dependency, changed, visiting)
        visiting.remove(plugin_id)
        if plugin_id not in self._installed:
            self._installed[plugin_id] = {"installed_at": _utc_now()}
            changed.append(plugin_id)

    def uninstall(self, plugin_id: str) -> dict[str, Any]:
        with self._lock:
            if plugin_id not in self._catalog:
                raise ValueError(f"Unknown plugin: {plugin_id}")
            dependents = [
                plugin.plugin_id
                for plugin in self._catalog.values()
                if plugin.plugin_id in self._installed
                and plugin_id in plugin.dependencies
            ]
            if dependents:
                raise ValueError(
                    f"Uninstall dependent plugins first: {', '.join(sorted(dependents))}"
                )
            changed = []
            if plugin_id in self._installed:
                del self._installed[plugin_id]
                changed.append(plugin_id)
                self._save()
            result = self.status()
            result["changed"] = changed
            return result

    def installed_ids(self) -> set[str]:
        with self._lock:
            return set(self._installed)

    def installed_features(self) -> set[str]:
        with self._lock:
            return {
                feature
                for plugin_id in self._installed
                for feature in self._catalog[plugin_id].features
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            plugins = [
                plugin.public_dict(
                    installed=plugin.plugin_id in self._installed,
                    installed_at=self._installed.get(plugin.plugin_id, {}).get(
                        "installed_at"
                    ),
                )
                for plugin in sorted(
                    self._catalog.values(),
                    key=lambda item: (item.order, item.plugin_id),
                )
            ]
            return {
                "schema_version": 1,
                "core": {
                    "name": "CAM Automation Studio Core",
                    "version": "0.6.0",
                    "capabilities": [
                        "plugin.catalog",
                        "plugin.install",
                        "plugin.uninstall",
                        "health",
                    ],
                },
                "installed_count": len(self._installed),
                "available_count": len(self._catalog),
                "features": sorted(self.installed_features()),
                "plugins": plugins,
            }


class PluginNotInstalled(ValueError):
    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id
        super().__init__(f"Install plugin '{plugin_id}' to use this feature.")


PRODUCT_PLUGINS = {
    "nx": "ug-cam-copilot",
    "powermill": "powermill-cam-copilot",
}


def products_for_plugins(plugin_ids: Iterable[str]) -> set[str]:
    installed = set(plugin_ids)
    return {
        product
        for product, plugin_id in PRODUCT_PLUGINS.items()
        if plugin_id in installed
    }
