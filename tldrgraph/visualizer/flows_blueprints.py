"""
The workflows TLDRGraph knows about in its own codebase, written by hand.

Every other repository gets its workflows discovered from the call graph (see
``flows_discover``); these blueprints exist because this project's own journeys
are worth describing precisely. A blueprint whose steps do not resolve against
the repository being scanned is dropped rather than shown as fiction.

Each step is ``(file, symbol, what it does)``.
"""

from __future__ import annotations

CURATED_BLUEPRINTS = [
    {
        "id": "flow_cli_init_pipeline",
        "title": "CLI Init & Dynamic Architecture Discovery",
        "category": "Architecture & Ingestion",
        "summary": "Orchestrates end-to-end repository initialization: runs Graphify AST extraction, discovers dynamic architectural layers from evidence, classifies symbols, updates SQLite hash gate, and builds visualizer.",
        "steps": [
            ("tldrgraph/cli.py", "init", "Entry point for 'tldrgraph init' CLI command; parses flags and dispatches to init pipeline."),
            ("tldrgraph/installer.py", "install_agent_rules", "Writes .tldrgraph/AGENT_CONTRACT.md and configures slash commands across Claude Code, Cursor, and Windsurf."),
            ("tldrgraph/graph_loader.py", "load_or_extract", "Runs Graphify detection, AST extraction, community clustering, and loads cached graph."),
            ("tldrgraph/propose_layers.py", "propose_layers", "Scans repository evidence to propose a dynamic 6-layer architecture taxonomy with glob rules."),
            ("tldrgraph/layer_config.py", "save_layer_config", "Persists discovered architectural layer taxonomy to .tldrgraph/layers.config.yaml."),
            ("tldrgraph/hash_gate.py", "check_node", "Computes SHA-256 fingerprint for AST node content to track clean vs. dirty nodes in SQLite."),
            ("tldrgraph/snapshot_sync.py", "save_graph_snapshot", "Synchronizes in-memory NetworkX graph state and layer assignments to .tldrgraph/graph.json."),
            ("tldrgraph/visualizer/render.py", "generate_visualizer_html", "Generates the standalone interactive clustered HTML visualizer at .tldrgraph/TLDRGRAPH_VISUALIZER.html."),
        ],
    },
    {
        "id": "flow_hybrid_semantic_search_query",
        "title": "Hybrid Semantic Search & Flow Query Engine",
        "category": "Search & Pathfinding",
        "summary": "Performs hybrid retrieval combining lexical TF-IDF cosine similarity with FastEmbed ONNX dense embeddings. Discovers top matching entry point nodes and executes bridge-aware Dijkstra pathfinding.",
        "steps": [
            ("tldrgraph/cli.py", "query", "Entry point for 'tldrgraph query <intent>'; parses search prompt and token budget."),
            ("tldrgraph/flow_engine.py", "query_flow", "Orchestrates semantic vector search across indexed symbols and initiates downstream path expansion."),
            ("tldrgraph/dense_embedder.py", "encode", "Generates 384-dimensional normalized dense embedding for the user query using FastEmbed ONNX model."),
            ("tldrgraph/vector_store.py", "search", "Computes cosine similarity against indexed symbol intents and applies dynamic score fusion."),
            ("tldrgraph/vector_tfidf.py", "query_vector", "Performs lexical BM25 token matching for exact code identifier and symbol lookup."),
            ("tldrgraph/flow_traversal.py", "bridge_aware_walk", "Follows caller/callee and cross-layer bridge edges to extract full end-to-end execution slice."),
            ("tldrgraph/flow_engine.py", "render_markdown_table", "Formats extracted nodes into layer-grounded Markdown table with budget control."),
        ],
    },
    {
        "id": "flow_cross_layer_trace_engine",
        "title": "Cross-Layer Shortest Path Trace Engine",
        "category": "Search & Pathfinding",
        "summary": "Resolves source and optional target symbols using exact identifiers or vector similarity, and calculates the exact directed shortest path between them across architectural layers using Dijkstra pathfinding.",
        "steps": [
            ("tldrgraph/cli.py", "trace", "Entry point for 'tldrgraph trace <Source> <Target>'; parses symbol names and flags."),
            ("tldrgraph/flow_traversal.py", "resolve_node_id", "Resolves fuzzy symbol or query strings into exact graph node IDs using label normalization and vector fallback."),
            ("tldrgraph/flow_engine.py", "trace_path", "Main routing method calculating targeted shortest path search across architectural boundaries."),
            ("tldrgraph/call_resolver.py", "resolve_call_target", "Fuzzy-matches symbol string identifiers and evaluates cross-layer bridge relationships."),
            ("tldrgraph/flow_engine.py", "render_markdown_table", "Renders step-by-step path results with transition relations, layer rankings, and parameters."),
        ],
    },
    {
        "id": "flow_ast_route_seam_extraction",
        "title": "AST Analysis & Route / Seam Extraction Pipeline",
        "category": "Extraction & Seams",
        "summary": "Extracts deterministic cross-layer architectural seams connecting frontend HTTP clients, API endpoints, backend controllers, and database models into interconnected graph seams.",
        "steps": [
            ("tldrgraph/graph_loader.py", "load_or_extract", "Loads AST graph from graphify and triggers deterministic extractor pipelines."),
            ("tldrgraph/extractors_client.py", "extract_client_calls", "Scans UI/client source files for Axios, fetch, and API client calls with URL literals."),
            ("tldrgraph/extractors_route.py", "extract_http_routes", "Scans backend controllers for @Controller and method decorators (@Get, @Post) and handler functions."),
            ("tldrgraph/extractors_prisma.py", "extract_prisma_models", "Parses schema.prisma files extracting database models, fields, field types, and relation maps."),
            ("tldrgraph/node_registrar.py", "register_nodes", "Creates synthetic API endpoint and model nodes and binds deterministic bridge edges."),
            ("tldrgraph/labels.py", "build_display_labels", "Computes human-friendly disambiguated display labels across all nodes based on module and relation context."),
        ],
    },
    {
        "id": "flow_incremental_hash_gate_indexing",
        "title": "Incremental Hash Gate & Vector Store Indexing",
        "category": "Storage & Indexing",
        "summary": "Tracks source code modifications with SHA-256 content signatures and an SQLite node cache to ensure zero-token incremental graph updates. Syncs TF-IDF vocabulary and FastEmbed ONNX dense embeddings.",
        "steps": [
            ("tldrgraph/snapshot_sync.py", "load_file_hashes", "Calculates SHA-256 hashes for all project source files to detect modified files."),
            ("tldrgraph/snapshot_sync.py", "node_signature", "Computes composite hash signature per symbol incorporating file hash, layer, and location."),
            ("tldrgraph/hash_gate.py", "check_node", "Queries SQLite node_cache table to determine if node is dirty or can be restored from cache."),
            ("tldrgraph/graph_loader.py", "_attach_cached_node_state", "Attaches cached intent, summary, and parameter fields to clean nodes, queuing dirty nodes for enrichment."),
            ("tldrgraph/vector_store.py", "add_documents", "Updates indexed documents and computes term inverse document frequencies (IDF)."),
            ("tldrgraph/dense_embedder.py", "encode", "Encodes new/modified documents via DenseEmbedder ONNX model and caches vector outputs."),
            ("tldrgraph/snapshot_sync.py", "save_graph_snapshot", "Persists complete graph snapshot containing nodes, layers, and edges into .tldrgraph/graph.json."),
        ],
    },
    {
        "id": "flow_interactive_visualizer_assembly",
        "title": "Interactive Visualizer Compilation & Serving",
        "category": "Visualization & UI",
        "summary": "Constructs a clustered two-tier graph hierarchy, computes high-contrast layer color palettes, extracts declaration line ranges, and compiles CSS, JS, and JSON data into a standalone HTML file.",
        "steps": [
            ("tldrgraph/cli.py", "visualizer_cmd", "Parses UI command options (--serve, --port, --open) and initiates HTML compilation."),
            ("tldrgraph/visualizer/render.py", "generate_visualizer_html", "Coordinates visualizer data payload preparation and writes .tldrgraph/TLDRGRAPH_VISUALIZER.html."),
            ("tldrgraph/visualizer/palette.py", "build_layers_config", "Builds high-contrast color palette configs (colors, borders, glows) for active architectural layers."),
            ("tldrgraph/visualizer/data.py", "prepare_visualizer_data", "Builds Tier 1 parent modules, Tier 2 child symbol nodes, aggregated module edges, and workflow traces."),
            ("tldrgraph/visualizer/source.py", "locate_symbol", "Detects symbol declaration start and end line ranges using indentation or brace balancing."),
            ("tldrgraph/visualizer/flows_data.py", "extract_visualizer_workflows", "Extracts multi-step end-to-end execution workflows from top entry point roots."),
            ("tldrgraph/visualizer/render.py", "render_html", "Performs safe single-pass regex replacement to produce standalone .tldrgraph/TLDRGRAPH_VISUALIZER.html."),
            ("tldrgraph/cli_commands.py", "serve_visualizer", "Starts local HTTP server on specified port and opens default web browser."),
        ],
    },
    {
        "id": "flow_dead_code_reachability_detection",
        "title": "Dead-Code Reachability & Review Candidate Detection",
        "category": "Analysis & Quality",
        "summary": "Performs forward reachability analysis across the dependency graph to classify symbols into review candidates, unreviewed items, live referenced nodes, framework entry points, or non-code.",
        "steps": [
            ("tldrgraph/cli.py", "dead_code", "Entry point for 'tldrgraph dead-code'; parses status filter, limit, and json output options."),
            ("tldrgraph/cli_commands.py", "run_dead_code_report", "Loads graph snapshot or rebuilds graph to inspect dead code classifications."),
            ("tldrgraph/deadcode.py", "compute_enrichment_coverage", "Calculates ratio of source-enriched nodes to total non-utility code nodes."),
            ("tldrgraph/deadcode.py", "classify_dead_code", "Iterates all graph nodes, categorizing nodes with in_degree > 0 as live."),
            ("tldrgraph/deadcode.py", "_classify_unconnected_node", "Evaluates zero-inbound nodes against entry points, re-export barrels, and coverage floor."),
            ("tldrgraph/deadcode.py", "entry_point_reason", "Inspects file patterns for framework conventions (NestJS, Next.js, Prisma, Docker, CI, tests)."),
        ],
    },
    {
        "id": "flow_dynamic_layer_proposal",
        "title": "Dynamic Layer Architecture Discovery & Proposal",
        "category": "Architecture & Ingestion",
        "summary": "Gathers repository evidence including framework markers, directory sample trees, detected archetypes, and extracted symbols to propose a tailored architectural layer set with matching predicates.",
        "steps": [
            ("tldrgraph/propose_layers.py", "propose_layers", "Main entry point to gather repository evidence and synthesize architectural layer taxonomy."),
            ("tldrgraph/layer_evidence.py", "collect_layer_evidence", "Samples repository directory clusters, framework markers, and extracted symbol names."),
            ("tldrgraph/layer_evidence.py", "detect_repository_archetype", "Classifies project architecture (CLI tool, fullstack, backend API, library, monorepo)."),
            ("tldrgraph/layer_config.py", "validate_layer_config", "Validates unique IDs, sequential integer orders, rule objects, and designated utility catch-all."),
            ("tldrgraph/layer_config.py", "save_layer_config", "Writes validated configuration to .tldrgraph/layers.config.yaml and updates active LayerRegistry."),
        ],
    },
    {
        "id": "flow_llm_agent_enrichment_queue",
        "title": "LLM Enrichment Batch Queue & Audit Flow",
        "category": "Enrichment & Hash Gate",
        "summary": "Prioritizes un-enriched architectural bottleneck symbols by cross-layer and hub degree, generates batched enrichment requests with source locations, and applies agent/LLM responses.",
        "steps": [
            ("tldrgraph/cli_enrichment.py", "enrichment_candidates", "Filters out utility and non-code nodes, ranking candidates by cross_layer_degree and degree."),
            ("tldrgraph/cli_enrichment.py", "build_enrichment_batch", "Constructs batch request payload with source file paths, line locations, and rank indices."),
            ("tldrgraph/cli_enrichment.py", "apply_enrichment_items", "Updates node intents, parameter schemas, layer overrides, and degrees in the graph."),
            ("tldrgraph/call_resolver.py", "resolve_call_target", "Resolves downstream calls against candidate symbols or vector similarity, adding cross_layer_link edges."),
            ("tldrgraph/hash_gate.py", "update_node", "Persists enriched summary, intent, and fields into SQLite node cache."),
            ("tldrgraph/cli_enrichment.py", "append_enrichment_audit", "Appends timestamped log of applied nodes, fields, bridges, and unresolved targets to enrichment_audit.log."),
        ],
    },
    {
        "id": "flow_agent_rules_installer",
        "title": "Agent Slash Commands & Rule Installer",
        "category": "CLI & Agent Surface",
        "summary": "Installs TLDRGraph slash commands, skill files, and architectural execution rules across all major AI coding agent environments (Claude Code, Cursor, Windsurf, Antigravity).",
        "steps": [
            ("tldrgraph/installer.py", "install_agent_rules", "Scans repository for .claude, .cursor, .windsurf, .gemini, and .agents directories."),
            ("tldrgraph/installer.py", "ensure_gitignore", "Upserts the managed # BEGIN TLDRGRAPH block in .gitignore, un-ignoring AGENT_CONTRACT.md."),
            ("tldrgraph/installer_contract.py", "contract_text", "Generates repository-specific AGENT_CONTRACT.md incorporating active architectural layers."),
            ("tldrgraph/agent_commands.py", "install_agent_commands", "Generates /tldrgraph-init command markdown and SKILL.md for detected agent environments."),
            ("tldrgraph/agent_commands.py", "remove_superseded", "Deletes superseded command files (/tldrgraph-scan, /tldrgraph-layers, /tldrgraph-enrich)."),
        ],
    },
]

