# Application Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document and scaffold optional, independent TOML configuration for the three existing Trafficlab applications.

**Architecture:** Add one common architecture owner for selection, precedence, validation, and `launch.toml`. Keep application-specific settings with their applications; establish empty versioned templates, ignored local working copies, and separate user-facing descriptions without inventing settings.

**Tech Stack:** Markdown, TOML, Git.

## Global Constraints

- Application configuration is optional and independent: no application reads another application's configuration.
- Selection arguments are mutually exclusive: `--config-file PATH` or `--config-dir DIRECTORY`.
- No selection argument means built-in defaults only; never search `configs/` or `.configs/` implicitly.
- Resolution order is exactly built-in default, selected TOML file, then explicit command-line argument.
- There is no environment-variable configuration layer.
- A selected missing file, malformed TOML, unknown setting, or invalid value stops work before it starts.
- Each successful application artifact includes its complete resolved `launch.toml`.
- `configs/` is versioned; `.configs/` working files are ignored without changing the pre-existing root `.gitignore`.
- Configuration field definitions remain empty until their owning application defines them.

---

### Task 1: Establish the common configuration architecture owner

**Files:**
- Create: `architecture/CONFIGURATION.md`
- Modify: `architecture/README.md`

**Interfaces:**
- Consumes: the approved design in `docs/superpowers/specs/2026-07-17-application-configuration-design.md`.
- Produces: the authoritative shared rules that all application documents reference.

- [ ] **Step 1: Create `architecture/CONFIGURATION.md`**

  State the common ownership boundary and include these exact rules:

  ```text
  --config-file PATH
  --config-dir DIRECTORY
  built-in default -> selected config file -> explicit command-line argument
  launch.toml
  ```

  Define selection as mutually exclusive; `--config-file` as an exact path;
  and `--config-dir` as the application’s numbered file in that directory.
  State defaults-only behavior when both options are absent, strict pre-work
  validation, no environment-variable layer, independent application files,
  and the future-secret rule.

- [ ] **Step 2: Add the common owner to the reading order**

  In `architecture/README.md`, place a relative link to `CONFIGURATION.md`
  immediately after `DEVELOPMENT.md`, then renumber the existing application
  and script entries. Add a layout sentence that assigns shared configuration
  behavior to `CONFIGURATION.md` and individual setting ownership to the
  relevant application document.

- [ ] **Step 3: Validate Task 1 documentation**

  Run:

  ```bash
  git diff --check
  rg -n -- '--config-file|--config-dir|launch\.toml|environment-variable' architecture/CONFIGURATION.md architecture/README.md
  ```

  Expected: no whitespace errors; each shared rule is present once in the
  common owner and the reading order links to it.

- [ ] **Step 4: Commit Task 1**

  ```bash
  git add architecture/README.md architecture/CONFIGURATION.md
  git commit -m "docs(architecture): define application configuration"
  ```

### Task 2: Connect the application owners to the shared rule

**Files:**
- Modify: `architecture/apps/00_preflight/README.md`
- Modify: `architecture/apps/10_capture/README.md`
- Modify: `architecture/apps/20_convert/README.md`
- Modify: `architecture/contracts/10_20_capture_directions/README.md`

**Interfaces:**
- Consumes: `architecture/CONFIGURATION.md` from Task 1.
- Produces: application documents that state their individual numbered
  configuration identities without duplicating shared selection rules.

- [ ] **Step 1: Add one configuration boundary to each application document**

  Add a `## Configuration` section to each file. Give the app’s exact
  template name—`00_preflight.toml`, `10_capture.toml`, or
  `20_convert.toml`—and say that its settings, when introduced, belong to
  that application only. Link relatively to `../../CONFIGURATION.md`.

  State that `00_preflight` does not read `10_capture` configuration. State
  that each successful application result includes the shared `launch.toml`
  snapshot. Do not add any setting names or duplicate precedence rules.

- [ ] **Step 2: Remove superseded deferrals precisely**

  In `00_preflight`, replace the general deferred “configuration values”
  phrase with a statement that concrete preflight settings are not yet
  defined. In `10_capture`, replace the general deferred “configuration
  schema” phrase with the same scoped statement for capture. In `20_convert`,
  preserve the separate decision that the reference-profile field
  serialization is not yet defined; the shared TOML selection mechanism does
  not decide profile fields.

- [ ] **Step 3: Add the conversion run snapshot to its published contract**

  In `architecture/contracts/10_20_capture_directions/README.md`, add
  `launch.toml` to the fixed successful package layout and state that it is
  the complete effective configuration snapshot owned by
  `architecture/CONFIGURATION.md`. The conversion package is no longer
  described as containing only five files. Do not add configuration fields to
  the contract.

