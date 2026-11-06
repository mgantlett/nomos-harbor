# Nomos Harbor Evaluation Harness Adapter (`nomos-harbor`)

Deterministic governance adapter wrapping Harbor's built-in `ClaudeCode` and `GeminiCli` agents with compiled Go [`nomos hook`](https://github.com/mgantlett/nomos) interceptors (`pre-tool` anti-reward-hacking & 3-strike loop circuit-breaker + `post-tool` sub-15ms in-memory polyglot AST & syntax radar).

Designed for reproducible, apples-to-apples paired Harbor evaluations (`Control` vs. `Control + Nomos`) across Claude Code and Gemini CLI on [Harbor](https://github.com/harbor-framework/harbor) and the [Harbor Index](https://github.com/harbor-framework/harbor-index) leaderboard.

## Empirical Benchmark Results (`Terminal-Bench 2.0` Controlled A/B)

Full `89 / 89`-task paired evaluation on [`terminal-bench@2.0`](https://github.com/laude-institute/terminal-bench-2) holding the base agent (`Gemini CLI 0.67.0`), model (`vertex_ai/gemini-3.5-flash`), and unmodified task timeouts constant:

| Arm | Agent | Score (`89` Tasks) | Lift vs. Control | Timeouts | API Errors | Public Harbor Hub Trajectories |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **Pure Stock Control (`pass@1`)** | `VertexGeminiCli` (`vertex_media_guard=False`, zero hooks) | **`58 / 89` (`65.17%`)** | Baseline | `24` | `3` | [`49f21995-f52a-4f33-837e-a3f86bbfadd0`](https://hub.harborframework.com/jobs/49f21995-f52a-4f33-837e-a3f86bbfadd0) |
| **Nomos Single-Shot (`pass@1`)** | `NomosGeminiCli` (`nomos hook` + Vertex media guard) | **`63 / 89` (`70.79%`)** | **`+5.62` pp** (`14W / 9L`) | `16` | `0` | [`39059e9a-8236-472a-a43b-ab500146abe1`](https://hub.harborframework.com/jobs/39059e9a-8236-472a-a43b-ab500146abe1) |
| **Nomos `v4.7.0` Upgrade Resume (`pass<=2`)** | `NomosGeminiCli` (`v4.7.0` Budget Radar + interactive/binary guards resumed on 26 `0.0` trials) | **`75 / 89` (`84.27%`)** | **`+19.10` pp** (`20W / 3L`) | `13` | `0` | [`6b9f81a5-0750-4242-80f2-0b5d0830355e`](https://hub.harborframework.com/jobs/6b9f81a5-0750-4242-80f2-0b5d0830355e) |

*Methodology note:* `63 / 89` (`70.79%`) is the strict single-shot `pass@1` Treatment score against Pure Stock Control (`58 / 89`, `65.17%`). `75 / 89` (`84.27%`) retains the `63` `pass@1` passes and re-evaluates the `26` failed tasks after shipping `nomos v4.7.0` (`--budget-sec` wall-clock advisories, interactive pager/REPL guard, and raw binary stdout guard), solving `12 / 26` (`pass<=2`). Paired 20-task stratified `Terminal-Bench 4.0 CPU` (`30m` cap) trajectories are also published on Harbor Hub ([Treatment `83e8d1ba`](https://hub.harborframework.com/jobs/83e8d1ba-b95b-4365-9003-51ca9c9a3eb9) vs. [Control `6979cbb9`](https://hub.harborframework.com/jobs/6979cbb9-13ca-4f72-a977-b541d2a52f57), cutting `30m` timeouts from `15/20` to `10/20`).

## Architecture & Verifier Safety

1. **100% Native Harbor Auth, Trajectory & Cost Parity:** Subclasses Harbor's official `ClaudeCode` (`NomosClaudeCode` -> `agent.name = "nomos-claude-code"`) and `GeminiCli` (`NomosGeminiCli` -> `agent.name = "nomos-gemini-cli"`), reusing Harbor's built-in model parsing, credential resolution (`agent_auth_resolution`), ATIF `trajectory.json` conversion, and token/cost accounting.
2. **Runtime Anti-Reward-Hacking Guard (`--container-sandbox`):** Physically blocks reads, writes, directory listings, searches, and shell commands targeting Harbor's protected `/logs/verifier` (`reward.txt` / `reward.json`), `/solution`, and root `/tests` directories while allowing legitimate OS-wide sysadmin paths (`/etc`, `/tmp`, `/var`, `sqlite3`).
3. **Zero `/app` Workspace Pollution:** Injects `nomos hook` additively into `$CLAUDE_CONFIG_DIR/settings.json` (`PreToolUse` / `PostToolUse`) or `~/.gemini/settings.json` (`BeforeTool` / `AfterTool`) outside `/app`, keeping `/app` completely clean for strict workspace verifiers and Harbor's `/judge` supervisor.
4. **Native Google Cloud Vertex AI ADC Bridge (`$0` API Key Setup):** `VertexGeminiCli` (stock Control baseline) and `NomosGeminiCli` (Treatment arm) automatically detect `gcloud auth application-default login` credentials on the host, strip conflicting ambient `GEMINI_API_KEY` / `GOOGLE_API_KEY` variables, stage an ephemeral `0600` credential inside `/tmp/gemini-secrets/adc.json` for `vertex_ai/gemini-*` runs, enforce `NODE_OPTIONS=--dns-result-order=ipv4first` to prevent Node 22 Docker bridge IPv6 stalls, recover EOL Debian `apt` mirrors via `archive.debian.org`, guard Vertex inline-media `HTTP 400` crashes, and wipe `/tmp/gemini-secrets` on teardown.
5. **Automatic Per-Task Wall-Clock Budget Radar (`--budget-sec`):** Automatically reads each Harbor trial's `task.toml` `[agent] timeout_sec` (scaled by `timeout_multiplier`) and wires `nomos hook --budget-sec <seconds>` so agents receive deduplicated `75%` and `90%` elapsed wall-clock advisories before hitting `AgentTimeoutError`.
6. **Flexible Binary Provisioning:** Downloads the official `nomos` static Linux binary via `https://sophialabs.ca/install` by default (reproducible on any cloud sandbox or macOS/Linux runner), or uploads a local static Linux binary in `<50ms` via `--ak nomos_binary_path=/path/to/nomos` (or `NOMOS_HARBOR_BINARY`).

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

# Treatment Arm (Gemini CLI + Nomos Governance Hooks - Recommended Vertex DSQ Concurrency)
env -u GEMINI_API_KEY -u GOOGLE_API_KEY \
  harbor run --dataset terminal-bench@2.0 \
  --agent nomos_harbor:NomosGeminiCli \
  --model vertex_ai/gemini-3.5-flash \
  --n-concurrent 4 \
  --n-concurrent-agents 3 \
  --max-retries 3
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
