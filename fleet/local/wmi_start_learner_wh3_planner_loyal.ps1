$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\sshuser\re1_rl'
$cmd = 'cmd.exe /c "' + $repo + '\fleet\local\start_learner_detached_wh3_planner_loyal.cmd"'
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = $cmd
  CurrentDirectory = $repo
}
Write-Output ("WMI ReturnValue={0} ProcessId={1}" -f $result.ReturnValue, $result.ProcessId)
if ($result.ReturnValue -ne 0) { exit 1 }
