# I-131: restart the resident host service to load index registration code.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*host_service*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output ("stopped " + $_.ProcessId) }
Start-Sleep -Seconds 1
Start-Process -FilePath "C:\Python313\python.exe" -ArgumentList "host_service.py" `
  -WorkingDirectory "G:\Playground\isaac_flapper_sim\lp_sync" -WindowStyle Hidden
Write-Output "started new host service"
