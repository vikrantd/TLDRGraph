# Installation Guide

Install TLDRGraph directly from PyPI.

## Prerequisites

- **Python**: `>= 3.10` (Python 3.10, 3.11, and 3.12 are fully tested and supported).
- **Operating System**: macOS, Linux, or Windows (WSL recommended on Windows).
- **Git**: Installed and available in `$PATH`.

---

## 1. Install via pip

Install the official package:

```bash
pip install tldrgraph
```

Or using `uv`:

```bash
uv pip install tldrgraph
```

---

## 2. Verify Installation

Check that the CLI is accessible:

```bash
tldrgraph --version
```

Output:
```text
tldrgraph, version 0.2.0
```

---

## 3. Optional Dependencies

TLDRGraph comes with FastEmbed ONNX dense embeddings pre-configured. If you plan to build or serve the documentation locally:

```bash
pip install "tldrgraph[docs]"
```

---

## 4. Install Coding Agent Commands

TLDRGraph installs identical workflows and execution rules across your preferred coding agents:

```bash
# Detect and install for all active agent environments in the repository
tldrgraph install --all-agents
```

This sets up:
- **Claude Code**: `.claude/commands/tldrgraph-init.md`
- **Cursor**: `.cursor/commands/tldrgraph-init.md`
- **Codex**: `.agents/skills/tldrgraph-init/SKILL.md`
- **Antigravity / Gemini CLI / Copilot / Zed**: `AGENTS.md`
- **Windsurf**: `.windsurf/workflows/`
- **Cline / Roo Code / KiloCode**: `.clinerules/workflows/`
