#Requires -Version 5.1
<#
.SYNOPSIS
    Universal Compiler v2.0 - Script to EXE Compiler
.DESCRIPTION
    Compiles PowerShell, Python, Batch, Node.js, C#, Go, Ruby, VBScript, and AutoHotkey scripts to Windows executables.
    Features: Drag & Drop, Batch Compilation, Build Profiles, Recent Files, Theme Toggle, Code Signing, and more.
#>

param([switch]$ForceSetup, [switch]$SkipSetup, [string]$File, [string]$Output, [string]$Profile)

# ============================================================================
# GLOBAL CONFIGURATION
# ============================================================================

$script:AppName = "Universal Compiler"
$script:AppVersion = "2.0"
$script:ConfigDir = Join-Path $env:APPDATA "UniversalCompiler"
$script:ConfigFile = Join-Path $script:ConfigDir "config.json"
$script:ProfilesFile = Join-Path $script:ConfigDir "profiles.json"
$script:HistoryFile = Join-Path $script:ConfigDir "history.json"
$script:RecentFile = Join-Path $script:ConfigDir "recent.json"
$script:SettingsFile = Join-Path $script:ConfigDir "settings.json"
$script:LogFile = Join-Path $script:ConfigDir "install.log"
$script:TemplatesDir = Join-Path $script:ConfigDir "Templates"

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Ensure config directory
if (-not (Test-Path $script:ConfigDir)) { New-Item -ItemType Directory -Path $script:ConfigDir -Force | Out-Null }

# ============================================================================
# SETTINGS & THEMES
# ============================================================================

$script:DefaultSettings = @{ Theme = 'Dark'; PostBuildAction = 'None'; PostBuildCopyPath = ''; ShowNotifications = $true; AutoCheckUpdates = $true; MaxRecentFiles = 10; MaxHistoryItems = 50; DefaultProfile = 'Default'; SigningCertPath = ''; SigningCertPassword = '' }

function Get-AppSettings { if (Test-Path $script:SettingsFile) { try { $loaded = Get-Content $script:SettingsFile -Raw | ConvertFrom-Json; $s = $script:DefaultSettings.Clone(); foreach ($p in $loaded.PSObject.Properties) { if ($s.ContainsKey($p.Name)) { $s[$p.Name] = $p.Value } }; return $s } catch { } }; return $script:DefaultSettings.Clone() }
function Save-AppSettings { param([hashtable]$S); $S | ConvertTo-Json -Depth 3 | Set-Content -Path $script:SettingsFile -Force }

$script:Settings = Get-AppSettings

$script:Themes = @{
    Dark = @{ Bg='#020617'; Card='#0f172a'; CardHover='#1e293b'; Border='#1e293b'; Input='#0f172a'; Green='#22c55e'; GreenHover='#16a34a'; Blue='#60a5fa'; Red='#ef4444'; Yellow='#eab308'; T1='#f8fafc'; T2='#94a3b8'; T3='#64748b'; LogBg='#0a0f1a' }
    Light = @{ Bg='#f8fafc'; Card='#ffffff'; CardHover='#f1f5f9'; Border='#e2e8f0'; Input='#ffffff'; Green='#16a34a'; GreenHover='#15803d'; Blue='#3b82f6'; Red='#dc2626'; Yellow='#ca8a04'; T1='#0f172a'; T2='#475569'; T3='#94a3b8'; LogBg='#f1f5f9' }
}

# ============================================================================
# RECENT FILES, PROFILES, HISTORY
# ============================================================================

function Get-RecentFiles { if (Test-Path $script:RecentFile) { try { return @(Get-Content $script:RecentFile -Raw | ConvertFrom-Json | Where-Object { Test-Path $_ }) } catch { } }; return @() }
function Add-RecentFile { param([string]$F); $r = @(Get-RecentFiles); $r = @($F) + @($r | Where-Object { $_ -ne $F }) | Select-Object -First $script:Settings.MaxRecentFiles; $r | ConvertTo-Json | Set-Content -Path $script:RecentFile -Force }

$script:DefaultProfiles = @{
    'Default' = @{ Console=$false; Admin=$false; SingleFile=$true; Version='1.0.0.0'; Company=''; Copyright=''; Description=''; Product='' }
    'Console App' = @{ Console=$true; Admin=$false; SingleFile=$true; Version='1.0.0.0'; Company=''; Copyright=''; Description=''; Product='' }
    'Admin Tool' = @{ Console=$true; Admin=$true; SingleFile=$true; Version='1.0.0.0'; Company=''; Copyright=''; Description=''; Product='' }
    'GUI Application' = @{ Console=$false; Admin=$false; SingleFile=$true; Version='1.0.0.0'; Company=''; Copyright=''; Description=''; Product='' }
}

function Get-BuildProfiles { if (Test-Path $script:ProfilesFile) { try { $l = Get-Content $script:ProfilesFile -Raw | ConvertFrom-Json; $p = @{}; foreach ($prop in $l.PSObject.Properties) { $p[$prop.Name] = @{}; foreach ($sp in $prop.Value.PSObject.Properties) { $p[$prop.Name][$sp.Name] = $sp.Value } }; foreach ($k in $script:DefaultProfiles.Keys) { if (-not $p.ContainsKey($k)) { $p[$k] = $script:DefaultProfiles[$k] } }; return $p } catch { } }; return $script:DefaultProfiles.Clone() }
function Save-BuildProfiles { param([hashtable]$P); $P | ConvertTo-Json -Depth 3 | Set-Content -Path $script:ProfilesFile -Force }
function Save-BuildProfile { param([string]$N, [hashtable]$P); $profiles = Get-BuildProfiles; $profiles[$N] = $P; Save-BuildProfiles $profiles }

function Get-CompilationHistory { if (Test-Path $script:HistoryFile) { try { return @(Get-Content $script:HistoryFile -Raw | ConvertFrom-Json) } catch { } }; return @() }
function Add-CompilationHistory { param([string]$Src, [string]$Out, [string]$Type, [bool]$Success, [string]$Prof, [long]$Size); $h = @(Get-CompilationHistory); $e = @{ Timestamp=(Get-Date).ToString("o"); Source=$Src; Output=$Out; Type=$Type; Success=$Success; Profile=$Prof; Size=$Size }; $h = @($e) + $h | Select-Object -First $script:Settings.MaxHistoryItems; $h | ConvertTo-Json -Depth 3 | Set-Content -Path $script:HistoryFile -Force }

# ============================================================================
# NOTIFICATIONS & UTILITIES
# ============================================================================

function Show-ToastNotification { param([string]$Title, [string]$Message, [string]$Type='Info')
    if (-not $script:Settings.ShowNotifications) { return }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $balloon = New-Object System.Windows.Forms.NotifyIcon
        $balloon.Icon = [System.Drawing.SystemIcons]::Information
        $balloon.BalloonTipIcon = switch ($Type) { 'Success' { 'Info' } 'Error' { 'Error' } default { 'Info' } }
        $balloon.BalloonTipTitle = $Title
        $balloon.BalloonTipText = $Message
        $balloon.Visible = $true
        $balloon.ShowBalloonTip(5000)
        Start-Sleep -Milliseconds 5100
        $balloon.Dispose()
    } catch { }
}

function Get-EstimatedOutputSize { param([string]$Src, [string]$Type)
    if (-not (Test-Path $Src)) { return "Unknown" }
    $sz = (Get-Item $Src).Length
    $est = @{ 'ps1'=@{B=5MB;M=1.5}; 'py'=@{B=15MB;M=2}; 'bat'=@{B=50KB;M=1.2}; 'cmd'=@{B=50KB;M=1.2}; 'js'=@{B=40MB;M=1.5}; 'vbs'=@{B=50KB;M=1.2}; 'ahk'=@{B=1MB;M=1.3}; 'cs'=@{B=10KB;M=1.1}; 'go'=@{B=2MB;M=1.2}; 'rb'=@{B=20MB;M=2} }
    if ($est.ContainsKey($Type)) { $e = $est[$Type]; return (FmtSize ([long]($e.B + ($sz * $e.M)))) }
    return "Unknown"
}

function FmtSize { param([long]$S); if ($S -gt 1GB) { "{0:N1} GB" -f ($S/1GB) } elseif ($S -gt 1MB) { "{0:N1} MB" -f ($S/1MB) } elseif ($S -gt 1KB) { "{0:N1} KB" -f ($S/1KB) } else { "$S bytes" } }

function Export-BuildLog { param([string]$Log, [string]$Src)
    $ts = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"; $bn = [IO.Path]::GetFileNameWithoutExtension($Src)
    $path = Join-Path ([Environment]::GetFolderPath('Desktop')) "BuildLog_${bn}_${ts}.txt"
    $hdr = "================================================================================`r`nUniversal Compiler v$($script:AppVersion) - Build Log`r`n================================================================================`r`nDate: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`nSource: $Src`r`n================================================================================`r`n`r`n"
    Set-Content -Path $path -Value ($hdr + $Log) -Encoding UTF8; return $path
}

function Sign-Executable { param([string]$Exe, [string]$Cert, [string]$Pass)
    if (-not $Cert -or -not (Test-Path $Cert)) { return @{ Success=$false; Message="Certificate not found" } }
    if (-not (Test-Path $Exe)) { return @{ Success=$false; Message="Executable not found" } }
    try {
        $certificate = if ($Pass) { Get-PfxCertificate -FilePath $Cert -Password (ConvertTo-SecureString $Pass -AsPlainText -Force) } else { Get-PfxCertificate -FilePath $Cert }
        $result = Set-AuthenticodeSignature -FilePath $Exe -Certificate $certificate -TimestampServer "http://timestamp.digicert.com"
        if ($result.Status -eq 'Valid') { return @{ Success=$true; Message="Signed successfully" } }
        return @{ Success=$false; Message=$result.StatusMessage }
    } catch { return @{ Success=$false; Message=$_.Exception.Message } }
}

