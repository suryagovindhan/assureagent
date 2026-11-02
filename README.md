# AssureAgent standalone recorder

This repository contains AssureDen's standalone Windows recorder and local
browser test runner. It records actions, captures ranked element alternatives,
lets you review and edit steps, and replays tests locally without a server or
account.

## Start

Install Python with Tcl/Tk and Microsoft Edge, then run `Start-Local-Tests.cmd`.
The launcher installs no software and changes no startup settings. For manual
setup, install `execution_agent/requirements.txt` and run:

```powershell
python -m execution_agent.standalone
```

See [execution_agent/STANDALONE.md](execution_agent/STANDALONE.md) and
[execution_agent/RECORDING.md](execution_agent/RECORDING.md) for the workflow.

The recorder captures up to 16 uniquely validated ways to find an element,
preferring stable labels and names while excluding recognized generated IDs.
Existing recordings should be recorded again to collect these alternatives.

