# dung-tools — Claude Code plugin marketplace

A personal [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces.md): a single Git repo that distributes reusable extensions across **many projects and many machines** (incl. fresh Docker containers). Push once → every project picks it up.

## What's inside

```
dung-tools/                              ← marketplace repo (push this to GitHub)
├── .claude-plugin/
│   └── marketplace.json                 ← marketplace manifest (name + plugins[])
└── plugins/
    ├── roles/                           ← plugin "roles" (one install unit)
    │   ├── .claude-plugin/plugin.json
    │   └── skills/
    │       ├── cto/SKILL.md             → invoked as /roles:cto
    │       ├── cto/scripts/             ← session-cost.py (token spend report)
    │       └── em/SKILL.md              → invoked as /roles:em
    └── system-report/                   ← plugin "system-report"
        ├── .claude-plugin/plugin.json
        ├── commands/                    → /system-report:init | :run | :status
        └── skills/system-report/
            ├── SKILL.md                 ← framework (mechanism only)
            ├── templates/               ← config, runner, triage prompt, reporters (C#/Py/TS/Go)
            └── reference/ARCHITECTURE.md
```

- **`/roles:cto`** — CTO advisor: research → self-sufficient Plan → independent review. Read-only while EM runs.
- **`/roles:em`** — EM executor: receive Plan → Coder/Reviewer loop → branch-per-batch (risk-tiered ritual) → verify → merge.
- **`/system-report:init`** — scaffold automated daily health reporting into the current repo; then cron runs `ops/system-report/run.sh` every day: collect multi-instance logs → READ-ONLY AI triage (dead instances, regressions vs `KNOWN_ISSUES.md`, open items in `WATCHLIST.md`, code correlation) → severity-ranked digest to a webhook + a full dated file. `:run` for manual/debug, `:status` for readiness.
- All are **project-agnostic**. Project specifics stay in each repo: roles read `docs/PROJECT_STATE.md` + `docs/workflow/PROJECT_CONTEXT.md`; system-report reads `ops/system-report/config.yml`.

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
/plugin install system-report@dung-tools
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
    "roles@dung-tools": true,
    "system-report@dung-tools": true
  }
}
```
With `autoUpdate: true`, each new session pulls the latest pushed skills automatically.

## Docker notes

- Plugin state lives in `~/.claude/plugins/` — **machine/container-local**, not shared between containers. The declarative `.claude/settings.json` (option B) is what makes each fresh container re-fetch the plugin from GitHub on first trust.
- **Trust prompt**: appears once per machine per repo (interactive). Fine for VS Code dev sessions.
- **Headless / CI / cron** (no one to click trust): pre-seed at image build with `CLAUDE_CODE_PLUGIN_SEED_DIR` (mirror of `~/.claude/plugins/`), or pre-trust via managed settings.

## Versioning

- ⚠️ **`version` lives in TWO files — bump BOTH in the same commit:**
  1. `plugins/<name>/.claude-plugin/plugin.json` — **wins at install time** (`calculatePluginVersion` precedence).
  2. `.claude-plugin/marketplace.json` → `plugins[].version` — what the marketplace listing shows.

  A stale entry in #2 is *silently ignored* at install, so nothing breaks loudly — it just misinforms
  anyone browsing the marketplace, and hides which release is current. Don't rely on noticing it by eye:
  ```bash
  claude plugin validate .      # flags the exact mismatch + the value to use. Must be warning-free.
  ```
  Keep `description` in the two files in sync too — the marketplace one is what people read before installing.
- Users only update when the version bumps (or via `autoUpdate`); the installed copy lives in `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`.
- Pin a project to a specific state with `ref` (branch/tag) or `sha` (commit) in the project's `extraKnownMarketplaces.source`.

## Growth path

A plugin can bundle more than skills. Later add to `plugins/roles/` (or new plugins):
- `agents/coder.md`, `agents/reviewer.md` — the Coder/Reviewer subagents EM spawns.
- `commands/` , `hooks/hooks.json` — e.g. a notify hook, selfcheck shipping.
- `.mcp.json` — shared MCP servers.
All install/update together as one unit.
