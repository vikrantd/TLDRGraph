"""
Layer Classification Engine for TLDRGraph.

Layer identity and rule sets live in :mod:`.layers`.
This module evaluates rules over the active registry in rank order: first match
wins. If no rule matches across any layer, the registry's designated utility
bucket is assigned with default provenance.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

from .layers import (
    DEFAULT_LAYERS,
    LAYER_API,
    LAYER_ASYNC,
    LAYER_DATA,
    LAYER_DEVOPS,
    LAYER_SERVICE,
    LAYER_UI,
    LAYER_UTILITY,
    Layer,
    get_registry,
)

#: Display names of the built-in default set, by id. Used only to seed the
#: legacy ``LayerType`` enum below -- logic must not read this.
_DEFAULT_NAMES = {layer.id: layer.name for layer in DEFAULT_LAYERS}


class LayerType(str, Enum):
    """
    Deprecated compatibility shim over the *default* layer set.

    Frozen at import time, so it cannot follow a swapped registry. Kept only so
    existing callers and tests that hold ``LayerType.X.value`` keep working; new
    code must use :mod:`tldrgraph.layers` ids instead.
    """

    LAYER_1_UI = _DEFAULT_NAMES[LAYER_UI]
    LAYER_2_API = _DEFAULT_NAMES[LAYER_API]
    LAYER_3_SERVICE = _DEFAULT_NAMES[LAYER_SERVICE]
    LAYER_4_DATA = _DEFAULT_NAMES[LAYER_DATA]
    LAYER_5_ASYNC = _DEFAULT_NAMES[LAYER_ASYNC]
    LAYER_6_DEVOPS = _DEFAULT_NAMES[LAYER_DEVOPS]
    UNKNOWN = _DEFAULT_NAMES[LAYER_UTILITY]


def classify_node_with_source(node_id: str, node_data: Mapping[str, Any]) -> Tuple[Layer, str]:
    """
    Classifies a node and returns (layer, layer_source).

    layer_source is "rule" when matched by a rule in the active registry, or
    "default" when fallen back to the utility bucket.
    """
    registry = get_registry()
    for layer in registry.ordered():
        for rule in layer.rules:
            if rule.matches(node_id, node_data):
                return layer, "rule"

    return registry.utility, "default"


def classify_node(node_id: str, node_data: Dict[str, Any]) -> Layer:
    """
    Classifies an AST / graph node into one of the architectural layers.

    Evaluates rules across the active registry in rank order: first match wins.
    Returns the :class:`~tldrgraph.layers.Layer` from the active registry.
    """
    layer, _ = classify_node_with_source(node_id, node_data)
    return layer
