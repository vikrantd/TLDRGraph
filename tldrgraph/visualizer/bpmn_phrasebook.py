"""
The phrases shipped with TLDRGraph for its own workflows.

These are the defaults: a repository that runs ``tldrgraph bpmn-enrich`` builds
its own store in ``.tldrgraph/bpmn_phrases.yaml``, and those phrases take
precedence over anything here.
"""

from __future__ import annotations

from typing import Dict, Tuple


# --------------------------------------------------------------------------
# Workflow-level phrasing: the opening event, the closing event, and the title
# of each numbered step.
#   (workflow_id, step_number, key) -> phrase
# Step 0 carries the workflow's own start/finish events.
# --------------------------------------------------------------------------
STEP_PHRASES: Dict[Tuple[str, int, str], str] = {
    # flow_cli_init_pipeline
    ("flow_cli_init_pipeline", 0, "start"): "You run TLDRGraph on a repository",
    ("flow_cli_init_pipeline", 0, "finish"): "The repository is mapped and ready to explore",
    ("flow_cli_init_pipeline", 1, "title"): "Start a first-time setup",
    ("flow_cli_init_pipeline", 2, "title"): "Give the coding agent its house rules",
    ("flow_cli_init_pipeline", 3, "title"): "Read the codebase and build the map",
    ("flow_cli_init_pipeline", 4, "title"): "Work out the architecture layers",
    ("flow_cli_init_pipeline", 5, "title"): "Save the agreed layer plan",
    ("flow_cli_init_pipeline", 6, "title"): "Fingerprint each symbol to spot changes",
    ("flow_cli_init_pipeline", 7, "title"): "Save the finished map",
    ("flow_cli_init_pipeline", 8, "title"): "Build the interactive picture",
    # flow_hybrid_semantic_search_query
    ("flow_hybrid_semantic_search_query", 0, "start"): "You ask a question in plain language",
    ("flow_hybrid_semantic_search_query", 0, "finish"): "You get an answer with the code path",
    ("flow_hybrid_semantic_search_query", 1, "title"): "Take the question and the budget",
    ("flow_hybrid_semantic_search_query", 2, "title"): "Plan the search",
    ("flow_hybrid_semantic_search_query", 3, "title"): "Turn the question into numbers the search understands",
    ("flow_hybrid_semantic_search_query", 4, "title"): "Find symbols that mean the same thing",
    ("flow_hybrid_semantic_search_query", 5, "title"): "Also match the exact words used",
    ("flow_hybrid_semantic_search_query", 6, "title"): "Follow the path through the code",
    ("flow_hybrid_semantic_search_query", 7, "title"): "Write the answer as a table",
    # flow_cross_layer_trace_engine
    ("flow_cross_layer_trace_engine", 0, "start"): "You ask how two things connect",
    ("flow_cross_layer_trace_engine", 0, "finish"): "You see the exact path between the two",
    ("flow_cross_layer_trace_engine", 1, "title"): "Take the two symbol names",
    ("flow_cross_layer_trace_engine", 2, "title"): "Work out which symbols you meant",
    ("flow_cross_layer_trace_engine", 3, "title"): "Find the shortest route between them",
    ("flow_cross_layer_trace_engine", 4, "title"): "Match calls that cross a boundary",
    ("flow_cross_layer_trace_engine", 5, "title"): "Write out the route step by step",
    # flow_ast_route_seam_extraction
    ("flow_ast_route_seam_extraction", 0, "start"): "A scan of the repository begins",
    ("flow_ast_route_seam_extraction", 0, "finish"): "The seams between front end, API and database are mapped",
    ("flow_ast_route_seam_extraction", 1, "title"): "Read the code into a graph",
    ("flow_ast_route_seam_extraction", 2, "title"): "Find where the front end calls an API",
    ("flow_ast_route_seam_extraction", 3, "title"): "Find the API endpoints on the server",
    ("flow_ast_route_seam_extraction", 4, "title"): "Find the database tables",
    ("flow_ast_route_seam_extraction", 5, "title"): "Join the front end, API and database together",
    ("flow_ast_route_seam_extraction", 6, "title"): "Give everything a readable name",
    # flow_incremental_hash_gate_indexing
    ("flow_incremental_hash_gate_indexing", 0, "start"): "A repeat scan begins",
    ("flow_incremental_hash_gate_indexing", 0, "finish"): "Only the changed parts cost anything",
    ("flow_incremental_hash_gate_indexing", 1, "title"): "Take a fingerprint of every file",
    ("flow_incremental_hash_gate_indexing", 2, "title"): "Fingerprint each symbol too",
    ("flow_incremental_hash_gate_indexing", 3, "title"): "Ask the cache what has changed",
    ("flow_incremental_hash_gate_indexing", 4, "title"): "Reuse everything that is unchanged",
    ("flow_incremental_hash_gate_indexing", 5, "title"): "Re-index what did change",
    ("flow_incremental_hash_gate_indexing", 6, "title"): "Refresh the meaning vectors",
    ("flow_incremental_hash_gate_indexing", 7, "title"): "Save the updated map",
    # flow_interactive_visualizer_assembly
    ("flow_interactive_visualizer_assembly", 0, "start"): "You ask to see the picture",
    ("flow_interactive_visualizer_assembly", 0, "finish"): "The picture is open in front of you",
    ("flow_interactive_visualizer_assembly", 1, "title"): "Take the display options",
    ("flow_interactive_visualizer_assembly", 2, "title"): "Assemble the page",
    ("flow_interactive_visualizer_assembly", 3, "title"): "Pick colours for each layer",
    ("flow_interactive_visualizer_assembly", 4, "title"): "Gather the modules, symbols and flows",
    ("flow_interactive_visualizer_assembly", 5, "title"): "Find where each symbol lives in its file",
    ("flow_interactive_visualizer_assembly", 6, "title"): "Work out the end-to-end journeys",
    ("flow_interactive_visualizer_assembly", 7, "title"): "Write one self-contained page",
    ("flow_interactive_visualizer_assembly", 8, "title"): "Open it in your browser",
    # flow_dead_code_reachability_detection
    ("flow_dead_code_reachability_detection", 0, "start"): "You ask what code may be unused",
    ("flow_dead_code_reachability_detection", 0, "finish"): "You get review candidates, never deletions",
    ("flow_dead_code_reachability_detection", 1, "title"): "Take the filters you chose",
    ("flow_dead_code_reachability_detection", 2, "title"): "Load the map of the codebase",
    ("flow_dead_code_reachability_detection", 3, "title"): "Check how much of the map is trustworthy",
    ("flow_dead_code_reachability_detection", 4, "title"): "Sort every symbol by who calls it",
    ("flow_dead_code_reachability_detection", 5, "title"): "Look closer at anything nothing calls",
    ("flow_dead_code_reachability_detection", 6, "title"): "Excuse the things frameworks call for you",
    # flow_dynamic_layer_proposal
    ("flow_dynamic_layer_proposal", 0, "start"): "The tool needs to learn this codebase's shape",
    ("flow_dynamic_layer_proposal", 0, "finish"): "The codebase has an agreed architecture",
    ("flow_dynamic_layer_proposal", 1, "title"): "Gather the evidence",
    ("flow_dynamic_layer_proposal", 2, "title"): "Sample folders, frameworks and names",
    ("flow_dynamic_layer_proposal", 3, "title"): "Decide what kind of project this is",
    ("flow_dynamic_layer_proposal", 4, "title"): "Check the proposed layers make sense",
    ("flow_dynamic_layer_proposal", 5, "title"): "Adopt the layers",
    # flow_llm_agent_enrichment_queue
    ("flow_llm_agent_enrichment_queue", 0, "start"): "Parts of the map still lack meaning",
    ("flow_llm_agent_enrichment_queue", 0, "finish"): "The map now explains itself",
    ("flow_llm_agent_enrichment_queue", 1, "title"): "Pick the symbols worth explaining",
    ("flow_llm_agent_enrichment_queue", 2, "title"): "Write a work order for the coding agent",
    ("flow_llm_agent_enrichment_queue", 3, "title"): "Fold the agent's answers back in",
    ("flow_llm_agent_enrichment_queue", 4, "title"): "Link the calls the agent described",
    ("flow_llm_agent_enrichment_queue", 5, "title"): "Remember the result so it is not asked twice",
    ("flow_llm_agent_enrichment_queue", 6, "title"): "Log what changed and why",
    # flow_agent_rules_installer
    ("flow_agent_rules_installer", 0, "start"): "You add TLDRGraph to a repository",
    ("flow_agent_rules_installer", 0, "finish"): "Every coding agent here knows the rules",
    ("flow_agent_rules_installer", 1, "title"): "Look for the agent tools in use",
    ("flow_agent_rules_installer", 2, "title"): "Keep generated files out of version control",
    ("flow_agent_rules_installer", 3, "title"): "Write the house rules for this repo",
    ("flow_agent_rules_installer", 4, "title"): "Add the slash commands each agent needs",
    ("flow_agent_rules_installer", 5, "title"): "Clear out commands from older versions",
}

