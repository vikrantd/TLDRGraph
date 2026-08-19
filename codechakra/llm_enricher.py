"""
LLM Enrichment & Cross-Layer Linker for CodeChakra.
Calls an LLM (Gemini, OpenAI, or a local Ollama) with dense batching to generate
natural language summaries, form field mappings, and cross-layer connection bridges.

NOTE: the primary enrichment path is the host coding agent (Claude Code, Cursor,
Antigravity) via `codechakra queue-enrichment` / `apply-enrichment` -- it can read
the actual source files, which this API path cannot (no snippet is sent). These
providers are a convenience fallback.
"""

import os
import json
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional

from .layers import (
    LAYER_API,
    LAYER_DATA,
    LAYER_DEVOPS,
    LAYER_SERVICE,
    LAYER_UI,
    get_registry,
    layer_id_of,
)

_PROMPT_TEMPLATE = """You are CodeChakra, an expert software architecture intelligence engine.
Analyze the following batch of code symbols across {layer_count} architectural layers:
{layer_lines}

For each symbol provided, return a JSON object with:
1. "id": exact node ID provided
2. "intent": A crisp, 1-2 sentence plain-English explanation of what this symbol does
3. "fields": Array of key parameters, fields, configuration keys, or schema columns handled
4. "calls": Array of downstream services, modules, APIs, or data stores this symbol connects to

Return strictly a valid JSON array of objects with no markdown wrapping or conversational text."""

PROPOSE_LAYERS_SYSTEM_PROMPT = """You are CodeChakra, an expert software architecture intelligence engine.
Analyze the provided repository evidence (frameworks, file clusters, entry points, dependencies) and design an optimal multi-layer architectural breakdown (typically 3 to 6 layers) customized specifically for this codebase.

Examples of customized architectures:
- CLI Tools: CLI Interface & Commands -> Core Processing Engine -> State/Storage -> External Integrations -> Utility
- Backend APIs: API Gateway/Endpoints -> Domain Services -> Persistence/Repositories -> Background/Workers -> Utility
- Libraries: Public API -> Core Processing -> Data Structures/Models -> Adapters -> Utility
- Fullstack: UI Presentation -> API Gateway -> Domain Logic -> Data/DB -> Async/Tasks -> DevOps -> Utility

Output format: Return strictly a valid JSON object matching this schema:
{
  "utility_id": "unique_string_id_of_the_fallback_utility_layer",
  "layers": [
    {
      "id": "short_unique_machine_id",
      "name": "Human Friendly Display Name",
      "order": 1,
      "description": "Crisp 1-sentence description of what sits in this layer",
      "rules": [
        {
          "file_contains": ["substring1", "substring2"],
          "exclude_file": ["optional_exclude"],
          "label_contains": ["optional_label_substring"]
        }
      ]
    }
  ]
}

Ensure:
1. Every layer has a unique `id`, unique `name`, and sequential integer `order` (1 to N).
2. Exactly one layer has `id` matching `utility_id` (the fallback catch-all bucket) with empty rules `[]`.
3. Rules should accurately match the files sampled in the evidence.
Return only valid JSON without markdown wrapping."""


def build_system_prompt() -> str:
    """
    The enrichment system prompt, with the layer roster rendered from the active
    registry (display name + one-line description). Nothing here is hardcoded,
    so a swapped layer set describes itself correctly.

    The utility bucket is deliberately omitted: it is a catch-all, not an
    architectural layer worth describing to the model.
    """
    registry = get_registry()
    layers = [layer for layer in registry if layer.id != registry.utility_id]
    return _PROMPT_TEMPLATE.format(
        layer_count=len(layers),
        layer_lines="\n".join(f"- {layer.name} ({layer.description})" for layer in layers),
    )


#: Snapshot of the prompt for the built-in default layer set. Retained for
#: backward compatibility; the call sites below rebuild it per request so a
#: swapped registry is honoured.
ENRICHMENT_PROMPT_SYSTEM = build_system_prompt()

#: Intent templates keyed by stable layer id. A layer with no template (async,
#: utility, anything a future registry adds) falls through to the generic one --
#: which is exactly what the old substring chain did.
_INTENT_TEMPLATES = {
    LAYER_UI: "User Interface component ({label}) handling interactions and triggering workflows.",
    LAYER_API: "REST API Controller endpoint ({label}) routing requests, validating sessions, and invoking domain services.",
    LAYER_SERVICE: "Domain Service logic ({label}) executing business calculations, rules, and state transitions.",
    LAYER_DATA: "Database entity/schema model ({label}) persisting application state.",
    LAYER_DEVOPS: "DevOps deployment and orchestration configuration ({label}).",
}

_GENERIC_INTENT = "Core module symbol ({label}) at {file_path}."