# ============================================================================
# TEMPLATE SCRIPTS
# ============================================================================

function Initialize-Templates {
    if (-not (Test-Path $script:TemplatesDir)) { New-Item -ItemType Directory -Path $script:TemplatesDir -Force | Out-Null }
    $templates = @{
        'HelloWorld.ps1' = "# PowerShell Hello World`r`nparam([string]`$Name = 'World')`r`nAdd-Type -AssemblyName PresentationFramework`r`n[System.Windows.MessageBox]::Show(`"Hello, `$Name!`", 'Hello', 'OK', 'Information')"
        'HelloWorld.py' = "# Python Hello World`r`nimport tkinter as tk`r`nfrom tkinter import messagebox`r`nroot = tk.Tk()`r`nroot.withdraw()`r`nmessagebox.showinfo('Hello', 'Hello, World!')`r`nroot.destroy()"
        'HelloWorld.bat' = "@echo off`r`necho Hello, World!`r`npause"
        'HelloWorld.js' = "// Node.js Hello World`r`nconsole.log('Hello, World!');"
        'HelloWorld.cs' = "using System; using System.Windows.Forms; class Program { [STAThread] static void Main() { MessageBox.Show(`"Hello, World!`", `"Hello`"); } }"
        'HelloWorld.go' = "package main`r`nimport `"fmt`"`r`nfunc main() { fmt.Println(`"Hello, World!`") }"
        'HelloWorld.rb' = "# Ruby Hello World`r`nputs 'Hello, World!'"
        'HelloWorld.vbs' = "MsgBox `"Hello, World!`", vbInformation, `"Hello`""
        'HelloWorld.ahk' = "MsgBox, Hello, World!"
    }
    foreach ($f in $templates.Keys) { $p = Join-Path $script:TemplatesDir $f; if (-not (Test-Path $p)) { Set-Content -Path $p -Value $templates[$f] -Encoding UTF8 } }
}

Initialize-Templates

# ============================================================================
# DEPENDENCY MANAGEMENT
# ============================================================================

function Write-InstallLog { param([string]$M); Add-Content -Path $script:LogFile -Value "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] $M" -ErrorAction SilentlyContinue }
function Test-PS2EXEInstalled { $m = Get-Module -ListAvailable -Name ps2exe -EA SilentlyContinue; if ($m) { return $true }; $paths = @((Join-Path ([Environment]::GetFolderPath('MyDocuments')) "WindowsPowerShell\Modules\ps2exe")); foreach ($p in $paths) { if (Test-Path (Join-Path $p "ps2exe.psm1")) { return $true } }; return $false }

function Install-PS2EXEDirect {
    $modPath = Join-Path ([Environment]::GetFolderPath('MyDocuments')) "WindowsPowerShell\Modules\ps2exe"
    if (-not (Test-Path $modPath)) { New-Item -ItemType Directory -Path $modPath -Force | Out-Null }
    $zipUrl = "https://github.com/MScholtes/PS2EXE/archive/refs/heads/master.zip"; $zipFile = Join-Path $env:TEMP "ps2exe_$(Get-Random).zip"; $extractDir = Join-Path $env:TEMP "ps2exe_ext_$(Get-Random)"
    try {
        $wc = New-Object System.Net.WebClient; $wc.Headers.Add("User-Agent", "PowerShell"); $wc.DownloadFile($zipUrl, $zipFile)
        Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::ExtractToDirectory($zipFile, $extractDir)
        $inner = Get-ChildItem -Path $extractDir -Directory | Select-Object -First 1; $src = Join-Path $inner.FullName "Module"
        if (-not (Test-Path $src)) { $src = $inner.FullName }
        Get-ChildItem -Path $src -File | ForEach-Object { Copy-Item $_.FullName $modPath -Force }
        return (Test-Path (Join-Path $modPath "ps2exe.psm1"))
    } catch { return $false } finally { Remove-Item $zipFile -Force -EA SilentlyContinue; Remove-Item $extractDir -Recurse -Force -EA SilentlyContinue }
}

function Install-GoDirect {
    try {
        $zipUrl = "https://go.dev/dl/go1.22.5.windows-amd64.zip"; $installDir = "$env:LOCALAPPDATA\Programs\Go"; $zipFile = Join-Path $env:TEMP "go_$(Get-Random).zip"
        $wc = New-Object System.Net.WebClient; $wc.Headers.Add("User-Agent", "PowerShell"); $wc.DownloadFile($zipUrl, $zipFile)
        $parent = Split-Path $installDir; if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        if (Test-Path $installDir) { Remove-Item $installDir -Recurse -Force }
        Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::ExtractToDirectory($zipFile, $parent)
        $goBin = Join-Path $installDir "bin"; $env:PATH = "$goBin;$env:PATH"
        $userPath = [Environment]::GetEnvironmentVariable("PATH", "User"); if ($userPath -notlike "*$goBin*") { [Environment]::SetEnvironmentVariable("PATH", "$goBin;$userPath", "User") }
        Remove-Item $zipFile -Force -EA SilentlyContinue; return (Test-Path (Join-Path $goBin "go.exe"))
    } catch { return $false }
}

function Install-RubyDirect {
    try {
        $url = "https://github.com/oneclick/rubyinstaller2/releases/download/RubyInstaller-3.2.4-1/rubyinstaller-3.2.4-1-x64.exe"; $inst = Join-Path $env:TEMP "ruby_$(Get-Random).exe"
        $wc = New-Object System.Net.WebClient; $wc.Headers.Add("User-Agent", "PowerShell"); $wc.DownloadFile($url, $inst)
        Start-Process -FilePath $inst -ArgumentList "/verysilent /norestart /tasks=modpath" -Wait; Remove-Item $inst -Force -EA SilentlyContinue
        $rubyPaths = @("C:\Ruby32-x64\bin", "C:\Ruby31-x64\bin"); foreach ($rp in $rubyPaths) { if (Test-Path (Join-Path $rp "ruby.exe")) { $env:PATH = "$rp;$env:PATH"; return $true } }
        return $false
    } catch { return $false }
}

function Install-AutoHotkeyDirect {
    try {
        $url = "https://www.autohotkey.com/download/ahk-v2.exe"; $inst = Join-Path $env:TEMP "ahk_$(Get-Random).exe"
        $wc = New-Object System.Net.WebClient; $wc.Headers.Add("User-Agent", "PowerShell"); $wc.DownloadFile($url, $inst)
        Start-Process -FilePath $inst -ArgumentList "/silent" -Wait; Remove-Item $inst -Force -EA SilentlyContinue
        $ahkPaths = @("${env:ProgramFiles}\AutoHotkey\Compiler\Ahk2Exe.exe", "${env:ProgramFiles}\AutoHotkey\v2\Compiler\Ahk2Exe.exe")
        foreach ($ap in $ahkPaths) { if (Test-Path $ap) { return $true } }; return $false
    } catch { return $false }
}

function Get-DependencyStatus {
    $deps = [ordered]@{
        'PS2EXE' = @{ Name='PS2EXE'; Desc='PowerShell (.ps1)'; Installed=(Test-PS2EXEInstalled); Size='~2 MB'; Func='Install-PS2EXE' }
        'PyInstaller' = @{ Name='PyInstaller'; Desc='Python (.py)'; Installed=$false; Size='~15 MB'; Func='Install-PyInstaller'; Req='Python' }
        'pkg' = @{ Name='pkg'; Desc='Node.js (.js)'; Installed=$false; Size='~50 MB'; Func='Install-Pkg'; Req='Node.js' }
        'Go' = @{ Name='Go'; Desc='Go (.go)'; Installed=$false; Size='~150 MB'; Func='Install-Go' }
        'Ruby' = @{ Name='Ruby+Ocra'; Desc='Ruby (.rb)'; Installed=$false; Size='~120 MB'; Func='Install-Ruby' }
        'AutoHotkey' = @{ Name='AutoHotkey'; Desc='AHK (.ahk)'; Installed=$false; Size='~5 MB'; Func='Install-AutoHotkey' }
        'CSC' = @{ Name='CSC'; Desc='C# (.cs)'; Installed=$false; Size='Built-in'; BuiltIn=$true }
        'IExpress' = @{ Name='IExpress'; Desc='Batch/VBS'; Installed=$false; Size='Built-in'; BuiltIn=$true }
    }
    # Check each
    $pyCmd = Get-Command python -EA SilentlyContinue; if ($pyCmd) { $deps['PyInstaller'].BaseOK=$true; $pipChk = & pip show pyinstaller 2>&1; $deps['PyInstaller'].Installed = ($pipChk -match "Name: pyinstaller") }
    $nodeCmd = Get-Command node -EA SilentlyContinue; if ($nodeCmd) { $deps['pkg'].BaseOK=$true; $pkgChk = & npm list -g pkg 2>&1; $deps['pkg'].Installed = ($pkgChk -match "pkg@") }
    $goCmd = Get-Command go -EA SilentlyContinue; if (-not $goCmd) { if (Test-Path "$env:LOCALAPPDATA\Programs\Go\bin\go.exe") { $goCmd = $true } }; $deps['Go'].Installed = ($null -ne $goCmd)
    $rubyCmd = Get-Command ruby -EA SilentlyContinue; if (-not $rubyCmd) { @("C:\Ruby32-x64\bin\ruby.exe","C:\Ruby31-x64\bin\ruby.exe") | ForEach-Object { if (Test-Path $_) { $rubyCmd = $_ } } }
    if ($rubyCmd) { $ocraChk = & gem list ocra 2>&1; $deps['Ruby'].Installed = ($ocraChk -match "ocra \(") }
    @("${env:ProgramFiles}\AutoHotkey\Compiler\Ahk2Exe.exe","${env:ProgramFiles}\AutoHotkey\v2\Compiler\Ahk2Exe.exe") | ForEach-Object { if (Test-Path $_) { $deps['AutoHotkey'].Installed = $true } }
    @("${env:WINDIR}\Microsoft.NET\Framework64\v4.0.30319\csc.exe","${env:WINDIR}\Microsoft.NET\Framework\v4.0.30319\csc.exe") | ForEach-Object { if (Test-Path $_) { $deps['CSC'].Installed = $true } }
    $deps['IExpress'].Installed = (Test-Path "$env:WINDIR\System32\iexpress.exe")
    return $deps
}

