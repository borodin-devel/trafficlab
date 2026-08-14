# Research Fitness Assessment Design

## Purpose

Assess the current Trafficlab repository against every independent criterion in
`architecture/RESEARCH_FITNESS_CRITERIA.md` and publish the results in
`docs/RESEARCH_FITNESS_ASSESSMENT.md`.

The assessment concerns only:

- scientific precision and correctness of each stage's results;
- configurability;
- scientific precision and correctness of the methods;
- robustness; and
- reproducibility.

It does not grade feature breadth, enterprise architecture, multi-user
operation, distributed execution, hosted deployment, generic security
hardening, or speculative infrastructure.

## Assessment boundary

The report records the exact Git commit assessed. Evidence created by the
assessment document itself is not treated as evidence about the product.

Each of the 37 criteria receives one independent grade:

- `dreadful`;
- `poor`;
- `partial`;
- `acceptable`; or
- `excellent`.

The assessment derives no overall grade, weighted score, numeric conversion, or
cross-criterion compensation. Section-level grade counts are permitted only as
a navigation summary.

## Evidence policy

Use four explicitly distinguished evidence classes.

### Source and mathematical evidence

- Compare implementation behavior with the authoritative architecture.
- Trace formulas, estimators, stochastic draws, aggregation, bounds, failure
  arbitration, publication, and lineage through source and direct tests.
- Compare declared scientific methods with their cited primary mathematical
  literature.
- Treat internal consistency as weaker evidence than an independent derivation,
  hand calculation, reference implementation, or controlled simulation.

### Fresh verification evidence

- Run locked dependency and lockfile checks.
- Run Ruff formatting and lint checks and strict Pyright.
- Run the bounded non-Docker branch-coverage gate.
- Run available bounded Docker and Internet tests serially.
- Run deterministic fixture checks and offline reconstruction checks.
- Run targeted hand-calculation, boundary, failure, and reproducibility tests
  when the broad gate does not directly evidence a criterion.
- Record exact commands, result counts, failures, coverage, and external
  availability without silently substituting old evidence.

### Audited retained evidence

- Strictly parse the checked Phase 7 prerequisite, configuration, result, and
  report artifacts.
- Recompute retained hashes and verify schema, artifact counts, run ordering,
  family and winner lineage, reproduction claims, and report arithmetic.
- Identify retained observations as historical evidence even when their files
  pass a fresh audit.
- Do not describe an audited retained experiment as a newly rerun experiment.

### Primary-literature evidence

- Use primary papers, standards, or authoritative specifications rather than
  secondary summaries where available.
- Cite the source that supports each material scientific-method conclusion.
- Separate a method's scientific limitations from implementation defects.
- Observe source quotation and copyright limits; summarize rather than reproduce
  substantial source text.

## Grading procedure

For every criterion:

1. collect the relevant evidence from all applicable classes;
2. compare the evidence with all five criterion-specific anchors;
3. assign the highest anchor whose complete requirements are supported;
4. cite the exact evidence supporting the grade;
5. explain the scientific or engineering reasoning concisely; and
6. state the principal missing or weaker evidence preventing the next grade.

Missing evidence limits a grade. Ordinary material failures within the declared
scope cannot be averaged away. Test quantity, code size, feature count, and
documentation volume do not independently raise a grade.

An external service or endpoint that is unavailable during fresh verification
is an evidence limitation, not automatically a product failure. A reproducible
product defect lowers only criteria it materially affects. Expected inability to
meet a criterion is reported directly rather than hidden by an unrelated strong
result.

## Assessment document

`docs/RESEARCH_FITNESS_ASSESSMENT.md` contains:

1. the assessed commit and date;
2. scope and grading rules;
3. an evidence inventory separating fresh, retained, source, and literature
   evidence;
4. section-level grade distributions without an aggregate grade;
5. all 37 criterion results in the same order as the rubric;
6. a concise cross-cutting limitations section; and
7. exact reproduction commands for fresh local evidence.

Each result uses this structure:

```text
### Criterion name

Grade: acceptable

Evidence:
- source or mathematical evidence
- fresh verification evidence
- retained real-study evidence, when relevant

Rationale:
Why the evidence satisfies the selected anchor.

Limitation:
The specific missing or weaker evidence that prevents the next grade.
```

Local references use repository-relative Markdown links. Internet references
link directly to primary sources. Evidence claims remain close to their source
rather than relying on one undifferentiated bibliography.

## Change boundary

The assessment work changes only
`docs/RESEARCH_FITNESS_ASSESSMENT.md` after this design is approved. It does not
modify production source, tests, fixtures, architecture, configuration, or
scientific artifacts. Diagnostic commands may create ignored caches or coverage
files, which must not be committed.

If verification exposes a defect, the assessment records it and grades the
affected criteria. Fixing the defect is a separate task and is not folded into
the assessment.

## Validation

Before committing the assessment:

- confirm all 37 rubric headings have exactly one grade;
- confirm every result has evidence, rationale, and limitation fields;
- confirm every grade is one of the five approved labels;
- confirm no overall grade or numerical score is present;
- validate repository-relative links and primary-source URLs;
- run Markdown and diff consistency checks used by the repository;
- verify the assessment is the only tracked change; and
- self-audit a sample from every category back to raw evidence before checking
  the complete set.

The assessment is complete only when every result is traceable to evidence and
the working tree is clean after its dedicated commit.
