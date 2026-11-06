<!-- NOMOS_OPEN_CORE_PROTOCOL_START -->
<!-- ⚠️ NOMOS OPEN-CORE PROTOCOL BELOW. DO NOT EDIT THIS SECTION. ⚠️ -->

# AGENTS.md - Nomos Open-Core Protocol (Deterministic Substrate)

**Role:** Tier 1 Coding Agent Operating Under Deterministic Indirect Control  
**Objective:** Build, verify, and ship repository tasks inside hermetic worktrees governed by `nomos` machine-enforced quality gates.

---

## 0.0 🛡️ THE ONE-PAGE INVARIANT CARD (THE 7 LAWS)

| Law # | Law Name | Core Architectural Invariant | Machine Enforcement Mechanism |
| :---: | :--- | :--- | :--- |
| **1** | **The Dual-Core Inversion Law** | Substrate Go core governs state & execution; the **Tier 1 coding agent** (AGY-IDE, opencode, Claude Code, Cursor…) operates strictly as a bounded cognitive co-processor under **indirect control**. | `nomos verify`, Phase Discipline state machine |
| **2** | **The Non-Verbal Proof Law** | Zero trust in conversational claims; code correctness requires compilation exit code 0 and AST proof. | The 7x3 Canonical Invariant Suite (7 Laws, 21 Invariant Cells) (`nomos verify`) |
| **3** | **The Hermetic Worktree Law** | Protected trunk branches (`main`) are physically read-only; mutations occur strictly inside isolated task worktrees. | `ValidateWorktreeBoundary`, Pre-Commit Hooks |
| **4** | **The Single Source of Truth Law** | Every contract, schema, and configuration possesses exactly one compiled Go SSoT with schema parity. | Static Go AST & JSON Schema parity (`nomos verify`) |
| **5** | **The Zero-Hardcoded-Path Law** | Path literals and workstation directory paths are strictly prohibited in source; paths resolve dynamically. | Configuration discovery (`nomos.yaml`) |
| **6** | **The Canonical Singularity Law** | Zero aliases, duplicates, convenience wrappers, or symlinks tolerated in CLI engines or contract schemas. | Zero-Alias AST Parser & ZeroFilesystemAliasGate (`nomos verify`) |
| **7** | **The Fail-Closed Pre-Flight Law** | Mutations require explicit declarative configuration, strict 2-plane hemisphere separation, and pass mandatory DoD quality gates. | CapBAC Object Capabilities, PlaneSeparationGate, DoD quality gates |

---

## 0.1 ⚡ BOOTUP, HATEOAS AFFORDANCES & EMBEDDED PLAYBOOKS

1. **Session Boot & Invisible Harness:** Run `nomos handshake` on session boot (auto-injected when `SessionStart` hooks are active) to initialize state, verify CapBAC tokens, auto-install Git defense hooks, and receive phase-specific `Next Actions:`.
2. **1-Turn Task Inception & Phase Machine:** Workspace mutability is procedurally locked across lifecycle phases:
   `IDLE` (`nomos task chore "<title>"` or `nomos task start <KEY> --edit`) → `PLAN` → `EDIT` (strictly in `worktrees/<repo>-<KEY>`) → `REVIEW` (`nomos verify` DoD gates) → `SYNC` (`nomos task sync <KEY> -F <walkthrough-file>` 2PC atomic release) → `LEARN` (distillation).
3. **Self-Contained Embedded Discovery (Zero Nomos Source Required):**
   - `nomos directive` / `nomos directive <name>` — List and read embedded Just-In-Time (JIT) phase playbooks.
   - `nomos capabilities` / `nomos capabilities <id>` — Inspect active quality gates, ambient hooks, directives, and CLI engines.
   - `nomos verify --impact` — Inspect deterministic 1-hop AST blast radius for modified worktree files.
4. **Pure 2-Plane Singularity:** All tasks strictly use keys `INT-<N>` (intent/config plane) or `SUB-<N>` (code/substrate plane). Cross-plane mutations in a single task fail closed (`PlaneSeparationGate`).
5. **Database Encapsulation:** Tier 1 agents are strictly prohibited from directly querying or mutating internal database files. Use canonical CLI commands (`nomos task`, `nomos mem`) per ADR-0062.
6. **Memory Scoping:** Nomos Mneme memories are partitioned per repository (`repo_id`). Use `nomos mem recall <query>` for workspace search, `-A/--cross-repo` for cross-repo discovery, `nomos mem list [-A]` for inspection, and `nomos mem save -p <repo> <content>` for reflections.

---

## 0.2 📋 Table-Driven Protocol Compliance Vectors

| Protocol Check | Input / Action | Invariant Enforced | Substrate Gate | Expected Outcome |
| :--- | :--- | :--- | :--- | :--- |
| `TC-AGT-01` | Direct commit on `main` | Law 3 (Hermetic Worktree) | CapBAC Pre-Commit Hook | Fail Closed (Exit 1) |
| `TC-AGT-02` | invalid: `nomos task create "Task" --key CUST-1` | Law 6 (Canonical Singularity) | Regex Key Gate (`task.ValidateTaskKey`) | Fail Closed (`ERR_CANONICAL_VIOLATION`) |
| `TC-AGT-03` | Direct internal database file tampering | Law 1 & ADR-0062 (Encapsulation) | ForbiddenCommandGate | Fail Closed (`ERR_DIRECT_DB_ACCESS_FORBIDDEN`) |
| `TC-AGT-04` | Execute `nomos verify` | Law 2 (Non-Verbal Proof) | 42 Quality Gates | Pass (Exit 0) |
| `TC-AGT-05` | Symlink creation in repository | Law 6 (Canonical Singularity) | ZeroFilesystemAliasGate | Fail Closed (`ERR_SYMLINK_FORBIDDEN`) |
| `TC-AGT-06` | Cross-plane commit (`INT-*` modifying code roots) | Law 7 (Plane Separation) | PlaneSeparationGate | Fail Closed (`ERR_CROSS_PLANE_MUTATION`) |
| `TC-AGT-07` | Function returns hollow stub or skips test | Law 2 (Non-Verbal Proof) | AntiShimGate | Fail Closed (`ERR_TRIVIAL_PASS_SHIM`) |
| `TC-AGT-08` | Hardcoded forbidden path literal in source | Law 5 (Zero-Hardcoded-Path) | ForbiddenLiteralsGate | Fail Closed (`ERR_FORBIDDEN_LITERAL`) |
| `TC-AGT-09` | Configuration or contract drifts from Go SSoT | Law 4 (SSoT) | ContractParityGate / IntentParityGate | Fail Closed (`ERR_CONTRACT_DRIFT`) |

<!-- NOMOS_OPEN_CORE_PROTOCOL_END -->
