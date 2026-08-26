"""
Standalone HTML assembly.

The markup, styling and canvas application live as plain files under
``assets/`` so they stay readable (and lintable) on their own; this module only
inlines them, together with the JSON payload, into a single zero-dependency
HTML file.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List


from .data import prepare_visualizer_data
from .palette import build_layers_config

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

OUTPUT_FILENAME = "TLDRGRAPH_VISUALIZER.html"


def _read_asset(name: str) -> str:
    with open(os.path.join(ASSETS_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def _json_for_script(payload: Any) -> str:
    """Serializes ``payload`` so it can be embedded safely inside a script tag."""
    return (
        json.dumps(payload, separators=(",", ":"))
        .replace("</script>", "<\\/script>")
        .replace("<!--", "<\\!--")
    )


def render_html(data: Dict[str, Any], layers_config: List[Dict[str, Any]]) -> str:

    """Inlines visualizer data, styles and app code into the standalone shell."""
    template = _read_asset("index.html")
    replacements = {
        "/*__STYLES__*/": _read_asset("app.css"),
        "/*__DATA_JSON__*/": _json_for_script(data),
        "/*__LAYERS_JSON__*/": _json_for_script(layers_config),
        "/*__SOURCEVIEW_JS__*/": _read_asset("sourceview.js"),
        "/*__APP_JS__*/": _read_asset("app.js"),
    }
    pattern = re.compile("|".join(re.escape(k) for k in replacements.keys()))
    return pattern.sub(lambda m: replacements[m.group(0)], template)



def generate_visualizer_html(root_dir: str = ".") -> str:
    """
    Builds the clustered free-flowing canvas visualizer and writes
    ``.tldrgraph/TLDRGRAPH_VISUALIZER.html``. Returns the written path.
    """
    root_dir = os.path.abspath(root_dir)
    out_dir = os.path.join(root_dir, ".tldrgraph")
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, OUTPUT_FILENAME)

    data = prepare_visualizer_data(root_dir)
    layers_config = build_layers_config(root_dir)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html(data, layers_config))

    return html_path