function Install-PS2EXE { $ok = Install-PS2EXEDirect; if (-not $ok) { try { Install-Module ps2exe -Scope CurrentUser -Force -EA Stop; $ok = $true } catch { } }; return $ok }
function Install-PyInstaller { try { & pip install pyinstaller --quiet 2>&1 | Out-Null; return ((& pip show pyinstaller 2>&1) -match "Name: pyinstaller") } catch { return $false } }
function Install-Pkg { try { & npm install -g pkg 2>&1 | Out-Null; return ((& npm list -g pkg 2>&1) -match "pkg@") } catch { return $false } }
function Install-Go { return Install-GoDirect }
function Install-Ruby { $ok = Install-RubyDirect; if ($ok) { Start-Sleep -Seconds 2; & gem install ocra --no-document 2>&1 | Out-Null }; return $ok }
function Install-AutoHotkey { return Install-AutoHotkeyDirect }

# ============================================================================
# SETUP GUI
# ============================================================================

function Show-SetupWindow {
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
    
    # Enable DPI Awareness
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class SetupDpiAwareness {
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
    [DllImport("shcore.dll")]
    public static extern int SetProcessDpiAwareness(int value);
}
"@ -ErrorAction SilentlyContinue
    try { [SetupDpiAwareness]::SetProcessDpiAwareness(2) | Out-Null } catch { try { [SetupDpiAwareness]::SetProcessDPIAware() | Out-Null } catch { } }
    
    $deps = Get-DependencyStatus
    $th = $script:Themes[$script:Settings.Theme]
    
    $xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Setup" Height="600" Width="650" WindowStartupLocation="CenterScreen" Background="$($th.Bg)" WindowStyle="None" AllowsTransparency="True" ResizeMode="NoResize">
    <Border Background="$($th.Bg)" CornerRadius="12" BorderBrush="$($th.Border)" BorderThickness="1">
        <Grid>
            <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
            <Border Background="$($th.Card)" CornerRadius="12,12,0,0" Padding="20,15">
                <Grid><StackPanel><StackPanel Orientation="Horizontal"><TextBlock Text="⚡" FontSize="22" Foreground="$($th.Green)" Margin="0,0,10,0"/><TextBlock Text="Universal Compiler" FontSize="20" FontWeight="Bold" Foreground="$($th.T1)"/><TextBlock Text="v2.0" FontSize="10" Foreground="$($th.T3)" VerticalAlignment="Bottom" Margin="8,0,0,4"/></StackPanel>
                <TextBlock Text="Select compilers to install" FontSize="11" Foreground="$($th.T2)" Margin="32,4,0,0"/></StackPanel>
                <Button x:Name="btnClose" Content="✕" HorizontalAlignment="Right" VerticalAlignment="Top" Background="Transparent" Foreground="$($th.T3)" BorderThickness="0" FontSize="14" Cursor="Hand" Padding="8,4"/></Grid>
            </Border>
            <ScrollViewer Grid.Row="1" Margin="15,10" VerticalScrollBarVisibility="Auto"><StackPanel x:Name="depList"/></ScrollViewer>
            <Border x:Name="progSection" Grid.Row="2" Background="$($th.LogBg)" Padding="15" Visibility="Collapsed"><StackPanel><TextBlock x:Name="progText" Text="Installing..." Foreground="$($th.T2)" FontSize="11" Margin="0,0,0,6"/><ProgressBar x:Name="progBar" Height="5" Background="$($th.Border)" Foreground="$($th.Green)" BorderThickness="0"/></StackPanel></Border>
            <Border Grid.Row="3" Background="$($th.Card)" CornerRadius="0,0,12,12" Padding="15">
                <Grid><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
                <TextBlock x:Name="lblSel" Text="0 selected" Foreground="$($th.T3)" FontSize="11" VerticalAlignment="Center"/>
                <Button x:Name="btnSkip" Grid.Column="1" Content="Skip" Padding="16,10" Margin="0,0,8,0" Background="$($th.Border)" Foreground="$($th.T1)" BorderThickness="0" Cursor="Hand"/>
                <Button x:Name="btnInstall" Grid.Column="2" Content="Install Selected" Padding="16,10" Background="$($th.Green)" Foreground="$($th.Bg)" FontWeight="SemiBold" BorderThickness="0" Cursor="Hand"/></Grid>
            </Border>
        </Grid>
    </Border>
</Window>
"@
    $reader = [System.Xml.XmlReader]::Create([System.IO.StringReader]::new($xaml)); $win = [Windows.Markup.XamlReader]::Load($reader)
    $btnClose = $win.FindName("btnClose"); $depList = $win.FindName("depList"); $progSection = $win.FindName("progSection"); $progText = $win.FindName("progText"); $progBar = $win.FindName("progBar"); $lblSel = $win.FindName("lblSel"); $btnSkip = $win.FindName("btnSkip"); $btnInstall = $win.FindName("btnInstall")
    
    $script:depCbs = @{}; $script:setupDone = $false
    
    foreach ($key in $deps.Keys) {
        $d = $deps[$key]
        $cardXaml = "<Border xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' Background='$($th.Card)' CornerRadius='6' Margin='0,0,0,6' Padding='12'><Grid><Grid.ColumnDefinitions><ColumnDefinition Width='Auto'/><ColumnDefinition Width='*'/><ColumnDefinition Width='Auto'/></Grid.ColumnDefinitions><CheckBox x:Name='chk' VerticalAlignment='Center' Margin='0,0,12,0'/><StackPanel Grid.Column='1'><StackPanel Orientation='Horizontal'><TextBlock x:Name='lblN' FontSize='13' FontWeight='SemiBold' Foreground='$($th.T1)'/><Border x:Name='badge' CornerRadius='3' Padding='5,1' Margin='8,0,0,0' Visibility='Collapsed'><TextBlock x:Name='badgeTxt' FontSize='9'/></Border></StackPanel><TextBlock x:Name='lblD' FontSize='10' Foreground='$($th.T2)' Margin='0,2,0,0'/></StackPanel><TextBlock Grid.Column='2' x:Name='lblS' Foreground='$($th.T3)' FontSize='10' VerticalAlignment='Center'/></Grid></Border>"
        $cReader = [System.Xml.XmlReader]::Create([System.IO.StringReader]::new($cardXaml)); $card = [Windows.Markup.XamlReader]::Load($cReader)
        $chk = $card.FindName("chk"); $lblN = $card.FindName("lblN"); $lblD = $card.FindName("lblD"); $lblS = $card.FindName("lblS"); $badge = $card.FindName("badge"); $badgeTxt = $card.FindName("badgeTxt")
        $lblN.Text = $d.Name; $lblD.Text = $d.Desc; $lblS.Text = $d.Size
        if ($d.Installed) { $badge.Visibility='Visible'; $badge.Background=[System.Windows.Media.BrushConverter]::new().ConvertFromString('#166534'); $badgeTxt.Text='✓ Installed'; $badgeTxt.Foreground=[System.Windows.Media.BrushConverter]::new().ConvertFromString($th.Green); $chk.IsEnabled=$false; $card.Opacity=0.6 }
        elseif ($d.BuiltIn) { $badge.Visibility='Visible'; $badge.Background=[System.Windows.Media.BrushConverter]::new().ConvertFromString('#1e3a5f'); $badgeTxt.Text='Built-in'; $badgeTxt.Foreground=[System.Windows.Media.BrushConverter]::new().ConvertFromString($th.Blue); $chk.IsEnabled=$false; $card.Opacity=0.6 }
        elseif ($d.Req -and -not $d.BaseOK) { $badge.Visibility='Visible'; $badge.Background=[System.Windows.Media.BrushConverter]::new().ConvertFromString('#7f1d1d'); $badgeTxt.Text="Needs $($d.Req)"; $badgeTxt.Foreground=[System.Windows.Media.BrushConverter]::new().ConvertFromString($th.Red); $chk.IsEnabled=$false; $card.Opacity=0.5 }
        else { $chk.IsChecked = ($key -eq 'PS2EXE') }
        $script:depCbs[$key] = @{ Cb=$chk; Dep=$d }
        $depList.Children.Add($card)
    }
    
    $updateSel = { $c=0; foreach ($k in $script:depCbs.Keys) { if ($script:depCbs[$k].Cb.IsChecked -and $script:depCbs[$k].Cb.IsEnabled) { $c++ } }; $lblSel.Text="$c selected"; $btnInstall.IsEnabled=($c -gt 0) }
    foreach ($k in $script:depCbs.Keys) { $script:depCbs[$k].Cb.Add_Checked($updateSel); $script:depCbs[$k].Cb.Add_Unchecked($updateSel) }
    & $updateSel
    
    $win.Add_MouseLeftButtonDown({ $win.DragMove() })
    $btnClose.Add_Click({ $win.Close() })
    $btnSkip.Add_Click({ $script:setupDone=$true; $win.Close() })
    $btnInstall.Add_Click({
        $btnInstall.IsEnabled=$false; $btnSkip.IsEnabled=$false; $progSection.Visibility='Visible'
        $toInstall = @(); foreach ($k in $script:depCbs.Keys) { $i=$script:depCbs[$k]; if ($i.Cb.IsChecked -and $i.Cb.IsEnabled -and -not $i.Dep.Installed) { $toInstall += @{K=$k;D=$i.Dep} } }
        if ($toInstall.Count -eq 0) { $script:setupDone=$true; $win.Close(); return }
        $progBar.Maximum=$toInstall.Count; $cur=0
        foreach ($inst in $toInstall) {
            $cur++; $progBar.Value=$cur; $progText.Text="Installing $($inst.D.Name)..."; $win.Dispatcher.Invoke([Action]{},[System.Windows.Threading.DispatcherPriority]::Background)
            $fn = $inst.D.Func; if ($fn) { try { & $fn | Out-Null } catch { } }
        }
        $progText.Text="Complete!"; Start-Sleep -Seconds 1; $script:setupDone=$true; $win.Close()
    })
    
    $win.ShowDialog() | Out-Null
    @{ DependenciesInstalled=$true; SetupDate=(Get-Date).ToString("o") } | ConvertTo-Json | Set-Content -Path $script:ConfigFile -Force
    return $script:setupDone
}

