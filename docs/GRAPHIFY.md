# Graphify for Astra Research

Graphify 0.9.97 is installed locally in `.tools/graphify/`, separately from the
application's Python dependencies. Its Codex skill is registered in
`.codex/skills/graphify/`, and `AGENTS.md` tells coding agents to query the graph
before exploring source code.

Run commands from PowerShell at the project root:

```powershell
.\scripts\graphify.ps1 --version
.\scripts\graphify.ps1 query "build_stock_model" --budget 1200
.\scripts\graphify.ps1 explain "build_stock_model()"
.\scripts\graphify.ps1 god-nodes --top 10
```

The launcher also works from another folder when invoked by its full path. It
sets the working directory and UTF-8 mode for the CLI without changing your PATH.

## Build and refresh

```powershell
# Full source-code extraction; no model or API key required
.\scripts\graphify.ps1 extract . --code-only
.\scripts\graphify.ps1 cluster-only . --no-label

# After source changes
.\scripts\graphify.ps1 update .
```

The generated files are `graphify-out/graph.json`, `graphify-out/graph.html`, and
`graphify-out/GRAPH_REPORT.md`. The HTML provides an interactive graph. The report
summarizes connected symbols and communities. Generated output and the tool
environment are ignored by Git.

This index covers code only. Documents and media are skipped rather than sent to
a model. `.graphifyignore` excludes the tool installation, assistant configuration,
private files, and exports. `.gitignore` also applies. Dynamic runtime connections
may be missing or inferred; verify relevant source before changing behavior.

## Reinstall on a fresh checkout

After setting up the application Python environment with `uv sync --locked`:

```powershell
uv --cache-dir .tools/uv-cache venv .tools/graphify --python .venv/Scripts/python.exe --no-project
uv --cache-dir .tools/uv-cache pip install --python .tools/graphify/Scripts/python.exe graphifyy==0.9.97
.\scripts\graphify.ps1 extract . --code-only
.\scripts\graphify.ps1 cluster-only . --no-label
```

Tool installations, local skills and hooks are private local setup and are not
included in the GitHub upload. To install the project skill, use
`.\scripts\graphify.ps1 install --project --platform codex`. Start a new Codex
chat to pick up a newly installed skill. Use `$graphify` in that chat, or use the
launcher directly in the current chat.

This local graph is independent of the hosted Graphify connector. It does not
register a cloud repository or require a Git remote or Graphify workspace.

Official references: [installation](https://docs.graphify.com/open-source/installation)
and [CLI quickstart](https://docs.graphify.com/open-source/cli-quickstart).
