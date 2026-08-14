# Genetic Operators Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace vague genetic reproduction names with exact configurable,
bounded, deterministic operators for all three MVP model families.

**Architecture:** Put per-family operator configuration in the system contract,
generic normalized-coordinate crossover/mutation and duplicate behavior in the
basic genetic strategy, and chromosome coordinate/repair ownership in each
traffic-model document. Preserve the existing population, selection, checkpoint,
fitness, and final-evaluation design.

**Tech Stack:** Markdown architecture contracts, real-coded genetic operators,
Python-style deterministic RNG ordering, three heterogeneous traffic-model
chromosomes, pytest unit and integration scopes

## Global Constraints

- Configure `crossover_probability`, `mutation_probability`, and
  `mutation_scale` separately for every enabled family.
- Validate finite probabilities in `[0, 1]` and finite mutation scale in
  `(0, 1]`.
- Default to `p_c = 0.9`, `p_m = 1 / d_f`, and `sigma = 0.1` for chromosome
  length `d_f`.
- Use no global or per-gene crossover/mutation settings.
- Require every continuous gene bound to satisfy finite `L < U`; logarithmic
  genes additionally require `L > 0`.
- Require integer bounds to be integers containing at least two values.
- Use uniform same-family crossover and normalized Gaussian mutation with
  reflection into `[0, 1]`.
- Use linear coordinates for `q1`, `q2`, and `alpha`; integer coordinates for
  `r`; logarithmic coordinates for all rate and timing multiplier genes.
- Never cross unlike model families; force mutation after a cross-family clone.
- Define duplicates by family plus exact repaired gene-tuple equality.
- Keep duplicate mutation bounded by the existing configured attempt count.
- Preserve fixed random-draw order and include all family operator settings in
  checkpoint resume compatibility.
- Keep invalid mathematical offspring at fitness `0`; infrastructure failures
  still abort fitting.
- Add no strategy, registry, plugin interface, schema version, adaptive operator,
  self-tuning gene, or parallel evaluator.
- Do not change selection, elitism, family champions, fitness, observation
  window, checkpoint cadence, or final-seed policy.

---

### Task 1: Define per-family operator configuration

**Files:**
- Modify: `architecture/SYSTEM.md:60-77`
- Modify: `architecture/SYSTEM.md:127-145`
- Modify: `architecture/SYSTEM.md:209-213`

**Interfaces:**
- Consumes: enabled family names and fixed chromosome lengths
- Produces: validated family operator values consumed by initialization,
  reproduction, duplicate retry, and checkpoint compatibility

- [ ] **Step 1: Replace ambiguous global operator settings**

In the experiment-configuration list, replace the existing population bullet
that names generic crossover and mutation with:

```text
- population size, generation count, tournament size, elitism, trial seeds,
  duplicate-mutation attempts, early stopping, and checkpoint behavior;
- per-enabled-family crossover probability, per-gene mutation probability, and
  normalized mutation scale.
```

Retain family-specific gene bounds as a separate bullet. State that there is no
global crossover/mutation setting and no per-gene operator setting.

- [ ] **Step 2: Define values, defaults, and TOML ownership**

After the configuration list, add:

```text
Each enabled family owns finite crossover_probability and
mutation_probability values in [0, 1], plus finite mutation_scale in (0, 1].
Missing values default to p_c = 0.9, p_m = 1 / d_f, and sigma = 0.1, where d_f
is the fixed chromosome length. An enabled family may override the three values
independently. Unknown family/setting names and settings for disabled families
are errors.
```

Use the exact runtime family names `poisson_empirical`, `markov_renewal`, and
`mmpp`. Do not add an example schema version or configuration file.

- [ ] **Step 3: Add bound and preflight validation**

Extend local preflight validation to require:

```text
- finite L < U for every continuous gene;
- L > 0 for logarithmic genes;
- integer L < U for r;
- valid family operator probabilities/scales after defaults.
```

State that all effective defaulted per-family values appear in the saved
`experiment.toml` snapshot and the injected Python API returns the same values.

- [ ] **Step 4: Align fairness and resume ownership**

In the research-interface paragraph, require every candidate of a family to use
that family's effective operator values. State that `checkpoint.json` stores
them as genetic settings and exact resume compatibility compares them.

