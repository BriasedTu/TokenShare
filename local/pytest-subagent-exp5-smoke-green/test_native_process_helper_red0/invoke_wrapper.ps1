. 'E:\TokenEcnomic\TokenShare\local\invoke_native_process_with_logs.ps1'
$native = Join-Path $PSHOME 'powershell.exe'
$nativeArgs = @('-NoProfile', '-File', 'E:\TokenEcnomic\TokenShare\local\pytest-subagent-exp5-smoke-green\test_native_process_helper_red0\long_lived_child.ps1')
$code = Invoke-TokenShareNativeProcessWithLogs -FilePath $native -ArgumentList $nativeArgs -StdoutPath 'E:\TokenEcnomic\TokenShare\local\pytest-subagent-exp5-smoke-green\test_native_process_helper_red0\live.stdout.log' -StderrPath 'E:\TokenEcnomic\TokenShare\local\pytest-subagent-exp5-smoke-green\test_native_process_helper_red0\live.stderr.log' -SecretValues @('sentinel-live-secret-1234567890', 'tiny-key', '')
exit $code