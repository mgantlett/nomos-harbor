# Nomos Harbor Evaluation Harness Adapter (`nomos-harbor`)

Deterministic governance adapter wrapping Harbor's built-in `ClaudeCode` agent with compiled Go [`nomos hook`](https://github.com/mgantlett/nomos) interceptors (`pre-tool` anti-reward-hacking & 3-strike loop circuit-breaker + `post-tool` sub-15ms in-memory polyglot AST & syntax radar).

Designed for reproducible, apples-to-apples paired Harbor evaluations (`Claude Code` vs. `Claude Code + Nomos`) on [Harbor](https://github.com/harbor-framework/harbor) and the [Harbor Index](https://github.com/harbor-framework/harbor-index) leaderboard.

## Architecture & Verifier Safety

1. **100% Native Harbor Auth, Trajectory & Cost Parity:** Subclasses Harbor's official `ClaudeCode` (`harbor.agents.installed.claude_code.ClaudeCode`), reusing Harbor's built-in model parsing, credential resolution (`agent_auth_resolution`), ATIF `trajectory.json` conversion (tagged `agent.name = "nomos-claude-code"`), and token/cost accounting.
2. **Runtime Anti-Reward-Hacking Guard (`--container-sandbox`):** Physically blocks reads, writes, and shell commands targeting Harbor's protected `/logs/verifier` (`reward.txt` / `reward.json`), `/solution`, and root `/tests` directories while allowing legitimate OS-wide sysadmin paths (`/etc`, `/tmp`, `/var`, `sqlite3`).
3. **Zero `/app` Workspace Pollution:** Injects `nomos hook` additively into `$CLAUDE_CONFIG_DIR/settings.json` (`/logs/agent/sessions/settings.json`) outside `/app`, preserving task-local `.claude/settings.json` hooks without CLI `--settings` shadowing and keeping `/app` completely clean for strict workspace verifiers and Harbor's `/judge` supervisor.
4. **Flexible Binary Provisioning:** Downloads the official `nomos` static Linux binary via `https://sophialabs.ca/install` by default (reproducible on any cloud sandbox or macOS/Linux runner), or uploads a local static Linux binary in `<50ms` via `--ak nomos_binary_path=/path/to/nomos` (or `NOMOS_HARBOR_BINARY`).

## Quickstart (Paired Control vs. Treatment)

Install Harbor and `nomos-harbor` into your Python environment:

```sh
pip install harbor
pip install "git+https://github.com/mgantlett/nomos-harbor.git"
```

Run an apples-to-apples comparison on any Harbor dataset:

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

- `--ak nomos_version=v4.5.0`: Pin a specific Nomos GitHub release version during container setup.
- `--ak nomos_binary_path=/abs/path/to/linux-nomos`: Upload a prebuilt static Linux `nomos` binary directly from the host instead of fetching over HTTPS (validated fail-closed during Harbor preflight).
- `--ak hermetic_dir=/workspace`: Optionally enforce strict single-directory path confinement (`--hermetic-dir`) instead of full-container `--container-sandbox` mode.

## Submitting to the Harbor Index Leaderboard

From a clone of [`harbor-framework/harbor-index`](https://github.com/harbor-framework/harbor-index):

```sh
uv run --with "git+https://github.com/mgantlett/nomos-harbor.git" \
  harbor run -c job-config.yaml \
  --agent nomos_harbor:NomosClaudeCode \
  -m anthropic/claude-sonnet-5 \
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