Do not claim that different family operator settings make search effort equal;
this task only makes the selected policy explicit and reproducible.

- [ ] **Step 5: Verify the configuration contract**

Run:

```bash
rg -n \
  -e 'per-enabled-family|crossover_probability|mutation_probability' \
  -e 'mutation_scale|p_c = 0.9|p_m = 1 / d_f|sigma = 0.1' \
  -e 'poisson_empirical|markov_renewal|mmpp|disabled families' \
  -e 'finite `L < U`|logarithmic|integer.*`r`|checkpoint.*operator' \
  architecture/SYSTEM.md
! rg -n 'elitism, crossover,.*mutation|global crossover|per-gene operator setting is supported' \
  architecture/SYSTEM.md
git diff --check
```

Expected: one effective per-family configuration contract exists, global
operator ambiguity is absent, validation is explicit, and whitespace is clean.

- [ ] **Step 6: Commit configuration ownership**

```bash
git add architecture/SYSTEM.md
git commit -m "docs: configure family operators"
```

### Task 2: Define exact generic reproduction mathematics

**Files:**
- Modify: `architecture/genetic_models/basic_generational.md:23-78`
- Modify: `architecture/genetic_models/basic_generational.md:80-139`

**Interfaces:**
- Consumes: ordered chromosome values, per-family `p_c`, `p_m`, `sigma`, gene
  coordinate kinds/bounds, master RNG, and duplicate-attempt count
- Produces: initialized/repaired chromosomes and deterministic same-family,
  cross-family, mutation, duplicate, and checkpoint behavior

- [ ] **Step 1: Define bound validation and coordinate transforms**

Before the algorithm, add the exact generic coordinate contract:

```text
Every continuous gene has finite L < U; a log gene also has L > 0. An integer
gene has integer L < U. Linear genes use z = (x - L) / (U - L) and
x = L + z(U - L). Log genes use
z = (log x - log L) / (log U - log L) and
x = exp(log L + z(log U - log L)). Integer genes use the linear coordinate and
decode as L + floor(z(U - L) + 1/2).
```

Define initialization as `z ~ Uniform(0, 1)` for continuous/log genes and a
discrete uniform draw from `{L, ..., U}` for integer genes, followed by family
repair and validation.

- [ ] **Step 2: Define normalized reflection**

Publish:

```text
r = v mod 2, with r in [0, 2)
reflect(v) = r      for 0 <= r <= 1
             2 - r  for 1 < r < 2
```

State that mutation reflects rather than clamps, while defensive family repair
may clamp finite externally supplied/unselected values. Add examples:

```text
reflect(-0.2) = 0.2
reflect(1.2) = 0.8
reflect(2.2) = 0.2
```

- [ ] **Step 3: Replace vague same-family reproduction**

Define one child as:

```text
C ~ Bernoulli(p_c)
if C = 1:
    for each gene j in chromosome order:
        B_j ~ Bernoulli(1/2)
        child[j] = parent_a[j] if B_j = 0 else parent_b[j]
else:
    child = clone(fitter parent, stable-ID tie rule)

for each gene j in chromosome order:
    M_j ~ Bernoulli(p_m)
for each selected j in chromosome order:
    epsilon_j ~ Normal(0, sigma^2)
    z'_j = reflect(z_j + epsilon_j)
    child[j] = decode_j(z'_j)
repair_and_validate(child)
```

State that zero genes may mutate for a same-family child. Elites and family
champions copy without any operator draw.

- [ ] **Step 4: Define different-family mandatory mutation**

Retain fitter-parent cloning and no cross-family crossover. Apply the cloned
family's `p_m` and `sigma`. When no gene is selected, draw one uniform gene index
and mutate it.

For a mandatory integer mutation that decodes unchanged, move one step in the
Gaussian sign and reflect at the endpoint; exact zero uses the positive sign.
If a continuous result remains identical after repair, duplicate handling owns
the next forced mutation.

- [ ] **Step 5: Make repair failure behavior explicit**

Replace “reject unrepaired candidates” with:

```text
Family repair consumes no RNG. A valid repair returns the canonical ordered gene
tuple. Failed repair leaves the individual in the population as an invalid
candidate with fitness 0 and a reason. It is not silently redrawn.
```