# --------------------------------------------------------------------------
# Per-shape phrasing for decisions, loops and error paths.
#   "file:line:kind" -> {"say": <phrase>, "when": <code the phrase was written for>,
#                        "yes": <label for the true branch>, "no": <label for the false branch>}
# "when" is compared against the current code; a mismatch means the source moved
# on and the phrase is ignored.
# --------------------------------------------------------------------------
ELEMENT_PHRASES: Dict[str, Dict[str, str]] = {
    "tldrgraph/cli_commands.py:264:gateway": {
        "say": "Did the scan find anything to report?",
        "when": "not annotated",
        "yes": "Nothing found", "no": "Found some",
    },
    "tldrgraph/cli_commands.py:266:gateway": {
        "say": "Did you ask for raw JSON?",
        "when": "as_json",
        "yes": "JSON", "no": "Readable table",
    },
    "tldrgraph/cli_commands.py:274:gateway": {
        "say": "Did you cap how many to show?",
        "when": "limit and limit > 0",
        "yes": "Capped", "no": "Show all",
    },
    "tldrgraph/cli_commands.py:286:gateway": {
        "say": "Did you ask for raw JSON?",
        "when": "as_json",
        "yes": "JSON", "no": "Readable table",
    },
    "tldrgraph/deadcode.py:53:loop": {
        "say": "Look at every symbol in the codebase",
        "when": "graph.nodes(data=True)",
    },
    "tldrgraph/deadcode.py:56:gateway": {
        "say": "Is this just a utility we do not judge?",
        "when": "lid in excluded or (not lid and (layer_name in {'General / Utility', 'Utility'} or 'utility' in layer_name.lower()))",
        "yes": "Skip it", "no": "Judge it",
    },
    "tldrgraph/deadcode.py:58:gateway": {
        "say": "Is this prose or config rather than code?",
        "when": "str(data.get('type') or '').lower() in NON_CODE_NODE_TYPES",
        "yes": "Not code", "no": "Real code",
    },
    "tldrgraph/deadcode.py:62:gateway": {
        "say": "Has a human or agent explained this symbol?",
        "when": "source and source != HEURISTIC_ENRICHMENT_SOURCE",
        "yes": "Explained", "no": "Still a guess",
    },
    "tldrgraph/deadcode.py:228:error": {
        "say": "If the coverage figure is unusable",
        "when": "(TypeError, ValueError)",
    },
    "tldrgraph/deadcode.py:233:loop": {
        "say": "Check every symbol one by one",
        "when": "graph.nodes(data=True)",
    },
    "tldrgraph/deadcode.py:234:gateway": {
        "say": "Does anything else in the code call this?",
        "when": "graph.in_degree(node_id) > 0",
        "yes": "Yes, it is used", "no": "Nothing calls it",
    },
    "tldrgraph/deadcode.py:185:gateway": {
        "say": "Is this prose or config rather than code?",
        "when": "node_type in NON_CODE_NODE_TYPES",
        "yes": "Not code", "no": "Real code",
    },
    "tldrgraph/deadcode.py:191:gateway": {
        "say": "Is this something a framework calls for you?",
        "when": "reason",
        "yes": "Framework entry point", "no": "No framework reason",
    },
    "tldrgraph/deadcode.py:196:gateway": {
        "say": "Is it re-exported as part of a public API?",
        "when": "_has_reexport_edge(graph, node_id)",
        "yes": "Public API", "no": "Not re-exported",
    },
    "tldrgraph/deadcode.py:202:gateway": {
        "say": "Does it come from outside this repository?",
        "when": "file_path and _looks_external(file_path, root_dir)",
        "yes": "External package", "no": "Ours",
    },
    "tldrgraph/deadcode.py:207:gateway": {
        "say": "Do we even know which file it lives in?",
        "when": "not file_path",
        "yes": "No file recorded", "no": "File known",
    },
    "tldrgraph/deadcode.py:212:gateway": {
        "say": "Does that file path point at a real source file?",
        "when": "not os.path.splitext(os.path.basename(file_path))[1]",
        "yes": "Not a real file", "no": "Real file",
    },
    "tldrgraph/deadcode.py:217:gateway": {
        "say": "Is enough of the map trustworthy to make a call?",
        "when": "coverage >= CANDIDATE_COVERAGE_FLOOR",
        "yes": "Trustworthy - flag for review", "no": "Too thin - say unreviewed",
    },
    "tldrgraph/deadcode.py:133:gateway": {
        "say": "Is there a file path to judge?",
        "when": "not path",
        "yes": "No path", "no": "Path known",
    },
    "tldrgraph/installer.py:192:gateway": {
        "say": "Were any stale command files found?",
        "when": "removed",
        "yes": "Some to remove", "no": "Nothing stale",
    },
    "tldrgraph/installer.py:131:gateway": {
        "say": "Does this repo already have a .gitignore?",
        "when": "os.path.isfile(path)",
        "yes": "It exists", "no": "Create one",
    },
    "tldrgraph/installer.py:135:error": {
        "say": "If the .gitignore cannot be read",
        "when": "OSError",
    },
    "tldrgraph/installer.py:142:gateway": {
        "say": "Is the ignore block already correct?",
        "when": "existing == updated",
        "yes": "Leave it alone", "no": "Needs updating",
    },
    "tldrgraph/installer.py:148:error": {
        "say": "If the .gitignore cannot be written",
        "when": "OSError",
    },
    "tldrgraph/installer_contract.py:163:loop": {
        "say": "Try each place the rules might already live",
        "when": "_CONTRACT_CANDIDATES",
    },
    "tldrgraph/installer_contract.py:164:gateway": {
        "say": "Are there existing rules at this location?",
        "when": "os.path.isfile(candidate)",
        "yes": "Found", "no": "Not here",
    },
    "tldrgraph/installer_contract.py:168:gateway": {
        "say": "Do those rules actually have content?",
        "when": "text.strip()",
        "yes": "Use them", "no": "Empty, keep looking",
    },
    "tldrgraph/installer_contract.py:170:error": {
        "say": "If the existing rules cannot be read",
        "when": "OSError",
    },
    "tldrgraph/agent_commands.py:376:gateway": {
        "say": "Does the repo already have an AGENTS file?",
        "when": "os.path.isfile(agents_md)",
        "yes": "Update it", "no": "Skip it",
    },
    "tldrgraph/agent_commands.py:380:error": {
        "say": "If the AGENTS file cannot be written",
        "when": "OSError",
    },
    "tldrgraph/agent_commands.py:388:loop": {
        "say": "For each coding agent this repo uses",
        "when": "active_targets(root, all_agents=all_agents)",
    },
    "tldrgraph/agent_commands.py:389:gateway": {
        "say": "Does this agent support slash commands?",
        "when": "target.command_path",
        "yes": "Install the command", "no": "No commands",
    },
    "tldrgraph/agent_commands.py:393:gateway": {
        "say": "Does this agent read an instructions file?",
        "when": "target.instructions_path",
        "yes": "Write the instructions", "no": "None needed",
    },
    "tldrgraph/agent_commands.py:339:loop": {
        "say": "For each command an older version installed",
        "when": "SUPERSEDED",
    },
    "tldrgraph/agent_commands.py:341:gateway": {
        "say": "Is that old command still on disk?",
        "when": "os.path.isfile(path)",
        "yes": "Delete it", "no": "Already gone",
    },
    "tldrgraph/agent_commands.py:345:error": {
        "say": "If the old command cannot be deleted",
        "when": "OSError",
    },
    "tldrgraph/agent_commands.py:350:gateway": {
        "say": "Did that leave an empty folder behind?",
        "when": "parent != root and (not os.listdir(parent))",
        "yes": "Tidy it away", "no": "Folder still in use",
    },
    "tldrgraph/agent_commands.py:352:error": {
        "say": "If the empty folder cannot be removed",
        "when": "OSError",
    },
}

