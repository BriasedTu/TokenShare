function Resolve-TokenShareRuntimePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    return [IO.Path]::GetFullPath((Join-Path (Get-Location) $Path))
}

function Invoke-TokenShareNativeProcessWithLogs {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,
        [Parameter(Mandatory = $true)]
        [string]$StdoutPath,
        [Parameter(Mandatory = $true)]
        [string]$StderrPath,
        [string[]]$SecretValues = @()
    )

    $patterns = @(Get-TokenShareNativeLogSecretPatterns -SecretValues $SecretValues)
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = (
        @(
            foreach ($argument in $ArgumentList) {
                ConvertTo-TokenShareWindowsCommandLineArgument -Argument $argument
            }
        ) -join " "
    )
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    $stdoutWriter = New-Object System.IO.StreamWriter(
        $StdoutPath,
        $false,
        $utf8WithoutBom
    )
    $stderrWriter = New-Object System.IO.StreamWriter(
        $StderrPath,
        $false,
        $utf8WithoutBom
    )
    $started = $false

    try {
        $started = $process.Start()
        if (-not $started) {
            throw "native process did not start"
        }

        $stdoutComplete = $false
        $stderrComplete = $false
        $stdoutTask = $process.StandardOutput.ReadLineAsync()
        $stderrTask = $process.StandardError.ReadLineAsync()
        while (-not ($stdoutComplete -and $stderrComplete)) {
            $handledLine = $false
            if (-not $stdoutComplete -and $stdoutTask.IsCompleted) {
                $stdoutLine = $stdoutTask.GetAwaiter().GetResult()
                if ($null -eq $stdoutLine) {
                    $stdoutComplete = $true
                } else {
                    $redactedLine = Protect-TokenShareNativeLogText `
                        -Text $stdoutLine `
                        -Patterns $patterns
                    $stdoutWriter.WriteLine($redactedLine)
                    $stdoutWriter.Flush()
                    $stdoutTask = $process.StandardOutput.ReadLineAsync()
                }
                $handledLine = $true
            }
            if (-not $stderrComplete -and $stderrTask.IsCompleted) {
                $stderrLine = $stderrTask.GetAwaiter().GetResult()
                if ($null -eq $stderrLine) {
                    $stderrComplete = $true
                } else {
                    $redactedLine = Protect-TokenShareNativeLogText `
                        -Text $stderrLine `
                        -Patterns $patterns
                    $stderrWriter.WriteLine($redactedLine)
                    $stderrWriter.Flush()
                    $stderrTask = $process.StandardError.ReadLineAsync()
                }
                $handledLine = $true
            }
            if (-not $handledLine) {
                Start-Sleep -Milliseconds 10
            }
        }

        $process.WaitForExit()
        return [int]$process.ExitCode
    } finally {
        if ($started -and -not $process.HasExited) {
            $process.Kill()
            $process.WaitForExit()
        }
        $stdoutWriter.Dispose()
        $stderrWriter.Dispose()
        $process.Dispose()
    }
}

function Get-TokenShareNativeLogSecretPatterns {
    [CmdletBinding()]
    param([string[]]$SecretValues = @())

    $patterns = New-Object System.Collections.Generic.List[string]
    foreach ($secretValue in $SecretValues) {
        if ([string]::IsNullOrWhiteSpace($secretValue)) {
            continue
        }
        $patterns.Add($secretValue)
        if ($secretValue.Length -ge 12) {
            $patterns.Add($secretValue.Substring(0, 8))
            $patterns.Add($secretValue.Substring($secretValue.Length - 8))
        }
    }
    return @($patterns | Select-Object -Unique)
}

function Protect-TokenShareNativeLogText {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Text,
        [string[]]$Patterns = @()
    )

    $redacted = $Text
    foreach ($pattern in $Patterns) {
        $redacted = $redacted.Replace($pattern, "[REDACTED]")
    }
    return $redacted
}

function ConvertTo-TokenShareWindowsCommandLineArgument {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Argument
    )

    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') {
        return $Argument
    }

    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashCount = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq [char]'\') {
            $backslashCount += 1
            continue
        }
        if ($character -eq [char]'"') {
            [void]$builder.Append(
                ((('\' * ((2 * $backslashCount) + 1))) -join '')
            )
            [void]$builder.Append('"')
            $backslashCount = 0
            continue
        }
        if ($backslashCount -gt 0) {
            [void]$builder.Append((('\' * $backslashCount) -join ''))
            $backslashCount = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashCount -gt 0) {
        [void]$builder.Append((('\' * (2 * $backslashCount)) -join ''))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}
