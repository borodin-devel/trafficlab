# Capture Workspace Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document the full dedicated-workspace architecture for `10_capture`, preflight, manual scripts, and the host-network alternative.

**Architecture:** The authoritative corpus describes the active capture boundary, its read-only gate, and each operator script. `docs/network_workspaces/` contains detailed, non-authoritative design material for the active dedicated workspace and the inactive host-network alternative.

**Tech Stack:** Markdown, POSIX shell, Git

## Global Constraints

- Current approved decisions take precedence; the old draft is reference material only.
- Normal Trafficlab commands, capture, invoker, target tree, and automated tests never invoke `sudo` or run as root.
- Manual setup and rollback are the only privileged workspace operations; temporary and reboot-cleared changes are preferred.
- One workspace captures one target application tree at a time.
- The active backend is dedicated workspace; host-network remains inactive.
- Successful capture publishes closed, validated `raw/target.pcapng`.
- Capture defaults to 256 bytes per packet; full payload is explicit configuration.
- All Markdown links are relative and resolve locally. Do not stage `.gitignore`.

---

## File Structure

- Modify: `architecture/README.md`
- Modify: `architecture/apps/00_preflight/README.md`
- Modify: `architecture/apps/10_capture/README.md`
- Create: `architecture/scripts/README.md`
- Create: `architecture/scripts/setup_capture_workspace.md`
- Create: `architecture/scripts/rollback_capture_workspace.md`
- Create: `architecture/scripts/verify_capture_workspace.md`
- Create: `architecture/scripts/backup_system_configuration.md`
- Create: `architecture/scripts/workspace_orchestration.md`
- Create: `docs/network_workspaces/README.md`
- Create: `docs/network_workspaces/dedicated_workspace.md`
- Create: `docs/network_workspaces/host_network.md`

### Task 1: Establish the Capture and Preflight Owners

**Files:**

- Modify: `architecture/README.md`
- Modify: `architecture/apps/00_preflight/README.md`
- Modify: `architecture/apps/10_capture/README.md`

**Interfaces:**

- Consumes: `docs/superpowers/specs/2026-07-17-capture-workspace-design.md`.
- Produces: the authoritative active capture boundary, its readiness gate, and script reading path.

- [ ] **Step 1: Update root layout and reading order**

Add `scripts/<name>.md` to `## Layout`, stating that each document owns one manual workspace script's behavior and privilege boundary. Order reading as root governance, [00 preflight](apps/00_preflight/README.md), [10 capture](apps/10_capture/README.md), [workspace scripts](scripts/README.md), then relevant contracts and models.

- [ ] **Step 2: Expand preflight readiness**

Keep its existing read-only rule. Define a capture-preparation request and a readiness decision that checks workspace identity/ownership/state, invoker health, unprivileged recorder access, tools/resources, current configuration, network/DNS support, requested bridges, packet-length policy, and storage. State that it never runs scripts, repairs the environment, or escalates privilege.

- [ ] **Step 3: Replace the capture stub**

Use these sections: `Role`, `Decision Scope`, `Inputs`, `Dedicated Workspace Backend`, `Workspace Invoker`, `Capture Lifecycle`, `Output and Publication`, `Data Handling`, `Failure and Workspace State`, `Security and Privilege Boundary`, `Deferred Decisions`, and `Reading`.

Document one normal-user invoker and exclusive target-tree slot; argument-vector commands; pseudoterminal bridging for interactive targets; recorder readiness before target launch; no runtime privilege escalation; workspace loopback capture; explicit bridges to host services; normal outbound access; `raw/target.pcapng`; atomic validation/publication; 256-byte default; full-payload opt-in; independent target/capture status; and ready/busy/unhealthy workspace states. Link to preflight, script index, and root governance only.

- [ ] **Step 4: Verify first task**

Run: `rg -q '\[00 preflight application\]\(apps/00_preflight/README.md\)' architecture/README.md`

Expected: exit 0.

Run: `rg -q '\[workspace scripts\]\(scripts/README.md\)' architecture/README.md && rg -q 'raw/target.pcapng' architecture/apps/10_capture/README.md && rg -q '256 bytes' architecture/apps/10_capture/README.md`

Expected: exit 0.

### Task 2: Document Manual Workspace Scripts

**Files:**

- Create: every file under `architecture/scripts/` listed in File Structure.

**Interfaces:**

- Consumes: the workspace and privilege requirements from the capture owner.
- Produces: one owner document for every script in `scripts/`.

- [ ] **Step 1: Create the script index**

Map every script to its owner and privilege level. State exactly: `Normal Trafficlab commands never invoke these scripts.` Require each owner to define inputs, authority, effects, manifest behavior, failure handling, rollback, and tests.

- [ ] **Step 2: Document setup and rollback**

