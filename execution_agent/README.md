# AssureDen HTTP browser agent

For normal product use, follow [in-product setup and testing](../PRODUCT_TESTING.md).
The CLI instructions below remain available for development and troubleshooting.

For the local recorder → draft → test-case workflow, see [RECORDING.md](RECORDING.md).

This is the agent for the canonical `backend/` application. It uses authenticated
HTTP polling and callbacks, not the legacy agents' WebSocket endpoints. Execution
is deterministic and does not call an LLM.

## Start locally

From the repository root, with Python 3.14:

```powershell
python -m venv .venv-agent
.venv-agent/Scripts/python.exe -m pip install -r execution_agent/requirements.txt
.venv-agent/Scripts/python.exe -m playwright install chromium
```

Start the backend and register an agent using `POST /api/agents/register` as an
ADMIN (the backend's `/api/docs` page can make this request). Use a registration body
such as `{"name":"Local browser agent","max_parallel_sessions":1}`. Registration
returns `api_key` once. Set it in the terminal running the agent:

```powershell
$env:ASSUREDEN_AGENT_API_KEY = '<registered api_key>'
.venv-agent/Scripts/python.exe -m execution_agent --server http://127.0.0.1:8000
```

For an already installed Microsoft Edge browser, omit the browser installation
command and add `--channel msedge`. `--headed` shows the browser; `--once` polls
once and exits after processing an available run. Remote servers require HTTPS.

The agent advertises its capabilities before polling. Create a test case with a
NAVIGATE step, locator-backed actions and assertions, then trigger a run from the
application. A SCREENSHOT step captures evidence; failed steps also capture a
screenshot when `screenshot_on_failure` is enabled. Open the run's Artifacts tab
to view its screenshots using authenticated access.

## Supported execution contract

- Protocol 2 and execution plan/snapshot version 2. Older snapshots cannot recover
  settings that were omitted when they were created; queue a new run for those
  cases. An obsolete context encountered at dispatch is marked FAILED.
- Actions: NAVIGATE, CLICK, DOUBLE_CLICK, RIGHT_CLICK, TYPE, APPEND, CLEAR,
  SELECT, CHECK, UNCHECK, HOVER, SCROLL_TO, WAIT_FOR, PRESS_KEY and SCREENSHOT.
- All 15 current assertion types, including negation and fatal/nonfatal behavior.
  Required failures stop subsequent actions (recorded as SKIPPED); optional step
  failures are recorded as OPTIONAL_FAILED and execution continues.
- Active locators are frozen with primary first, then priority order. CSS, XPath,
  ID, ARIA_LABEL, TEST_ID and exact TEXT are supported. The agent resolves a
  unique matching locator before acting; it does not repeat a mutating action
  against fallback selectors after an action error.
- Authored step timeout is a total action/assertion budget. The run also has an
  overall timeout. Each run gets an isolated browser context.
- Supported hints: `iframe_selector`, `focus_before_action`. Supported
  `step_metadata.execution` options: `clear_before_type`, `press_enter`.
  Other nonempty execution options fail explicitly instead of being ignored.
- Nonsecret variables are frozen when queued. Secret values are resolved only
  in the transient dispatch payload; log messages do not include browser error
  text or resolved inputs. Secret overrides are rejected. If the environment
  revision changes before a secret-bearing run dispatches, queue a new run.
- Callbacks renew the lease every ten seconds. Lease/connection rejection stops
  execution; abort detection can take up to that interval. Duplicate result
  callbacks do not duplicate counters/events, and terminal results cannot be
  overwritten. The server limits concurrent claims to registered capacity; this
  worker executes one run at a time.
- PNG uploads require the assigned agent, active lease and an existing step
  result. Screenshots are linked to that result and served with user auth.

## Verification

The backend suite includes real-browser tests using a local fixture webpage,
PostgreSQL and FastAPI's authenticated routes through an ASGI HTTP transport.
They cover locator fallback, typing, assertions, optional/nonfatal outcomes,
step correlation and screenshot upload/retrieval. Browser assertion tests also
exercise every assertion type. CI installs Chromium and enables these tests.

Against an isolated, migrated and seeded test database, from `backend/`:

```powershell
python -m pip install -r requirements-dev.txt -r ../execution_agent/requirements.txt
$env:RUN_BROWSER_TESTS = '1'
$env:ASSUREDEN_BROWSER_CHANNEL = 'msedge' # omit for Playwright Chromium
python -m pytest tests -q
```

## Still outside this milestone

EXECUTE_SCRIPT, DRAG_DROP and UPLOAD_FILE are not advertised; runs needing them
remain queued until a capable agent exists. Video, trace and HAR capture are not
implemented by this agent. Immutable flow/secret revisions, flow authoring in
the test editor, retry timing, watchdog consolidation and production secret
configuration remain roadmap work. A passing browser run does not close those
foundations or the recorder/promotion product work.
