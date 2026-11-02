$ErrorActionPreference = 'Stop'
$agentInstall = Join-Path $env:LOCALAPPDATA 'AssureDenAgent'
New-Item -ItemType Directory -Force -Path $agentInstall | Out-Null
$agentSource = Join-Path $PSScriptRoot 'execution_agent'
Copy-Item -LiteralPath $agentSource -Destination $agentInstall -Recurse -Force
$agentVenv = Join-Path $agentInstall 'runtime'
python -m venv $agentVenv
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.14 with Tcl/Tk and Add to PATH, then run this installer again.' }
$agentPython = Join-Path $agentVenv 'Scripts\python.exe'
& $agentPython -m pip install -r (Join-Path $agentInstall 'execution_agent\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependencies could not be installed. Check your internet connection.' }
$agentPythonw = Join-Path $agentVenv 'Scripts\pythonw.exe'
$shortcutShell = New-Object -ComObject WScript.Shell
foreach ($shortcutFolder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Startup'))) {
    $agentShortcut = $shortcutShell.CreateShortcut((Join-Path $shortcutFolder 'AssureDen Agent.lnk'))
    $agentShortcut.TargetPath = $agentPythonw
    $agentShortcut.Arguments = '-m execution_agent.desktop'
    $agentShortcut.WorkingDirectory = $agentInstall
    $agentShortcut.Save()
}
Start-Process -FilePath $agentPythonw -ArgumentList '-m execution_agent.desktop' -WorkingDirectory $agentInstall
Write-Host 'Installed. Pair the agent using the code from AssureDen. It starts automatically when you sign in.'
