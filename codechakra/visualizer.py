"""
Interactive Multi-Layer Compound Visualizer Generator for CodeChakra.

Renders a 3-tier compound hierarchical graph (schema ``codechakra/hierarchy@2``)
into a self-contained, standalone HTML application with zero external dependencies.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .hierarchy import build_multilayer_hierarchy
from .layers import get_registry

#: High-contrast modern color palette for dynamic layer styling
PALETTE = [
    {"color": "#c084fc", "border": "#9333ea", "bg": "rgba(192, 132, 252, 0.12)", "name": "Purple"},
    {"color": "#38bdf8", "border": "#0284c7", "bg": "rgba(56, 189, 248, 0.12)", "name": "Sky"},
    {"color": "#34d399", "border": "#059669", "bg": "rgba(52, 211, 153, 0.12)", "name": "Emerald"},
    {"color": "#fbbf24", "border": "#d97706", "bg": "rgba(251, 191, 36, 0.12)", "name": "Amber"},
    {"color": "#f87171", "border": "#dc2626", "bg": "rgba(248, 113, 113, 0.12)", "name": "Rose"},
    {"color": "#fb923c", "border": "#ea580c", "bg": "rgba(251, 146, 60, 0.12)", "name": "Orange"},
    {"color": "#818cf8", "border": "#4f46e5", "bg": "rgba(129, 140, 248, 0.12)", "name": "Indigo"},
    {"color": "#2dd4bf", "border": "#0d9488", "bg": "rgba(45, 212, 191, 0.12)", "name": "Teal"},
    {"color": "#f472b6", "border": "#db2777", "bg": "rgba(244, 114, 182, 0.12)", "name": "Pink"},
    {"color": "#a78bfa", "border": "#7c3aed", "bg": "rgba(167, 139, 250, 0.12)", "name": "Violet"},
]

FALLBACK_COLOR = {"color": "#94a3b8", "border": "#475569", "bg": "rgba(148, 163, 184, 0.12)", "name": "Slate"}


def build_layers_config() -> List[Dict[str, Any]]:
    """
    Generates dynamic column and styling configuration from the active layer registry.
    """
    registry = get_registry()
    config: List[Dict[str, Any]] = []
    layers = [layer for layer in registry.ordered() if layer.id != registry.utility_id]

    for idx, layer in enumerate(layers):
        palette_item = PALETTE[idx % len(PALETTE)]
        config.append({
            "id": layer.id,
            "name": layer.name,
            "order": layer.order,
            "description": layer.description,
            "color": palette_item["color"],
            "border": palette_item["border"],
            "bg": palette_item["bg"],
            "colIndex": idx,
        })
    return config


def generate_visualizer_html(root_dir: str = ".") -> str:
    """
    Builds the 3-tier multilayer hierarchy and writes the self-contained HTML visualizer.

    Returns the absolute path to .codechakra/CODECHAKRA_VISUALIZER.html.
    """
    root_dir = os.path.abspath(root_dir)
    out_dir = os.path.join(root_dir, ".codechakra")
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, "CODECHAKRA_VISUALIZER.html")

    hierarchy_data = build_multilayer_hierarchy(root_dir)
    layers_config = build_layers_config()

    html_content = _render_html(hierarchy_data, layers_config)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return html_path


def _render_html(hierarchy: Dict[str, Any], layers_config: List[Dict[str, Any]]) -> str:
    """Inlines hierarchy data and layer configurations into the HTML template."""
    raw_data_json = json.dumps(hierarchy, separators=(',', ':')).replace('</script>', '<\\/script>')
    layers_config_json = json.dumps(layers_config, separators=(',', ':')).replace('</script>', '<\\/script>')

    css_vars = []
    for idx, lc in enumerate(layers_config):
        css_vars.append(f"--layer-{lc['id']}-color: {lc['color']};")
        css_vars.append(f"--layer-{lc['id']}-border: {lc['border']};")
        css_vars.append(f"--layer-{lc['id']}-bg: {lc['bg']};")
    css_var_block = "\n    ".join(css_vars)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CodeChakra ☸️ Multi-Layer Architecture Visualizer</title>
  <style>
    :root {{
      --bg-base: #070913;
      --bg-panel: #0d1224;
      --bg-card: #131b35;
      --bg-card-hover: #1b264a;
      --bg-subnode: #0a0e1c;
      --border-subtle: #1e2942;
      --border-focus: #3b82f6;
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --accent: #6366f1;
      --accent-glow: rgba(99, 102, 241, 0.3);
      --method-get: #38bdf8;
      --method-post: #34d399;
      --method-put: #fbbf24;
      --method-delete: #f87171;
      --method-patch: #c084fc;
      {css_var_block}
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg-base);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      user-select: none;
    }}

    /* Header */
    header {{
      background: var(--bg-panel);
      border-bottom: 1px solid var(--border-subtle);
      padding: 10px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      z-index: 100;
      flex-shrink: 0;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 16px;
      font-weight: 700;
      letter-spacing: 0.5px;
    }}
    .brand span {{
      color: var(--accent);
    }}
    .stat-pills {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .stat-pill {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-subtle);
      padding: 4px 10px;
      border-radius: 9999px;
      font-size: 11px;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 5px;
    }}
    .stat-pill strong {{
      color: var(--text-main);
    }}

    /* Controls */
    .controls {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .view-switcher {{
      display: flex;
      background: rgba(0, 0, 0, 0.3);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 2px;
    }}
    .view-btn {{
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 600;
      border-radius: 4px;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .view-btn.active {{
      background: var(--accent);
      color: #fff;
    }}
    .search-box {{
      position: relative;
      width: 260px;
    }}
    .search-input {{
      width: 100%;
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid var(--border-subtle);
      color: var(--text-main);
      padding: 6px 12px 6px 28px;
      border-radius: 6px;
      font-size: 12px;
      outline: none;
    }}
    .search-input:focus {{
      border-color: var(--border-focus);
      box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
    }}
    .search-icon {{
      position: absolute;
      left: 9px;
      top: 7px;
      font-size: 12px;
      color: var(--text-dim);
    }}
    .btn-action {{
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      color: var(--text-main);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .btn-action:hover {{
      background: var(--bg-card-hover);
    }}

    /* Main workspace */
    main {{
      flex: 1;
      display: flex;
      position: relative;
      overflow: hidden;
    }}
    #canvas-container {{
      flex: 1;
      position: relative;
      overflow: auto;
      display: flex;
    }}
    #svg-connections {{
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      pointer-events: none;
      z-index: 10;
    }}

    /* Column architecture view */
    .columns-wrapper {{
      display: flex;
      gap: 20px;
      padding: 24px;
      min-width: 100%;
      position: relative;
      z-index: 5;
    }}
    .layer-column {{
      flex: 1;
      min-width: 320px;
      max-width: 440px;
      background: rgba(13, 18, 36, 0.6);
      border: 1px solid var(--border-subtle);
      border-radius: 10px;
      display: flex;
      flex-direction: column;
      height: fit-content;
      max-height: calc(100vh - 120px);
    }}
    .column-header {{
      padding: 12px 16px;
      border-bottom: 1px solid var(--border-subtle);
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-top-left-radius: 9px;
      border-top-right-radius: 9px;
      font-weight: 700;
      font-size: 13px;
      letter-spacing: 0.3px;
    }}
    .column-body {{
      padding: 12px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}

    /* Container Card */
    .card {{
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      padding: 12px;
      transition: all 0.2s ease;
      cursor: pointer;
      position: relative;
    }}
    .card:hover {{
      background: var(--bg-card-hover);
      border-color: rgba(255, 255, 255, 0.2);
    }}
    .card.selected {{
      border-color: var(--accent) !important;
      box-shadow: 0 0 16px var(--accent-glow);
    }}
    .card.dimmed {{
      opacity: 0.15;
    }}
    .card.highlighted {{
      border-color: #38bdf8;
      box-shadow: 0 0 12px rgba(56, 189, 248, 0.3);
    }}
    .card-header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 6px;
    }}
    .card-title {{
      font-size: 13px;
      font-weight: 600;
      color: var(--text-main);
      word-break: break-word;
      line-height: 1.3;
    }}
    .badge {{
      font-size: 10px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      flex-shrink: 0;
    }}
    .badge-page {{ background: rgba(192, 132, 252, 0.2); color: #c084fc; border: 1px solid #c084fc; }}
    .badge-component {{ background: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid #38bdf8; }}
    .badge-module {{ background: rgba(148, 163, 184, 0.2); color: #94a3b8; border: 1px solid #94a3b8; }}
    .badge-shared {{ background: rgba(251, 191, 36, 0.2); color: #fbbf24; border: 1px solid #fbbf24; }}

    .card-file {{
      font-size: 11px;
      color: var(--text-dim);
      font-family: monospace;
      margin-bottom: 8px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .card-meta {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 11px;
      color: var(--text-muted);
      border-top: 1px solid rgba(255, 255, 255, 0.05);
      padding-top: 6px;
      margin-top: 6px;
    }}

    /* Subnode list inside card */
    .subnodes-container {{
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px dashed var(--border-subtle);
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .subnode-item {{
      background: var(--bg-subnode);
      border: 1px solid var(--border-subtle);
      border-radius: 5px;
      padding: 6px 8px;
      font-size: 11px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      transition: all 0.15s ease;
    }}
    .subnode-item:hover {{
      border-color: rgba(255, 255, 255, 0.25);
      background: rgba(255, 255, 255, 0.04);
    }}
    .subnode-item.selected {{
      border-color: var(--accent);
    }}
    .subnode-label {{
      font-family: monospace;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      flex: 1;
    }}

    .method-pill {{
      font-size: 9px;
      font-weight: 800;
      padding: 1px 5px;
      border-radius: 3px;
      text-transform: uppercase;
    }}
    .method-get {{ background: rgba(56, 189, 248, 0.2); color: var(--method-get); }}
    .method-post {{ background: rgba(52, 211, 153, 0.2); color: var(--method-post); }}
    .method-put {{ background: rgba(251, 191, 36, 0.2); color: var(--method-put); }}
    .method-delete {{ background: rgba(248, 113, 113, 0.2); color: var(--method-delete); }}
    .method-patch {{ background: rgba(192, 132, 252, 0.2); color: var(--method-patch); }}

    /* Spine View */
    .spine-view {{
      padding: 24px;
      width: 100%;
      max-width: 1400px;
      margin: 0 auto;
      display: none;
      flex-direction: column;
      gap: 20px;
      overflow-y: auto;
    }}
    .spine-selector {{
      background: var(--bg-panel);
      border: 1px solid var(--border-subtle);
      padding: 16px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      gap: 16px;
    }}
    .spine-select {{
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      color: var(--text-main);
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 13px;
      flex: 1;
      outline: none;
    }}
    .spine-chain {{
      display: flex;
      align-items: stretch;
      gap: 12px;
      overflow-x: auto;
      padding: 16px 0;
    }}
    .spine-step {{
      flex: 1;
      min-width: 240px;
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      position: relative;
    }}
    .spine-arrow {{
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--accent);
      font-size: 20px;
      font-weight: bold;
    }}

    /* Shared Inspector View */
    .shared-view {{
      padding: 24px;
      width: 100%;
      max-width: 1400px;
      margin: 0 auto;
      display: none;
      flex-direction: column;
      gap: 20px;
      overflow-y: auto;
    }}
    .shared-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 16px;
    }}

    /* Detail Drawer */
    #detail-drawer {{
      width: 380px;
      background: var(--bg-panel);
      border-left: 1px solid var(--border-subtle);
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      padding: 20px;
      flex-shrink: 0;
      transition: transform 0.2s ease;
    }}
    .drawer-title {{
      font-size: 16px;
      font-weight: 700;
      margin-bottom: 4px;
      word-break: break-word;
    }}
    .drawer-file {{
      font-family: monospace;
      font-size: 12px;
      color: var(--text-dim);
      margin-bottom: 16px;
    }}
    .drawer-section {{
      margin-bottom: 16px;
    }}
    .drawer-section h4 {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      margin-bottom: 6px;
    }}
    .drawer-intent {{
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 10px;
      font-size: 12px;
      line-height: 1.4;
      color: var(--text-main);
    }}
    .field-tag {{
      display: inline-block;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-subtle);
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-family: monospace;
      margin: 2px;
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span>CodeChakra</span> ☸️ Multi-Tier Architecture
    </div>
    <div class="stat-pills">
      <div class="stat-pill">Containers: <strong id="stat-containers">0</strong></div>
      <div class="stat-pill">Elements: <strong id="stat-elements">0</strong></div>
      <div class="stat-pill">Endpoints: <strong id="stat-endpoints">0</strong></div>
      <div class="stat-pill">Edges: <strong id="stat-edges">0</strong></div>
      <div class="stat-pill">Shared Components: <strong id="stat-shared">0</strong></div>
      <div class="stat-pill">Tests: <strong id="stat-tests">0</strong></div>
    </div>
    <div class="controls">
      <div class="view-switcher">
        <button class="view-btn active" onclick="switchView('columns')">Columns</button>
        <button class="view-btn" onclick="switchView('spine')">Spine Flow</button>
        <button class="view-btn" onclick="switchView('shared')">Shared</button>
        <button id="btn-toggle-tests" class="view-btn" onclick="toggleTests()" title="Toggle visibility of test files and suites">🧪 Tests (All)</button>
      </div>
      <div class="search-box">
        <span class="search-icon">🔍</span>
        <input type="text" id="global-search" class="search-input" placeholder="Search routes, symbols, endpoints..." oninput="handleSearch(this.value)" />
      </div>
      <button class="btn-action" onclick="resetFlowTrace()">Reset Flow</button>
    </div>
  </header>

  <main>
    <div id="canvas-container">
      <svg id="svg-connections"></svg>
      <div id="view-columns" class="columns-wrapper"></div>
      <div id="view-spine" class="spine-view"></div>
      <div id="view-shared" class="shared-view"></div>
    </div>

    <aside id="detail-drawer">
      <div class="drawer-section">
        <div style="display:flex; gap:6px; align-items:center; margin-bottom:8px;">
          <div class="badge" id="drawer-tier-badge">Container</div>
          <div class="badge" id="drawer-test-badge" style="display:none; background:#495057; color:#f8f9fa; border:1px solid #6c757d;">🧪 TEST</div>
        </div>
        <h3 class="drawer-title" id="drawer-title">Select a node</h3>
        <div class="drawer-file" id="drawer-file">Click on any container or element to inspect</div>
      </div>
      <div class="drawer-section" id="drawer-intent-section">
        <h4>Natural Language Intent</h4>
        <div class="drawer-intent" id="drawer-intent" style="white-space: pre-wrap; line-height: 1.5;">No intent recorded</div>
      </div>
      <div class="drawer-section" id="drawer-input-fields-section">
        <h4>Input Parameters / Fields</h4>
        <div id="drawer-input-fields">(None)</div>
      </div>
      <div class="drawer-section" id="drawer-output-fields-section">
        <h4>Output Returns / Emitted</h4>
        <div id="drawer-output-fields">(None)</div>
      </div>
      <div class="drawer-section" id="drawer-connections-section">
        <h4>Connected Edges</h4>
        <div id="drawer-connections">(None)</div>
      </div>
    </aside>
  </main>

  <script>
    const HIERARCHY = {raw_data_json};
    const LAYERS_CONFIG = {layers_config_json};

    let activeView = 'columns';
    let selectedId = null;
    let traceNodes = new Set();
    let expandedContainers = new Set();

    console.time('CodeChakra Init');

    // Index Lookups
    const containersById = {{}};
    const elementsById = {{}};
    const incomingEdges = {{}};
    const outgoingEdges = {{}};

    HIERARCHY.containers.forEach(c => {{
      containersById[c.id] = c;
      incomingEdges[c.id] = [];
      outgoingEdges[c.id] = [];
      if (c.subnodes) {{
        c.subnodes.forEach(s => {{
          elementsById[s.id] = s;
          incomingEdges[s.id] = [];
          outgoingEdges[s.id] = [];
        }});
      }}
    }});

    HIERARCHY.edges.forEach(e => {{
      const src = e.source_subnode || e.source_container;
      const tgt = e.target_subnode || e.target_container;
      if (outgoingEdges[src]) outgoingEdges[src].push(e);
      if (incomingEdges[tgt]) incomingEdges[tgt].push(e);
    }});

    // Populate Top Stats
    document.getElementById('stat-containers').innerText = HIERARCHY.stats.containers;
    document.getElementById('stat-elements').innerText = HIERARCHY.stats.elements;
    document.getElementById('stat-endpoints').innerText = HIERARCHY.stats.endpoints;
    document.getElementById('stat-edges').innerText = HIERARCHY.stats.edges;
    const sharedCount = HIERARCHY.containers.filter(c => c.shared).length;
    document.getElementById('stat-shared').innerText = sharedCount;
    const testContainersCount = HIERARCHY.containers.filter(c => c.is_test).length;
    document.getElementById('stat-tests').innerText = testContainersCount;
    document.getElementById('btn-toggle-tests').innerText = `🧪 Tests (${{testContainersCount}})`;

    let hideTests = false;
    function toggleTests() {{
      hideTests = !hideTests;
      const btn = document.getElementById('btn-toggle-tests');
      btn.classList.toggle('active', hideTests);
      btn.innerText = hideTests ? '🧪 Tests (Hidden)' : `🧪 Tests (${{testContainersCount}})`;
      renderActiveView();
    }}

    function renderActiveView() {{
      if (activeView === 'columns') renderColumns();
      else if (activeView === 'spine') renderSpineView();
      else if (activeView === 'shared') renderSharedView();
    }}

    // View switcher
    function switchView(viewName) {{
      activeView = viewName;
      document.querySelectorAll('.view-btn').forEach(btn => {{
        if (btn.id !== 'btn-toggle-tests') {{
          btn.classList.toggle('active', btn.innerText.toLowerCase().includes(viewName));
        }}
      }});
      document.getElementById('view-columns').style.display = viewName === 'columns' ? 'flex' : 'none';
      document.getElementById('view-spine').style.display = viewName === 'spine' ? 'flex' : 'none';
      document.getElementById('view-shared').style.display = viewName === 'shared' ? 'flex' : 'none';
      renderActiveView();
      clearSvg();
    }}

    // Render Architecture Columns
    function renderColumns() {{
      const wrapper = document.getElementById('view-columns');
      wrapper.innerHTML = '';

      LAYERS_CONFIG.forEach(layer => {{
        const col = document.createElement('div');
        col.className = 'layer-column';
        col.innerHTML = `
          <div class="column-header" style="background: ${{layer.bg}}; color: ${{layer.color}}; border-top: 3px solid ${{layer.color}};">
            <span>${{layer.name}}</span>
            <span class="badge" style="background: rgba(0,0,0,0.3); color: ${{layer.color}};">${{layer.id}}</span>
          </div>
          <div class="column-body" id="col-body-${{layer.id}}"></div>
        `;
        wrapper.appendChild(col);
      }});

      // Group containers by layer_id
      HIERARCHY.containers.forEach(c => {{
        if (hideTests && c.is_test) return;

        const targetCol = document.getElementById(`col-body-${{c.layer_id}}`);
        if (!targetCol) return;

        const card = document.createElement('div');
        card.className = `card ${{c.is_test ? 'test-container' : ''}}`;
        card.id = `card-${{c.id}}`;
        card.onclick = (ev) => {{ ev.stopPropagation(); selectContainer(c.id); }};

        const tierBadgeClass = c.tier === 'page' ? 'badge-page' : (c.tier === 'component' ? 'badge-component' : 'badge-module');
        const sharedBadge = c.shared ? `<span class="badge badge-shared">🔗 Shared (${{c.parent_containers.length}})</span>` : '';
        const testBadge = c.is_test ? `<span class="badge" style="background:#495057; color:#f8f9fa; border:1px solid #6c757d;">🧪 test</span>` : '';

        card.innerHTML = `
          <div class="card-header">
            <div class="card-title">${{c.display_label || c.label}}</div>
            <div style="display:flex; gap:4px; align-items:center;">
              ${{testBadge}}
              ${{sharedBadge}}
              <span class="badge ${{tierBadgeClass}}">${{c.tier}}</span>
            </div>
          </div>
          <div class="card-file" title="${{c.file}}">${{c.file}}</div>
          <div class="card-meta">
            <span>Elements: ${{c.subnode_count}}</span>
            ${{c.subnode_count > 0 ? `<button class="btn-action" style="padding: 2px 6px; font-size: 10px;" onclick="toggleExpand('${{c.id}}', event)">${{expandedContainers.has(c.id) ? 'Collapse' : 'Expand'}}</button>` : ''}}
          </div>
          <div class="subnodes-container" id="subnodes-${{c.id}}" style="display: ${{expandedContainers.has(c.id) ? 'flex' : 'none'}};">
            ${{renderSubnodesHtml(c)}}
          </div>
        `;
        targetCol.appendChild(card);
      }});
    }}

    function renderSubnodesHtml(c) {{
      if (!c.subnodes || c.subnodes.length === 0) return '';
      return c.subnodes.map(s => {{
        let methodPill = '';
        if (s.method) {{
          methodPill = `<span class="method-pill method-${{s.method.toLowerCase()}}">${{s.method}}</span>`;
        }}
        const testSubnodeBadge = s.is_test ? `<span class="badge" style="background:#495057; color:#f8f9fa; font-size:9px; padding:1px 3px;">🧪</span>` : '';
        return `
          <div class="subnode-item ${{s.is_test ? 'test-subnode' : ''}}" id="subnode-${{s.id}}" onclick="selectElement('${{s.id}}', event)">
            ${{methodPill}}
            ${{testSubnodeBadge}}
            <span class="subnode-label" title="${{s.display_label || s.label}}">${{s.display_label || s.label}}</span>
            <span class="badge" style="font-size: 9px; padding: 1px 4px;">${{s.kind || s.type}}</span>
          </div>
        `;
      }}).join('');
    }}

    function toggleExpand(containerId, ev) {{
      ev.stopPropagation();
      if (expandedContainers.has(containerId)) {{
        expandedContainers.delete(containerId);
      }} else {{
        expandedContainers.add(containerId);
      }}
      const el = document.getElementById(`subnodes-${{containerId}}`);
      if (el) {{
        el.style.display = expandedContainers.has(containerId) ? 'flex' : 'none';
      }}
      const btn = ev.target;
      if (btn) {{
        btn.innerText = expandedContainers.has(containerId) ? 'Collapse' : 'Expand';
      }}
    }}

    // Selection & Flow Tracing
    function selectContainer(id) {{
      selectedId = id;
      const c = containersById[id];
      if (!c) return;

      // Update Inspector Drawer
      document.getElementById('drawer-tier-badge').innerText = c.tier.toUpperCase();
      document.getElementById('drawer-test-badge').style.display = c.is_test ? 'inline-block' : 'none';
      document.getElementById('drawer-title').innerText = c.display_label || c.label;
      document.getElementById('drawer-file').innerText = c.file;
      document.getElementById('drawer-intent').innerText = c.intent || 'No intent recorded';
      
      const inFields = (c.input_fields && c.input_fields.length > 0) ? c.input_fields : (c.fields || []);
      const outFields = c.output_fields || [];
      document.getElementById('drawer-input-fields').innerHTML = (inFields && inFields.length > 0)
        ? inFields.map(f => `<span class="field-tag" style="background:#e8f4fd; color:#0d6efd; border-color:#b6d4fe;">${{f}}</span>`).join('')
        : '<em>(None)</em>';
      document.getElementById('drawer-output-fields').innerHTML = (outFields && outFields.length > 0)
        ? outFields.map(f => `<span class="field-tag" style="background:#f3e8fd; color:#6f42c1; border-color:#d8b4fe;">${{f}}</span>`).join('')
        : '<em>(None)</em>';

      // Show connected edges
      const outs = outgoingEdges[id] || [];
      const ins = incomingEdges[id] || [];
      document.getElementById('drawer-connections').innerHTML = `
        <div style="font-size:11px; margin-bottom:4px;"><strong>Outbound (${{outs.length}}):</strong></div>
        ${{outs.slice(0, 10).map(e => `<div>➔ ${{e.relation}} ➔ ${{e.target_container}}</div>`).join('')}}
        <div style="font-size:11px; margin-top:8px; margin-bottom:4px;"><strong>Inbound (${{ins.length}}):</strong></div>
        ${{ins.slice(0, 10).map(e => `<div>⬅ ${{e.relation}} ⬅ ${{e.source_container}}</div>`).join('')}}
      `;

      // Flow trace
      traceFlow(id);
    }}

    function selectElement(id, ev) {{
      ev.stopPropagation();
      selectedId = id;
      const s = elementsById[id];
      if (!s) return;

      document.getElementById('drawer-tier-badge').innerText = s.kind || s.type;
      document.getElementById('drawer-test-badge').style.display = s.is_test ? 'inline-block' : 'none';
      document.getElementById('drawer-title').innerText = s.display_label || s.label;
      document.getElementById('drawer-file').innerText = `${{s.file}} ${{s.source_location ? `(${{s.source_location}})` : ''}}`;
      document.getElementById('drawer-intent').innerText = s.intent || 'No intent recorded';
      
      const inFields = (s.input_fields && s.input_fields.length > 0) ? s.input_fields : (s.fields || []);
      const outFields = s.output_fields || [];
      document.getElementById('drawer-input-fields').innerHTML = (inFields && inFields.length > 0)
        ? inFields.map(f => `<span class="field-tag" style="background:#e8f4fd; color:#0d6efd; border-color:#b6d4fe;">${{f}}</span>`).join('')
        : '<em>(None)</em>';
      document.getElementById('drawer-output-fields').innerHTML = (outFields && outFields.length > 0)
        ? outFields.map(f => `<span class="field-tag" style="background:#f3e8fd; color:#6f42c1; border-color:#d8b4fe;">${{f}}</span>`).join('')
        : '<em>(None)</em>';

      traceFlow(id);
    }}

    function traceFlow(startId) {{
      traceNodes.clear();
      const queue = [startId];
      traceNodes.add(startId);

      // BFS downstream
      while (queue.length > 0) {{
        const curr = queue.shift();
        const outs = outgoingEdges[curr] || [];
        outs.forEach(e => {{
          const tgt = e.target_subnode || e.target_container;
          if (tgt && !traceNodes.has(tgt)) {{
            traceNodes.add(tgt);
            queue.push(tgt);
          }}
        }});
      }}

      // BFS upstream
      const inQueue = [startId];
      while (inQueue.length > 0) {{
        const curr = inQueue.shift();
        const ins = incomingEdges[curr] || [];
        ins.forEach(e => {{
          const src = e.source_subnode || e.source_container;
          if (src && !traceNodes.has(src)) {{
            traceNodes.add(src);
            inQueue.push(src);
          }}
        }});
      }}

      // Apply dimming / highlighting
      document.querySelectorAll('.card').forEach(card => {{
        const cid = card.id.replace('card-', '');
        const isParticipant = traceNodes.has(cid) || (containersById[cid] && containersById[cid].subnodes && containersById[cid].subnodes.some(s => traceNodes.has(s.id)));
        card.classList.toggle('dimmed', !isParticipant);
        card.classList.toggle('selected', cid === startId);
        card.classList.toggle('highlighted', isParticipant && cid !== startId);
      }});
    }}

    function resetFlowTrace() {{
      traceNodes.clear();
      selectedId = null;
      document.querySelectorAll('.card').forEach(card => {{
        card.classList.remove('dimmed', 'selected', 'highlighted');
      }});
      clearSvg();
    }}

    function clearSvg() {{
      const svg = document.getElementById('svg-connections');
      if (svg) svg.innerHTML = '';
    }}

    // Render Spine View
    function renderSpineView() {{
      const spineContainer = document.getElementById('view-spine');
      const pages = HIERARCHY.containers.filter(c => c.tier === 'page');

      spineContainer.innerHTML = `
        <div class="spine-selector">
          <strong>Select End-to-End Spine:</strong>
          <select class="spine-select" onchange="displaySpineChain(this.value)">
            ${{pages.map(p => `<option value="${{p.id}}">${{p.display_label || p.label}} (${{p.file}})</option>`).join('')}}
          </select>
        </div>
        <div class="spine-chain" id="spine-chain-display"></div>
      `;

      if (pages.length > 0) {{
        displaySpineChain(pages[0].id);
      }}
    }}

    function displaySpineChain(pageId) {{
      const chainDisplay = document.getElementById('spine-chain-display');
      const page = containersById[pageId];
      if (!page) return;

      // Find components inside page
      const compEdges = (outgoingEdges[pageId] || []).filter(e => e.relation === 'page_contains');
      const components = compEdges.map(e => containersById[e.target_container]).filter(Boolean);

      let chainHtml = `
        <div class="spine-step" style="border-left: 4px solid var(--layer-ui-color);">
          <span class="badge badge-page">Page (UI)</span>
          <h4 style="margin-top:8px;">${{page.display_label || page.label}}</h4>
          <div style="font-size:11px; color:var(--text-dim); margin-top:4px;">${{page.file}}</div>
        </div>
      `;

      if (components.length > 0) {{
        const comp = components[0];
        chainHtml += `
          <div class="spine-arrow">➔</div>
          <div class="spine-step" style="border-left: 4px solid var(--layer-ui-color);">
            <div style="display:flex; justify-content:space-between;">
              <span class="badge badge-component">Component</span>
              ${{comp.shared ? `<span class="badge badge-shared">Shared (${{comp.parent_containers.length}} pages)</span>` : ''}}
            </div>
            <h4 style="margin-top:8px;">${{comp.label}}</h4>
            <div style="font-size:11px; color:var(--text-dim); margin-top:4px;">${{comp.file}}</div>
          </div>
        `;

        // Find endpoint calls
        const epEdges = (outgoingEdges[comp.id] || []).filter(e => e.relation === 'calls_endpoint');
        if (epEdges.length > 0) {{
          const epSub = elementsById[epEdges[0].target_subnode];
          if (epSub) {{
            chainHtml += `
              <div class="spine-arrow">➔</div>
              <div class="spine-step" style="border-left: 4px solid var(--layer-api-color);">
                <span class="badge" style="background: rgba(56, 189, 248, 0.2); color: #38bdf8;">API Endpoint</span>
                <h4 style="margin-top:8px;">${{epSub.display_label}}</h4>
                <div style="font-size:11px; color:var(--text-dim); margin-top:4px;">${{epSub.file}}</div>
              </div>
            `;

            // Find Handler
            const hEdges = (outgoingEdges[epSub.id] || []).filter(e => e.relation === 'handled_by');
            if (hEdges.length > 0) {{
              const hSub = elementsById[hEdges[0].target_subnode];
              if (hSub) {{
                chainHtml += `
                  <div class="spine-arrow">➔</div>
                  <div class="spine-step" style="border-left: 4px solid var(--layer-service-color);">
                    <span class="badge" style="background: rgba(52, 211, 153, 0.2); color: #34d399;">Service Handler</span>
                    <h4 style="margin-top:8px;">${{hSub.display_label}}</h4>
                    <div style="font-size:11px; color:var(--text-dim); margin-top:4px;">${{hSub.file}}</div>
                  </div>
                `;
              }}
            }}
          }}
        }}
      }}

      chainDisplay.innerHTML = chainHtml;
    }}

    // Render Shared Components View
    function renderSharedView() {{
      const sharedContainer = document.getElementById('view-shared');
      const sharedComponents = HIERARCHY.containers.filter(c => c.shared);

      sharedContainer.innerHTML = `
        <div style="margin-bottom:12px;">
          <h2>Shared Components (${{sharedComponents.length}})</h2>
          <p style="color:var(--text-muted); font-size:13px;">Components imported by multiple pages across the application DAG.</p>
        </div>
        <div class="shared-grid">
          ${{sharedComponents.map(c => `
            <div class="card" onclick="selectContainer('${{c.id}}')">
              <div class="card-header">
                <div class="card-title">${{c.label}}</div>
                <span class="badge badge-shared">🔗 ${{c.parent_containers.length}} Parents</span>
              </div>
              <div class="card-file">${{c.file}}</div>
              <div style="font-size:12px; margin-top:8px;">
                <div style="color:var(--text-muted); margin-bottom:4px;">Imported by:</div>
                <ul style="padding-left:16px; font-size:11px; color:var(--text-dim);">
                  ${{c.parent_containers.map(p => `<li>${{containersById[p] ? containersById[p].display_label || containersById[p].label : p}}</li>`).join('')}}
                </ul>
              </div>
            </div>
          `).join('')}}
        </div>
      `;
    }}

    // Search filter
    function handleSearch(query) {{
      const q = query.trim().toLowerCase();
      if (!q) {{
        resetFlowTrace();
        return;
      }}
      document.querySelectorAll('.card').forEach(card => {{
        const cid = card.id.replace('card-', '');
        const c = containersById[cid];
        if (!c) return;
        const matchesContainer = (c.label && c.label.toLowerCase().includes(q)) ||
                                 (c.display_label && c.display_label.toLowerCase().includes(q)) ||
                                 (c.file && c.file.toLowerCase().includes(q)) ||
                                 (c.intent && c.intent.toLowerCase().includes(q));
        const matchesSubnode = c.subnodes && c.subnodes.some(s =>
          (s.label && s.label.toLowerCase().includes(q)) ||
          (s.display_label && s.display_label.toLowerCase().includes(q)) ||
          (s.intent && s.intent.toLowerCase().includes(q)) ||
          (s.path && s.path.toLowerCase().includes(q))
        );
        card.classList.toggle('dimmed', !matchesContainer && !matchesSubnode);
        card.classList.toggle('highlighted', matchesContainer || matchesSubnode);
      }});
    }}

    // Initial render
    renderColumns();
    console.timeEnd('CodeChakra Init');
  </script>
</body>
</html>
"""