class LLMEnricher:
    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = provider or os.getenv("LLM_PROVIDER")
        self.api_key = (
            os.getenv("GEMINI_API_KEY") or
            os.getenv("OPENAI_API_KEY") or
            os.getenv("ANTHROPIC_API_KEY")
        )
        self.model = model

    def enrich_batch(self, nodes_batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Enriches a batch of up to 10 nodes using an LLM.
        Falls back gracefully if no API key is set.
        """
        if not nodes_batch:
            return []

        # Prepare compact payload
        prompt_input = []
        for n in nodes_batch:
            prompt_input.append({
                "id": n["id"],
                "label": n["label"],
                "layer": n.get("layer", ""),
                "file": n.get("file", ""),
                "code_snippet": n.get("snippet", "")[:400]
            })

        user_content = json.dumps(prompt_input, indent=2)

        # 1. Try Gemini if GEMINI_API_KEY is available
        if os.getenv("GEMINI_API_KEY"):
            return self._call_gemini(user_content, nodes_batch)
        
        # 2. Try OpenAI if OPENAI_API_KEY is available
        if os.getenv("OPENAI_API_KEY"):
            return self._call_openai(user_content, nodes_batch)

        # 3. Try Local Ollama if available on localhost:11434
        ollama_res = self._call_ollama(user_content, nodes_batch)
        if ollama_res:
            return ollama_res

        # 4. Fallback Heuristic Enrichment (Zero-Token offline generator)
        return self._heuristic_enrichment(nodes_batch)

    def _call_gemini(self, user_content: str, fallback_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        api_key = os.getenv("GEMINI_API_KEY")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": build_system_prompt()}]},
            "contents": [{"parts": [{"text": user_content}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
        except Exception:
            return self._heuristic_enrichment(fallback_nodes)

    def _call_openai(self, user_content: str, fallback_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        api_key = os.getenv("OPENAI_API_KEY")
        url = "https://api.openai.com/v1/chat/completions"
        # response_format=json_object cannot return a bare array, so ask for the
        # array under a known key -- the parser below already unwraps it.
        payload = {
            "model": self.model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {"role": "system", "content": 'Return a JSON object of the form {"symbols": [ ... ]} '
                                              'whose "symbols" value is the array described above.'},
                {"role": "user", "content": user_content}
            ],
            "response_format": {"type": "json_object"}
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["choices"][0]["message"]["content"]
                parsed = json.loads(text)
                return parsed if isinstance(parsed, list) else parsed.get("nodes", parsed.get("symbols", []))
        except Exception:
            return self._heuristic_enrichment(fallback_nodes)

    def _call_ollama(self, user_content: str, fallback_nodes: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        url = f"{host}/api/generate"
        payload = {
            "model": self.model or "llama3.2:latest",
            "system": build_system_prompt(),
            "prompt": user_content,
            "stream": False,
            "format": "json"
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data.get("response", "")
                return json.loads(text)
        except Exception:
            return None

    def propose_layers(self, evidence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Calls the configured LLM to synthesize a tailored multi-layer architectural definition.
        Returns the parsed dictionary with 'utility_id' and 'layers' or None.
        """
        user_content = json.dumps(evidence, indent=2)

        # 1. Try Gemini
        if os.getenv("GEMINI_API_KEY"):
            api_key = os.getenv("GEMINI_API_KEY")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
            payload = {
                "system_instruction": {"parts": [{"text": PROPOSE_LAYERS_SYSTEM_PROMPT}]},
                "contents": [{"parts": [{"text": user_content}]}],
                "generationConfig": {"response_mime_type": "application/json"}
            }
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text)
                    if isinstance(parsed, dict) and "layers" in parsed:
                        return parsed
            except Exception:
                pass

        # 2. Try OpenAI
        if os.getenv("OPENAI_API_KEY"):
            api_key = os.getenv("OPENAI_API_KEY")
            url = "https://api.openai.com/v1/chat/completions"
            payload = {
                "model": self.model or "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": PROPOSE_LAYERS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ],
                "response_format": {"type": "json_object"}
            }
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}"
                    }
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    text = data["choices"][0]["message"]["content"]
                    parsed = json.loads(text)
                    if isinstance(parsed, dict) and "layers" in parsed:
                        return parsed
            except Exception:
                pass

        # 3. Try Local Ollama
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        url = f"{host}/api/generate"
        payload = {
            "model": self.model or "llama3.2:latest",
            "system": PROPOSE_LAYERS_SYSTEM_PROMPT,
            "prompt": user_content,
            "stream": False,
            "format": "json"
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data.get("response", "")
                parsed = json.loads(text)
                if isinstance(parsed, dict) and "layers" in parsed:
                    return parsed
        except Exception:
            pass

        return None

    def _heuristic_enrichment(self, nodes_batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Smart static heuristic enricher when no LLM API is configured.
        Extracts intent, fields, and layer bridges deterministically from symbols.
        """
        results = []
        for n in nodes_batch:
            label = n.get("label", "")
            layer_id = layer_id_of(n)
            file_path = n.get("file", "")

            fields = []
            calls = []

            template = _INTENT_TEMPLATES.get(layer_id, _GENERIC_INTENT)
            intent = template.format(label=label, file_path=file_path)

            results.append({
                "id": n["id"],
                "intent": intent,
                "fields": fields,
                "calls": calls,
                "source": "heuristic"
            })
        return results
