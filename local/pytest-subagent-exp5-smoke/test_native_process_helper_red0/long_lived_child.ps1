$longSecret = 'sentinel-live-secret-1234567890'
$shortSecret = 'tiny-key'
$terminatePath = 'E:\TokenEcnomic\TokenShare\local\pytest-subagent-exp5-smoke\test_native_process_helper_red0\child.terminate'
[Console]::Out.WriteLine('stdout-marker')
[Console]::Out.WriteLine($longSecret)
[Console]::Out.WriteLine($longSecret.Substring(0, 8))
[Console]::Out.WriteLine($shortSecret)
[Console]::Out.WriteLine('empty-secret-marker')
[Console]::Out.Flush()
[Console]::Error.WriteLine('stderr-marker')
[Console]::Error.WriteLine($longSecret)
[Console]::Error.WriteLine($longSecret.Substring($longSecret.Length - 8))
[Console]::Error.WriteLine($shortSecret)
[Console]::Error.WriteLine('empty-secret-marker')
[Console]::Error.Flush()
Set-Content -Encoding UTF8 -LiteralPath 'E:\TokenEcnomic\TokenShare\local\pytest-subagent-exp5-smoke\test_native_process_helper_red0\child.ready' -Value $PID
while (-not (Test-Path -LiteralPath $terminatePath)) {
    Start-Sleep -Milliseconds 25
}
exit 17