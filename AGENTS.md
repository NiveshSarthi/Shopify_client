# AGENTS.md
Guidance for coding agents working in this repository.

## 1) Repository Snapshot
- Stack: Python, FastAPI, Pillow, requests, python-dotenv.
- Container runtime target is `pillowtest_v2:app` (see `Dockerfile`).
- `pillowtest.py` is an older variant and still useful for reference.
- Webhook flow is async in FastAPI but still performs blocking IO in helpers.
- Integration with Shopify Admin API and WhatsApp APIs is central.
- Several scripts are test-like but are actually integration runners.

## 2) Important Files
- `pillowtest_v2.py`: main app routes, sequence flow, image serving.
- `pillowtest.py`: alternate implementation with similar utilities.
- `requirements.txt`: runtime deps.
- `Dockerfile`: canonical build/run shape for deployments.
- `test_functions.py`: Shopify token + webhook registration checks.
- `test_second_template.py`: sends second template through external API.
- `test_variant_view.py`: renders image preview with forced variant title.
- `mock_test_image.py`: image generation smoke script.
- `verify_webhooks.py`: lists active Shopify webhooks.

## 3) Cursor/Copilot Rule Status
- No `.cursor/rules/` directory found.
- No `.cursorrules` file found.
- No `.github/copilot-instructions.md` found.
- This `AGENTS.md` is currently the effective agent instruction source.
- If those rule files appear later, merge their constraints into this file.

## 4) Local Environment Setup
- Create env: `python3 -m venv .venv`
- Activate env: `source .venv/bin/activate`
- Upgrade pip: `python3 -m pip install --upgrade pip`
- Install deps: `python3 -m pip install -r requirements.txt`
- Required config file: `.env` in repo root.
- Do not commit `.env` (already ignored by git).

## 5) Build and Run Commands
- Preferred local run:
  - `uvicorn pillowtest_v2:app --host 0.0.0.0 --port 5002 --reload`
- Alternate run:
  - `python3 pillowtest_v2.py`
- Health check:
  - `curl http://127.0.0.1:5002/`
- Register webhooks:
  - `curl http://127.0.0.1:5002/setup`

## 6) Docker Commands
- Build:
  - `docker build -t shopify-client .`
- Run:
  - `docker run --rm -p 5002:5002 --env-file .env shopify-client`
- Entrypoint command in image:
  - `uvicorn pillowtest_v2:app --host 0.0.0.0 --port 5002`

## 7) Lint/Static Commands
There is no pinned linter/formatter config in repo right now.
- Minimum syntax validation:
  - `python3 -m py_compile pillowtest_v2.py pillowtest.py test_functions.py test_second_template.py test_variant_view.py mock_test_image.py verify_webhooks.py`
- Optional only if installed in your environment:
  - `python3 -m ruff check .`
  - `python3 -m black --check .`

## 8) Test Commands (Single-Test Focus)
Note: current tests are integration scripts that may hit live APIs.
- Run one script (most common single-test pattern):
  - `python3 test_functions.py`
  - `python3 test_second_template.py`
  - `python3 test_variant_view.py`
  - `python3 mock_test_image.py`
  - `python3 verify_webhooks.py`
- Run one function directly:
  - `python3 -c "from test_functions import test_get_shopify_token; test_get_shopify_token()"`
- Run all scripts in sequence:
  - `python3 test_functions.py && python3 test_second_template.py && python3 test_variant_view.py && python3 mock_test_image.py && python3 verify_webhooks.py`
- If pytest is introduced later, single test command should be:
  - `pytest -q path/to/test_file.py::test_name -s`

## 9) Code Style Guidelines

### Imports
- Order imports as: stdlib, third-party, local.
- Keep one import per line unless a grouped import improves clarity.
- Remove unused imports when touching a file.
- Avoid circular imports; refactor shared helpers into utility sections.

### Formatting
- Use 4-space indentation and Black-compatible formatting.
- Target ~88 char line length for new/edited lines.
- Avoid dense one-line `if`/`try` statements in new code.
- Keep clear spacing between routes and helper functions.
- Preserve ASCII by default unless file already requires Unicode.

### Types
- Add type hints for all new/modified function signatures.
- Use explicit return types (`str | None`, `dict[str, Any]`, etc.).
- For structured payloads, prefer `TypedDict` or dataclasses.
- Do not add fake/inaccurate annotations just to satisfy style.

### Naming
- Use `snake_case` for functions, variables, and helpers.
- Use `UPPER_SNAKE_CASE` for constants/env-derived config.
- Prefer descriptive names (`template_payload`) over short names (`p`).
- Keep route function names aligned with endpoint purpose.

### Error Handling
- Do not introduce bare `except:` in new code.
- Catch specific exceptions where practical.
- Include context in logs (order id, topic, API name).
- Fail closed for HMAC/signature checks.
- Return structured fallback values when soft-failing helpers.

### Network/API Practices
- Always pass explicit timeouts to `requests` calls.
- Validate status codes before using `response.json()`.
- Guard against missing keys in third-party payloads.
- Never log secrets, tokens, or client secrets.
- Reuse existing token-cache/refresh behavior where possible.

### FastAPI and Async
- Keep route handlers small and move business logic to helpers.
- Avoid blocking operations directly in async route bodies.
- Use `HTTPException` for HTTP-layer errors.
- Keep webhook processing idempotent (`_processed_orders`).

### File/Image Handling
- Use context managers for file reads/writes.
- Remove generated image artifacts when flow completes.
- Keep output filenames predictable and collision-safe.
- Preserve existing cross-platform font fallback behavior.

### Config and Secrets
- Read all secrets from environment variables.
- Keep `.env` local and untracked.
- Validate critical env vars at startup or before first use.
- Use defaults only for non-sensitive values.

### Maintainability
- Prefer small composable helpers over large monoliths.
- Keep changes scoped to requested behavior.
- Avoid refactoring both `pillowtest.py` and `pillowtest_v2.py` unless needed.
- Preserve route contracts unless a breaking change is requested.

## 10) Agent Pre-PR Checklist
- Run the single relevant test script(s) for changed behavior.
- Run syntax checks even when lint tooling is unavailable.
- Confirm app still boots with `uvicorn pillowtest_v2:app`.
- Ensure no secrets are added to logs or committed artifacts.
- Document external dependencies needed to reproduce integration checks.
