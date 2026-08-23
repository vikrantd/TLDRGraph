"""
Host coding-agent runner for TLDRGraph.

**This is the secondary path.** TLDRGraph is normally driven *by* an agent
through the file handshake in ``tldrgraph init``, which works with every tool
because it only needs "read a file, read source, run a command". Shelling out is
opt-in (``--agent-cli``) and exists for a plain terminal with no agent attached.

It is not the default for good reasons, all observed in the field: every CLI has
different flags, auth and headless semantics; a blocking ``subprocess.run`` shows
the user nothing while the agent thinks; and some IDEs ship no usable CLI at all
(Antigravity's ``agy`` is a broken symlink on a stock install).

Nesting is refused. When TLDRGraph is itself being run by a coding agent
(``CLAUDECODE``, ``CURSOR_AGENT``, ...), spawning a second agent burns tokens to
duplicate context the host already has, so the host is told what to do instead.
``TLDRGRAPH_AGENT_NESTED=1`` overrides.

Environment:
    TLDRGRAPH_NO_AGENT=1        never shell out to an agent CLI
    TLDRGRAPH_AGENT_CLI=<name>  force a specific agent ("claude", "gemini", ...)
    TLDRGRAPH_AGENT_NESTED=1    allow spawning even inside an agent session
    TLDRGRAPH_AGENT_TIMEOUT=<s> per-call timeout in seconds (default 600)
    TLDRGRAPH_AGENT_MODEL=<id>  model to pass to the agent CLI

Model choice only applies to the CLI shell-out. When your own agent drives
TLDRGraph through the file handshake -- the normal path -- the model is whatever
that session is already using, chosen in your IDE.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

#: Opt-out and override knobs, documented in the module docstring.
ENV_DISABLE = "TLDRGRAPH_NO_AGENT"
ENV_FORCE_CLI = "TLDRGRAPH_AGENT_CLI"
ENV_ALLOW_NESTED = "TLDRGRAPH_AGENT_NESTED"
ENV_TIMEOUT = "TLDRGRAPH_AGENT_TIMEOUT"
ENV_MODEL = "TLDRGRAPH_AGENT_MODEL"

DEFAULT_TIMEOUT = 600

#: Environment markers meaning "a coding agent is already driving this process".
#: Spawning another one here would pay twice for context the host already holds.
NESTED_MARKERS = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CURSOR_AGENT",
    "CURSOR_TRACE_ID",
    "GEMINI_CLI",
    "AI_AGENT",
)


@dataclass(frozen=True)
class AgentCLI:
    """One headless coding-agent CLI TLDRGraph knows how to drive."""

    #: Stable short name, also accepted by $TLDRGRAPH_AGENT_CLI.
    name: str
    #: Executable to look for on PATH.
    binary: str
    #: Human-facing label used in CLI output.
    display: str
    #: Builds the argv (minus the binary) for a one-shot prompt.
    build_args: Callable[[str], List[str]]
    #: Pulls the assistant's text out of the process stdout.
    parse_stdout: Callable[[str], str] = field(default=lambda text: text)
    #: This CLI's flag for selecting a model, if it has one.
    model_flag: Optional[str] = "--model"

    def argv(self, prompt: str, model: Optional[str] = None) -> List[str]:
        args = list(self.build_args(prompt))
        model = model or os.environ.get(ENV_MODEL, "").strip() or None
        if model and self.model_flag:
            args = [self.model_flag, model] + args
        return [self.binary] + args


def _claude_args(prompt: str) -> List[str]:
    # Read-only toolset: the agent must inspect the repo, never modify it.
    # TLDRGraph owns every write, so the agent's only job is to answer.
    return [
        "-p", prompt,
        "--output-format", "json",
        "--tools", "Read,Grep,Glob",
        "--permission-mode", "default",
    ]


def _claude_parse(text: str) -> str:
    """`--output-format json` wraps the answer in a result envelope."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(payload, dict):
        if payload.get("is_error"):
            raise AgentError(str(payload.get("result") or "claude reported an error"))
        result = payload.get("result")
        if isinstance(result, str):
            return result
    return text


