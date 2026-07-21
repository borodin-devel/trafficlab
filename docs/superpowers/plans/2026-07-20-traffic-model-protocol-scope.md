# Traffic-Model Protocol Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Layer 2 the explicit primary scope of Trafficlab traffic models.

**Architecture:** The traffic-model registry owns one concise protocol-scope policy. Individual model documents continue to own model behavior and do not duplicate the shared policy.

**Tech Stack:** Markdown, Git.

## Global Constraints

- Layer 2 frame timing and size are the primary traffic-model concern.
- IPv4, IPv6, TCP, and UDP are allowed only in explicitly advanced models.
- Application-layer and other higher-level protocol simulation is out of scope.
- Existing model schemas and behavior do not change.

---

### Task 1: Add the protocol-scope policy

**Files:**
- Modify: `architecture/traffic_models/README.md`

**Interfaces:**
- Consumes: the approved policy in `docs/superpowers/specs/2026-07-20-traffic-model-protocol-scope-design.md`.
- Produces: the authoritative protocol-depth boundary for all traffic models.

- [ ] **Step 1: Add `## Protocol Scope` after `## Role`**

  State that traffic models primarily simulate Layer 2 behavior, especially
  Ethernet frame timing and frame size. State that basic models remain entirely
  at Layer 2. State that only explicitly advanced models may add IPv4, IPv6,
  TCP, or UDP behavior. State that application-layer and other higher-level
  protocol simulation is outside Trafficlab's traffic-model scope.

- [ ] **Step 2: Validate the owner and scope**

  Run:

  ```bash
  git diff --check
  rg -n -- 'Protocol Scope|Layer 2|IPv4|IPv6|TCP|UDP|Application-layer|higher-level' architecture/traffic_models/README.md
  git status --short
  ```

  Expected: every approved boundary appears in the registry owner, no model
  schema changes exist, and there are no whitespace errors.

- [ ] **Step 3: Commit**

  ```bash
  git add architecture/traffic_models/README.md
  git commit -m "docs(architecture): clarify traffic-model scope"
  ```
