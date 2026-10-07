param([string]$In, [string]$Out, [int]$Bitrate = 3500000, [int]$W = 0, [int]$H = 0)
# Re-encode an MP4 to H.264 at a set bitrate with Windows' own MediaTranscoder (no ffmpeg).
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskOp = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskOpP = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncActionWithProgress`1' })[0]
function Await($op, [Type]$t) {
    $task = $asTaskOp.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result
}
function AwaitP($op, [Type]$p) {
    $task = $asTaskOpP.MakeGenericMethod($p).Invoke($null, @($op)); $task.Wait(-1) | Out-Null
}
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Media.Transcoding.MediaTranscoder, Windows.Media.Transcoding, ContentType = WindowsRuntime]
[void][Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media.MediaProperties, ContentType = WindowsRuntime]

$src = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path $In).Path)) ([Windows.Storage.StorageFile])
$outDir = Split-Path -Parent ([IO.Path]::GetFullPath($Out))
$folder = Await ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync($outDir)) ([Windows.Storage.StorageFolder])
$dst = Await ($folder.CreateFileAsync((Split-Path -Leaf $Out), [Windows.Storage.CreationCollisionOption]::ReplaceExisting)) ([Windows.Storage.StorageFile])

$prof = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateMp4([Windows.Media.MediaProperties.VideoEncodingQuality]::HD720p)
$prof.Audio = $null
$prof.Video.Bitrate = [uint32]$Bitrate
if ($W -gt 0) { $prof.Video.Width = [uint32]$W; $prof.Video.Height = [uint32]$H }
$tc = New-Object Windows.Media.Transcoding.MediaTranscoder
$tc.HardwareAccelerationEnabled = $true
$prep = Await ($tc.PrepareFileTranscodeAsync($src, $dst, $prof)) ([Windows.Media.Transcoding.PrepareTranscodeResult])
if (-not $prep.CanTranscode) { throw "cannot transcode: $($prep.FailureReason)" }
AwaitP ($prep.TranscodeAsync()) ([double])
'{0}  {1:N2} MiB' -f $Out, ((Get-Item $Out).Length / 1MB)
