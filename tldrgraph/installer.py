"""
Agent Installer for TLDRGraph.

Writes the two things a repository needs to be agent-ready:

    <root>/.tldrgraph/AGENT_CONTRACT.md   -- the request/response schema
    <root>/.gitignore                     -- managed block for generated state

plus per-tool instruction and command files.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .agent_commands import install_agent_commands, remove_superseded
from .installer_contract import (
    CONTRACT_REL_PATH,
    _CONTRACT_CANDIDATES,
    _DEAD_CODE_NOTE,
    _LOOP,
    _RESPONSE_SCHEMA,
    _RULES_SHORT,
    contract_text,
    generate_layers_prose,
    make_agent_contract_fallback,
)
from .layer_config import load_layer_config

#: Delimiters for the managed region inside a user-owned CLAUDE.md.
CLAUDE_MD_BEGIN = "<!-- BEGIN TLDRGRAPH -->"
CLAUDE_MD_END = "<!-- END TLDRGRAPH -->"

#: Delimiters for the managed region inside a user-owned .gitignore.
GITIGNORE_BEGIN = "# BEGIN TLDRGRAPH"
GITIGNORE_END = "# END TLDRGRAPH"

#: Files inside .tldrgraph/ that are worth committing.
GITIGNORE_KEEP = ("AGENT_CONTRACT.md", "layers.config.yaml")

#: The managed .gitignore body.
GITIGNORE_BLOCK = "\n".join(
    [
        "# TLDRGraph analysis state. Generated artifacts are ignored; the agent",
        "# contract and layer map are committed so the whole team shares them.",
        ".tldrgraph/*",
    ]
    + [f"!.tldrgraph/{name}" for name in GITIGNORE_KEEP]
)

# Legacy module-level aliases for backwards compatibility.
_LAYERS = generate_layers_prose()
AGENT_CONTRACT_FALLBACK = make_agent_contract_fallback()


def _contract_text(registry=None) -> str:
    return contract_text(registry)


def _write_if_changed(path: str, content: str) -> bool:
    """Writes ``content`` to ``path`` unless it is already byte-identical. Returns True if written."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                if f.read() == content:
                    return False
        except OSError:
            pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def upsert_block(existing: Optional[str], body: str, begin: str, end: str) -> str:
    """Replaces the region between ``begin`` and ``end`` with ``body``."""
    block = f"{begin}\n{body.strip()}\n{end}\n"
    if not existing or not existing.strip():
        return block

    start = existing.find(begin)
    stop = existing.find(end)
    if start != -1 and stop != -1 and stop > start:
        head = existing[:start]
        tail = existing[stop + len(end):].lstrip("\n")
        return f"{head}{block}{tail}"

    separator = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return f"{existing}{separator}{block}"


def strip_block(existing: str, begin: str, end: str) -> str:
    """Returns ``existing`` with the managed region and its markers removed."""
    start = existing.find(begin)
    stop = existing.find(end)
    if start == -1 or stop == -1 or stop < start:
        return existing
    return (existing[:start].rstrip("\n") + "\n" + existing[stop + len(end):].lstrip("\n")).lstrip("\n")


def upsert_delimited_section(existing: Optional[str], section_body: str) -> str:
    """Replaces the TLDRGraph-delimited region with ``section_body``."""
    return upsert_block(existing, section_body, CLAUDE_MD_BEGIN, CLAUDE_MD_END)


def _neutralize_directory_ignores(text: str) -> str:
    """Comments out any unmanaged .tldrgraph directory ignores."""
    if not text:
        return text

    out: List[str] = []
    inside_block = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped == GITIGNORE_BEGIN:
            inside_block = True
        elif stripped == GITIGNORE_END:
            inside_block = False
        elif not inside_block and stripped.lstrip("/").rstrip("/") == ".tldrgraph" and not stripped.startswith("#"):
            out.append(f"# {raw}  # superseded by the TLDRGraph block below")
            continue
        out.append(raw)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def ensure_gitignore(root_dir: str = ".") -> Dict[str, str]:
    """Adds (or refreshes) TLDRGraph's managed block in .gitignore."""
    path = os.path.join(os.path.abspath(root_dir), ".gitignore")
    existing: Optional[str] = None
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError:
            existing = None

    updated = upsert_block(
        _neutralize_directory_ignores(existing or ""), GITIGNORE_BLOCK,
        GITIGNORE_BEGIN, GITIGNORE_END,
    )
    if existing == updated:
        return {"path": path, "status": "unchanged"}

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated)
    except OSError as err:
        return {"path": path, "status": f"failed: {err}"}

    return {"path": path, "status": "created" if existing is None else "updated"}


def _cleanup_legacy_claude_md(root: str, removed: List[str]) -> None:
    """Cleans up obsolete TLDRGraph block in CLAUDE.md."""
    claude_md_path = os.path.join(root, "CLAUDE.md")
    if not os.path.isfile(claude_md_path):
        return
    try:
        with open(claude_md_path, "r", encoding="utf-8") as f:
            existing = f.read()
    except OSError:
        existing = None

    if existing and CLAUDE_MD_BEGIN in existing:
        remainder = strip_block(existing, CLAUDE_MD_BEGIN, CLAUDE_MD_END)
        if remainder.strip():
            _write_if_changed(claude_md_path, remainder)
        else:
            try:
                os.remove(claude_md_path)
                removed.append("CLAUDE.md")
            except OSError:
                pass


def install_agent_rules(root_dir: str = ".", all_agents: bool = False) -> Dict[str, str]:
    """Makes a repository agent-ready with gitignore, contract, and agent commands."""
    root = os.path.abspath(root_dir)
    registry, _ = load_layer_config(root)
    written: Dict[str, str] = {}

    written["gitignore"] = ensure_gitignore(root)["path"]
    contract_path = os.path.join(root, CONTRACT_REL_PATH)
    _write_if_changed(contract_path, contract_text(registry))
    written["contract"] = contract_path

    written.update(install_agent_commands(root, all_agents=all_agents))
    removed = remove_superseded(root)
    _cleanup_legacy_claude_md(root, removed)

    if removed:
        written["superseded (removed)"] = ", ".join(removed)
    return written


def gitignore_warnings(root_dir: str = ".") -> List[str]:
    """Returns human-readable warnings for installed rule directories ignored by .gitignore."""
    gitignore_path = os.path.join(os.path.abspath(root_dir), ".gitignore")
    if not os.path.isfile(gitignore_path):
        return []
    try:
        with open(gitignore_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []

    watched = {".agents": ".agents/", ".claude": ".claude/", ".cursor": ".cursor/", "CLAUDE.md": "CLAUDE.md"}
    warnings: List[str] = []
    for lineno, raw in enumerate(lines, 1):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        key = entry.lstrip("/").rstrip("/")
        if key in watched:
            warnings.append(
                f".gitignore:{lineno} ignores '{entry}' - rules written under "
                f"{watched[key]} will not reach the rest of the team."
            )
    return warnings
