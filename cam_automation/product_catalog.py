from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ProductDescriptor:
    key: str
    label: str
    short_label: str
    plugin_id: str
    source_formats: tuple[str, ...]
    default_source_format: str
    process_names: frozenset[str]
    environment_roots: tuple[str, ...]
    connection_capabilities: tuple[str, ...]
    process_capabilities: tuple[str, ...]
    action_namespace: str
    proprietary_sources_supported: bool
    command_tasks_supported: bool
    flow_import_supported: bool


_PRODUCTS = (
    ProductDescriptor(
        key="nx",
        label="UG / NX",
        short_label="NX",
        plugin_id="ug-cam-copilot",
        source_formats=("nx_journal", "jsonl"),
        default_source_format="nx_journal",
        process_names=frozenset({"ugraf.exe", "nx.exe"}),
        environment_roots=("UGII_ROOT_DIR", "UGII_BASE_DIR"),
        connection_capabilities=("journal.parse", "recipe.preview", "simulation.gate"),
        process_capabilities=("journal.parse",),
        action_namespace="nx",
        proprietary_sources_supported=True,
        command_tasks_supported=True,
        flow_import_supported=True,
    ),
    ProductDescriptor(
        key="powermill",
        label="PowerMill",
        short_label="PM",
        plugin_id="powermill-cam-copilot",
        source_formats=("powermill_log", "jsonl"),
        default_source_format="powermill_log",
        process_names=frozenset({"powermill.exe", "pmill.exe"}),
        environment_roots=("POWERMILL_HOME", "PMILL_HOME", "POWERMILL_ROOT"),
        connection_capabilities=("macro.parse", "macro.export", "project.review"),
        process_capabilities=("macro.parse",),
        action_namespace="powermill",
        proprietary_sources_supported=True,
        command_tasks_supported=True,
        flow_import_supported=True,
    ),
    ProductDescriptor(
        key="cimatron",
        label="Cimatron",
        short_label="CM",
        plugin_id="cimatron-cam-copilot",
        source_formats=("cimatron_journal", "jsonl"),
        default_source_format="cimatron_journal",
        process_names=frozenset({"cimatron.exe", "cimatronlauncher.exe"}),
        environment_roots=("CIMATRON_HOME", "CIMATRON_ROOT"),
        connection_capabilities=("journal.parse", "workflow.learn", "project.review"),
        process_capabilities=("journal.parse",),
        # Metadata only: Cimatron observed calls use cam.source.call and never
        # enter the production action namespace set below.
        action_namespace="cimatron",
        proprietary_sources_supported=True,
        command_tasks_supported=False,
        flow_import_supported=False,
    ),
)

PRODUCT_CATALOG = {product.key: product for product in _PRODUCTS}
PRODUCT_KEYS = frozenset(PRODUCT_CATALOG)
PRODUCT_PLUGINS = {product.key: product.plugin_id for product in _PRODUCTS}
# Only products with reviewed production action contracts participate in the
# product namespace boundary. Cimatron remains static parsing evidence.
PRODUCT_ACTION_NAMESPACES = frozenset({"nx", "powermill"})


def get_product(product: str) -> ProductDescriptor | None:
    return PRODUCT_CATALOG.get(str(product).strip().casefold())


def require_product(product: str) -> ProductDescriptor:
    descriptor = get_product(product)
    if descriptor is None:
        supported = ", ".join(sorted(PRODUCT_KEYS))
        raise ValueError(f"Unsupported product: {product}. Choose {supported}.")
    return descriptor


def products_for_plugin_ids(plugin_ids: Iterable[str]) -> tuple[ProductDescriptor, ...]:
    installed = set(plugin_ids)
    return tuple(product for product in _PRODUCTS if product.plugin_id in installed)


__all__ = [
    "PRODUCT_ACTION_NAMESPACES",
    "PRODUCT_CATALOG",
    "PRODUCT_KEYS",
    "PRODUCT_PLUGINS",
    "ProductDescriptor",
    "get_product",
    "products_for_plugin_ids",
    "require_product",
]
