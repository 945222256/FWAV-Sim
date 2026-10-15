# I-130: restart the local sync agent so the params upstream channel takes effect.
Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Where-Object { $_.CommandLine -like "*sync_agent*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output ("stopped " + $_.ProcessId) }
Start-Sleep -Seconds 1
$wd = "G:\Playground\isaac_flapper_sim\lp_sync"
Start-Process -FilePath "C:\Python313\pythonw.exe" -ArgumentList "sync_agent.py" -WorkingDirectory $wd -WindowStyle Hidden
Write-Output "started new sync agent"
