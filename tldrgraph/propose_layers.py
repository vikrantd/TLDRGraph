"""
Propose Layers: Dynamic Multi-Layer Discovery & Configuration Generator.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
import yaml

from . import agent_runner, paths
from .layer_config import (
    CONFIG_FILENAME_YAML,
    config_path,
    load_layer_config,
    save_layer_config,
    validate_layer_config,
)
from .layer_evidence import (
    ARCHETYPE_BACKEND,
    ARCHETYPE_CLI,
    ARCHETYPE_DATA_ML,
    ARCHETYPE_FULLSTACK,
    ARCHETYPE_GENERIC,
    ARCHETYPE_LIBRARY,
    collect_framework_markers,
    detect_repository_archetype,
    extracted_symbol_evidence,
    sample_repo_files,
)
from .layers import LayerRegistry, get_registry

REQUEST_FILENAME = "propose_layers_request.json"
RESPONSE_FILENAME = "propose_layers_response.json"
NEEDS_LAYERS = "needs_layers"

_sample_repo_files = sample_repo_files
_collect_framework_markers = collect_framework_markers
_extracted_symbol_evidence = extracted_symbol_evidence

LAYER_SET_IDEAS = (
    "Full-stack web (e.g. Next.js + FastAPI): UI Pages & Components -> API Controllers & Routers -> Service / Domain Logic -> Data & Database Models -> Utility & Cross-Cutting",
    "CLI tool: Entry & Commands -> Orchestration -> Core Engine / Parsers -> Storage & Config -> Helpers",
    "Backend service: HTTP & Event Handlers -> Business Services -> Integrations & External Clients -> Persistence & Entities -> Shared Utilities",
    "Library / SDK: Public Entry Points -> Core Implementations -> Internal Adapters & Transports -> Common Primitives",
    "Data / ML: Ingestion & Pipelines -> Feature Processing -> Model Execution -> Storage & Outputs -> Shared Utilities",
)

AGENT_LAYERS_PROMPT = """You are designing the architectural layer map for the repository you currently have open.

Do this before answering:
1. Read the entry points and a representative sample of real source files across
   the main directories. The evidence bundle below lists candidates; it is a
   starting point, NOT a substitute for opening the files.
2. Work out what this codebase actually does and how responsibility is split.
   Name the layers after THIS repository's concepts, not a generic template.
   For the shape of an answer only:
{ideas}
3. Derive matching rules from real paths and real symbol names you saw.

Then return ONLY a JSON object, no prose and no markdown fence, of this shape:

{{
  "utility_id": "<id of the catch-all layer>",
  "layers": [
    {{
      "id": "short_machine_id",
      "name": "Layer 1: Human Friendly Name",
      "order": 1,
      "description": "One sentence on what lives here",
      "rules": [
        {{"file_contains": ["substring", "another"], "exclude_file": ["optional"]}},
        {{"label_contains": ["SymbolNamePart"]}}
      ]
    }}
  ]
}}

Hard requirements:
- 3 to 6 layers plus exactly one catch-all utility layer.
- Every layer needs a unique `id`, a unique `name`, and a sequential integer
  `order` starting at 1.
- Exactly one layer's `id` equals `utility_id`, and that layer has `rules: []`.
- Supported rule keys: file_contains, exclude_file, path_regex, label_contains,
  exclude_label, label_suffix, label_prefix, type_is, id_prefix.
- Supported regex keys compile with Python re: file_regex, path_regex,
  label_regex, id_regex.
- Every rule in `rules` must match at least one real file or symbol in this repo.
- The catch-all layer MUST have `rules: []`. Every other layer MUST have at
  least one non-empty rule.
- JSON ONLY. No explanation before or after.

Repository root: {root}

