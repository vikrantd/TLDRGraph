"""
Color palette and layer styling configuration for the visualizer.

The palette is deliberately high-contrast so that layers stay distinguishable
on the dark canvas background even at low zoom levels.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..layer_config import load_layer_config
from ..layers import get_registry

#: High-contrast modern color palette for dynamic layer styling
PALETTE: List[Dict[str, str]] = [
    {"color": "#38bdf8", "border": "#0284c7", "bg": "rgba(56, 189, 248, 0.12)", "glow": "rgba(56, 189, 248, 0.28)", "name": "Sky"},
    {"color": "#34d399", "border": "#059669", "bg": "rgba(52, 211, 153, 0.12)", "glow": "rgba(52, 211, 153, 0.28)", "name": "Emerald"},
    {"color": "#c084fc", "border": "#9333ea", "bg": "rgba(192, 132, 252, 0.12)", "glow": "rgba(192, 132, 252, 0.28)", "name": "Purple"},
    {"color": "#fbbf24", "border": "#d97706", "bg": "rgba(251, 191, 36, 0.12)", "glow": "rgba(251, 191, 36, 0.28)", "name": "Amber"},
    {"color": "#f87171", "border": "#dc2626", "bg": "rgba(248, 113, 113, 0.12)", "glow": "rgba(248, 113, 113, 0.28)", "name": "Rose"},
    {"color": "#818cf8", "border": "#4f46e5", "bg": "rgba(129, 140, 248, 0.12)", "glow": "rgba(129, 140, 248, 0.28)", "name": "Indigo"},
    {"color": "#fb923c", "border": "#ea580c", "bg": "rgba(251, 146, 60, 0.12)", "glow": "rgba(251, 146, 60, 0.28)", "name": "Orange"},
    {"color": "#2dd4bf", "border": "#0d9488", "bg": "rgba(45, 212, 191, 0.12)", "glow": "rgba(45, 212, 191, 0.28)", "name": "Teal"},
    {"color": "#f472b6", "border": "#db2777", "bg": "rgba(244, 114, 182, 0.12)", "glow": "rgba(244, 114, 182, 0.28)", "name": "Pink"},
]

#: Neutral styling used for the utility layer and for any unknown layer id.
FALLBACK_COLOR: Dict[str, str] = {
    "color": "#94a3b8",
    "border": "#475569",
    "bg": "rgba(148, 163, 184, 0.12)",
    "glow": "rgba(148, 163, 184, 0.28)",
    "name": "Slate",
}


def palette_at(index: int) -> Dict[str, str]:
    """Returns the palette entry for ``index``, cycling when it runs past the end."""
    return PALETTE[index % len(PALETTE)]


def build_layers_config(root_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Generates dynamic layer styling configuration from the active layer registry.
    """
    if root_dir is not None:
        load_layer_config(root_dir)
    registry = get_registry()
    config: List[Dict[str, Any]] = []
    layers = [layer for layer in registry.ordered() if layer.id != registry.utility_id]

    for idx, layer in enumerate(layers):
        palette_item = palette_at(idx)
        config.append({
            "id": layer.id,
            "name": layer.name,
            "order": layer.order,
            "description": layer.description,
            "color": palette_item["color"],
            "border": palette_item["border"],
            "bg": palette_item["bg"],
            "glow": palette_item.get("glow", palette_item["bg"]),
            "colIndex": idx,
        })
    return config
