# I-130: list every python/pythonw process with full command line and start time.
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match '^python' } |
  Select-Object ProcessId, Name, CreationDate, CommandLine |
  Format-List
