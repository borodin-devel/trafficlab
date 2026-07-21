# Architecture Root README Design

## Goal

Create the first authoritative document in a new, self-contained
`architecture/` corpus. The document establishes how architectural knowledge is
recorded and changed without defining the corpus's future structure or adopting
product decisions.

## Scope

The deliverable is `architecture/README.md`, written in English.

The README defines:

- the purpose and authority of `architecture/`;
- the README as the entry point and future ordered index;
- single-document ownership of architectural facts;
- relative references instead of duplicated definitions;
- the discipline for changing an owned fact; and
- the exceptional amendment process.

The README does not define future architecture areas, product components,
pipeline stages, contracts, implementations, or other architectural decisions.
It does not discuss sources outside the new architecture corpus.

## Document Structure

The README contains six concise sections:

1. **Purpose** establishes `architecture/` as the self-contained source of truth
   for project architecture.
2. **Authority** states that an architectural statement is authoritative only
   when recorded within the corpus.
3. **Reading** makes the README the initial entry point and says that an ordered
   index is added here as authoritative documents are created.
4. **Ownership and references** assigns every architectural fact one owner
   document and requires other documents to use brief context and relative
   links.
5. **Change discipline** requires reading and updating the owner, then checking
   affected references and consistency.
6. **Amendments** reserves `architecture/amendments/` for irreconcilable
   conflicts between authoritative sources.

No other architecture subdirectory is prescribed by this design.

## Change Flow

An ordinary architecture change follows this sequence:

1. Read this README and the document that owns the fact.
2. Change the owner document.
3. Update affected references.
4. Check the corpus for consistency and validate relative links.

If authoritative sources cannot be reconciled, the author creates an amendment
instead of silently choosing one source. An amendment must name the conflicting
sources, record the decision and rationale, define scope and consequences,
record alternatives, describe compatibility or migration effects, and state
its status, owner, and date. An amendment cannot substitute for missing
implementation work.

## Style

The README is concise, normative, and free of speculative examples. It uses
requirement language only for governance rules. It contains no links that do
not resolve when the file is created.

## Acceptance Criteria

- `architecture/README.md` exists and serves as the corpus entry point.
- It defines purpose, authority, reading, ownership, change, and amendment
  rules.
- It does not predefine future architecture areas.
- It does not discuss material outside the new architecture corpus.
- Every Markdown link resolves.
- Only intended files are changed.