Keep infrastructure failures distinct from mathematical invalidity.

- [ ] **Step 6: Define exact duplicate retry**

Replace the existing duplicate paragraph with:

```text
A duplicate has the same family and exact numeric equality of its repaired gene
tuple with a survivor or accepted child. For each bounded attempt, apply normal
mutation; force one uniform gene if none is selected; repair; accept the first
valid distinct tuple. An invalid attempt keeps the last valid duplicate as the
next base. Exhaustion retains the original valid duplicate and population size.
Invalid children skip duplicate handling.
```

State that equality uses no tolerance and no generated-output comparison.

- [ ] **Step 7: Fix RNG and checkpoint ordering**

Add this conditional draw order:

```text
1. same-family crossover decision;
2. parent choice for every gene when crossover occurs;
3. mutation-selection decision for every gene;
4. Gaussian draw for every selected gene;
5. forced-gene index and its Gaussian draw when required;
6. duplicate attempts repeating steps 3-5.
```

Steps that do not apply consume no draw; repair consumes none. Resume agreement
must include family operator values, bounds, coordinate kinds, and chromosome
order in addition to the existing settings and RNG state.

- [ ] **Step 8: Update Trafficlab choices, cost, and deterministic examples**

Name uniform crossover, transformed Gaussian mutation, reflection, fixed draw
order, exact duplicate identity, and family-specific operator values as
Trafficlab engineering definitions. State that reproduction remains `O(d)` per
child, excluding its already bounded duplicate attempts.

Add exact examples for probability `0`/`1`, all three reflection values, fitter
clone, uniform parent choices, forced mutation, duplicate exhaustion, and resumed
offspring equality.

- [ ] **Step 9: Verify the generic operator contract**

Run:

```bash
rg -n \
  -e 'z = .*x - L|log x - log L|floor.*1/2|Uniform' \
  -e 'reflect\(v\)|reflect\(-0.2\)|Bernoulli\(p_c\)|Bernoulli\(p_m\)' \
  -e 'Normal\(0, sigma\^2\)|zero genes|forced-gene|exact numeric equality' \
  -e 'fitness `0`|consumes no.*draw|coordinate kinds|O\(d\)' \
  architecture/genetic_models/basic_generational.md
! rg -n \
  'Each family defines bounded crossover and mutation|reject unrepaired candidates' \
  architecture/genetic_models/basic_generational.md
git diff --check
```

Expected: every generic operator, bound, failure, duplicate, and RNG rule is
executable from the document with no vague family-delegated algorithm remaining.

- [ ] **Step 10: Commit generic reproduction**

```bash
git add architecture/genetic_models/basic_generational.md
git commit -m "docs: define genetic reproduction"
```

### Task 3: Map and repair every family chromosome

**Files:**
- Modify: `architecture/traffic_models/poisson_empirical.md:30-42`
- Modify: `architecture/traffic_models/poisson_empirical.md:58-84`
- Modify: `architecture/traffic_models/markov_renewal.md:45-74`
- Modify: `architecture/traffic_models/markov_renewal.md:92-117`
- Modify: `architecture/traffic_models/mmpp.md:35-44`
- Modify: `architecture/traffic_models/mmpp.md:70-95`

**Interfaces:**
- Consumes: generic coordinate/repair contract from Task 2 and configured family
  gene bounds/operator values from Task 1
- Produces: fixed chromosome order, coordinate kind, and deterministic family
  repair used by initialization, reproduction, serialization, and resume

- [ ] **Step 1: Define Poisson mapping and repair**

State that canonical chromosome order is `(c_lambda)`, that `c_lambda` uses the
logarithmic coordinate, and its bounds are finite with `0 < L < U`. Repair
rejects nonfinite input, defensively clamps to the configured positive bounds,
and validates positivity.

Add deterministic checks for log encode/decode endpoints and repair at each
bound. Preserve the rate estimator and generation mathematics unchanged.

- [ ] **Step 2: Define Markov Renewal mapping and repair**

State canonical order and coordinate kind exactly:

```text
(q1: linear, q2: linear, alpha: linear, r: integer, c_t: logarithmic)
```

Define repair in this order:

```text
reject nonfinite values;
decode/round r and clamp every value to its named bound;
sort q1 and q2;
validate each named bound and 0 < q1 < q2 < 1;
validate alpha >= 0, integer r >= 1, and c_t > 0;
derive reference quantile thresholds and reject equality.
```

Do not jitter equal quantiles or thresholds. Add tests for reversed quantiles,
rounding, bound failure, equality, and distinct data thresholds.

- [ ] **Step 3: Define MMPP mapping and repair**

State canonical order as `(q01, q10, lambda0, lambda1)` and mark all four genes
logarithmic with finite positive bounds.

Define repair in this order:

```text
reject nonfinite values;
clamp every value to its named positive bound;
leave q01 and q10 in their named positions;
sort lambda0 and lambda1;
validate named bounds and 0 < lambda0 < lambda1.
```

Do not swap `q01`/`q10` or jitter equal arrival rates. Add deterministic cases
for named transition rates, reversed arrival rates, bounds, and equality.

- [ ] **Step 4: Verify all mappings and invariants**

Run:

```bash
rg -n 'canonical chromosome order|logarithmic coordinate|0 < L < U|defensively clamps' \
  architecture/traffic_models/poisson_empirical.md
rg -n \
  -e 'q1: linear.*q2: linear.*alpha: linear.*r: integer.*c_t: logarithmic' \
  -e 'sort.*q1.*q2|0 < q1 < q2 < 1|integer.*r|thresholds.*equal' \
  architecture/traffic_models/markov_renewal.md
rg -n \
  -e 'q01, q10, lambda0, lambda1|all four.*logarithmic' \
  -e 'leave.*q01.*q10|sort.*lambda0.*lambda1|0 < lambda0 < lambda1|jitter' \
  architecture/traffic_models/mmpp.md
git diff --check
```

Expected: every gene has one coordinate and one ordered repair, while all model
fit/generation mathematics remain unchanged.

- [ ] **Step 5: Commit family mappings**

```bash
git add architecture/traffic_models/
git commit -m "docs: map model gene operators"
```

### Task 4: Add operator evidence to testing and roadmap

**Files:**
- Modify: `architecture/TESTING.md:59-145`
- Modify: `architecture/ROADMAP.md:18-62`
- Modify: `architecture/ROADMAP.md:180-215`

**Interfaces:**
- Consumes: configuration, generic operator, and family mapping contracts from
  Tasks 1-3
- Produces: focused unit/integration evidence and implementation phase ownership

- [ ] **Step 1: Add configuration and mathematical unit evidence**

Extend unit coverage with exact checks for:

```text
per-family defaults/overrides; invalid probability, scale, and bounds; unknown
or disabled family settings; linear/log/integer encode-decode; initialization;
reflection -0.2 -> 0.2, 1.2 -> 0.8, 2.2 -> 0.2; crossover and mutation
probability endpoints; fixed RNG order; all three family repairs.
```

Add duplicate identity, forced mutation, invalid attempt, bounded exhaustion,
and population-size preservation. Retain the existing requirement that a failed
unit-test function receives 100% targeted line and branch coverage after repair.

- [ ] **Step 2: Strengthen heterogeneous integration and resume evidence**

Require one small three-family population with nondefault operator settings. It
must prove same-family-only crossover, use of the child's family settings,
cross-family forced mutation, and representation of every family.

Extend resume integration to require the same next repaired gene tuples, child
IDs, RNG state, history, and winner as uninterrupted execution. Change one family
operator value and require resume compatibility failure.

- [ ] **Step 3: Assign configuration to Phase 1**

In Phase 1, add focused per-family operator values/defaults to configuration
deliverables and add valid/invalid/default/unknown/disabled-family cases to TOML
unit tests. Keep config-only preflight free of Docker.

- [ ] **Step 4: Assign mathematics to Phase 5**

Replace the vague crossover/mutation deliverable with exact uniform crossover,
transformed Gaussian mutation, reflection, family mappings, repair, exact
duplicates, and fixed RNG draw order. Require the unit/integration/resume evidence
from Steps 1-2 in Phase 5 tests.

Do not add a phase or move model fitting, generation, or capture work.

- [ ] **Step 5: Verify testing and roadmap ownership**

Run:

```bash
rg -n \
  -e 'per-family.*operator|linear/log/integer|reflection.*-0.2' \
  -e 'probability endpoints|fixed RNG|duplicate.*exhaustion' \
  -e 'nondefault operator|cross-family forced|operator value.*resume' \
  architecture/TESTING.md
rg -n \
  -e 'Phase 1|per-family operator|defaults|disabled-family' \
  -e 'Phase 5|uniform crossover|Gaussian mutation|reflection|fixed RNG' \
  -e 'exact duplicate|nondefault operator|resume compatibility' \
  architecture/ROADMAP.md
test "$(rg -c '^## Phase [1-7]' architecture/ROADMAP.md)" = "7"
git diff --check
```

Expected: configuration lands in Phase 1, operator implementation/evidence lands
in Phase 5, exactly seven phases remain, and documentation is whitespace-clean.

- [ ] **Step 6: Commit evidence ownership**

```bash
git add architecture/TESTING.md architecture/ROADMAP.md
git commit -m "docs: test genetic operators"
```

### Task 5: Verify genetic-operator consistency across the architecture

**Files:**
- Verify: `architecture/SYSTEM.md`
- Verify: `architecture/genetic_models/`
- Verify: `architecture/traffic_models/`
- Verify: `architecture/TESTING.md`
- Verify: `architecture/ROADMAP.md`

**Interfaces:**
- Consumes: all documentation outputs from Tasks 1-4
- Produces: evidence that configuration, generic math, family repair, tests, and
  roadmap agree without expanding MVP scope

- [ ] **Step 1: Verify links and stable architecture counts**

Run:

```bash
uv run --locked python - <<'PY'
from pathlib import Path
import re

missing = []
for document in Path("architecture").rglob("*.md"):
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", document.read_text()):
        if "://" in target or target.startswith("#"):
            continue
        path = (document.parent / target.split("#", 1)[0]).resolve()
        if not path.exists():
            missing.append(f"{document}: {target}")
assert not missing, "\n".join(missing)

assert len(re.findall(r"^\| \[", Path("architecture/traffic_models/README.md").read_text(), re.MULTILINE)) == 3
assert len(re.findall(r"^\| \[", Path("architecture/similarity_methods/README.md").read_text(), re.MULTILINE)) == 4
assert len(re.findall(r"^- \[", Path("architecture/genetic_models/README.md").read_text(), re.MULTILINE)) == 1
assert len(re.findall(r"^## Phase [1-7]", Path("architecture/ROADMAP.md").read_text(), re.MULTILINE)) == 7
PY
```

Expected: links resolve and the MVP remains three models, four similarity
methods, one genetic strategy, and seven phases.

- [ ] **Step 2: Verify the complete operator chain**

Run:

```bash
rg -n 'crossover_probability|mutation_probability|mutation_scale|p_m = 1 / d_f' \
  architecture/SYSTEM.md
rg -n \
  -e 'Uniform\(0, 1\)|reflect\(v\)|Bernoulli\(p_c\)|Normal\(0, sigma\^2\)' \
  -e 'exact numeric equality|forced-gene|consume no random draw|coordinate kinds' \
  architecture/genetic_models/basic_generational.md
rg -n 'canonical chromosome order|linear|integer|logarithmic' \
  architecture/traffic_models/*.md
rg -n 'nondefault operator|cross-family forced|resume compatibility' \
  architecture/TESTING.md architecture/ROADMAP.md
```

Expected: settings flow through generic operators, family mappings, and evidence.

- [ ] **Step 3: Verify vague or expanded scope is absent**

Run:

```bash
! rg -n \
  -e 'Each family defines bounded crossover and mutation' \
  -e 'elitism, crossover,.*mutation' \
  -e 'adaptive mutation|self-tuning|per-gene operator settings|operator registry' \
  architecture
git diff --check HEAD~4..HEAD
git status --short
```

Expected: no undefined operator delegation or speculative operator framework
remains, the four implementation commits are clean, and the worktree is clean.

- [ ] **Step 4: Record only necessary consistency corrections**

If Steps 1-3 find a mismatch, make the smallest correction in the owning
architecture document, rerun all checks, and commit it:

```bash
git add architecture/
git commit -m "docs: fix genetic-operator consistency"
```

If all checks pass without changes, do not create an empty commit.
