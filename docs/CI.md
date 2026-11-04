# CI

Workflow: `.github/workflows/ci.yml`. It runs on every pull request and on pushes to `main`.

| Job (check name) | What it does |
|---|---|
| `lint` | `ruff check --no-fix .` (Python 3.12). Check only, never rewrites code. |
| `test` | Starts a `pgvector/pgvector:pg16` service, runs `alembic upgrade head` against it, asserts a single migration head, then runs `pytest`. |
| `docker build` | `docker build` of the `Dockerfile` (no push). |
| `ci` | Gate job that fails if any of the three above failed, was cancelled or skipped. **This is the one to require.** |

Python 3.12 matches the Dockerfile base image (`python:3.12-slim`).

## Lint rules

`ruff` is configured in `pyproject.toml` with a deliberately narrow ruleset: pyflakes (`F`) plus
pycodestyle `E4`, `E7`, `E9` (import placement, statements, syntax errors). Import sorting,
pyupgrade and similar style rules are off so the linter does not demand a codebase-wide rewrite.
A few intentional exceptions are listed under `per-file-ignores` (`migrations/env.py` imports models
for their side effect; Alembic revisions import `sqlalchemy` from the template).

Run locally (no autofix): `pip install ruff && ruff check --no-fix .`

## Tests and secrets

- `tests/conftest.py` sets placeholder `DATABASE_URL`, `GEMINI_API_KEY` and `INTERNAL_SERVICE_TOKEN`
  with `setdefault`. In CI only `DATABASE_URL` is set, pointing at the throwaway service container.
- Tests marked `@pytest.mark.needs_gemini` call the live Gemini API
  (`test_embedding_service.py::test_embed_query_returns_3072_floats` and the two
  `test_orchestrator.py::test_process_message_*`). They are **skipped** unless `GEMINI_API_KEY` is set
  to something other than the placeholder. CI never has a real key; do not add one.
- Run them locally with a real key: `GEMINI_API_KEY=... DATABASE_URL=... pytest -m needs_gemini`.

## Making CI a required check on `main` (repository owner)

This is a GitHub setting and cannot be done from the repo. The workflow must have run at least once
(open a PR) so the check names appear in the picker.

1. GitHub repo -> **Settings** -> **Branches** -> **Add branch protection rule**
   (or **Settings -> Rules -> Rulesets -> New branch ruleset** targeting `main`).
2. Branch name pattern: `main`.
3. Tick **Require a pull request before merging**.
4. Tick **Require status checks to pass before merging** and **Require branches to be up to date
   before merging**.
5. In the status-check search box add **`ci`** (the gate job). Optionally also add `lint`, `test`
   and `docker build`; `ci` alone is enough and survives job renames.
6. Tick **Do not allow bypassing the above settings** (or leave admin bypass off), then **Create**.

Or with the CLI, once the workflow has run:

```bash
gh api -X PUT repos/<owner>/<repo>/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["ci"] },
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "restrictions": null
}
JSON
```
