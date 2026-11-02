# Record and run tests on this computer

This first desktop workspace runs without a server, account or pairing code.
It uses the same browser recording and replay code as connected Test Computers.

## Open the software

For this source-code preview, install Python with Tcl/Tk and Microsoft Edge.
From the project folder, install the dependencies once:

```powershell
python -m pip install -r execution_agent/requirements.txt
```

Then double-click `Start-Local-Tests.cmd` in the project folder, or run:

```powershell
python -m execution_agent.standalone
```

This launcher does not install software or change automatic startup settings.
The existing server-connected companion remains a separate application.

## Try a test

1. Enter a test name and website address, then choose **Record a Test**.
2. Use the website in the new Edge window. Choose **Stop Recording** in the
   desktop window when finished. The last text field is captured when you stop.
3. Double-click a step, or choose **Edit Step**. Review its value and **How to
   Find It**. Recorded alternatives are tried in their listed order.
4. Choose an **Expected Result**, such as **Entered value equals**, and its
   expected text. Choose **Save Step**.
5. Choose **Save Test**, then **Run Test**. Named settings such as `{{PASSWORD}}`
   are requested for each run and their entered values are not saved.
6. Review each step's result. **Open Results Folder** shows the saved results
   and screenshots. Edit the test and run it again as needed.

Test files preserve the reviewed recording and checks. Each run gets a separate
folder next to the saved test. Screenshots may contain visible website data;
turn off **Take screenshot when step fails** if those images should not be saved.
Use setting references for sensitive values rather than typing them into saved
step values. Password fields are recorded as references automatically.

## Current scope

Recording captures up to 16 distinct ways to find each element, checking that
each matches only the recorded element. Testing labels and stable names come
first, followed by labels, text, field hints, appearance and named areas when
available. Recognized generated names such as `ember1049` are excluded.
Page and document positions are last-resort choices and may change when the
page layout changes. Review the scrollable **How to Find It** list in **Edit
Step** to adjust the alternatives. Replay checks alternatives in order and
polls again if none matches uniquely; it does not repeat a failed click.

Re-record existing tests to collect these additional alternatives. A saved
recording cannot recover information that was not captured originally. These
alternatives improve resilience but cannot guarantee success when the page
changes; a unique positional match can still refer to a different element
after a layout change.

The step list shows the recorded **Element** separately from its input **Value**.
Clicks normally have an empty Value. Submit buttons are recorded as clicks;
visible text and control names are captured as additional ways to find elements.
Failures distinguish finding an element, using it, and checking its expected result.
Recordings made before the submit-button fix must be recorded again if the login
click is absent. A later folder/menu click cannot succeed while still on login.

Supports opening websites, clicks, text fields, single choices and tick boxes;
checks cover visibility, text, entered values, page addresses and titles.
Steps can be edited, moved or removed. A failed step stops subsequent actions;
they appear as skipped. Cancelling a test closes its browser and saves partial
results. Recording is limited to the main tab and top-level page.

Not yet included: a standalone element picker, reusable actions, output capture,
server synchronization of local tests/checks, a self-contained installer, and a
results-history screen. Local files with checks are not server import files.
Windows visual acceptance remains necessary before production release.
