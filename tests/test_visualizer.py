"""
Tests for CodeChakra Standalone Interactive Visualizer (Phase 4).
"""

import os
from codechakra.layers import default_registry, set_registry, use_registry
from codechakra.visualizer import build_layers_config, generate_visualizer_html


def test_build_layers_config_default_registry():
    cfg = build_layers_config()
    assert len(cfg) == 6  # 6 non-utility layers in default registry
    assert cfg[0]["id"] == "ui"
    assert "color" in cfg[0]
    assert "border" in cfg[0]
    assert "bg" in cfg[0]


def test_build_layers_config_custom_registry():
    r = default_registry().replacing("ui", name="Frontend Layer")
    with use_registry(r):
        cfg = build_layers_config()
        assert cfg[0]["name"] == "Frontend Layer"


def test_generate_visualizer_html_file(mini_repo):
    html_path = generate_visualizer_html(str(mini_repo.root))
    assert os.path.isfile(html_path)
    assert html_path.endswith("CODECHAKRA_VISUALIZER.html")

    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Zero external CDN scripts or fonts
    assert "http://" not in content
    assert "https://" not in content
    assert "unpkg.com" not in content
    assert "cdnjs.cloudflare.com" not in content
    assert "fonts.googleapis.com" not in content

    # Self contained
    assert "<!DOCTYPE html>" in content
    assert "HIERARCHY =" in content
    assert "LAYERS_CONFIG =" in content
    assert "CodeChakra" in content
