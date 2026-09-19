# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Branch Strategy

**Always work on the `develop` branch unless explicitly told otherwise.**

- **`develop`** — Active development branch. All substantial changes, new features, refactors, and bug fixes go here.
- **`main`** — Release branch. Only minor changes for preparing releases (version bumps, changelog entries, doc links). Never add features or make structural changes here.

Before starting any work, check the current branch:
```bash
git branch --show-current
```
If on `main`, switch to `develop` first:
```bash
git checkout develop
```

### New Feature Branch Strategy

When the user indicates they want to add a **new feature**, follow these steps:

1. **Switch to `develop`** — ensure you're on the development branch.
2. **Check for uncommitted changes** — run `git status`.
   - If there are **uncommitted changes** on `develop` that could interfere with the new feature, **propose creating a dedicated feature branch**: `feature/<name>` (e.g., `feature/ontology-import`). Ask the user for confirmation before creating it.
   - If there are **no conflicting changes**, proceed directly on `develop`.
3. **If no uncommitted changes exist** but the user has work-in-progress from previous sessions that is not yet committed, ask about it before proceeding.

This ensures new features are developed in isolation when there are active changes on `develop` that might conflict.

### Parallel sessions — use a worktree, not just a branch

If the user says another Claude Code session (or another person/tool) may be working on this repo **at the same time**, or explicitly says "treballa en un worktree a `<path>`": create a **git worktree** for this session's work, not just a new branch in the shared working directory.

```bash
git worktree add <path> -b <branch-name>
```

Why: a plain `git checkout -b` still shares the *same working directory* as every other session on this machine. Two sessions editing concurrently — even on different branches — collide on disk: uncommitted changes from one can be silently overwritten, stashed, or left stranded by the other's `git checkout`/`git stash` operations. A worktree gives this session its own working directory while still sharing the same `.git` object database, so concurrent sessions never touch each other's files.

**If a worktree wasn't set up and this is discovered mid-task** (e.g. `git status` shows unrelated modified/untracked files that don't match what this session touched): do not run `git stash`, `git checkout --`, `git reset --hard`, or anything else that discards working-tree state. Those files are very likely another session's uncommitted WIP. Leave them untouched, stage and commit only the files this session actually intended to change, and tell the user what was found.

**Extra safety net: commit often.** Once a change is committed — even to a throwaway WIP branch — it lives in git's object database and is effectively unlosable to another session's working-tree operations. The real risk is always uncommitted changes sitting in a shared working directory. This doesn't relax the "never commit unless explicitly asked" rule above — it's a reason to *ask* for permission to commit proactively when a parallel-session risk is known upfront, not a license to auto-commit.

## Development Workflow

### Test-Driven Development (TDD)

Follow a strict TDD cycle for all new features and substantial changes:

1. **Write tests first** — Before any implementation, write failing tests that define the expected behavior.
2. **Run tests and confirm failure** — Verify the tests fail (red phase).
3. **Implement the feature** — Write the minimum code to make the tests pass.
4. **Run tests and confirm success** — Verify all tests pass (green phase).
5. **Refactor** — Clean up code while keeping tests passing.

Never implement code without corresponding tests. If a test fails, fix the implementation — never lower test expectations.

### Commit Discipline

**Never commit changes unless explicitly asked.** Only commit when the user says "fes el commit", "commit", "commit and push", or similar. Do not auto-commit after completing tasks.

### Daily Commit Policy

If the repository has uncommitted changes from previous days when you start working, the first commit of the day must be split into two:

1. **First commit** — Stage and commit all changes from previous days with a message like `chore: daily commit — <date>` summarizing what was done.
2. **Second commit** — Stage and commit only the changes made during the current session with a descriptive message about today's work.

Check for existing uncommitted changes at the start of each session:
```bash
git status
```
If there are staged or unstaged changes, ask the user for the date they were made and split the commit accordingly.

## Development Commands

### Installation
- Install in editable mode: `pip install -e .`

### Testing
- Run all tests: `python -m pytest test/ -v`
- Run a single test file: `python -m pytest test/<test_file_name>.py`

### Documentation
- Generate HTML documentation with Sphinx:
  `sphinx-build -b html docs/ docs/_build/html/`
  (Or use the virtual environment: `.venv/bin de sphinx-build -b html docs/ docs/_build/html/`)

## Architecture & Core Concepts

### Overview
This library implements a graph-based document representation model. It supports two backends: `Neo4jGraph` for persistent storage in Neo4j and `NetworkXGraph` for lightweight in-memory testing.

### Core Data Models
- **Node**: The fundamental building block, consisting of a `main_label` and a `pk` (primary key).
- **WeakNode**: A node whose identity is tied to a parent. Its `pk` is a composite of the parent's `pk` and its own. This models hierarchical structures like *Document $\rightarrow$ Section $\rightarrow$ Page*.
- **Relation**: A typed edge connecting two nodes.
- **WeakRelation**: A relation between a parent and a `WeakNode` that carries a `_propagate=True` flag, enabling **cascade delete** (deleting the parent automatically deletes the child).

### IMPORTANT: Backend Integrity
- **DO NOT MODIFY** the core logic, functions, or internal implementation of the Neo4j backend (specifically files in `cvcdocdb/` and `test/test_neo4j_real.py`) unless explicitly instructed to do so within a dedicated, isolated branch and for a specific, documented task.
- Any structural changes to the backend must follow a formal review process.

## PyPI Publishing

**NEVER publish to PyPI automatically.** Only publish when explicitly asked by the user.

- Do not run `twine upload`, `python -m build`, or `pip install --upload` without explicit user instruction
- Do not suggest publishing as part of a "next step" or "todo"
- Publishing is a manual, user-initiated action — the user will say "publica al PyPI" or similar
- Always confirm the version number before publishing

## Key Concepts
- **Semantic Entities**: Domain-specific node types (e.g., `IndividuPadro`, `LlocPadro`) defined within the graph.
- **Cascade Delete**: Enabled by `WeakNode` and `WeakRelation` via the `_propagate` flag.
- **Backends**:
    - `Neo4jGraph`: Full Neo4j integration.
    - `NetworkXGraph`: In-memory implementation for testing.
