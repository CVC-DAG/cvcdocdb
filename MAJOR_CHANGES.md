# Major (breaking) changes

This branch accumulates changes that would require a **major** version
bump (a breaking change to the public API), kept separate from the
normal `develop` → `main` flow, which only ever carries backward-compatible
minor/patch work.

## Why a separate branch

Version numbers only change at the moment of an actual PyPI publish (see
`CLAUDE.md`'s branch strategy). Mixing a breaking change into `develop`
would force every subsequent minor/patch release to either carry that
breaking change along or require awkward cherry-picking to keep it out.
Keeping breaking changes isolated here means:

- `develop` → `main` releases stay backward-compatible (minor/patch) for
  as long as no major release is needed.
- Breaking changes accumulate here, reviewed and merged into `main` as a
  single deliberate major release when it's actually time to cut one —
  not by accident because they got mixed in with unrelated work.

## Workflow

- A change identified as breaking the public API is committed here
  instead of on `develop` (flagged and confirmed with the user first,
  never assumed automatically).
- Each breaking change gets an entry below, in the same spirit as
  `CHANGELOG.md`, describing what breaks and why.
- When ready to cut a major release: merge `develop` into `major_release`
  first (to include all the accumulated minor/patch work too), bump
  `VERSION` to the next major (`X.0.0`) **only when actually publishing**,
  merge into `main`, and publish.

## Pending major changes

### Neo4j: node and relation ids are `elementId()` strings, not `id()` integers

**What breaks.** On `Neo4jGraph`, every id cvcdocdb exposes is now the
`elementId()` string (e.g. `'4:3f2c…:12'`) instead of the `id()` integer:
the return value of `insertNode`/`insertRelation`, `get_node_ids()`,
`get_edges()`, `node.neo4j_id`, the `node_id` field of dict-filter `query()`
results, `get_node_attrs(node_id)`/`get_edge_attrs(...)` arguments, and the
ids `migrate()` reads from a Neo4j source. Code that stores these ids, does
arithmetic or comparisons on them (`id >= 0`), or passes them to hand-written
Cypher with `id(n) = $id` must switch to strings and `elementId(n) = $id`.
`NetworkXGraph` ids stay integers. In `cvcdocdb.torch_dataloader`,
`Data.node_ids` is a tensor for integer ids (NetworkX) and a plain list for
Neo4j ids (a tensor can't hold strings).

**Why.** `id()` is deprecated in Neo4j 5 (Neo4j logged a deprecation notice
per query: a ~100-node migration logged 479 of them) and ids from `id()` can
be reused after a node is deleted; `elementId()` is the supported,
stable-for-the-lifetime-of-the-element replacement. 1.x silences the notices
(`fix: filter Neo4j id() deprecation notices`); this removes their cause.

**Also in this change.** Ids that were concatenated into Cypher
(`"... WHERE id(a) = " + str(node_id)`) are now query parameters — with
string ids, concatenation would have been an injection risk. A propagated
(WeakNode) child was rebuilt with the driver's legacy integer `_id`; it now
uses `element_id`.

**Migrating to 2.0.** Treat Neo4j ids as opaque strings; replace `id(x)` by
`elementId(x)` in your own Cypher; don't persist 1.x integer ids expecting
them to still address the same elements.

### Neo4j: one pk shape per label, enforced by a database constraint

**What breaks.** `Neo4jGraph(..., pk_constraints=True)` is the new default:

- A label has **exactly one pk shape** (the set of pk property names). The
  first shape inserted for a label becomes its shape; inserting a node of
  that label with another shape (or with no pk, i.e. a backend-assigned id)
  raises `ValueError` *before* anything is written. In 1.x the same label
  could be used with several pk shapes (e.g. `User` by `email` and by
  `niu`).
- After each commit, a database-level pk constraint is created for every
  new label: `IS NODE KEY` (uniqueness + existence of the pk properties) on
  Enterprise, `IS UNIQUE` on Community (`cvcdocdb_pkc_<Label>`). Neo4j
  itself now rejects duplicates, also from writes that bypass cvcdocdb.
- This needs `CONSTRAINT MANAGEMENT` on the database. Without it the
  constraint isn't created and a `RuntimeWarning` is emitted, but the
  one-shape rule still applies.
- The shape rule holds across instances/processes: each label's shape is
  read from the database's `cvcdocdb_pkc_*` constraints.

**Why.** In 1.x uniqueness was only checked in Python (a `MATCH` before
each insert), so it didn't hold against concurrent writers or writes that
bypass cvcdocdb, and without an index every pk lookup scanned the whole
label. A constraint gives both: real uniqueness, enforced by Neo4j, and the
index it brings. It's only possible with a single pk shape per label, which
is why that rule comes with it.

**New API** (also added in this change):
`GraphStore.ensure_pk_constraints(pk_shapes=None)` (no-op on
`NetworkXGraph`) and `migrate(..., create_constraints=False)`
(`MigrationStats.pk_constraints_created`). A constraint replaces any
standalone pk index on the same label and properties (Neo4j refuses both),
and the index is restored if the constraint can't be created. Existing data
that violates the constraint (duplicates, missing pk properties, mixed
shapes) makes `ensure_pk_constraints` fail with a clear `ValueError`.

**Migrating to 2.0.**

1. Graphs whose labels use a single pk shape: nothing to change; grant the
   Neo4j user `CONSTRAINT MANAGEMENT` so the constraints get created.
2. Graphs with several pk shapes per label: either split them into
   different labels (e.g. `UserByEmail`/`UserByNiu`), or pass
   `pk_constraints=False` to keep the 1.x behaviour (then
   `auto_pk_indexes=True` gives the lookup indexes without constraints).
3. To add the constraints to an existing database:
   `graph.ensure_pk_constraints([(label, pk_props), ...])` — it reports any
   data that prevents them.

**Don't share a database between 1.x and 2.0 clients.** The 2.0
constraints pin each label to one pk shape for every client of that
database: a 1.x client (or test suite) that inserts another shape for the
same label then gets constraint violations, and 1.x `ensure_pk_indexes`
can't create an index on a schema that already has a constraint. Found
while testing: a 2.0 test run left constraints in a shared test database
and the 1.x suite failed on it. The 2.0 test suite now drops its
`cvcdocdb_pk*` constraints/indexes at session end (`conftest.py`), using the
real driver captured at import time (`test_drm.py` stubs `neo4j`).

**NetworkXGraph applies the same rule** (decided: backend parity is
cvcdocdb's core promise — the same code must behave the same on any
backend). `NetworkXGraph(..., pk_constraints=True)` is the new default too:
a second pk shape for a label raises the same `ValueError`, before anything
is written. With no database constraints to read from, each label's shape
is saved in the persistence file (`label_pk_shapes` in the pickle), so the
rule holds across instances and processes; a pre-2.0 pickle is rebuilt from
its nodes (each label's first real pk, skipping backend-assigned ids).
`pk_constraints=False` restores the 1.x behaviour. `ensure_pk_constraints`
stays a no-op there (the rule itself is the constraint). Same semantics on
both backends for nodes without a pk: they don't set a label's shape, but
are rejected on a label that has one; a `WeakNode` with a backend-assigned
id counts as "no pk" for its own label (not its parent's shape).

**Measured impact** (2026-09-27): cvcdocdb's own suite needed no test
changes for NetworkX (each test uses a fresh pickle); on Neo4j, the tests
had to reset the pk schema between tests. A real downstream graph checked
(the ABCDs app's, 27 labels) had no label with mixed pk shapes.
