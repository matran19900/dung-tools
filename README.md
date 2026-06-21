# dung-tools — Claude Code plugin marketplace

A personal [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces.md): a single Git repo that distributes reusable extensions across **many projects and many machines** (incl. fresh Docker containers). Push once → every project picks it up.

## What's inside

```
dung-tools/                              ← marketplace repo (push this to GitHub)
├── .claude-plugin/
│   └── marketplace.json                 ← marketplace manifest (name + plugins[])
└── plugins/
    └── roles/                           ← plugin "roles" (one install unit)
        ├── .claude-plugin/plugin.json
        └── skills/
            ├── cto/SKILL.md             → invoked as /roles:cto
            └── em/SKILL.md              → invoked as /roles:em
```

- **`/roles:cto`** — CTO advisor: research → self-sufficient Plan → independent review. Read-only while EM runs.
- **`/roles:em`** — EM executor: receive Plan → Coder/Reviewer loop → branch-per-step → verify → merge.
- Both are **project-agnostic**. Project specifics (core goals, invariants, stack, test baselines, deploy, git quirks) stay in each repo's `docs/PROJECT_STATE.md` + `docs/workflow/PROJECT_CONTEXT.md`; the skills' §0 tells the model to read them.

> Skills in a plugin are **namespaced by plugin name** → you type `/roles:cto`, `/roles:em` (not `/cto`). Rename the plugin dir + `plugin.json` name + `marketplace.json` source if you want a different prefix.

## One-time: publish (CEO)

1. Create a GitHub repo `matran19900/dung-tools` (public = no auth needed in containers; private = containers need `GITHUB_TOKEN`/`gh auth`).
2. From this folder:
   ```bash
   git init && git add . && git commit -m "dung-tools: roles plugin (cto + em)"
   git branch -M main
   git remote add origin https://github.com/matran19900/dung-tools.git
   git push -u origin main
   ```
3. (Optional) validate before pushing: `claude plugin validate .`

## Install — two ways

### A) Manual, once per machine
```
/plugin marketplace add matran19900/dung-tools
/plugin install roles@dung-tools
```
Update later: push to this repo → `/plugin marketplace update dung-tools`.

### B) ⭐ Declarative per-project (best for Docker / many projects)
Commit this into **each project repo** at `.claude/settings.json` — when a container opens the project and you trust the folder, the marketplace auto-registers and the plugin auto-enables (no manual `/plugin install`):
```jsonc
{
  "extraKnownMarketplaces": {
    "dung-tools": {
      "source": { "source": "github", "repo": "matran19900/dung-tools" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": {
    "roles@dung-tools": true
  }
}
```
With `autoUpdate: true`, each new session pulls the latest pushed skills automatically.

## Docker notes

- Plugin state lives in `~/.claude/plugins/` — **machine/container-local**, not shared between containers. The declarative `.claude/settings.json` (option B) is what makes each fresh container re-fetch the plugin from GitHub on first trust.
- **Trust prompt**: appears once per machine per repo (interactive). Fine for VS Code dev sessions.
- **Headless / CI / cron** (no one to click trust): pre-seed at image build with `CLAUDE_CODE_PLUGIN_SEED_DIR` (mirror of `~/.claude/plugins/`), or pre-trust via managed settings.

## Versioning

- Bump `version` in `plugins/roles/.claude-plugin/plugin.json` on changes; users only update when it bumps (or via `autoUpdate`).
- Pin a project to a specific state with `ref` (branch/tag) or `sha` (commit) in the project's `extraKnownMarketplaces.source`.

## Growth path

A plugin can bundle more than skills. Later add to `plugins/roles/` (or new plugins):
- `agents/coder.md`, `agents/reviewer.md` — the Coder/Reviewer subagents EM spawns.
- `commands/` , `hooks/hooks.json` — e.g. a notify hook, selfcheck shipping.
- `.mcp.json` — shared MCP servers.
All install/update together as one unit.
