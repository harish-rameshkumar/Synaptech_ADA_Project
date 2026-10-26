$ErrorActionPreference = 'Stop'

# Run this script from the Synaptech_ADA_Project root in PowerShell.
# It assumes the strict Tier 1 build currently running has finished. If its
# panel exists at the usual path, the script reuses it and runs its analysis.

$cli = Join-Path (Get-Location) '.venv\Scripts\cinema-mobility-model.exe'
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
$dataDir = Join-Path (Get-Location) 'data\processed'
$resultsDir = Join-Path (Get-Location) 'results\modeling'
$primary = Join-Path $dataDir 'credits_final_v2.parquet'

if (-not (Test-Path $cli)) { throw "CLI not found: $cli. Run this from the project root." }
if (-not (Test-Path $primary)) { throw "Primary dataset not found: $primary" }

$runRoot = Join-Path $resultsDir ("automated_sensitivities_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
$logsDir = Join-Path $runRoot 'logs'
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$statusLog = Join-Path $logsDir 'run_status.txt'

function Invoke-ModelCommand {
    param([string]$Label, [string[]]$Arguments)
    $safeLabel = $Label -replace '[^A-Za-z0-9_-]', '_'
    $stepLog = Join-Path $logsDir ($safeLabel + '.log')
    Add-Content -Path $statusLog -Value ("[{0}] START {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Label)
    Write-Host "`n===== $Label ====="
    & $cli @Arguments 2>&1 | Tee-Object -FilePath $stepLog | Out-Host
    $exitCode = $LASTEXITCODE
    Add-Content -Path $statusLog -Value ("[{0}] EXIT {1}: {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Label, $exitCode)
    if ($exitCode -ne 0) {
        throw "Command failed ($exitCode): $Label. See $stepLog"
    }
}

function Invoke-BuildAndAnalyze {
    param(
        [string]$Name,
        [string]$Credits,
        [string[]]$BuildFlags = @(),
        [string]$SourceMode = 'dynamic',
        [string]$ExistingPanel = ''
    )
    $panel = Join-Path $runRoot ("actor_year_" + $Name + '.parquet')
    $analysisDir = Join-Path $runRoot $Name

    if ($ExistingPanel -and (Test-Path $ExistingPanel)) {
        $panel = $ExistingPanel
        Write-Host "Reusing existing panel: $panel"
    } else {
        $buildArgs = @('build', '--credits', $Credits, '--source-mode', $SourceMode, '--out', $panel) + $BuildFlags
        Invoke-ModelCommand -Label ("build_" + $Name) -Arguments $buildArgs
    }

    $analyzeArgs = @('analyze', '--actor-years', $panel, '--train-end-year', '2016',
                     '--validation-end-year', '2019', '--bootstrap-reps', '1000', '--outdir', $analysisDir)
    Invoke-ModelCommand -Label ("analyze_" + $Name) -Arguments $analyzeArgs
    return [pscustomobject]@{ Name = $Name; Panel = $panel; AnalysisDir = $analysisDir }
}

$runs = @()

# Reuse the strict Tier 1 panel from the command already running, if present.
$strictPanel = Join-Path $resultsDir 'actor_year_strict_t1.parquet'
$runs += Invoke-BuildAndAnalyze -Name 'strict_t1' -Credits $primary `
    -BuildFlags @('--strict') -ExistingPanel $strictPanel

$runs += Invoke-BuildAndAnalyze -Name 'unknown_counted' -Credits $primary `
    -BuildFlags @('--count-unknown-in-share')

$runs += Invoke-BuildAndAnalyze -Name 'two_year_exposure' -Credits $primary `
    -BuildFlags @('--exposure-lookback-years', '2')

$runs += Invoke-BuildAndAnalyze -Name 'fixed_source' -Credits $primary `
    -SourceMode 'fixed_legacy'

$finalSensitivity = Join-Path $dataDir 'credits_final.parquet'
if (Test-Path $finalSensitivity) {
    $runs += Invoke-BuildAndAnalyze -Name 'dataset_final' -Credits $finalSensitivity
} else {
    throw "Sensitivity dataset not found: $finalSensitivity"
}

$baseline = Join-Path $dataDir 'credits.parquet'
if (Test-Path $baseline) {
    $runs += Invoke-BuildAndAnalyze -Name 'dataset_baseline' -Credits $baseline
} else {
    throw "Sensitivity dataset not found: $baseline"
}

# The registered permutation is last because 1,000 repetitions can take a long time.
$permutationOut = Join-Path $runRoot 'timing_permutation.csv'
Invoke-ModelCommand -Label 'timing_permutation_1000' -Arguments @(
    'permutation', '--credits', $primary, '--train-end-year', '2016',
    '--validation-end-year', '2019', '--reps', '1000', '--seed', '2026',
    '--source-mode', 'dynamic', '--out', $permutationOut
)

# Generate the implementation's connectivity audit separately. The Data Lead's
# connectivity_final_v2.csv is retained as the supplied report-table audit.
$graphOut = Join-Path $runRoot 'graph_connectivity_code_audit.csv'
Invoke-ModelCommand -Label 'graph_connectivity_audit' -Arguments @(
    'graph-audit', '--credits', $primary, '--out', $graphOut
)

# Consolidate this run's results together with the already-completed primary
# and ml->ta analyses into one easy-to-share CSV.
$analysisDirs = @(
    [pscustomobject]@{ Name = 'primary_dynamic'; Dir = (Join-Path $resultsDir 'final_v2') },
    [pscustomobject]@{ Name = 'pair_ml_to_ta'; Dir = (Join-Path $resultsDir 'ml_ta') }
)
foreach ($run in $runs) {
    $analysisDirs += [pscustomobject]@{ Name = $run.Name; Dir = $run.AnalysisDir }
}

$summary = foreach ($item in $analysisDirs) {
    $metricsFile = Join-Path $item.Dir 'model_comparison.csv'
    if (Test-Path $metricsFile) {
        foreach ($row in (Import-Csv $metricsFile)) {
            [pscustomobject]@{
                run = $item.Name
                model = $row.model
                validation_AUPRC = $row.validation_AUPRC
                validation_ROC_AUC = $row.validation_ROC_AUC
                validation_Brier = $row.validation_Brier
                test_AUPRC = $row.test_AUPRC
                test_ROC_AUC = $row.test_ROC_AUC
                test_Brier = $row.test_Brier
                train_rows = $row.train_rows
                validation_rows = $row.validation_rows
                test_rows = $row.test_rows
                validation_events = $row.validation_events
                test_events = $row.test_events
                delta_XD_AUPRC = $row.delta_XD_minus_career_AUPRC
                delta_XD_AUPRC_CI_low = $row.delta_XD_minus_career_AUPRC_CI_low
                delta_XD_AUPRC_CI_high = $row.delta_XD_minus_career_AUPRC_CI_high
                delta_SD_AUPRC = $row.delta_SD_increment_AUPRC
                delta_SD_AUPRC_CI_low = $row.delta_SD_increment_AUPRC_CI_low
                delta_SD_AUPRC_CI_high = $row.delta_SD_increment_AUPRC_CI_high
            }
        }
    }
}

$summaryPath = Join-Path $runRoot 'modeling_summary.csv'
$summary | Export-Csv -Path $summaryPath -NoTypeInformation
$summary | Format-Table run, model, test_AUPRC, test_ROC_AUC, test_Brier, test_events -AutoSize

Write-Host "`nCompleted. Summary: $summaryPath"
Write-Host "Logs: $logsDir"
Write-Host "Permutation: $permutationOut"
Write-Host "Connectivity audit: $graphOut"
