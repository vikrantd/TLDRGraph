# Supported Coding Agents

TLDRGraph was built from day one to pair with modern AI coding assistants.

---

## Universal Agent Support

Every supported agent receives byte-identical instructions and workflows. No tool receives special treatment or proprietary workarounds.

| Agent Tool | Integration Method | File Location | Command |
| :--- | :--- | :--- | :--- |
| **Claude Code** | Native command file | `.claude/commands/tldrgraph-init.md` | `/tldrgraph-init` |
| **Cursor** | Slash command | `.cursor/commands/tldrgraph-init.md` | `/tldrgraph-init` |
| **Codex** | Agent skill | `.agents/skills/tldrgraph-init/SKILL.md` | `$tldrgraph-init` or `/skills` |
| **Antigravity / Gemini CLI** | Cross-tool rules standard | `AGENTS.md` | Auto-invoked |
| **Windsurf** | Workflows directory | `.windsurf/workflows/tldrgraph-init.md` | Slash workflow |
| **Cline / Roo Code** | Rule and command workflows | `.clinerules/workflows/` | Slash workflow |
| **KiloCode / OpenCode** | Custom commands | `.opencode/command/` | Command prompt |
| **Continue.dev** | Custom prompt slash | `.continue/prompts/` | `/tldrgraph-init` |

---

## Automated Tool Detection

Running `tldrgraph install` detects active configuration folders in your project:

```bash
tldrgraph install
```

If your project contains a `.cursor` or `.claude` folder, TLDRGraph automatically registers the appropriate command. 

To install across every known agent environment:

```bash
tldrgraph install --all-agents
```

---

## The Cross-Tool Standard: `AGENTS.md`

TLDRGraph maintains `AGENTS.md` in your repository root. Most modern coding tools (Claude Code, Cursor, Codex, Antigravity, Copilot, Zed) parse `AGENTS.md` on startup.

It instructs the agent to:
1. **Never guess symbols or paths**: Always query the graph first using `tldrgraph query` or `tldrgraph trace`.
2. **Respect architectural layers**: Avoid introducing backward dependencies.
3. **Follow code health standards**: Maintain files $\le 400$ lines and functions $\le 50$ lines.