Evidence:
{evidence}
"""


def collect_layer_evidence(root_dir: str = ".") -> Dict[str, Any]:
    root = os.path.abspath(root_dir)
    evidence: Dict[str, Any] = {
        "framework_markers": collect_framework_markers(root),
        "sampled_directory_clusters": sample_repo_files(root),
        "detected_archetype": detect_repository_archetype(root),
    }
    extracted = extracted_symbol_evidence(root)
    if extracted:
        evidence["extracted_symbols"] = extracted
    existing = config_path(root)
    if existing:
        evidence["active_layers"] = [layer.as_record() for layer in get_registry().ordered()]
        evidence["active_utility_id"] = get_registry().utility_id
    return evidence


def build_agent_layer_prompt(root: str, evidence: Dict[str, Any]) -> str:
    return AGENT_LAYERS_PROMPT.format(
        root=root,
        ideas="\n".join(f"     - {idea}" for idea in LAYER_SET_IDEAS),
        evidence=json.dumps(evidence, indent=2, default=str),
    )


def generate_propose_request(root_dir: str = ".") -> str:
    root = os.path.abspath(root_dir)
    state_dir = os.path.join(root, ".tldrgraph")
    os.makedirs(state_dir, exist_ok=True)
    req_path = os.path.join(state_dir, REQUEST_FILENAME)

    evidence = collect_layer_evidence(root)
    existing = config_path(root)
    if existing:
        evidence["active_layers"] = [layer.as_record() for layer in get_registry().ordered()]
        evidence["active_utility_id"] = get_registry().utility_id

    payload = {
        "schema": "codechakra/propose-layers-request@1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "response_file": os.path.join(".tldrgraph", RESPONSE_FILENAME),
        "config_target": os.path.join(".tldrgraph", CONFIG_FILENAME_YAML),
        "instructions": [
            "Analyze the repository evidence and propose a dynamic architectural layer set tailored to this codebase.",
            "Each layer must have: id (unique machine string), name (display string), order (1..N), description, and rules.",
            "Rules support: file_contains, exclude_file, path_regex, label_contains, exclude_label, label_ends_with, type_in, id_prefix.",
            "Must designate a utility_id matching one of the proposed layer ids (the catch-all bucket, with empty rules).",
            "Name the layers after THIS repository's concepts. TLDRGraph ships no layer templates and none will be applied for you.",
            "A rule that matches nothing is worse than no rule; a rule that matches everything collapses the map.",
            "See 'layer_set_ideas' below for the SHAPE of an answer. They are sketches from other codebases, not a menu -- none of them fits this repository.",
            f"Write the JSON or YAML response to .tldrgraph/{RESPONSE_FILENAME}, then run `tldrgraph init`.",
        ],
        "layer_set_ideas": list(LAYER_SET_IDEAS),
        "evidence": evidence,
    }
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return req_path


def propose_layers_with_agent(
    root_dir: str = ".",
    agent: Optional[Any] = None,
    model: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if agent is None:
        agent = agent_runner.find_agent_cli()
    if agent is None:
        status = agent_runner.agent_status()
        return None, str(status.get("detail") or status.get("reason") or "no agent available")

    root = os.path.abspath(root_dir)
    prompt = build_agent_layer_prompt(root, collect_layer_evidence(root))

    try:
        proposal = agent_runner.run_agent_json(agent, prompt, root, model=model)
    except agent_runner.AgentError as err:
        return None, str(err)

    if isinstance(proposal, dict) and "layers" not in proposal:
        for key in ("proposal", "layer_set", "config"):
            nested = proposal.get(key)
            if isinstance(nested, dict) and "layers" in nested:
                proposal = nested
                break

    if not isinstance(proposal, dict) or "layers" not in proposal:
        return None, f"{agent.display} did not return a layer set"

    try:
        validate_layer_config(proposal)
    except ValueError as err:
        return None, f"{agent.display} returned an invalid layer set: {err}"

    return proposal, agent.name


def propose_layers_with_llm(root_dir: str = ".", enricher: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    if enricher is None:
        try:
            from .llm_enricher import LLMEnricher
            enricher = LLMEnricher()
        except Exception:
            return None

    root = os.path.abspath(root_dir)
    evidence = collect_layer_evidence(root)
    try:
        proposal = enricher.propose_layers(evidence)
        if proposal and isinstance(proposal, dict):
            for key in ("proposal", "layer_set", "config"):
                nested = proposal.get(key)
                if isinstance(nested, dict) and "layers" in nested:
                    proposal = nested
                    break
            validate_layer_config(proposal)
            return proposal
    except Exception:
        pass
    return None


def _locate_proposal_response_file(root: str, response_path: Optional[str]) -> str:
    if response_path is not None:
        if not os.path.isfile(response_path):
            raise FileNotFoundError(f"Proposed layers response not found at {response_path}")
        return response_path
    for fn in (RESPONSE_FILENAME, "propose_layers_response.yaml"):
        cand = os.path.join(root, ".tldrgraph", fn)
        if os.path.isfile(cand):
            return cand
    raise FileNotFoundError(f"Proposed layers response not found in {os.path.join(root, '.tldrgraph')}")


def _parse_proposal_payload(response_path: str) -> Dict[str, Any]:
    with open(response_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        data = json.loads(content) if (response_path.endswith(".json") or content.startswith("{")) else yaml.safe_load(content)

    if isinstance(data, dict) and "layers" not in data:
        for key in ("proposal", "layer_set", "config"):
            if isinstance(data.get(key), dict) and "layers" in data[key]:
                data = data[key]
                break

    if not isinstance(data, dict) or "layers" not in data:
        raise ValueError("Invalid layer proposal format: missing root 'layers' list.")
    return data


def apply_proposed_layers(root_dir: str, response_path: Optional[str] = None) -> str:
    root = os.path.abspath(root_dir)
    resp_file = _locate_proposal_response_file(root, response_path)
    data = _parse_proposal_payload(resp_file)

    validate_layer_config(data)
    utility_id = str(data["utility_id"])
    reg = LayerRegistry.from_records(data["layers"], utility_id=utility_id)
    return save_layer_config(root, reg)


def auto_configure_layers(
    root_dir: str = ".",
    enricher: Optional[Any] = None,
    force: bool = False,
    use_llm: bool = True,
    use_agent: bool = False,
    agent: Optional[Any] = None,
    agent_model: Optional[str] = None,
    notes: Optional[List[str]] = None,
) -> Tuple[Optional[LayerRegistry], Optional[str], str]:
    root = os.path.abspath(root_dir)
    existing_path = config_path(root)
    log = notes if notes is not None else []

    if existing_path and not force:
        reg, _ = load_layer_config(root)
        return reg, existing_path, "existing_config"

    if use_agent:
        if agent is None:
            agent = agent_runner.find_agent_cli()
        proposal, detail = propose_layers_with_agent(root, agent=agent, model=agent_model)
        if proposal:
            registry = LayerRegistry.from_records(
                proposal["layers"], utility_id=str(proposal["utility_id"])
            )
            out_path = save_layer_config(root, registry)
            return registry, out_path, f"agent:{detail}"
        if detail:
            log.append(f"Agent CLI layer proposal unavailable: {detail}")

    if use_llm:
        proposal = propose_layers_with_llm(root, enricher=enricher)
        if proposal:
            utility_id = str(proposal["utility_id"])
            registry = LayerRegistry.from_records(proposal["layers"], utility_id=utility_id)
            out_path = save_layer_config(root, registry)
            return registry, out_path, "llm_synthesis"

    return None, None, NEEDS_LAYERS