Setup is manual privileged transaction: validate workspace identity, display its plan plus current/proposed durable configuration, make a manifest before mutation, create only workspace-owned resources, start the invoker as the ordinary user, and yield ready or diagnosable failed state.

Rollback is manual privileged manifest-scoped reverse-order teardown: stop invoker, remove only named resources, restore recorded durable changes, and be idempotent. It never performs global cleanup.

- [ ] **Step 3: Document verify, backup, and orchestration**

Verify is read-only: report identity, manifest consistency, ownership, invoker health, state, and detectable drift.

Backup is conditional: snapshot only durable values named in an approved setup plan, display original/proposed values, record restoration instructions, and perform no system mutation.

Orchestration is a manually invoked CLI/interactive entry point that displays the plan and required privilege and may sequence backup, setup, verify, and rollback. It is never called by normal runtime or tests.

- [ ] **Step 4: Verify script documents**

Run: `test -f architecture/scripts/README.md && test -f architecture/scripts/setup_capture_workspace.md && test -f architecture/scripts/rollback_capture_workspace.md && test -f architecture/scripts/verify_capture_workspace.md && test -f architecture/scripts/backup_system_configuration.md && test -f architecture/scripts/workspace_orchestration.md`

Expected: exit 0.

Run: `rg -q 'Normal Trafficlab commands never invoke these scripts' architecture/scripts/README.md && rg -q 'idempotent' architecture/scripts/rollback_capture_workspace.md && rg -q 'read-only' architecture/scripts/verify_capture_workspace.md && rg -q 'no system mutation' architecture/scripts/backup_system_configuration.md`

Expected: exit 0.

### Task 3: Describe Both Network Workspaces

**Files:**

- Create: `docs/network_workspaces/README.md`
- Create: `docs/network_workspaces/dedicated_workspace.md`
- Create: `docs/network_workspaces/host_network.md`

**Interfaces:**

- Consumes: the active-backend decision in `architecture/apps/10_capture/README.md`.
- Produces: detailed, non-authoritative backend design material linked to its owner.

- [ ] **Step 1: Create the index**

Link to [capture architecture](../../architecture/apps/10_capture/README.md). Mark the dedicated document active/default and host-network documented/inactive. State that architecture owns decisions and these documents elaborate implementation trade-offs.

- [ ] **Step 2: Describe dedicated workspace**

Describe its one prepared slot, normal-user invoker, external/loopback capture points, outbound access, bridge-only host-local access, pseudoterminal relay, ready/busy/unhealthy state, recorder-before-target sequence, temporary-resource preference, and manifest-scoped setup/rollback. State that normal capture never escalates privilege and the canonical result is the validated PCAPNG file.

- [ ] **Step 3: Describe host-network alternative**

Describe preserved host `localhost`, addresses, VPNs, and services. Explain its need for pre-installed process-tree attribution, physical plus loopback capture, proof excluding unrelated traffic, and strong privacy checks. Mark it `Inactive alternative`, forbid use by active capture, and require a `separate future architecture decision` before implementation.

- [ ] **Step 4: Verify workspace documents**

Run: `test -f docs/network_workspaces/README.md && test -f docs/network_workspaces/dedicated_workspace.md && test -f docs/network_workspaces/host_network.md && rg -q '\[capture architecture\](../../architecture/apps/10_capture/README.md)' docs/network_workspaces/README.md && rg -q 'Active default' docs/network_workspaces/dedicated_workspace.md && rg -q 'Inactive alternative' docs/network_workspaces/host_network.md`

Expected: exit 0.

### Task 4: Validate and Commit Documentation

**Files:**

- Modify/Create: all architecture and workspace documents above.

**Interfaces:**

- Consumes: completed Tasks 1–3.
- Produces: consistent committed documentation, excluding `.gitignore`.

- [ ] **Step 1: Validate the corpus**

Run: `! rg -ni 'draft|version_1' architecture docs/network_workspaces && ! rg -ni 'sudo' architecture/apps/10_capture architecture/apps/00_preflight && ! rg -n -e '[[:blank:]]+$' architecture docs/network_workspaces && git diff --check`

Expected: exit 0. This proves active runtime documents neither defer to the old draft nor prescribe privileged normal execution.

- [ ] **Step 2: Review exact scope**

Run: `git diff -- architecture docs/network_workspaces && git status --short`

Expected: only planned documentation and the plan itself appear; `.gitignore` remains untracked.

- [ ] **Step 3: Commit in two scopes**

Run: `git add docs/superpowers/plans/2026-07-17-capture-workspace-architecture.md && git commit -m "docs: plan capture workspace architecture"`

Expected: one plan-only commit.

Run: `git add architecture/README.md architecture/apps/00_preflight/README.md architecture/apps/10_capture/README.md architecture/scripts docs/network_workspaces && git commit -m "docs(architecture): define capture workspace"`

Expected: one architecture-documentation commit, with `.gitignore` unstaged.

