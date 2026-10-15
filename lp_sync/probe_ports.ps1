$ps = Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*zcode*' -and $_.Name -match 'node|ZCode' }
$ps | ForEach-Object { "{0}`t{1}`t{2}" -f $_.ProcessId, $_.Name, $_.CommandLine.Substring(0, [Math]::Min(100, $_.CommandLine.Length)) }
"---listeners owned by these pids---"
$pids = @($ps | ForEach-Object { $_.ProcessId })
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $pids -contains $_.OwningProcess } |
  ForEach-Object { "{0}:{1} pid={2}" -f $_.LocalAddress, $_.LocalPort, $_.OwningProcess }
"---ZCode.exe(50484) outbound sample---"
Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue |
  Where-Object { $_.OwningProcess -eq 50484 } |
  Select-Object -First 6 | ForEach-Object { "{0} -> {1}:{2}" -f $_.LocalPort, $_.RemoteAddress, $_.RemotePort }
