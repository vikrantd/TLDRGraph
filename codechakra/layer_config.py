"""
Layer Configuration Manager for CodeChakra.

Handles loading, validating, and persisting .codechakra/layers.config.yaml,
along with computing deterministic configuration hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Mapping, Optional, Tuple
import yaml

from .layers import (
    DEFAULT_LAYERS,
    LAYER_UTILITY,
    LayerRegistry,
    default_registry,
    set_registry,
)
from .rules import Rule

CONFIG_FILENAME_YAML = "layers.config.yaml"
CONFIG_FILENAME_JSON = "layers.config.json"
CONFIG_SCHEMA_VERSION = 1


def config_path(root_dir: str) -> Optional[str]:
    """Returns the path to an existing config file if present, preferring YAML."""
    dot_dir = os.path.join(os.path.abspath(root_dir), ".codechakra")
    yaml_path = os.path.join(dot_dir, CONFIG_FILENAME_YAML)
    if os.path.isfile(yaml_path):
        return yaml_path
    json_path = os.path.join(dot_dir, CONFIG_FILENAME_JSON)
    if os.path.isfile(json_path):
        return json_path
    return None


def compute_registry_hash(registry: LayerRegistry) -> str:
    """Computes a deterministic SHA-256 hash of a LayerRegistry."""
    canonical_records = [layer.as_record() for layer in registry.ordered()]
    payload = {
        "utility_id": registry.utility_id,
        "layers": canonical_records,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_layer_config(data: Mapping[str, Any]) -> None:
    """
    Validates a raw layer configuration dictionary.

    Raises ValueError with a specific, human-readable error on any violation.
    """
    if not isinstance(data, dict):
        raise ValueError("Layer configuration must be a mapping/object")

    layers = data.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ValueError("Configuration must contain a non-empty 'layers' list")

    utility_id = data.get("utility_id")
    if not utility_id:
        raise ValueError("Configuration must specify a 'utility_id'")

    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    seen_orders: set[int] = set()

    for idx, layer_record in enumerate(layers):
        if not isinstance(layer_record, dict):
            raise ValueError(f"Layer item at index {idx} must be an object")

        lid = str(layer_record.get("id") or "").strip()
        name = str(layer_record.get("name") or "").strip()
        order = layer_record.get("order")

        if not lid:
            raise ValueError(f"Layer at index {idx} has an empty 'id'")
        if lid in seen_ids:
            raise ValueError(f"Duplicate layer id {lid!r} found at index {idx}")
        seen_ids.add(lid)

        if not name:
            raise ValueError(f"Layer {lid!r} has an empty display 'name'")
        if name in seen_names:
            raise ValueError(f"Duplicate layer display name {name!r} found in layer {lid!r}")
        seen_names.add(name)

        if order is None or not isinstance(order, int):
            raise ValueError(f"Layer {lid!r} has invalid 'order': expected integer, got {order!r}")
        if order in seen_orders:
            raise ValueError(f"Duplicate order {order} found in layer {lid!r}")
        seen_orders.add(order)

        # Validate rules
        rules = layer_record.get("rules") or []
        if not isinstance(rules, list):
            raise ValueError(f"Layer {lid!r} 'rules' must be a list")

        for r_idx, rule_dict in enumerate(rules):
            if not isinstance(rule_dict, dict):
                raise ValueError(f"Rule at index {r_idx} in layer {lid!r} must be an object")
            try:
                Rule.from_record(rule_dict)
            except Exception as err:
                raise ValueError(f"Invalid rule in layer {lid!r} at index {r_idx}: {err}") from err

    if utility_id not in seen_ids:
        raise ValueError(
            f"Designated utility_id {utility_id!r} is not in the list of layer ids: {sorted(seen_ids)}"
        )


def load_layer_config(root_dir: str = ".") -> Tuple[LayerRegistry, str]:
    """
    Loads the layer configuration from .codechakra/layers.config.yaml (or .json).

    If found, validates and sets the active registry.
    If absent, falls back to the default registry.
    Returns (registry, config_hash).
    """
    c_path = config_path(root_dir)
    if not c_path:
        reg = default_registry()
        return reg, compute_registry_hash(reg)

    try:
        with open(c_path, "r", encoding="utf-8") as f:
            if c_path.endswith(".json"):
                data = json.load(f)
            else:
                data = yaml.safe_load(f)
    except Exception as err:
        raise ValueError(f"Could not parse layer configuration from {c_path}: {err}") from err

    validate_layer_config(data)
    utility_id = str(data.get("utility_id", LAYER_UTILITY))
    registry = LayerRegistry.from_records(data["layers"], utility_id=utility_id)
    set_registry(registry)
    c_hash = compute_registry_hash(registry)
    return registry, c_hash


def save_layer_config(root_dir: str, registry: LayerRegistry) -> str:
    """
    Persists a LayerRegistry into .codechakra/layers.config.yaml.

    Returns the written file path.
    """
    out_dir = os.path.join(os.path.abspath(root_dir), ".codechakra")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, CONFIG_FILENAME_YAML)

    payload = {
        "version": CONFIG_SCHEMA_VERSION,
        "utility_id": registry.utility_id,
        "layers": [layer.as_record() for layer in registry.ordered()],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(payload, f, default_flow_style=False, sort_keys=False)

    return out_path
