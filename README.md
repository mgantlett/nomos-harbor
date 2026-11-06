# Nomos Harbor Evaluation Harness Adapter (`nomos-harbor`)

Deterministic governance adapter wrapping Harbor's built-in `ClaudeCode` and `GeminiCli` agents with compiled Go [`nomos hook`](https://github.com/mgantlett/nomos) interceptors (`pre-tool` anti-reward-hacking & bounded loop circuit-breaker + `post-tool` sub-15ms in-memory polyglot AST/syntax radar, verified host-health snapshots, and MCTS-calibrated budget advisories).

Designed for reproducible, apples-to-apples paired Harbor evaluations (`Control` vs. `Control + Nomos`) across Claude Code and Gemini CLI on [Harbor](https://github.com/harbor-framework/harbor) and the [Harbor Index](https://github.com/harbor-framework/harbor-index) leaderboard.

## Empirical Benchmark Results (`Terminal-Bench 2.0` Controlled A/B)

Full `89 / 89`-task paired evaluation on [`terminal-bench@2.0`](https://github.com/laude-institute/terminal-bench-2) holding the base agent (`Gemini CLI 0.67.0`), model (`vertex_ai/gemini-3.5-flash`), and unmodified task timeouts constant:

| Arm | Agent | Score (`89` Tasks) | Lift vs. Control | Timeouts | API Errors | Public Harbor Hub Trajectories |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **Pure Stock Control (`pass@1`)** | `VertexGeminiCli` (`vertex_media_guard=False`, zero hooks) | **`58 / 89` (`65.17%`)** | Baseline | `24` | `3` | [`49f21995-f52a-4f33-837e-a3f86bbfadd0`](https://hub.harborframework.com/jobs/49f21995-f52a-4f33-837e-a3f86bbfadd0) |
| **Nomos Single-Shot (`pass@1`)** | `NomosGeminiCli` (`nomos hook` + Vertex media guard) | **`63 / 89` (`70.79%`)** | **`+5.62` pp** (`14W / 9L`) | `16` | `0` | [`39059e9a-8236-472a-a43b-ab500146abe1`](https://hub.harborframework.com/jobs/39059e9a-8236-472a-a43b-ab500146abe1) |
| **Nomos `v4.7.0` Upgrade Resume (`pass<=2`)** | `NomosGeminiCli` (`v4.7.0` Budget Radar + interactive/binary guards resumed on 26 `0.0` trials) | **`75 / 89` (`84.27%`)** | **`+19.10` pp** (`20W / 3L`) | `13` | `0` | [`6b9f81a5-0750-4242-80f2-0b5d0830355e`](https://hub.harborframework.com/jobs/6b9f81a5-0750-4242-80f2-0b5d0830355e) |

*Methodology note:* `63 / 89` (`70.79%`) is the strict single-shot `pass@1` Treatment score against Pure Stock Control (`58 / 89`, `65.17%`). `75 / 89` (`84.27%`) retains the `63` `pass@1` passes and re-evaluates the `26` failed tasks after shipping `nomos v4.7.0` (`--budget-sec` wall-clock advisories, interactive pager/REPL guard, and raw binary stdout guard), solving `12 / 26` (`pass<=2`). Paired 20-task stratified `Terminal-Bench 4.0 CPU` (`30m` cap) trajectories are also published on Harbor Hub ([Treatment `83e8d1ba`](https://hub.harborframework.com/jobs/83e8d1ba-b95b-4365-9003-51ca9c9a3eb9) vs. [Control `6979cbb9`](https://hub.harborframework.com/jobs/6979cbb9-13ca-4f72-a977-b541d2a52f57), cutting `30m` timeouts from `15/20` to `10/20`).

*`Terminal-Bench 4.0` separate-verifier note:* `TB 4.0` tasks run with `verifier_environment_mode = "separate"` using `rewardkit.Worker(private_paths=(Path("/tests"), Path("/logs/verifier")))`. Upstream Harbor's `SingleStepTrial._run_separate_verifier` currently executes `empty_dirs([env_paths.verifier_dir], chmod=True)` inside the separate verifier container after stopping the agent container, leaving world-writable directory permissions (`0777`) that `rewardkit.Worker` rejects with `Untrusted parent directory of /logs/verifier` whenever an agent finishes within the time budget instead of timing out. `nomos-harbor` enforces `0755` `root:root` hygiene on `/logs` inside shared-container trials without touching protected `/logs/verifier`.

## Architecture & Verifier Safety (`v0.4.0` + `nomos v4.8.0`)

1. **100% Native Harbor Auth, Trajectory & Cost Parity:** Subclasses Harbor's official `ClaudeCode` (`NomosClaudeCode` -> `agent.name = "nomos-claude-code"`) and `GeminiCli` (`NomosGeminiCli` -> `agent.name = "nomos-gemini-cli"`), reusing Harbor's built-in model parsing, credential resolution (`agent_auth_resolution`), ATIF `trajectory.json` conversion, and token/cost accounting.
2. **Runtime Anti-Reward-Hacking Guard (`--container-sandbox`):** Physically blocks reads, writes, directory listings, searches, and shell commands targeting Harbor's protected `/logs/verifier` (`reward.txt` / `reward.json`), `/solution`, and root `/tests` directories while allowing legitimate OS-wide sysadmin paths (`/etc`, `/tmp`, `/var`, `sqlite3`).
3. **Offline UCT/MCTS Model-Profile Calibration (`nomos_harbor.mcts_profiles`):** Tunes bounded advisory scaffolding per model family (`gemini`, `claude`, `gpt`, `default`) while holding all hard safety `deny` gates strictly invariant. Calibrated over `411` real Harbor ATIF trials (`1,893` web-search calls, `40` `cli_help` subagent spirals, `37` headless `<untrusted_context>` policy denials):
   - Excludes `google_web_search`, `web_fetch`, and `cli_help` (`tools.exclude` + `agents.overrides.cli_help.enabled = false` in `~/.gemini/settings.json`; `permissions.deny: ["WebSearch", "WebFetch"]` in `$CLAUDE_CONFIG_DIR/settings.json`).
   - Wires `nomos v4.8.0` `--budget-warn-pct 70 --budget-crit-pct 88 --advisory-style terse` on high-step Gemini/GPT runs and `75% / 90%` `standard` advisories on Claude runs.
   - Injects a concise `SessionStart` advisory guiding headless Gemini CLI around `<untrusted_context>` echoed `/` path checks using relative paths or `python3` subprocesses.
4. **Unconditional `PostToolUse` Verified Host-Health Snapshots:** Always attaches `Bash` / `run_shell_command` to `PostToolUse` / `AfterTool` matchers so `nomos v4.8.0` surfaces deduplicated 1-line remedies on verified DNS/egress, archived Debian `apt`, `EROFS`, `ENOSPC`/OOM, and missing-binary faults.
5. **Native Google Cloud Vertex AI ADC Bridge (`$0` API Key Setup):** `VertexGeminiCli` (stock Control baseline) and `NomosGeminiCli` (Treatment arm) stage host `gcloud` ADC credentials at `/tmp/gemini-secrets/adc.json` (`0600`), enforce `NODE_OPTIONS=--dns-result-order=ipv4first`, repair EOL Debian `apt` mirrors via `archive.debian.org`, guard Vertex inline-media `HTTP 400` crashes, and wipe `/tmp/gemini-secrets` on teardown.
6. **Flexible Binary Provisioning:** Downloads the official `nomos` static Linux binary via `https://sophialabs.ca/install` by default, or uploads a local static Linux binary in `<50ms` via `--ak nomos_binary_path=/path/to/nomos` (or `NOMOS_HARBOR_BINARY`).

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

# Treatment Arm (Gemini CLI + Nomos v4.8.0 Governance & MCTS Profile)
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

### Re-Calibrating MCTS Advisory Profiles on Local Harbor Jobs

```sh
python3 -m nomos_harbor.mcts_profiles --jobs-dir ./jobs --rollouts 240 --seed 42
```

### Optional Agent Kwargs (`--ak`)

- `--ak nomos_version=v4.8.0`: Pin a specific Nomos GitHub release version during container setup.
- `--ak nomos_binary_path=/abs/path/to/linux-nomos`: Upload a prebuilt static Linux `nomos` binary directly from the host instead of fetching over HTTPS (validated fail-closed during Harbor preflight).
- `--ak advisory_profile=gemini|claude|gpt|default`: Override automatic model-family profile selection.
- `--ak budget_warn_pct=70` / `--ak budget_crit_pct=88` / `--ak loop_strikes=3` / `--ak advisory_style=terse`: Override individual `nomos v4.8.0` advisory flags.
- `--ak disable_web_search=false` / `--ak exclude_cli_help=false` / `--ak block_web_search=false`: Re-enable web search or `cli_help` when evaluating open-web tasks.
- `--ak hermetic_dir=/workspace`: Enforce strict single-directory path confinement (`--hermetic-dir`) instead of full-container `--container-sandbox` mode.
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