# ============================================================================
# MAIN APPLICATION
# ============================================================================

# Check for first run
$needSetup = $true
if (Test-Path $script:ConfigFile) { try { $cfg = Get-Content $script:ConfigFile -Raw | ConvertFrom-Json; if ($cfg.DependenciesInstalled) { $needSetup = $false } } catch { } }
if (($needSetup -or $ForceSetup) -and -not $SkipSetup) { Show-SetupWindow | Out-Null }

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Windows.Forms

# Enable DPI Awareness for sharp rendering on high-DPI displays
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class DpiAwareness {
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
    
    [DllImport("shcore.dll")]
    public static extern int SetProcessDpiAwareness(int value);
}
"@ -ErrorAction SilentlyContinue

try {
    # Try Per-Monitor DPI awareness (Windows 8.1+)
    [DpiAwareness]::SetProcessDpiAwareness(2) | Out-Null
} catch {
    try {
        # Fallback to System DPI awareness (Windows Vista+)
        [DpiAwareness]::SetProcessDPIAware() | Out-Null
    } catch { }
}

$th = $script:Themes[$script:Settings.Theme]

# ============================================================================
# MAIN WINDOW XAML
# ============================================================================

$mainXaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Universal Compiler v2.0" Height="900" Width="1200" WindowStartupLocation="CenterScreen" Background="$($th.Bg)" AllowDrop="True" MinHeight="600" MinWidth="800" WindowState="Maximized">
    <Window.Resources>
        <Style x:Key="BtnG" TargetType="Button"><Setter Property="Background" Value="$($th.Green)"/><Setter Property="Foreground" Value="$($th.Bg)"/><Setter Property="FontWeight" Value="SemiBold"/><Setter Property="Padding" Value="16,10"/><Setter Property="BorderThickness" Value="0"/><Setter Property="Cursor" Value="Hand"/><Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button"><Border x:Name="bd" Background="{TemplateBinding Background}" CornerRadius="5" Padding="{TemplateBinding Padding}"><ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/></Border><ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="bd" Property="Background" Value="$($th.GreenHover)"/></Trigger><Trigger Property="IsEnabled" Value="False"><Setter TargetName="bd" Property="Background" Value="$($th.Border)"/><Setter Property="Foreground" Value="$($th.T3)"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
        <Style x:Key="BtnS" TargetType="Button"><Setter Property="Background" Value="$($th.Border)"/><Setter Property="Foreground" Value="$($th.T1)"/><Setter Property="Padding" Value="12,8"/><Setter Property="BorderThickness" Value="0"/><Setter Property="Cursor" Value="Hand"/><Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button"><Border x:Name="bd" Background="{TemplateBinding Background}" CornerRadius="5" Padding="{TemplateBinding Padding}"><ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/></Border><ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="bd" Property="Background" Value="$($th.CardHover)"/></Trigger></ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter></Style>
        <Style x:Key="Txt" TargetType="TextBox"><Setter Property="Background" Value="$($th.Input)"/><Setter Property="Foreground" Value="$($th.T1)"/><Setter Property="BorderBrush" Value="$($th.Border)"/><Setter Property="BorderThickness" Value="1"/><Setter Property="Padding" Value="10,8"/><Setter Property="CaretBrush" Value="$($th.Green)"/></Style>
        <Style x:Key="Chk" TargetType="CheckBox"><Setter Property="Foreground" Value="$($th.T1)"/><Setter Property="Cursor" Value="Hand"/><Setter Property="Margin" Value="0,0,0,6"/></Style>
        <Style x:Key="Cmb" TargetType="ComboBox">
            <Setter Property="Background" Value="$($th.Input)"/>
            <Setter Property="Foreground" Value="$($th.T1)"/>
            <Setter Property="BorderBrush" Value="$($th.Border)"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="8,6"/>
            <Setter Property="ItemContainerStyle">
                <Setter.Value>
                    <Style TargetType="ComboBoxItem">
                        <Setter Property="Background" Value="$($th.Card)"/>
                        <Setter Property="Foreground" Value="$($th.T1)"/>
                        <Setter Property="Padding" Value="8,6"/>
                        <Setter Property="BorderThickness" Value="0"/>
                        <Style.Triggers>
                            <Trigger Property="IsHighlighted" Value="True">
                                <Setter Property="Background" Value="$($th.CardHover)"/>
                                <Setter Property="Foreground" Value="$($th.T1)"/>
                            </Trigger>
                            <Trigger Property="IsSelected" Value="True">
                                <Setter Property="Background" Value="$($th.Green)"/>
                                <Setter Property="Foreground" Value="$($th.Bg)"/>
                            </Trigger>
                        </Style.Triggers>
                    </Style>
                </Setter.Value>
            </Setter>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="ComboBox">
                        <Grid>
                            <ToggleButton x:Name="ToggleButton" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" Focusable="False" IsChecked="{Binding IsDropDownOpen, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}" ClickMode="Press">
                                <ToggleButton.Template>
                                    <ControlTemplate TargetType="ToggleButton">
                                        <Border x:Name="Border" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="4">
                                            <Grid>
                                                <Grid.ColumnDefinitions>
                                                    <ColumnDefinition/>
                                                    <ColumnDefinition Width="20"/>
                                                </Grid.ColumnDefinitions>
                                                <TextBlock Grid.Column="1" Text="▼" Foreground="$($th.T3)" FontSize="10" HorizontalAlignment="Center" VerticalAlignment="Center"/>
                                            </Grid>
                                        </Border>
                                    </ControlTemplate>
                                </ToggleButton.Template>
                            </ToggleButton>
                            <ContentPresenter x:Name="ContentSite" Content="{TemplateBinding SelectionBoxItem}" ContentTemplate="{TemplateBinding SelectionBoxItemTemplate}" HorizontalAlignment="Left" VerticalAlignment="Center" Margin="10,0,25,0" IsHitTestVisible="False"/>
                            <Popup x:Name="Popup" Placement="Bottom" IsOpen="{TemplateBinding IsDropDownOpen}" AllowsTransparency="True" Focusable="False" PopupAnimation="Slide">
                                <Grid x:Name="DropDown" SnapsToDevicePixels="True" MinWidth="{TemplateBinding ActualWidth}" MaxHeight="{TemplateBinding MaxDropDownHeight}">
                                    <Border x:Name="DropDownBorder" Background="$($th.Card)" BorderBrush="$($th.Border)" BorderThickness="1" CornerRadius="4" Margin="0,2,0,0">
                                        <ScrollViewer Margin="4" SnapsToDevicePixels="True">
                                            <StackPanel IsItemsHost="True" KeyboardNavigation.DirectionalNavigation="Contained"/>
                                        </ScrollViewer>
                                    </Border>
                                </Grid>
                            </Popup>
                        </Grid>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
    </Window.Resources>
    <Grid Margin="20">
        <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
        <!-- Header -->
        <Grid Margin="0,0,0,15">
            <StackPanel><StackPanel Orientation="Horizontal"><TextBlock Text="⚡" FontSize="26" Foreground="$($th.Green)" Margin="0,0,10,0"/><TextBlock Text="Universal Compiler" FontSize="24" FontWeight="Bold" Foreground="$($th.T1)"/><TextBlock Text="v2.0" FontSize="10" Foreground="$($th.T3)" VerticalAlignment="Bottom" Margin="8,0,0,4"/></StackPanel><TextBlock Text="Drag files here or browse to compile" FontSize="11" Foreground="$($th.T2)" Margin="36,2,0,0"/></StackPanel>
            <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Top">
                <Button x:Name="btnTheme" Content="🌙" Style="{StaticResource BtnS}" Padding="10,8" ToolTip="Toggle Theme" Margin="0,0,8,0"/>
                <Button x:Name="btnSettings" Content="⚙" Style="{StaticResource BtnS}" Padding="10,8" ToolTip="Settings"/>
            </StackPanel>
        </Grid>
        <!-- Main Content -->
        <Grid Grid.Row="1">
            <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="320"/></Grid.ColumnDefinitions>
            <ScrollViewer Margin="0,0,15,0" VerticalScrollBarVisibility="Auto">
                <StackPanel>
                    <!-- Drop Zone / Source -->
                    <Border x:Name="dropZone" Background="$($th.Card)" CornerRadius="8" Padding="20" Margin="0,0,0,12" BorderBrush="$($th.Green)" BorderThickness="2">
                        <StackPanel>
                            <TextBlock Text="📁 Source File" FontSize="13" FontWeight="SemiBold" Foreground="$($th.T1)" Margin="0,0,0,10"/>
                            <Grid><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
                                <TextBox x:Name="txtSource" Style="{StaticResource Txt}" IsReadOnly="True"/>
                                <Button x:Name="btnBrowse" Grid.Column="1" Content="Browse" Style="{StaticResource BtnS}" Margin="8,0,0,0"/>
                                <Button x:Name="btnRecent" Grid.Column="2" Content="▼" Style="{StaticResource BtnS}" Margin="4,0,0,0" Padding="8,8" ToolTip="Recent Files"/>
                            </Grid>
                            <Border x:Name="pnlInfo" Background="$($th.Border)" CornerRadius="5" Padding="10" Margin="0,10,0,0" Visibility="Collapsed">
                                <Grid><Grid.ColumnDefinitions><ColumnDefinition Width="80"/><ColumnDefinition Width="*"/><ColumnDefinition Width="80"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions><Grid.RowDefinitions><RowDefinition/><RowDefinition/><RowDefinition/></Grid.RowDefinitions>
                                    <TextBlock Text="Type:" Foreground="$($th.T3)" FontSize="10"/><TextBlock x:Name="lblType" Grid.Column="1" Foreground="$($th.Blue)" FontSize="10" FontWeight="Medium"/>
                                    <TextBlock Grid.Column="2" Text="Size:" Foreground="$($th.T3)" FontSize="10"/><TextBlock x:Name="lblSize" Grid.Column="3" Foreground="$($th.T2)" FontSize="10"/>
                                    <TextBlock Grid.Row="1" Text="Compiler:" Foreground="$($th.T3)" FontSize="10"/><TextBlock x:Name="lblCompiler" Grid.Row="1" Grid.Column="1" Foreground="$($th.Green)" FontSize="10" FontWeight="Medium"/>
                                    <TextBlock Grid.Row="1" Grid.Column="2" Text="Est. Output:" Foreground="$($th.T3)" FontSize="10"/><TextBlock x:Name="lblEstSize" Grid.Row="1" Grid.Column="3" Foreground="$($th.Yellow)" FontSize="10"/>
                                    <TextBlock Grid.Row="2" Text="Status:" Foreground="$($th.T3)" FontSize="10"/><TextBlock x:Name="lblStatus" Grid.Row="2" Grid.Column="1" Foreground="$($th.Green)" FontSize="10"/>
                                </Grid>
                            </Border>
                            <!-- Icon Preview -->
                            <Border x:Name="iconPreview" Background="$($th.Border)" CornerRadius="5" Padding="8" Margin="0,10,0,0" Visibility="Collapsed" HorizontalAlignment="Left">
                                <StackPanel Orientation="Horizontal"><Image x:Name="imgIcon" Width="32" Height="32" Margin="0,0,8,0"/><TextBlock x:Name="lblIconPath" Foreground="$($th.T2)" FontSize="10" VerticalAlignment="Center"/></StackPanel>
                            </Border>
                        </StackPanel>
                    </Border>
                    <!-- Output -->
                    <Border Background="$($th.Card)" CornerRadius="8" Padding="16" Margin="0,0,0,12">
                        <StackPanel>
                            <TextBlock Text="📤 Output" FontSize="13" FontWeight="SemiBold" Foreground="$($th.T1)" Margin="0,0,0,10"/>
                            <Grid><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
                                <StackPanel Margin="0,0,6,0"><TextBlock Text="Output Name" Foreground="$($th.T2)" FontSize="10" Margin="0,0,0,4"/><TextBox x:Name="txtOutName" Style="{StaticResource Txt}"/></StackPanel>
                                <StackPanel Grid.Column="1" Margin="6,0,0,0"><TextBlock Text="Output Directory" Foreground="$($th.T2)" FontSize="10" Margin="0,0,0,4"/><Grid><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions><TextBox x:Name="txtOutDir" Style="{StaticResource Txt}" IsReadOnly="True"/><Button x:Name="btnOutDir" Grid.Column="1" Content="..." Style="{StaticResource BtnS}" Margin="4,0,0,0" Padding="10,8"/></Grid></StackPanel>
                            </Grid>
                            <TextBlock Text="Custom Icon" Foreground="$($th.T2)" FontSize="10" Margin="0,10,0,4"/>
                            <Grid><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
                                <TextBox x:Name="txtIcon" Style="{StaticResource Txt}" IsReadOnly="True"/>
                                <Button x:Name="btnIcon" Grid.Column="1" Content="Browse" Style="{StaticResource BtnS}" Margin="4,0,0,0"/>
                                <Button x:Name="btnIconClear" Grid.Column="2" Content="✕" Style="{StaticResource BtnS}" Margin="4,0,0,0" Padding="8,8"/>
                            </Grid>
                        </StackPanel>
                    </Border>
                    <!-- Build Options -->
                    <Border Background="$($th.Card)" CornerRadius="8" Padding="16" Margin="0,0,0,12">
                        <StackPanel>
                            <StackPanel Orientation="Horizontal" Margin="0,0,0,10"><TextBlock Text="🔧 Build Options" FontSize="13" FontWeight="SemiBold" Foreground="$($th.T1)"/><TextBlock Text="Profile:" Foreground="$($th.T3)" FontSize="10" VerticalAlignment="Center" Margin="20,0,6,0"/><ComboBox x:Name="cmbProfile" Style="{StaticResource Cmb}" Width="140"/><Button x:Name="btnSaveProfile" Content="💾" Style="{StaticResource BtnS}" Padding="6,4" Margin="4,0,0,0" ToolTip="Save Profile"/></StackPanel>
                            <Grid><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
                                <StackPanel Margin="0,0,8,0">
                                    <CheckBox x:Name="chkConsole" Content="Console Application" Style="{StaticResource Chk}" ToolTip="Show console window when running"/>
                                    <CheckBox x:Name="chkAdmin" Content="Require Administrator" Style="{StaticResource Chk}" ToolTip="Request admin elevation on launch"/>
                                    <CheckBox x:Name="chkSingle" Content="Single File" Style="{StaticResource Chk}" IsChecked="True" ToolTip="Bundle everything into one EXE"/>
                                </StackPanel>
                                <StackPanel Grid.Column="1">
                                    <CheckBox x:Name="chkSign" Content="Code Sign" Style="{StaticResource Chk}" ToolTip="Sign with certificate after build"/>
                                    <CheckBox x:Name="chkNotify" Content="Notify on Complete" Style="{StaticResource Chk}" IsChecked="True" ToolTip="Show notification when build finishes"/>
                                </StackPanel>
                            </Grid>
                        </StackPanel>
                    </Border>
                    <!-- Post-Build Actions -->
                    <Border Background="$($th.Card)" CornerRadius="8" Padding="16" Margin="0,0,0,12">
                        <StackPanel>
                            <TextBlock Text="⚡ Post-Build Action" FontSize="13" FontWeight="SemiBold" Foreground="$($th.T1)" Margin="0,0,0,10"/>
                            <StackPanel Orientation="Horizontal">
                                <ComboBox x:Name="cmbPostBuild" Style="{StaticResource Cmb}" Width="180">
                                    <ComboBoxItem Content="None" IsSelected="True"/><ComboBoxItem Content="Open Output Folder"/><ComboBoxItem Content="Run Executable"/><ComboBoxItem Content="Copy to Folder..."/>
                                </ComboBox>
                                <TextBox x:Name="txtPostBuildPath" Style="{StaticResource Txt}" Width="200" Margin="8,0,0,0" Visibility="Collapsed"/>
                                <Button x:Name="btnPostBuildPath" Content="..." Style="{StaticResource BtnS}" Margin="4,0,0,0" Visibility="Collapsed"/>
                            </StackPanel>
                        </StackPanel>
                    </Border>
                    <!-- Metadata -->
                    <Border Background="$($th.Card)" CornerRadius="8" Padding="16">
                        <StackPanel>
                            <TextBlock Text="📝 Metadata" FontSize="13" FontWeight="SemiBold" Foreground="$($th.T1)" Margin="0,0,0,10"/>
                            <Grid><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
                                <StackPanel Margin="0,0,6,0"><TextBlock Text="Product Name" Foreground="$($th.T2)" FontSize="10" Margin="0,0,0,4"/><TextBox x:Name="txtProduct" Style="{StaticResource Txt}" Margin="0,0,0,8"/><TextBlock Text="Company" Foreground="$($th.T2)" FontSize="10" Margin="0,0,0,4"/><TextBox x:Name="txtCompany" Style="{StaticResource Txt}"/></StackPanel>
                                <StackPanel Grid.Column="1" Margin="6,0,0,0"><TextBlock Text="Version" Foreground="$($th.T2)" FontSize="10" Margin="0,0,0,4"/><TextBox x:Name="txtVersion" Style="{StaticResource Txt}" Text="1.0.0.0" Margin="0,0,0,8"/><TextBlock Text="Copyright" Foreground="$($th.T2)" FontSize="10" Margin="0,0,0,4"/><TextBox x:Name="txtCopyright" Style="{StaticResource Txt}"/></StackPanel>
                            </Grid>
                            <TextBlock Text="Description" Foreground="$($th.T2)" FontSize="10" Margin="0,10,0,4"/><TextBox x:Name="txtDesc" Style="{StaticResource Txt}" Height="40" TextWrapping="Wrap" AcceptsReturn="True"/>
                        </StackPanel>
                    </Border>
                </StackPanel>
            </ScrollViewer>
            <!-- Right Panel - Log & Actions -->
            <Grid Grid.Column="1">
                <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
                <!-- Batch Queue -->
                <Border Background="$($th.Card)" CornerRadius="8" Padding="12" Margin="0,0,0,10">
                    <StackPanel>
                        <StackPanel Orientation="Horizontal" Margin="0,0,0,8"><TextBlock Text="📋 Batch Queue" FontSize="12" FontWeight="SemiBold" Foreground="$($th.T1)"/><TextBlock x:Name="lblQueueCount" Text=" (0)" Foreground="$($th.T3)" FontSize="12"/></StackPanel>
                        <ListBox x:Name="lstQueue" Background="$($th.LogBg)" Foreground="$($th.T2)" BorderThickness="0" Height="60" FontSize="10"/>
                        <StackPanel Orientation="Horizontal" Margin="0,6,0,0"><Button x:Name="btnAddQueue" Content="+ Add" Style="{StaticResource BtnS}" Padding="8,4" FontSize="10"/><Button x:Name="btnClearQueue" Content="Clear" Style="{StaticResource BtnS}" Padding="8,4" FontSize="10" Margin="4,0,0,0"/></StackPanel>
                    </StackPanel>
                </Border>
                <!-- Build Log -->
                <Border Grid.Row="1" Background="$($th.Card)" CornerRadius="8" Padding="12">
                    <Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
                        <StackPanel Orientation="Horizontal" Margin="0,0,0,8"><TextBlock Text="📜 Build Log" FontSize="12" FontWeight="SemiBold" Foreground="$($th.T1)"/><Button x:Name="btnClearLog" Content="Clear" Style="{StaticResource BtnS}" Padding="6,2" FontSize="9" Margin="8,0,0,0"/><Button x:Name="btnExportLog" Content="Export" Style="{StaticResource BtnS}" Padding="6,2" FontSize="9" Margin="4,0,0,0"/></StackPanel>
                        <Border Grid.Row="1" Background="$($th.LogBg)" CornerRadius="5"><ScrollViewer x:Name="logScroll" VerticalScrollBarVisibility="Auto"><TextBlock x:Name="txtLog" Foreground="$($th.T2)" FontFamily="Consolas" FontSize="10" Padding="10" TextWrapping="Wrap"/></ScrollViewer></Border>
                    </Grid>
                </Border>
                <!-- Actions -->
                <StackPanel Grid.Row="2" Margin="0,10,0,0">
                    <ProgressBar x:Name="progress" Height="4" Background="$($th.Border)" Foreground="$($th.Green)" BorderThickness="0" Visibility="Collapsed" Margin="0,0,0,8"/>
                    <TextBlock x:Name="lblStatusBar" Text="Ready" Foreground="$($th.T3)" FontSize="10" HorizontalAlignment="Center" Margin="0,0,0,8"/>
                    <Grid><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
                        <Button x:Name="btnManageDeps" Content="Manage Deps" Style="{StaticResource BtnS}" Margin="0,0,4,0"/>
                        <Button x:Name="btnCompile" Grid.Column="1" Content="⚡ Compile" Style="{StaticResource BtnG}" Margin="4,0,0,0" IsEnabled="False"/>
                    </Grid>
                    <Button x:Name="btnCompileAll" Content="Compile All in Queue" Style="{StaticResource BtnS}" Margin="0,8,0,0" Visibility="Collapsed"/>
                    <Grid Margin="0,8,0,0"><Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
                        <Button x:Name="btnTemplates" Content="📄 Templates" Style="{StaticResource BtnS}" Margin="0,0,4,0"/>
                        <Button x:Name="btnHistory" Grid.Column="1" Content="📊 History" Style="{StaticResource BtnS}" Margin="4,0,0,0"/>
                    </Grid>
                </StackPanel>
            </Grid>
        </Grid>
        <!-- Footer -->
        <TextBlock Grid.Row="2" Text="Universal Compiler v2.0 • Drag &amp; Drop • Batch Build • Profiles • Code Signing" Foreground="$($th.T3)" FontSize="9" HorizontalAlignment="Center" Margin="0,12,0,0"/>
    </Grid>