- [ ] **Step 4: Validate application references and scope**

  Run:

  ```bash
  rg -n -- 'configuration\.md|00_preflight\.toml|10_capture\.toml|20_convert\.toml|launch\.toml' architecture/apps architecture/contracts/10_20_capture_directions
  rg -n -- 'config-file|config-dir|built-in default' architecture/apps
  ```

  Expected: every application references the common owner and its own file;
  no application duplicates the shared CLI mechanism or precedence string.

- [ ] **Step 5: Commit Task 2**

  ```bash
  git add architecture/apps/00_preflight/README.md architecture/apps/10_capture/README.md architecture/apps/20_convert/README.md architecture/contracts/10_20_capture_directions/README.md
  git commit -m "docs(architecture): connect app configurations"
  ```

### Task 3: Add empty templates, ignored working copies, and descriptions

**Files:**
- Create: `configs/00_preflight.toml`
- Create: `configs/10_capture.toml`
- Create: `configs/20_convert.toml`
- Create: `.configs/.gitignore`
- Create: `docs/configs/README.md`
- Create: `docs/configs/00_preflight.md`
- Create: `docs/configs/10_capture.md`
- Create: `docs/configs/20_convert.md`

**Interfaces:**
- Consumes: shared rules from `architecture/CONFIGURATION.md` and each
  application’s numbered configuration identity from Task 2.
- Produces: valid empty TOML templates; a local-only working-config location;
  and descriptions that can gain fields when their application does.

- [ ] **Step 1: Create the three valid empty templates**

  Make each TOML file contain only this kind of explanatory comment, with its
  own application name:

  ```toml
  # No settings are defined for 10_capture yet.
  ```

  Do not place defaults, example settings, or placeholder keys in the files.

- [ ] **Step 2: Protect only local working configuration**

  Create `.configs/.gitignore` with:

  ```gitignore
  *
  !.gitignore
  ```

  This ignores every local working config while tracking only the narrow rule;
  do not modify the pre-existing root `.gitignore`.

- [ ] **Step 3: Write the configuration description index**

  In `docs/configs/README.md`, link to the common architecture owner and list
  the three description files with their matching template names. State that
  these files explain individual settings and do not replace the architecture
  rules.

- [ ] **Step 4: Write one focused description per app**

  In every numbered Markdown file, state its matching `configs/` template,
  the corresponding `.configs/` working-copy location, and that there are no
  settings defined yet. State that future fields must include type, accepted
  values, built-in default, and matching command-line override. Link to the
  application owner and the common configuration owner using relative links.

- [ ] **Step 5: Validate templates, ignore behavior, and links**

  Run:

  ```bash
  git check-ignore -v .configs/10_capture.toml
  for file in configs/*.toml; do python3.12 -c "import tomllib, pathlib; tomllib.loads(pathlib.Path('$file').read_text()); print('$file: valid')"; done
  git diff --check
  rg -n -- 'No settings are defined|configuration\.md|10_capture\.toml' configs docs/configs
  ```

  Expected: Git reports `.configs/.gitignore` as the ignore source; all three
  templates parse as TOML; no whitespace errors; all documentation points to
  the shared owner.

- [ ] **Step 6: Commit Task 3**

  ```bash
  git add .configs/.gitignore configs docs/configs
  git commit -m "docs: scaffold application configuration files"
  ```

### Task 4: Perform final architecture consistency validation

**Files:**
- Verify: `architecture/README.md`
- Verify: `architecture/CONFIGURATION.md`
- Verify: `architecture/apps/00_preflight/README.md`
- Verify: `architecture/apps/10_capture/README.md`
- Verify: `architecture/apps/20_convert/README.md`
- Verify: `configs/`
- Verify: `docs/configs/`

**Interfaces:**
- Consumes: all documentation and templates from Tasks 1–3.
- Produces: evidence that the delivered architecture contains one consistent
  configuration mechanism and no accidental changes to the root ignore file.

- [ ] **Step 1: Check the intended change set**

  Run:

  ```bash
  git diff --check HEAD~3..HEAD
  git status --short
  git diff -- .gitignore
  ```

  Expected: no whitespace errors; the pre-existing untracked `.gitignore`
  remains the only unrelated item; no root `.gitignore` diff is present.

- [ ] **Step 2: Check cross-document consistency**

  Run:

  ```bash
  rg -n -- 'configuration schema|configuration serialization|config-file|config-dir|launch\.toml' architecture
  rg -n -- '\.configs/' architecture/CONFIGURATION.md docs/configs
  ```

  Expected: shared CLI behavior appears in only the common owner; the convert
  reference-profile field serialization remains explicitly deferred; local
  working-copy locations are documented consistently.

- [ ] **Step 3: Report completion**

  Report the created common owner, templates, descriptions, validation
  results, and commits. Explicitly say that no application code, capture
  workspace, or root `.gitignore` was changed.
