# Development and Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one authoritative development/platform document and link the capture architecture to it.

**Architecture:** `architecture/DEVELOPMENT.md` owns shared development and platform facts. The root index reads it after governance. Capture owns its runtime support boundary; preflight keeps readiness checks; script and network-workspace indexes link rather than repeat shared facts.

**Tech Stack:** Markdown, POSIX shell, Git

## Global Constraints

- Linux-based operating systems are the development target; WSL 2 is primary.
- No native Windows application or capture support is planned.
- Applications use Python 3.12, uv, pytest, pytest-cov, and ruff.
- Scripts use Bash; automated script tests use `tests/scripts/` without elevation or real-system mutation.
- All links are relative and resolve locally. Do not stage `.gitignore`.

---

### Task 1: Add the Development Owner and References

**Files:**

- Modify: `architecture/README.md`
- Create: `architecture/DEVELOPMENT.md`
- Modify: `architecture/apps/00_preflight/README.md`
- Modify: `architecture/apps/10_capture/README.md`
- Modify: `architecture/scripts/README.md`
- Modify: `docs/network_workspaces/README.md`

**Interfaces:**

- Consumes: `docs/superpowers/specs/2026-07-17-development-platform-design.md`.
- Produces: one owner for platform/toolchain facts and references from affected documents.

- [ ] **Step 1: Create the development owner**

Create `architecture/DEVELOPMENT.md` with sections `Purpose`, `Platform`,
`Application Development`, `Script Development`, and `Reading`. State the
exact global constraints and link to root governance.

- [ ] **Step 2: Update root navigation**

Insert [development environment](DEVELOPMENT.md) after root governance in the
reading order. In `## Layout`, state that `DEVELOPMENT.md` owns shared platform
and toolchain constraints.

- [ ] **Step 3: Add owner references**

Add a `Platform Boundary` section to capture: Linux target applications, WSL 2
primary, no Windows applications or native Windows traffic capture, and a link
to development. Add one reading/reference sentence to preflight. Add a
development-toolchain reference to the script index and network-workspace index
without copying toolchain details.

- [ ] **Step 4: Validate documents**

Run: `test -f architecture/DEVELOPMENT.md && rg -q '\[development environment\](DEVELOPMENT.md)' architecture/README.md && rg -q '\[development environment\](../../DEVELOPMENT.md)' architecture/apps/10_capture/README.md && rg -q '\[development environment\](../../DEVELOPMENT.md)' architecture/apps/00_preflight/README.md && rg -q '\[development environment\](../DEVELOPMENT.md)' architecture/scripts/README.md && rg -q '\[development environment\](../../architecture/DEVELOPMENT.md)' docs/network_workspaces/README.md`

Expected: exit 0.

Run: `rg -q 'Python 3.12, uv, pytest, pytest-cov, and ruff' architecture/DEVELOPMENT.md && rg -q 'Bash' architecture/DEVELOPMENT.md && rg -q 'Windows applications and native Windows traffic capture are unsupported' architecture/apps/10_capture/README.md && ! rg -n -e '[[:blank:]]+$' architecture docs/network_workspaces && git diff --check`

Expected: exit 0.

- [ ] **Step 5: Commit in two scopes**

Run: `git add docs/superpowers/plans/2026-07-17-development-platform.md && git commit -m "docs: plan development platform"`

Expected: a plan-only commit.

Run: `git add architecture/README.md architecture/DEVELOPMENT.md architecture/apps/00_preflight/README.md architecture/apps/10_capture/README.md architecture/scripts/README.md docs/network_workspaces/README.md && git commit -m "docs(architecture): define development platform"`

Expected: an architecture-documentation commit with `.gitignore` unstaged.
