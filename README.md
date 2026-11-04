# Nomos Harbor Evaluation Harness Adapter (`nomos-harbor`)

Deterministic governance adapter wrapping Harbor's built-in `ClaudeCode` and `GeminiCli` agents with compiled Go [`nomos hook`](https://github.com/mgantlett/nomos) interceptors (`pre-tool` anti-reward-hacking & 3-strike loop circuit-breaker + `post-tool` sub-15ms in-memory polyglot AST & syntax radar).

Designed for reproducible, apples-to-apples paired Harbor evaluations (`Control` vs. `Control + Nomos`) across Claude Code and Gemini CLI on [Harbor](https://github.com/harbor-framework/harbor) and the [Harbor Index](https://github.com/harbor-framework/harbor-index) leaderboard.

## Architecture & Verifier Safety

1. **100% Native Harbor Auth, Trajectory & Cost Parity:** Subclasses Harbor's official `ClaudeCode` (`NomosClaudeCode` -> `agent.name = "nomos-claude-code"`) and `GeminiCli` (`NomosGeminiCli` -> `agent.name = "nomos-gemini-cli"`), reusing Harbor's built-in model parsing, credential resolution (`agent_auth_resolution`), ATIF `trajectory.json` conversion, and token/cost accounting.
2. **Runtime Anti-Reward-Hacking Guard (`--container-sandbox`):** Physically blocks reads, writes, directory listings, searches, and shell commands targeting Harbor's protected `/logs/verifier` (`reward.txt` / `reward.json`), `/solution`, and root `/tests` directories while allowing legitimate OS-wide sysadmin paths (`/etc`, `/tmp`, `/var`, `sqlite3`).
3. **Zero `/app` Workspace Pollution:** Injects `nomos hook` additively into `$CLAUDE_CONFIG_DIR/settings.json` (`PreToolUse` / `PostToolUse`) or `~/.gemini/settings.json` (`BeforeTool` / `AfterTool`) outside `/app`, keeping `/app` completely clean for strict workspace verifiers and Harbor's `/judge` supervisor.
4. **Native Google Cloud Vertex AI ADC Bridge (`$0` API Key Setup):** `VertexGeminiCli` (stock Control baseline) and `NomosGeminiCli` (Treatment arm) automatically detect `gcloud auth application-default login` credentials on the host, stage an ephemeral `0600` credential inside `/tmp/gemini-secrets/adc.json` for `vertex_ai/gemini-*` runs, auto-resolve `quota_project_id` and `global` Vertex endpoints, and wipe `/tmp/gemini-secrets` on teardown.
5. **Flexible Binary Provisioning:** Downloads the official `nomos` static Linux binary via `https://sophialabs.ca/install` by default (reproducible on any cloud sandbox or macOS/Linux runner), or uploads a local static Linux binary in `<50ms` via `--ak nomos_binary_path=/path/to/nomos` (or `NOMOS_HARBOR_BINARY`).

## Quickstart (Paired Control vs. Treatment)

Install Harbor and `nomos-harbor` into your Python environment:

```sh
pip install harbor
pip install "git+https://github.com/mgantlett/nomos-harbor.git"
```

### Option A: Google Cloud Vertex AI (`gcloud` Credits - Zero API Key Required)

```sh
# Authenticate once on host (uses your GCP billing / free credits)
gcloud auth application-default login

# Control Arm (Stock Gemini CLI + Vertex ADC Container Bridge)
harbor run --dataset terminal-bench@2.0 \
  --agent nomos_harbor:VertexGeminiCli \
  --model vertex_ai/gemini-3.5-flash \
  --n-concurrent 4

# Treatment Arm (Gemini CLI + Nomos Governance Hooks)
harbor run --dataset terminal-bench@2.0 \
  --agent nomos_harbor:NomosGeminiCli \
  --model vertex_ai/gemini-3.5-flash \
  --n-concurrent 4
```

### Option B: Claude Code (`ANTHROPIC_API_KEY`)

```sh
# Control Arm (Stock Claude Code)
harbor run --dataset terminal-bench@2.0 \
  --agent claude-code \
  --model anthropic/claude-sonnet-4-5 \
  --n-concurrent 4

# Treatment Arm (Claude Code + Nomos)
harbor run --dataset terminal-bench@2.0 \
  --agent nomos_harbor:NomosClaudeCode \
  --model anthropic/claude-sonnet-4-5 \
  --n-concurrent 4
```

### Optional Agent Kwargs (`--ak`)

- `--ak nomos_version=v4.5.1`: Pin a specific Nomos GitHub release version during container setup.
- `--ak nomos_binary_path=/abs/path/to/linux-nomos`: Upload a prebuilt static Linux `nomos` binary directly from the host instead of fetching over HTTPS (validated fail-closed during Harbor preflight).
- `--ak hermetic_dir=/workspace`: Optionally enforce strict single-directory path confinement (`--hermetic-dir`) instead of full-container `--container-sandbox` mode.
- `--ak gcp_project=<project-id>` / `--ak gcp_location=global`: Override Vertex AI project or region when running `vertex_ai/*` models.

## Submitting to the Harbor Index Leaderboard

From a clone of [`harbor-framework/harbor-index`](https://github.com/harbor-framework/harbor-index):

```sh
uv run --with "git+https://github.com/mgantlett/nomos-harbor.git" \
  harbor run -c job-config.yaml \
  --agent nomos_harbor:NomosGeminiCli \
  -m vertex_ai/gemini-3.1-pro-preview \
  --ak reasoning_effort=high \
  -e docker \
  --ve OPENAI_API_KEY="$OPENAI_API_KEY" \
  --ve ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --ve GEMINI_API_KEY="$GEMINI_API_KEY" \
  --upload \
  --public

cd leaderboard
uv run lb submit https://hub.harborframework.com/jobs/<uuid>
```
