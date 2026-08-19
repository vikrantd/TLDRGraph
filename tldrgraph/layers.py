"""
Layer registry for TLDRGraph -- the single owner of architectural layer identity.

Why this module exists
----------------------
Every layer has three separate things:
``id``: Stable machine key ("ui", "api", "service", "data", "async", "devops", "utility").
``name``: The human display string ("Layer 1: UI Trigger").
``order``: Explicit integer rank used for flow ordering and rule evaluation.
``rules``: Ordered classification rules evaluated to assign nodes to this layer.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from .rules import Rule

__all__ = [
    "Layer",
    "LayerRegistry",
    "LAYER_UI",
    "LAYER_API",
    "LAYER_SERVICE",
    "LAYER_DATA",
    "LAYER_ASYNC",
    "LAYER_DEVOPS",
    "LAYER_UTILITY",
    "DEFAULT_LAYERS",
    "default_registry",
    "get_registry",
    "set_registry",
    "use_registry",
    "layer_id_of",
    "layer_name",
    "layer_order",
    "utility_layer_id",
]


# --------------------------------------------------------------------------- #
# Stable machine ids. Never displayed, never parsed.
# --------------------------------------------------------------------------- #

LAYER_UI = "ui"
LAYER_API = "api"
LAYER_SERVICE = "service"
LAYER_DATA = "data"
LAYER_ASYNC = "async"
LAYER_DEVOPS = "devops"
LAYER_UTILITY = "utility"


@dataclass(frozen=True, eq=False)
class Layer:
    """One architectural layer: stable id + display name + explicit rank + rules."""

    id: str
    name: str
    order: int
    description: str = ""
    rules: Tuple[Rule, ...] = field(default_factory=tuple)

    @property
    def value(self) -> str:
        """The display name (legacy LayerType compatibility alias)."""
        return self.name

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Layer):
            return self.id == other.id and self.name == other.name
        if isinstance(other, str):
            return self.name == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.id, self.name))

    def as_record(self) -> Dict[str, Any]:
        rec: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "order": self.order,
            "description": self.description,
        }
        if self.rules:
            rec["rules"] = [r.as_record() for r in self.rules]
        return rec


# --------------------------------------------------------------------------- #
# Built-in Default Rule Heuristics
# --------------------------------------------------------------------------- #

_ASYNC_KEYWORDS = ("polling", "cron", "queue", "worker", "audit", "scripts/", "tasks")
_ASYNC_LABEL_KEYWORDS = ("polling", "worker", "job", "cron", "audit")
_DATA_KEYWORDS = ("prisma", "schema.prisma", "migrations", "repository", "entities", "entity", "database")
_DATA_LABEL_KEYWORDS = ("prisma", "repository")
_DATA_LABEL_SUFFIXES = ("entity", "entities", "dao")
_DEVOPS_KEYWORDS = (
    "docker", "dockerfile", "charts", "helm", "k8s", "kubernetes",
    ".github", "workflows", "deploy.yml", "values.yaml", "chart.yaml"
)

_DEFAULT_UI_RULES: Tuple[Rule, ...] = (
    Rule(file_contains=("frontend/src/app", "frontend/src/components", "frontend/"), exclude_file=_DEVOPS_KEYWORDS),
    Rule(file_contains=("/components/", "/pages/")),
    Rule(file_contains=("frontend/src",)),
)

_DEFAULT_API_RULES: Tuple[Rule, ...] = (
    Rule(type_in=("api_endpoint",), id_prefix=("endpoint_",)),
    Rule(file_contains=("controller", "route")),
    Rule(label_contains=("controller",)),
)

_DEFAULT_SERVICE_RULES: Tuple[Rule, ...] = (
    Rule(
        file_contains=("service", "calc", "strategy", "extraction", "engine", "rules", "workflow", "prompt", "auth", "cases"),
        exclude_file=_ASYNC_KEYWORDS + _DATA_KEYWORDS + _DEVOPS_KEYWORDS,
        exclude_label=_ASYNC_LABEL_KEYWORDS + _DATA_LABEL_KEYWORDS,
        exclude_label_ends_with=_DATA_LABEL_SUFFIXES,
    ),
    Rule(
        label_contains=("service", "calc", "workflow", "manager", "strategy", "handler", "guard", "helper"),
        exclude_file=_ASYNC_KEYWORDS + _DATA_KEYWORDS + _DEVOPS_KEYWORDS,
        exclude_label=_ASYNC_LABEL_KEYWORDS + _DATA_LABEL_KEYWORDS,
        exclude_label_ends_with=_DATA_LABEL_SUFFIXES,
    ),
    Rule(
        file_contains=("backend/src",),
        exclude_file=_ASYNC_KEYWORDS + _DATA_KEYWORDS + _DEVOPS_KEYWORDS,
        exclude_label=_ASYNC_LABEL_KEYWORDS + _DATA_LABEL_KEYWORDS,
        exclude_label_ends_with=_DATA_LABEL_SUFFIXES,
    ),
)

_DEFAULT_DATA_RULES: Tuple[Rule, ...] = (
    Rule(type_in=("db_model",), id_prefix=("prisma_model_",)),
    Rule(file_contains=_DATA_KEYWORDS),
    Rule(label_contains=_DATA_LABEL_KEYWORDS),
    Rule(label_ends_with=_DATA_LABEL_SUFFIXES),
)

_DEFAULT_ASYNC_RULES: Tuple[Rule, ...] = (
    Rule(file_contains=_ASYNC_KEYWORDS),
    Rule(label_contains=_ASYNC_LABEL_KEYWORDS),
)

_DEFAULT_DEVOPS_RULES: Tuple[Rule, ...] = (
    Rule(file_contains=_DEVOPS_KEYWORDS),
)

#: The built-in default layer set.
DEFAULT_LAYERS: Tuple[Layer, ...] = (
    Layer(LAYER_UI, "Layer 1: UI Trigger", 1,
          "React components, forms, buttons", _DEFAULT_UI_RULES),
    Layer(LAYER_API, "Layer 2: API Gateway", 2,
          "Controllers, endpoints, guards", _DEFAULT_API_RULES),
    Layer(LAYER_SERVICE, "Layer 3: Domain Service", 3,
          "Calculations, business logic, workflows", _DEFAULT_SERVICE_RULES),
    Layer(LAYER_DATA, "Layer 4: Data & Persistence", 4,
          "Prisma models, database tables", _DEFAULT_DATA_RULES),
    Layer(LAYER_ASYNC, "Layer 5: Async & System Tasks", 5,
          "Cron, polling, workers", _DEFAULT_ASYNC_RULES),
    Layer(LAYER_DEVOPS, "Layer 6: DevOps & Infrastructure", 6,
          "Docker, Helm, CI/CD", _DEFAULT_DEVOPS_RULES),
    Layer(LAYER_UTILITY, "General / Utility", 7,
          "Shared helpers and anything that fits no other layer", ()),
)


class LayerRegistry:
    """An immutable, ordered set of :class:`Layer` definitions."""

    def __init__(self, layers: Sequence[Layer], utility_id: str = LAYER_UTILITY):
        ordered = tuple(sorted(layers, key=lambda layer: (layer.order, layer.id)))
        if not ordered:
            raise ValueError("a layer registry needs at least one layer")

        by_id: Dict[str, Layer] = {}
        by_name: Dict[str, Layer] = {}
        for layer in ordered:
            if not layer.id:
                raise ValueError("every layer needs a non-empty id")
            if layer.id in by_id:
                raise ValueError(f"duplicate layer id: {layer.id!r}")
            if layer.name in by_name:
                raise ValueError(f"duplicate layer display name: {layer.name!r}")
            by_id[layer.id] = layer
            by_name[layer.name] = layer

        if utility_id not in by_id:
            raise ValueError(
                f"utility_id {utility_id!r} is not one of the registered layers"
            )

        self._layers = ordered
        self._by_id = by_id
        self._by_name = by_name
        self._utility_id = utility_id

    # -- construction ----------------------------------------------------- #

    @classmethod
    def from_records(cls, records: Sequence[Mapping[str, Any]],
                     utility_id: str = LAYER_UTILITY) -> "LayerRegistry":
        """Builds a registry from plain records."""
        layers = []
        for index, record in enumerate(records):
            rules_raw = record.get("rules") or ()
            rules = tuple(Rule.from_record(r) for r in rules_raw)
            layers.append(Layer(
                id=str(record["id"]),
                name=str(record["name"]),
                order=int(record.get("order", index + 1)),
                description=str(record.get("description", "")),
                rules=rules,
            ))
        return cls(layers, utility_id=utility_id)

    def replacing(self, layer_id: str, **changes: Any) -> "LayerRegistry":
        """A copy of this registry with one layer's attributes overridden."""
        if layer_id not in self._by_id:
            raise KeyError(layer_id)
        records = []
        for layer in self._layers:
            record = layer.as_record()
            if layer.id == layer_id:
                record.update(changes)
            records.append(record)
        return LayerRegistry.from_records(records, utility_id=self._utility_id)

    # -- access ------------------------------------------------------------ #

    def __iter__(self) -> Iterator[Layer]:
        return iter(self._layers)

    def __len__(self) -> int:
        return len(self._layers)

    def __contains__(self, layer_id: object) -> bool:
        return layer_id in self._by_id

    def ordered(self) -> Tuple[Layer, ...]:
        """Every layer, ranked by ``order``."""
        return self._layers

    def ids(self) -> Tuple[str, ...]:
        return tuple(layer.id for layer in self._layers)

    def names(self) -> Tuple[str, ...]:
        return tuple(layer.name for layer in self._layers)

    def get(self, layer_id: str) -> Layer:
        return self._by_id[layer_id]

    def by_id(self, layer_id: str) -> Optional[Layer]:
        return self._by_id.get(layer_id)

    def by_name(self, name: str) -> Optional[Layer]:
        return self._by_name.get(name)

    def name(self, layer_id: str) -> str:
        layer = self._by_id.get(layer_id)
        return layer.name if layer else ""

    def order(self, layer_id: str) -> int:
        layer = self._by_id.get(layer_id)
        return layer.order if layer else self.unranked_order

    @property
    def unranked_order(self) -> int:
        return self._layers[-1].order + 1

    # -- the utility / catch-all bucket ------------------------------------ #

    @property
    def utility_id(self) -> str:
        return self._utility_id

    @property
    def utility(self) -> Layer:
        return self._by_id[self._utility_id]

    def is_utility(self, layer_id: str) -> bool:
        return layer_id == self._utility_id

    # -- node helpers ------------------------------------------------------ #

    def id_for_name(self, name: str) -> str:
        layer = self._by_name.get(name or "")
        return layer.id if layer else ""

    def id_of(self, node_data: Mapping[str, Any]) -> str:
        layer_id = str(node_data.get("layer_id") or "")
        if layer_id:
            return layer_id
        return self.id_for_name(str(node_data.get("layer") or ""))