# --------------------------------------------------------------------------
# Business names for graph symbols, shared by every workflow that touches them.
#   node_id -> phrase
# --------------------------------------------------------------------------
NODE_PHRASES: Dict[str, str] = {
    "tldrgraph_cli_init": "Start a first-time setup",
    "tldrgraph_installer_install_agent_rules": "Give the coding agent its house rules",
    "tldrgraph_installer_ensure_gitignore": "Keep generated files out of version control",
    "tldrgraph_installer_contract_text": "Write the house rules for this repo",
    "tldrgraph_installer_upsert_block": "Update our block, leave the rest alone",
    "tldrgraph_agent_commands_install_agent_commands": "Add the slash commands each agent needs",
    "tldrgraph_agent_commands_remove_superseded": "Clear out commands from older versions",
    "tldrgraph_agent_commands_active_targets": "Work out which coding agents this repo uses",
    "tldrgraph_graph_loader_load_or_extract": "Read the codebase and build the map",
    "tldrgraph_propose_layers_propose_layers": "Work out the architecture layers",
    "tldrgraph_propose_layers_collect_layer_evidence": "Sample folders, frameworks and names",
    "tldrgraph_propose_layers_detect_repository_archetype": "Decide what kind of project this is",
    "tldrgraph_layer_config_save_layer_config": "Save the agreed layer plan",
    "tldrgraph_layer_config_validate_layer_config": "Check the proposed layers make sense",
    "tldrgraph_hash_gate_hashgate_check_node": "Ask the cache whether this changed",
    "tldrgraph_hash_gate_hashgate_update_node": "Remember this result for next time",
    "tldrgraph_snapshot_sync_save_graph_snapshot": "Save the finished map",
    "tldrgraph_snapshot_sync_load_file_hashes": "Take a fingerprint of every file",
    "tldrgraph_snapshot_sync_node_signature": "Fingerprint this symbol",
    "tldrgraph_visualizer_render_generate_visualizer_html": "Build the interactive picture",
    "tldrgraph_visualizer_render_render_html": "Write one self-contained page",
    "tldrgraph_visualizer_data_prepare_visualizer_data": "Gather the modules, symbols and flows",
    "tldrgraph_visualizer_palette_build_layers_config": "Pick colours for each layer",
    "tldrgraph_visualizer_source_locate_symbol": "Find where this symbol lives in its file",
    "tldrgraph_cli_commands_serve_visualizer": "Open the picture in your browser",
    "tldrgraph_deadcode_classify_dead_code": "Sort every symbol by who calls it",
    "tldrgraph_deadcode_compute_enrichment_coverage": "Check how much of the map is trustworthy",
    "tldrgraph_deadcode_entry_point_reason": "Excuse the things frameworks call for you",
    "tldrgraph_dense_embedder_denseembedder_encode": "Turn text into numbers the search understands",
    "tldrgraph_vector_store_localvectorstore_search": "Find symbols that mean the same thing",
    "tldrgraph_vector_store_localvectorstore_add_documents": "Re-index what changed",
    "tldrgraph_vector_tfidf_query_vector": "Match the exact words used",
    "tldrgraph_flow_traversal_bridge_aware_walk": "Follow the path through the code",
    "tldrgraph_flow_traversal_resolve_node_id": "Work out which symbol you meant",
    "tldrgraph_flow_engine_trace_path": "Find the shortest route between them",
    "tldrgraph_flow_engine_render_markdown_table": "Write the answer as a table",
    "tldrgraph_call_resolver_resolve_call_target": "Match a call that crosses a boundary",
    "tldrgraph_cli_enrichment_enrichment_candidates": "Pick the symbols worth explaining",
    "tldrgraph_cli_enrichment_build_enrichment_batch": "Write a work order for the coding agent",
    "tldrgraph_cli_enrichment_apply_enrichment_items": "Fold the agent's answers back in",
    "tldrgraph_cli_enrichment_append_enrichment_audit": "Log what changed and why",
    "tldrgraph_labels_build_display_labels": "Give everything a readable name",
    "tldrgraph_node_registrar_register_nodes": "Join the front end, API and database together",
    "tldrgraph_extractors_prisma_extract_prisma_models": "Find the database tables",
}
