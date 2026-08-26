"""
Interactive multi-layer clustered visualizer for TLDRGraph.

Module map:

- :mod:`~tldrgraph.visualizer.palette` - color palette and layer styling config
- :mod:`~tldrgraph.visualizer.data`    - graph snapshot -> two-tier render payload
- :mod:`~tldrgraph.visualizer.render`  - inlines payload + assets into one HTML file
- ``assets/index.html`` / ``assets/app.css`` / ``assets/app.js`` - the standalone app

Features:
- Dynamic zoom-driven level of detail (module overview -> component detail).
- Focus mode: clicking a node isolates it with only its direct callers/callees.
- Draggable nodes and free canvas panning.
- Self-contained standalone HTML application with zero external dependencies.
"""

from __future__ import annotations

from .data import prepare_visualizer_data
from .palette import FALLBACK_COLOR, PALETTE, build_layers_config
from .render import generate_visualizer_html, render_html

__all__ = [

    "PALETTE",
    "FALLBACK_COLOR",
    "build_layers_config",
    "prepare_visualizer_data",
    "render_html",
    "generate_visualizer_html",
]