</Window>
"@

$reader = [System.Xml.XmlReader]::Create([System.IO.StringReader]::new($mainXaml))
$window = [Windows.Markup.XamlReader]::Load($reader)

# Get all controls
$dropZone = $window.FindName("dropZone"); $txtSource = $window.FindName("txtSource"); $btnBrowse = $window.FindName("btnBrowse"); $btnRecent = $window.FindName("btnRecent")
$pnlInfo = $window.FindName("pnlInfo"); $lblType = $window.FindName("lblType"); $lblSize = $window.FindName("lblSize"); $lblCompiler = $window.FindName("lblCompiler"); $lblEstSize = $window.FindName("lblEstSize"); $lblStatus = $window.FindName("lblStatus")
$iconPreview = $window.FindName("iconPreview"); $imgIcon = $window.FindName("imgIcon"); $lblIconPath = $window.FindName("lblIconPath")
$txtOutName = $window.FindName("txtOutName"); $txtOutDir = $window.FindName("txtOutDir"); $btnOutDir = $window.FindName("btnOutDir")
$txtIcon = $window.FindName("txtIcon"); $btnIcon = $window.FindName("btnIcon"); $btnIconClear = $window.FindName("btnIconClear")
$cmbProfile = $window.FindName("cmbProfile"); $btnSaveProfile = $window.FindName("btnSaveProfile")
$chkConsole = $window.FindName("chkConsole"); $chkAdmin = $window.FindName("chkAdmin"); $chkSingle = $window.FindName("chkSingle"); $chkSign = $window.FindName("chkSign"); $chkNotify = $window.FindName("chkNotify")
$cmbPostBuild = $window.FindName("cmbPostBuild"); $txtPostBuildPath = $window.FindName("txtPostBuildPath"); $btnPostBuildPath = $window.FindName("btnPostBuildPath")
$txtProduct = $window.FindName("txtProduct"); $txtCompany = $window.FindName("txtCompany"); $txtVersion = $window.FindName("txtVersion"); $txtCopyright = $window.FindName("txtCopyright"); $txtDesc = $window.FindName("txtDesc")
$lstQueue = $window.FindName("lstQueue"); $lblQueueCount = $window.FindName("lblQueueCount"); $btnAddQueue = $window.FindName("btnAddQueue"); $btnClearQueue = $window.FindName("btnClearQueue")
$txtLog = $window.FindName("txtLog"); $logScroll = $window.FindName("logScroll"); $btnClearLog = $window.FindName("btnClearLog"); $btnExportLog = $window.FindName("btnExportLog")
$progress = $window.FindName("progress"); $lblStatusBar = $window.FindName("lblStatusBar")
$btnManageDeps = $window.FindName("btnManageDeps"); $btnCompile = $window.FindName("btnCompile"); $btnCompileAll = $window.FindName("btnCompileAll")
$btnTemplates = $window.FindName("btnTemplates"); $btnHistory = $window.FindName("btnHistory"); $btnTheme = $window.FindName("btnTheme"); $btnSettings = $window.FindName("btnSettings")

