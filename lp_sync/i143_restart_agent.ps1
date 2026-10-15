# I-143: restart sync agent to load the console collector thread.
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
  Where-Object { $_.CommandLine -like "*sync_agent*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output ("stopped " + $_.ProcessId) }
Start-Sleep -Seconds 1
Start-Process -FilePath "C:\Python313\pythonw.exe" -ArgumentList "sync_agent.py" `
  -WorkingDirectory "G:\Playground\isaac_flapper_sim\lp_sync" -WindowStyle Hidden `
  -RedirectStandardOutput "G:\Playground\isaac_flapper_sim\lp_sync\sync_agent.log" -RedirectStandardError "G:\Playground\isaac_flapper_sim\lp_sync\sync_agent.err.log"
Write-Output "started sync agent with collector"
