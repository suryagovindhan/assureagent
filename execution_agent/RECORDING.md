# Record, review and execute a test

This initial recorder connects to the canonical application through a reviewed
recording-file import. It does not use the legacy WebSocket recorder.

## Prepare

Update backend dependencies and run `python -m alembic upgrade head` from
`backend/`, then restart the backend and frontend. Install the agent dependencies
as described in [README.md](README.md). Create an application, module and page
in the Object Repository to hold this recording's objects.

## Record locally

From the repository root:

```powershell
.venv-agent/Scripts/python.exe -m execution_agent.recorder https://your-test-app.example --channel msedge --name "Login smoke test" --output login-recording.json
```

Interact with the opened browser. Finish each input by leaving the field. Close
the browser window when finished; the recorder writes the file. Existing files
are not overwritten. Omit `--channel msedge` to use installed Playwright Chromium.

Supported capture: initial navigation, clicks, text changes, single-value select
changes and checkbox changes. Locators include test ID, ID, ARIA label and CSS
fallbacks when available. Password input values are never exported: they become
placeholders such as `{{RECORDED_SECRET_2}}`.

## Review and promote

1. Open **Test Cases → Recording drafts** and import the recording file.
2. Review the name, ordered steps, values and locators. Remove unwanted steps;
   edit unstable selectors and replace environment-specific values with variables.
3. For password placeholders, create matching **secret environment variables**
   in Environments. Do not replace placeholders with passwords or use run overrides.
4. Select the destination application/module/page and create the test case.
   The server saves the review, creates repository objects and ordered test steps,
   and retains draft-to-case provenance. Repeating promotion returns the same case.
5. Add assertions in the test editor. Recording captures actions, not the intended
   outcome; an action-only pass is not a complete test of application behavior.
6. Start the HTTP agent, choose the environment and trigger the test case.
   Inspect step results and add a SCREENSHOT step if successful-run evidence is needed.

TESTER or higher can import, edit and promote. Drafts are organization-scoped;
concurrent stale edits are rejected. Promoted drafts are read-only.

## Initial scope

Capture is limited to the main tab and top-level document. Frames, popups, file
uploads, radio inputs and multiple-select inputs require manual authoring or
further agent support; the
recorder includes warnings for these unsupported surfaces. Address-bar navigation,
Enter-key submission, hover-only behavior and drag/drop are not captured. Start a
new recording for a different entry URL. Inputs still focused when the browser is
closed may not emit a change event and must be reviewed.

Promotion currently creates a **TestCase**, with objects grouped under the chosen
repository page. It does not yet promote BusinessActions or Flows, deduplicate
against previously authored objects, or provide a separate approval workflow.
Existing execution metadata, secret storage and immutable flow-revision roadmap
items remain separate work.