# State
$script:srcFile = $null; $script:fileType = $null; $script:outPath = $null; $script:compiling = $false; $script:batchQueue = @()

# Compiler definitions
$script:compilers = @{
    'ps1' = @{ Name='PowerShell'; Compiler='PS2EXE'; Desc='PowerShell Script'; Admin=$true; Console=$true; Check={ Test-PS2EXEInstalled } }
    'py' = @{ Name='Python'; Compiler='PyInstaller'; Desc='Python Script'; Admin=$true; Console=$true; Check={ (Get-Command pyinstaller -EA SilentlyContinue) -ne $null } }
    'bat' = @{ Name='Batch'; Compiler='IExpress'; Desc='Batch Script'; Admin=$true; Console=$true; Check={ Test-Path "$env:WINDIR\System32\iexpress.exe" } }
    'cmd' = @{ Name='Command'; Compiler='IExpress'; Desc='Command Script'; Admin=$true; Console=$true; Check={ Test-Path "$env:WINDIR\System32\iexpress.exe" } }
    'js' = @{ Name='Node.js'; Compiler='pkg'; Desc='JavaScript'; Admin=$false; Console=$true; Check={ (Get-Command pkg -EA SilentlyContinue) -ne $null } }
    'vbs' = @{ Name='VBScript'; Compiler='IExpress'; Desc='VBScript'; Admin=$true; Console=$false; Check={ Test-Path "$env:WINDIR\System32\iexpress.exe" } }
    'ahk' = @{ Name='AutoHotkey'; Compiler='Ahk2Exe'; Desc='AutoHotkey'; Admin=$true; Console=$false; Check={ @("${env:ProgramFiles}\AutoHotkey\Compiler\Ahk2Exe.exe","${env:ProgramFiles}\AutoHotkey\v2\Compiler\Ahk2Exe.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1 } }
    'cs' = @{ Name='C#'; Compiler='CSC'; Desc='C# Source'; Admin=$true; Console=$true; Check={ @("${env:WINDIR}\Microsoft.NET\Framework64\v4.0.30319\csc.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1 } }
    'go' = @{ Name='Go'; Compiler='go build'; Desc='Go Source'; Admin=$false; Console=$true; Check={ (Get-Command go -EA SilentlyContinue) -or (Test-Path "$env:LOCALAPPDATA\Programs\Go\bin\go.exe") } }
    'rb' = @{ Name='Ruby'; Compiler='Ocra'; Desc='Ruby Script'; Admin=$false; Console=$true; Check={ (Get-Command ocra -EA SilentlyContinue) -ne $null } }
}

# Helper functions
function Log { param([string]$M, [string]$L='Info'); $t = Get-Date -Format "HH:mm:ss"; $p = switch ($L) { 'Info'{'[*]'} 'Success'{'[OK]'} 'Warning'{'[!]'} 'Error'{'[X]'} default{'[*]'} }; $txtLog.Dispatcher.Invoke([Action]{ $txtLog.Text += "$t $p $M`r`n"; $logScroll.ScrollToEnd() }) }
function Status { param([string]$M); $lblStatusBar.Dispatcher.Invoke([Action]{ $lblStatusBar.Text = $M }) }
function Progress { param([int]$V, [bool]$Show=$true); $progress.Dispatcher.Invoke([Action]{ $progress.Value = $V; $progress.Visibility = if ($Show) { 'Visible' } else { 'Collapsed' } }) }
function TestCompiler { param([string]$T); $c = $script:compilers[$T]; if (-not $c) { return $false }; try { return [bool](& $c.Check) } catch { return $false } }

# Load profiles into combo
$profiles = Get-BuildProfiles
foreach ($pn in $profiles.Keys) { $cmbProfile.Items.Add($pn) | Out-Null }
$cmbProfile.SelectedItem = $script:Settings.DefaultProfile

# ============================================================================
# EVENT HANDLERS
# ============================================================================

# Load file function
function Load-SourceFile { param([string]$FilePath)
    if (-not (Test-Path $FilePath)) { return }
    $script:srcFile = $FilePath; $txtSource.Text = $FilePath
    Add-RecentFile $FilePath
    $ext = [IO.Path]::GetExtension($FilePath).TrimStart('.').ToLower()
    if ($script:compilers.ContainsKey($ext)) {
        $script:fileType = $ext; $ci = $script:compilers[$ext]
        $fi = Get-Item $FilePath; $lblType.Text = $ci.Desc; $lblSize.Text = FmtSize $fi.Length; $lblCompiler.Text = $ci.Compiler
        $lblEstSize.Text = Get-EstimatedOutputSize $FilePath $ext
        $avail = TestCompiler $ext
        $lblStatus.Text = if ($avail) { "Ready" } else { "Not installed" }
        $lblStatus.Foreground = [System.Windows.Media.BrushConverter]::new().ConvertFromString($(if ($avail) { $th.Green } else { $th.Red }))
        $pnlInfo.Visibility = 'Visible'
        $txtOutName.Text = [IO.Path]::GetFileNameWithoutExtension($FilePath) + ".exe"
        $txtOutDir.Text = Split-Path $FilePath
        $chkAdmin.IsEnabled = $ci.Admin; $chkConsole.IsEnabled = $ci.Console
        $btnCompile.IsEnabled = $avail
        Log "Loaded: $FilePath" -L Success
    } else {
        $script:fileType = $null; $pnlInfo.Visibility = 'Collapsed'; $btnCompile.IsEnabled = $false
        Log "Unsupported file type: $ext" -L Error
    }
}

# Drag & Drop
$window.Add_Drop({
    $files = $_.Data.GetData([Windows.Forms.DataFormats]::FileDrop)
    if ($files -and $files.Count -gt 0) {
        if ($files.Count -eq 1) { Load-SourceFile $files[0] }
        else { foreach ($f in $files) { $script:batchQueue += $f; $lstQueue.Items.Add([IO.Path]::GetFileName($f)) }; $lblQueueCount.Text = " ($($script:batchQueue.Count))"; $btnCompileAll.Visibility = 'Visible'; Log "Added $($files.Count) files to queue" -L Info }
    }
})
$window.Add_DragOver({ $_.Effects = [Windows.DragDropEffects]::Copy; $_.Handled = $true })

# Browse
$btnBrowse.Add_Click({
    $dlg = New-Object Microsoft.Win32.OpenFileDialog; $dlg.Title = "Select Script"; $dlg.Filter = "All Scripts|*.ps1;*.py;*.bat;*.cmd;*.js;*.vbs;*.ahk;*.cs;*.go;*.rb|All|*.*"
    if ($dlg.ShowDialog()) { Load-SourceFile $dlg.FileName }
})

# Recent files
$btnRecent.Add_Click({
    $recent = Get-RecentFiles
    if ($recent.Count -eq 0) { [System.Windows.MessageBox]::Show("No recent files", "Recent", 'OK', 'Information'); return }
    $cm = New-Object System.Windows.Controls.ContextMenu
    foreach ($rf in $recent) {
        $mi = New-Object System.Windows.Controls.MenuItem; $mi.Header = [IO.Path]::GetFileName($rf); $mi.Tag = $rf
        $mi.Add_Click({ Load-SourceFile $this.Tag })
        $cm.Items.Add($mi) | Out-Null
    }
    $cm.IsOpen = $true
})

# Icon browser
$btnIcon.Add_Click({ $dlg = New-Object Microsoft.Win32.OpenFileDialog; $dlg.Title = "Select Icon"; $dlg.Filter = "Icons|*.ico|All|*.*"; if ($dlg.ShowDialog()) { $txtIcon.Text = $dlg.FileName; try { $imgIcon.Source = [System.Windows.Media.Imaging.BitmapImage]::new([Uri]$dlg.FileName); $lblIconPath.Text = [IO.Path]::GetFileName($dlg.FileName); $iconPreview.Visibility = 'Visible' } catch { } } })
$btnIconClear.Add_Click({ $txtIcon.Text = ""; $iconPreview.Visibility = 'Collapsed' })

# Output dir
$btnOutDir.Add_Click({ $dlg = New-Object System.Windows.Forms.FolderBrowserDialog; if ($dlg.ShowDialog() -eq 'OK') { $txtOutDir.Text = $dlg.SelectedPath } })

# Post-build
$cmbPostBuild.Add_SelectionChanged({ $show = ($cmbPostBuild.SelectedIndex -eq 3); $txtPostBuildPath.Visibility = if ($show) { 'Visible' } else { 'Collapsed' }; $btnPostBuildPath.Visibility = $txtPostBuildPath.Visibility })
$btnPostBuildPath.Add_Click({ $dlg = New-Object System.Windows.Forms.FolderBrowserDialog; if ($dlg.ShowDialog() -eq 'OK') { $txtPostBuildPath.Text = $dlg.SelectedPath } })

# Profile selection
$cmbProfile.Add_SelectionChanged({
    $profiles = Get-BuildProfiles; $pn = $cmbProfile.SelectedItem
    if ($profiles.ContainsKey($pn)) {
        $p = $profiles[$pn]; $chkConsole.IsChecked = $p.Console; $chkAdmin.IsChecked = $p.Admin; $chkSingle.IsChecked = $p.SingleFile
        $txtVersion.Text = $p.Version; $txtCompany.Text = $p.Company; $txtCopyright.Text = $p.Copyright; $txtProduct.Text = $p.Product; $txtDesc.Text = $p.Description
    }
})

# Save profile
$btnSaveProfile.Add_Click({
    $name = $cmbProfile.Text; if (-not $name) { $name = "Custom" }
    $p = @{ Console=$chkConsole.IsChecked; Admin=$chkAdmin.IsChecked; SingleFile=$chkSingle.IsChecked; Version=$txtVersion.Text; Company=$txtCompany.Text; Copyright=$txtCopyright.Text; Product=$txtProduct.Text; Description=$txtDesc.Text }
    Save-BuildProfile $name $p
    if (-not $cmbProfile.Items.Contains($name)) { $cmbProfile.Items.Add($name) }
    Log "Profile '$name' saved" -L Success
})

# Queue
$btnAddQueue.Add_Click({ $dlg = New-Object Microsoft.Win32.OpenFileDialog; $dlg.Multiselect = $true; $dlg.Filter = "All Scripts|*.ps1;*.py;*.bat;*.cmd;*.js;*.vbs;*.ahk;*.cs;*.go;*.rb"; if ($dlg.ShowDialog()) { foreach ($f in $dlg.FileNames) { $script:batchQueue += $f; $lstQueue.Items.Add([IO.Path]::GetFileName($f)) }; $lblQueueCount.Text = " ($($script:batchQueue.Count))"; $btnCompileAll.Visibility = 'Visible' } })
$btnClearQueue.Add_Click({ $script:batchQueue = @(); $lstQueue.Items.Clear(); $lblQueueCount.Text = " (0)"; $btnCompileAll.Visibility = 'Collapsed' })

# Log
$btnClearLog.Add_Click({ $txtLog.Text = "" })
$btnExportLog.Add_Click({ if ($txtLog.Text -and $script:srcFile) { $path = Export-BuildLog $txtLog.Text $script:srcFile; Log "Log exported to: $path" -L Success; Start-Process explorer.exe "/select,`"$path`"" } })

# Theme toggle
$btnTheme.Add_Click({ $script:Settings.Theme = if ($script:Settings.Theme -eq 'Dark') { 'Light' } else { 'Dark' }; Save-AppSettings $script:Settings; [System.Windows.MessageBox]::Show("Theme changed to $($script:Settings.Theme). Please restart the app.", "Theme", 'OK', 'Information') })

# Templates
$btnTemplates.Add_Click({ Start-Process explorer.exe $script:TemplatesDir })

# History
$btnHistory.Add_Click({
    $hist = Get-CompilationHistory
    if ($hist.Count -eq 0) { [System.Windows.MessageBox]::Show("No compilation history", "History", 'OK', 'Information'); return }
    $msg = ""; foreach ($h in ($hist | Select-Object -First 10)) { $status = if ($h.Success) { "OK" } else { "FAIL" }; $msg += "[$status] $(Split-Path $h.Source -Leaf) -> $(Split-Path $h.Output -Leaf)`r`n" }
    [System.Windows.MessageBox]::Show($msg, "Recent Builds", 'OK', 'Information')
})

# Manage deps
$btnManageDeps.Add_Click({ $window.Hide(); Show-SetupWindow | Out-Null; $window.Show(); if ($script:fileType) { $avail = TestCompiler $script:fileType; $lblStatus.Text = if ($avail) { "Ready" } else { "Not installed" }; $btnCompile.IsEnabled = $avail } })

# Settings placeholder
$btnSettings.Add_Click({ [System.Windows.MessageBox]::Show("Settings: Theme=$($script:Settings.Theme), Notifications=$($script:Settings.ShowNotifications)", "Settings", 'OK', 'Information') })

# ============================================================================
# COMPILATION
# ============================================================================

function Compile-PS1 { param($Src, $Out, $Ico, $Admin, $NoConsole, $Meta)
    Log "Compiling PowerShell..." -L Info; Progress -V 20
    try {
        $modPath = Join-Path ([Environment]::GetFolderPath('MyDocuments')) "WindowsPowerShell\Modules\ps2exe"
        if (Test-Path (Join-Path $modPath "ps2exe.psm1")) { Import-Module (Join-Path $modPath "ps2exe.psm1") -Force } else { Import-Module ps2exe -Force }
        $params = @{ InputFile=$Src; OutputFile=$Out }
        if ($Ico -and (Test-Path $Ico)) { $params.IconFile = $Ico }
        if ($Admin) { $params.RequireAdmin = $true }
        if ($NoConsole) { $params.NoConsole = $true }
        if ($Meta.Title) { $params.Title = $Meta.Title }
        if ($Meta.Version) { $params.Version = $Meta.Version }
        if ($Meta.Company) { $params.Company = $Meta.Company }
        Progress -V 60; Invoke-PS2EXE @params 2>&1 | Out-Null; Progress -V 90
        return (Test-Path $Out)
    } catch { Log "Error: $($_.Exception.Message)" -L Error; return $false }
}

function Compile-Generic { param($Src, $Out, $Type)
    Log "Compiling $Type..." -L Info; Progress -V 30
    switch ($Type) {
        'py' { $pyi = (Get-Command pyinstaller -EA SilentlyContinue).Source; $dir = Split-Path $Out; $name = [IO.Path]::GetFileNameWithoutExtension($Out); & $pyi --distpath $dir --name $name --onefile --noconfirm $Src 2>&1 | Out-Null }
        'bat' { return Compile-BAT $Src $Out $null $false }
        'cmd' { return Compile-BAT $Src $Out $null $false }
        'js' { & pkg $Src --target node18-win-x64 --output $Out 2>&1 | Out-Null }
        'cs' { $csc = "${env:WINDIR}\Microsoft.NET\Framework64\v4.0.30319\csc.exe"; & $csc /out:$Out $Src 2>&1 | Out-Null }
        'go' { $goExe = if (Get-Command go -EA SilentlyContinue) { "go" } else { "$env:LOCALAPPDATA\Programs\Go\bin\go.exe" }; & $goExe build -o $Out $Src 2>&1 | Out-Null }
        'ahk' { $ahk = @("${env:ProgramFiles}\AutoHotkey\Compiler\Ahk2Exe.exe","${env:ProgramFiles}\AutoHotkey\v2\Compiler\Ahk2Exe.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1; & $ahk /in $Src /out $Out 2>&1 | Out-Null }
        'rb' { & ocra $Src --output $Out 2>&1 | Out-Null }
        'vbs' { return Compile-BAT $Src $Out $null $false }
    }
    Progress -V 90; return (Test-Path $Out)
}

function Compile-BAT { param($Src, $Out, $Ico, $Admin)
    $tmp = Join-Path $env:TEMP "uc_$(Get-Random)"; New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $bn = [IO.Path]::GetFileName($Src); Copy-Item $Src (Join-Path $tmp $bn) -Force
    $sed = "[Version]`r`nClass=IEXPRESS`r`nSEDVersion=3`r`n[Options]`r`nPackagePurpose=InstallApp`r`nShowInstallProgramWindow=0`r`nHideExtractAnimation=1`r`nUseLongFileName=1`r`nInsideCompressed=0`r`nCAB_FixedSize=0`r`nRebootMode=N`r`nTargetName=$Out`r`nFriendlyName=App`r`nAppLaunched=cmd /c `"$bn`"`r`nPostInstallCmd=<None>`r`nSourceFiles=SourceFiles`r`n[Strings]`r`n[SourceFiles]`r`nSourceFiles0=$tmp\`r`n[SourceFiles0]`r`n%FILE0%=$bn"
    Set-Content (Join-Path $tmp "c.sed") $sed -Encoding ASCII
    & "$env:WINDIR\System32\iexpress.exe" /N /Q (Join-Path $tmp "c.sed") 2>&1 | Out-Null
    Remove-Item $tmp -Recurse -Force -EA SilentlyContinue
    return (Test-Path $Out)
}

function Start-Compile {
    if ($script:compiling) { return }
    $script:compiling = $true; $btnCompile.IsEnabled = $false
    try {
        if (-not $script:srcFile) { Log "No source file" -L Error; return }
        $name = $txtOutName.Text.Trim(); if (-not $name.EndsWith('.exe')) { $name += '.exe' }
        $dir = $txtOutDir.Text.Trim(); if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $script:outPath = Join-Path $dir $name
        $ico = $txtIcon.Text.Trim(); if ($ico -and -not (Test-Path $ico)) { $ico = $null }
        $meta = @{ Title=$txtProduct.Text; Version=$txtVersion.Text; Company=$txtCompany.Text; Copyright=$txtCopyright.Text; Description=$txtDesc.Text }
        Log "========================================" -L Info; Log "Source: $($script:srcFile)" -L Info; Log "Output: $($script:outPath)" -L Info
        Status "Compiling..."; Progress -V 10
        $ok = $false
        switch ($script:fileType) {
            'ps1' { $ok = Compile-PS1 -Src $script:srcFile -Out $script:outPath -Ico $ico -Admin $chkAdmin.IsChecked -NoConsole (-not $chkConsole.IsChecked) -Meta $meta }
            default { $ok = Compile-Generic -Src $script:srcFile -Out $script:outPath -Type $script:fileType }
        }
        if ($ok -and (Test-Path $script:outPath)) {
            Progress -V 95
            # Code signing
            if ($chkSign.IsChecked -and $script:Settings.SigningCertPath) {
                Log "Signing executable..." -L Info
                $signResult = Sign-Executable $script:outPath $script:Settings.SigningCertPath $script:Settings.SigningCertPassword
                if ($signResult.Success) { Log "Code signed successfully" -L Success } else { Log "Signing failed: $($signResult.Message)" -L Warning }
            }
            Progress -V 100; $fi = Get-Item $script:outPath
            Log "========================================" -L Success; Log "BUILD SUCCESSFUL" -L Success; Log "Size: $(FmtSize $fi.Length)" -L Success
            Status "Complete!"
            Add-CompilationHistory $script:srcFile $script:outPath $script:fileType $true $cmbProfile.SelectedItem $fi.Length
            # Post-build
            switch ($cmbPostBuild.SelectedIndex) {
                1 { Start-Process explorer.exe "/select,`"$($script:outPath)`"" }
                2 { Start-Process $script:outPath }
                3 { if ($txtPostBuildPath.Text) { Copy-Item $script:outPath $txtPostBuildPath.Text -Force; Log "Copied to $($txtPostBuildPath.Text)" -L Info } }
            }
            # Notification
            if ($chkNotify.IsChecked) { Show-ToastNotification "Build Complete" "$(Split-Path $script:outPath -Leaf) compiled successfully" "Success" }
        } else {
            Status "Failed"; Log "BUILD FAILED" -L Error
            Add-CompilationHistory $script:srcFile $script:outPath $script:fileType $false $cmbProfile.SelectedItem 0
            if ($chkNotify.IsChecked) { Show-ToastNotification "Build Failed" "Compilation failed" "Error" }
        }
    } catch { Log "Error: $($_.Exception.Message)" -L Error; Status "Error" }
    finally { $script:compiling = $false; $btnCompile.IsEnabled = $true; Start-Sleep -Seconds 1; Progress -V 0 -Show $false }
}

$btnCompile.Add_Click({ Start-Compile })

# Batch compile
$btnCompileAll.Add_Click({
    if ($script:batchQueue.Count -eq 0) { return }
    $btnCompileAll.IsEnabled = $false; $total = $script:batchQueue.Count; $done = 0
    foreach ($f in $script:batchQueue) {
        Load-SourceFile $f
        if ($script:fileType -and (TestCompiler $script:fileType)) {
            Start-Compile; $done++
        }
    }
    $script:batchQueue = @(); $lstQueue.Items.Clear(); $lblQueueCount.Text = " (0)"; $btnCompileAll.Visibility = 'Collapsed'; $btnCompileAll.IsEnabled = $true
    Log "Batch complete: $done/$total files" -L Success
    if ($chkNotify.IsChecked) { Show-ToastNotification "Batch Complete" "$done of $total files compiled" "Success" }
})

# ============================================================================
# INITIALIZE
# ============================================================================

Log "Universal Compiler v2.0 ready" -L Success
Log "Drag files here or click Browse" -L Info
if (Test-PS2EXEInstalled) { Log "PS2EXE: Ready" -L Success } else { Log "PS2EXE: Not installed" -L Warning }
Status "Ready"

$window.ShowDialog() | Out-Null