def default_registry() -> LayerRegistry:
    """A fresh registry over the built-in default layer set."""
    return LayerRegistry(DEFAULT_LAYERS)


_ACTIVE_REGISTRY: LayerRegistry = default_registry()


def get_registry() -> LayerRegistry:
    """The registry every module reads. Resolved per call, never cached."""
    return _ACTIVE_REGISTRY


def set_registry(registry: LayerRegistry) -> LayerRegistry:
    """Installs *registry* as the active one and returns the previous one."""
    global _ACTIVE_REGISTRY
    if not isinstance(registry, LayerRegistry):
        raise TypeError("expected a LayerRegistry")
    previous = _ACTIVE_REGISTRY
    _ACTIVE_REGISTRY = registry
    return previous


@contextlib.contextmanager
def use_registry(registry: LayerRegistry) -> Iterator[LayerRegistry]:
    """Temporarily activates *registry*."""
    previous = set_registry(registry)
    try:
        yield registry
    finally:
        set_registry(previous)


# --------------------------------------------------------------------------- #
# Convenience wrappers over the active registry
# --------------------------------------------------------------------------- #

def layer_id_of(node_data: Mapping[str, Any]) -> str:
    return get_registry().id_of(node_data)


def layer_name(layer_id: str) -> str:
    return get_registry().name(layer_id)


def layer_order(layer_id: str) -> int:
    return get_registry().order(layer_id)


def utility_layer_id() -> str:
    return get_registry().utility_id
