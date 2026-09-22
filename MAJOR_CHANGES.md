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

_(none yet — this file will accumulate entries here as breaking changes
are identified and committed to this branch)_