def _cursor_parse(text: str) -> str:
    """cursor-agent's json envelope also carries the answer under `result`."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(payload, dict):
        for key in ("result", "response", "text"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return text


#: Known agents, in preference order.
KNOWN_AGENTS: Sequence[AgentCLI] = (
    AgentCLI(
        name="claude",
        binary="claude",
        display="Claude Code",
        build_args=_claude_args,
        parse_stdout=_claude_parse,
    ),
    AgentCLI(
        name="cursor-agent",
        binary="cursor-agent",
        display="Cursor Agent",
        build_args=lambda prompt: ["-p", prompt, "--output-format", "json"],
        parse_stdout=_cursor_parse,
    ),
    AgentCLI(
        name="gemini",
        binary="gemini",
        display="Gemini CLI",
        build_args=lambda prompt: ["-p", prompt],
        model_flag="-m",
    ),
)


class AgentError(RuntimeError):
    """Raised when an agent CLI fails, times out, or returns unusable output."""


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def nesting_marker() -> Optional[str]:
    """The env var proving a coding agent is already driving us, if any."""
    for marker in NESTED_MARKERS:
        if os.environ.get(marker):
            return marker
    return None


def agent_disabled() -> bool:
    return os.environ.get(ENV_DISABLE, "").strip().lower() in ("1", "true", "yes", "on")


def _nesting_allowed() -> bool:
    return os.environ.get(ENV_ALLOW_NESTED, "").strip().lower() in ("1", "true", "yes", "on")


def find_agent_cli(respect_nesting: bool = True) -> Optional[AgentCLI]:
    """
    The agent CLI TLDRGraph may shell out to right now, or None.

    Returns None when disabled, when no known binary is on PATH, or when we are
    already running inside an agent session (unless nesting is opted into) --
    in that last case the caller should print instructions for the host agent.
    """
    if agent_disabled():
        return None
    if respect_nesting and nesting_marker() and not _nesting_allowed():
        return None

    forced = os.environ.get(ENV_FORCE_CLI, "").strip()
    if forced:
        for agent in KNOWN_AGENTS:
            if agent.name == forced or agent.binary == forced:
                return agent if shutil.which(agent.binary) else None
        # An unknown name is treated as a claude-compatible binary path.
        if shutil.which(forced):
            return AgentCLI(
                name=forced,
                binary=forced,
                display=forced,
                build_args=_claude_args,
                parse_stdout=_claude_parse,
            )
        return None

    for agent in KNOWN_AGENTS:
        if shutil.which(agent.binary):
            return agent
    return None


def agent_status() -> Dict[str, Any]:
    """
    Why TLDRGraph will or will not shell out, in a form the CLI can print.

    ``reason`` is one of: ``ready``, ``disabled``, ``nested``, ``not_found``.
    """
    if agent_disabled():
        return {"agent": None, "reason": "disabled", "detail": f"${ENV_DISABLE} is set"}

    marker = nesting_marker()
    installed = [a for a in KNOWN_AGENTS if shutil.which(a.binary)]

    if marker and not _nesting_allowed():
        return {
            "agent": None,
            "reason": "nested",
            "detail": f"already running inside a coding agent (${marker})",
            "installed": [a.name for a in installed],
        }

    agent = find_agent_cli()
    if agent is None:
        return {
            "agent": None,
            "reason": "not_found",
            "detail": "no agent CLI on PATH ("
                      + ", ".join(a.binary for a in KNOWN_AGENTS) + ")",
        }
    return {"agent": agent, "reason": "ready", "detail": agent.display}


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"```(?:json|yaml|yml)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """
    Pulls a JSON object/array out of an agent's answer.

    Agents wrap JSON in prose and code fences no matter how firmly asked not to,
    so this tries the whole string, then fenced blocks, then the widest
    balanced ``{...}`` / ``[...]`` span.
    """
    if not text or not text.strip():
        raise AgentError("agent returned empty output")

    candidates: List[str] = [text.strip()]
    candidates.extend(block.strip() for block in _FENCE_RE.findall(text))

    # Widest balanced span per delimiter, tried outermost-first. Ordering by the
    # opener's position matters: in `Here you go: [{"id": "x"}]` the first `{` is
    # *inside* the array, so trying braces first would return one element and
    # silently drop the rest of the batch.
    spans = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            spans.append((start, text[start:end + 1]))
    candidates.extend(span for _, span in sorted(spans))

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except ValueError:
            continue

    preview = text.strip().replace("\n", " ")[:200]
    raise AgentError(f"could not parse JSON from agent output: {preview}")


def call_timeout() -> int:
    raw = os.environ.get(ENV_TIMEOUT, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_TIMEOUT


def run_agent(agent: AgentCLI, prompt: str, cwd: str, timeout: Optional[int] = None,
              model: Optional[str] = None) -> str:
    """
    Runs the agent headlessly in ``cwd`` and returns its answer text.

    Raises AgentError on non-zero exit, timeout, or an empty answer.
    """
    try:
        proc = subprocess.run(
            agent.argv(prompt, model=model),
            cwd=os.path.abspath(cwd),
            capture_output=True,
            text=True,
            timeout=timeout or call_timeout(),
        )
    except subprocess.TimeoutExpired as err:
        raise AgentError(
            f"{agent.display} timed out after {timeout or call_timeout()}s "
            f"(raise ${ENV_TIMEOUT} to allow longer)"
        ) from err
    except OSError as err:
        raise AgentError(f"could not run {agent.binary}: {err}") from err

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[:300]
        raise AgentError(f"{agent.display} exited {proc.returncode}: {detail or 'no output'}")

    return agent.parse_stdout(proc.stdout or "")


def run_agent_json(agent: AgentCLI, prompt: str, cwd: str, timeout: Optional[int] = None,
                   model: Optional[str] = None) -> Any:
    """``run_agent`` plus JSON extraction. Raises AgentError on any failure."""
    return extract_json(run_agent(agent, prompt, cwd, timeout=timeout, model=model))
