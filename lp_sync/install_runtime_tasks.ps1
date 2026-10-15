param([switch]$Start)
$ErrorActionPreference = 'Stop'
$runtimeProject = 'G:\Playground\isaac_flapper_sim'
$runtimeEntry = Join-Path $runtimeProject 'lp_sync\svc_entry.py'
$runtimePython = 'C:\Python313\pythonw.exe'
$runtimeUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$runtimePrincipal = New-ScheduledTaskPrincipal -UserId $runtimeUser -LogonType Interactive -RunLevel Limited
$runtimeTrigger = New-ScheduledTaskTrigger -AtLogOn -User $runtimeUser
$runtimeRecoveryTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$runtimeSettings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$runtimeDefinitions = @(
    @{Name='LP Production Host I208'; Mode='host'},
    @{Name='LP Production Kernel'; Mode='kernel'},
    @{Name='LP Production Collector'; Mode='collector'},
    @{Name='LP Notification Preparation'; Mode='notifications'}
)
foreach ($runtimeDefinition in $runtimeDefinitions) {
    $runtimeExisting = Get-ScheduledTask -TaskName $runtimeDefinition.Name -ErrorAction SilentlyContinue
    if ($runtimeExisting -and $runtimeExisting.State -eq 'Running') {
        throw ('先完成维护和断点保存；本安装器不停止正在运行的任务：' + $runtimeDefinition.Name)
    }
}
foreach ($runtimeDefinition in $runtimeDefinitions) {
    $runtimeAction = New-ScheduledTaskAction -Execute $runtimePython -Argument ('"' + $runtimeEntry + '" ' + $runtimeDefinition.Mode) -WorkingDirectory $runtimeProject
    $runtimeTask = New-ScheduledTask -Action $runtimeAction -Principal $runtimePrincipal -Trigger @($runtimeTrigger,$runtimeRecoveryTrigger) -Settings $runtimeSettings -Description '常驻编排设施：系统托管、无窗口；每分钟检查并补起已退出服务，活跃任务忽略重复启动；不依赖经理或施工会话生命周期。'
    Register-ScheduledTask -TaskName $runtimeDefinition.Name -InputObject $runtimeTask -Force -ErrorAction Stop | Out-Null
    $runtimeReadback = Get-ScheduledTask -TaskName $runtimeDefinition.Name
    if ($runtimeReadback.Actions.Execute -ne $runtimePython) { throw '系统任务动作回读失败' }
    if ($Start) { Start-ScheduledTask -TaskName $runtimeDefinition.Name }
}
$runtimeDefinitions | ForEach-Object { Get-ScheduledTask -TaskName $_.Name } | Select-Object TaskName,State
