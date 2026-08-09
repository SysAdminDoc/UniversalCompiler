# ⚡ Universal Compiler

<p align="center">
  <img src="https://img.shields.io/badge/version-2.1.0-green?style=for-the-badge" alt="Version 2.1.0">
  <img src="https://img.shields.io/badge/platform-Windows-blue?style=for-the-badge" alt="Windows">
  <img src="https://img.shields.io/badge/PowerShell-5.1+-purple?style=for-the-badge" alt="PowerShell 5.1+">
  <img src="https://img.shields.io/badge/license-MIT-orange?style=for-the-badge" alt="MIT License">
</p>

<p align="center">
  <b>A capability-aware, offline-first source-to-artifact compiler</b>
</p>

<p align="center">
  Plan and build supported source types through the Python core, with optional Windows GUI shells and explicit artifact/runtime boundaries.
</p>

---

## ✨ Features

### 🎯 Multi-Language Support
| Language | Extension | Backend family | Lifecycle and artifact boundary |
|----------|-----------|-----------------|--------------------------------|
| PowerShell | `.ps1` | PS2EXE | Stable; capability-gated Windows PE output |
| Python | `.py`, `.pyw` | PyInstaller / Nuitka | Stable; capability-gated Windows PE output with packaging/runtime inputs |
| Batch | `.bat`, `.cmd` | IExpress | Stable; capability-gated self-extracting Windows package |
| Node.js | `.js` | Bun / Deno | Stable; capability-gated platform executable with bundled runtime semantics |
| Node.js (legacy) | `.js` | pkg | Deprecated; explicit-only and never auto-selected |
| TypeScript | `.ts` | Bun | Stable; capability-gated platform executable |
| C# | `.cs` | CSC (.NET) | Stable; managed executable with framework/deployment dependencies |
| Go | `.go` | go build | Stable; capability-gated native platform executable |
| Ruby | `.rb` | Ocra | Experimental; capability-gated Windows PE packaging |
| VBScript | `.vbs` | IExpress | Stable; capability-gated self-extracting Windows package |
| AutoHotkey | `.ahk` | Ahk2Exe | Stable; capability-gated Windows PE packaging |
| Rust | `.rs` | Cargo / rustc | Stable; capability-gated native platform executable |
| Lua | `.lua` | srlua / luastatic | Experimental; capability-gated Windows PE packaging |
| Perl | `.pl`, `.pm` | PAR::Packer | Experimental; capability-gated Windows PE packaging |
| Kotlin | `.kt`, `.kts` | Kotlin/Native | Experimental; capability-gated native platform executable |
| WebAssembly Text | `.wat` | wat2wasm | Stable; emits a `.wasm` module requiring a host/WASI runtime, not a Windows EXE |

The table describes backend policy, not installed-tool availability. Generate
the current host-specific matrix with `python .\UniversalCompiler.py
compatibility --json`; its `uc.compatibility.v1` output is authoritative for
availability, targets, architectures, lifecycle, and artifact boundaries.

### 🚀 Key Features

- **🖱️ Drag & Drop** - Simply drag files onto the window to compile
- **📋 Batch Compilation** - Compile multiple scripts at once
- **💾 Build Profiles** - Save and load compilation presets
- **📁 Recent Files** - Quick access to recently compiled scripts
- **🌙 Dark/Light Theme** - Toggle between themes for comfortable viewing
- **🔔 Toast Notifications** - Get notified when builds complete
- **📊 Compilation History** - Track all your previous builds
- **📄 Template Scripts** - Pre-made "Hello World" for all languages
- **⚡ Post-Build Actions** - Auto-run, open folder, or copy after build
- **📤 Export Build Log** - Save detailed logs for troubleshooting
- **📏 Size Estimation** - See estimated output size before compiling
- **🖼️ Icon Extraction** - Reuse an icon from an existing executable
- **📦 Unsigned MSIX** - Wrap a finished EXE for package distribution
- **💡 Tooltips** - Hover for helpful explanations
- **🖥️ DPI Aware** - Sharp rendering on high-DPI displays

---

## 📸 Screenshots

### Main Interface
```
┌─────────────────────────────────────────────────────────────────┐
│ ⚡ Universal Compiler v2.1.0                  [🌙 Theme] [⚙]    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  📁 SOURCE FILE (Drag & Drop)        │  📋 Batch Queue         │
│  ┌────────────────────────────────┐  │  ┌───────────────────┐  │
│  │ C:\Scripts\MyScript.ps1       │  │  │ script1.ps1       │  │
│  └────────────────────────────────┘  │  │ script2.py        │  │
│  Type: PowerShell │ Est: ~5.2 MB     │  └───────────────────┘  │
│                                      │                         │
│  📤 OUTPUT                           │  📜 Build Log           │
│  [MyScript.exe] [C:\Output]          │  ┌───────────────────┐  │
│                                      │  │ [OK] Ready        │  │
│  🔧 BUILD OPTIONS    Profile: [▼]    │  │ [*] Compiling...  │  │
│  ☐ Console  ☐ Admin  ☑ Single File  │  │ [OK] Complete!    │  │
│                                      │  └───────────────────┘  │
│  ⚡ POST-BUILD: [None ▼]             │                         │
│                                      │  [Manage Deps][Compile] │
│  📝 METADATA                         │                         │
│  Product | Version | Company         │  [Templates] [History]  │
│                                      │                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔧 Installation

### Quick Start

The Python CLI/core is the canonical build path:

1. Install Python 3.10 or newer.
2. Inspect the local capability contract:
   `python .\UniversalCompiler.py list-toolchains --json`
3. Preview or build without opening a window:
   `python .\UniversalCompiler.py build .\myscript.py --output .\dist\myscript.exe --verify`
4. Optionally use the Windows WPF shell with `.\UniversalCompiler.ps1 -SkipSetup`.

The Python GUI (`python .\UniversalCompiler.py` with no command) is optional
and requires an explicit `customtkinter` installation. Normal launches never
download or install tools.

### Requirements

- **Windows 10/11** for the supported WPF shell and built-in Windows backend paths
- **Python 3.10+** for the canonical CLI/core (CI exercises Python 3.12)
- **PowerShell 5.1+** and **.NET Framework 4.5+** for the optional WPF shell
- Python CLI builds do not install packages implicitly; install `customtkinter` explicitly only when launching the Python GUI

`version.json` is the authoritative application version source. The Python
core, PowerShell shell, VS Code extension manifest, README badge, and release
notes are checked consumers of that value; source artifact metadata versions
remain independent project metadata.

### Optional Dependencies

The setup wizard is opt-in. `-ForceSetup -SetupMode Install` uses the pinned
catalog and the selected package manager; it never downloads a mutable branch
archive or launches an untracked installer. The current catalog pins PS2EXE
1.12.0, PyInstaller 6.20.0, Go 1.22.5, Ocra 1.3.11, and AutoHotkey 2.0.18.

| Compiler | For | Acquisition |
|----------|-----|-------------|
| PS2EXE | PowerShell scripts | Pinned PowerShell Gallery package, or manual/offline verification |
| PyInstaller | Python scripts | Pinned pip package, or manual/offline verification |
| Bun / Deno | JavaScript and TypeScript scripts | Manual/SDK-managed; capability-gated |
| pkg (legacy) | JavaScript scripts | Deprecated; never selected or installed automatically |
| Go | Go scripts | Pinned winget package, or manual/offline verification |
| Ruby + Ocra | Ruby scripts | Pinned RubyGems package, or manual/offline verification |
| AutoHotkey | AHK scripts | Pinned winget package, or manual/offline verification |
| CSC | C# scripts | ✅ Built-in |
| IExpress | Batch/VBS | ✅ Built-in |

Setup modes are explicit and emit JSON suitable for support or automation:

```powershell
# Read-only capability and acquisition diagnostic; no setup state is written
.\UniversalCompiler.ps1 -SetupMode Diagnostic

# Plan all pinned acquisitions and write bounded provenance records; no package action
.\UniversalCompiler.ps1 -SetupMode DryRun

# Record manual acquisition instructions without changing installed tools
.\UniversalCompiler.ps1 -SetupMode Manual

# Verify an offline artifact; it is never executed or installed by this command
.\UniversalCompiler.ps1 -SetupMode OfflineArtifact -Toolchain pyinstaller `
  -ArtifactPath .\pyinstaller.whl -ExpectedSha256 <64-hex-sha256>

# The only mode that can invoke package-manager installation
.\UniversalCompiler.ps1 -ForceSetup -SetupMode Install
```

Acquisition records are retained at
`%APPDATA%\UniversalCompiler\toolchain-acquisitions.json` using the
`uc.toolchain-acquisition.v1` schema. Records include source URL, pinned
version, license, expected and observed SHA-256 fields, acquisition mode, and
result. Package-manager records identify package-manager integrity; offline
verification requires an explicit 64-character SHA-256 and records a measured
hash. No network telemetry is sent.

---

## 📖 Usage

### Basic Usage

1. **Launch** Universal Compiler
2. **Drag & drop** your script file (or click Browse)
3. **Configure** options as needed
4. **Click** "⚡ Compile"
5. **Done!** Your EXE is ready

### Command Line Usage

The same build engine is available without opening the GUI:

```powershell
# Preview a backend command without executing it
python .\UniversalCompiler.py build .\myscript.py --backend nuitka --preview

# Build with a named YAML profile and static artifact verification
python .\UniversalCompiler.py build .\myscript.py --profile Release --verify

# Keep the bounded default policy, or tune its per-command limits
python .\UniversalCompiler.py build .\myscript.py --timeout 900 --max-output-bytes 4194304

# Dependency installation is never implicit; both permissions and an approved lock are required
python .\UniversalCompiler.py build .\myscript.py --prefetch --dependency-lock .\universal-compiler.lock.json --allow-network --allow-dependency-install

# Build a queue in parallel
python .\UniversalCompiler.py batch .\src\*.py --jobs 4 --output-dir .\dist

# Produce x86/x64/arm64-named matrix artifacts when the toolchain supports them
python .\UniversalCompiler.py build .\myscript.go --matrix x86 x64 arm64 --jobs 3

# Cross-target Go/Rust/Bun builds when the installed SDK has that target
python .\UniversalCompiler.py build .\main.go --target linux --arch x64 --output .\dist\main

# Coalesce watch changes; Ctrl+C cancels the active process tree
python .\UniversalCompiler.py build .\myscript.py --watch --watch-debounce 0.35

# Compile Python bytecode only, or inspect local build analytics
python .\UniversalCompiler.py bytecode .\module.py
python .\UniversalCompiler.py analytics --recent 10
python .\UniversalCompiler.py diagnostics --recent 10 --json
python .\UniversalCompiler.py diagnostics --export .\diagnostics-export.json --allow-telemetry --json

# Select a message locale explicitly; English is the fallback for missing keys
python .\UniversalCompiler.py --locale es list-toolchains

# Inspect available toolchains or verify an artifact without running it
python .\UniversalCompiler.py list-toolchains
python .\UniversalCompiler.py list-toolchains --json
python .\UniversalCompiler.py list-toolchains --adapter vendor.backend --json
python .\UniversalCompiler.py compatibility --json
python .\UniversalCompiler.py inspect .\myscript.py --json
python .\UniversalCompiler.py verify .\dist\myscript.exe
python .\UniversalCompiler.py verify .\dist\myscript.exe.manifest.json
python .\UniversalCompiler.py extract-icon .\existing.exe --output .\icon.ico
python .\UniversalCompiler.py wrap-msix .\dist\myscript.exe --output .\dist\myscript.msix
python .\UniversalCompiler.py obfuscate .\myscript.py --method pyarmor --output .\obfuscated

# Generate a language-specific workflow template
python .\UniversalCompiler.py init-actions --language py

# Create and verify a local unsigned release evidence bundle
python .\UniversalCompiler.py release --artifact .\dist\myscript.exe --output-dir .\release --source-root . --json
python .\UniversalCompiler.py release verify --path .\release --json

# Initialize, inspect, migrate, or recover canonical project state
python .\UniversalCompiler.py manifest init --scope user --json
python .\UniversalCompiler.py manifest show --path .\universal-compiler.json --json
python .\UniversalCompiler.py manifest migrate --path .\universal-compiler.json --scope workspace --json
python .\UniversalCompiler.py manifest rollback --path .\universal-compiler.json --json

# Build from a canonical manifest instead of a legacy profiles file
python .\UniversalCompiler.py build .\myscript.py --manifest .\universal-compiler.json --profile Release
```

### Compatibility and artifact policy

Generate the current matrix without installing or executing a source artifact:

```powershell
python .\UniversalCompiler.py compatibility --json
python .\UniversalCompiler.py compatibility
```

The `uc.compatibility.v1` matrix is evaluated on the current host. Each entry
reports lifecycle (`stable`, `experimental`, `deprecated`, or `optional`),
installed availability, host support, target platforms, architectures,
required SDKs, and a structured artifact policy:

- Stable means the adapter contract and static verification path are supported;
  it does not mean the SDK is installed or that every target is available.
- Experimental means the adapter is retained for explicit use but has narrower
  packaging assumptions and should be validated with a fixture before release.
- Deprecated means it is never selected automatically. Archived Node `pkg` is
  explicit-only and remains in the matrix so migrations can identify it.
- Optional means the tool transforms an already-built artifact, such as UPX;
  it is not a source compiler.

Windows PE and native/platform executable entries still depend on their
declared runtime, SDK, library, permission, and asset inputs. A `.wasm` entry
is a WebAssembly module, not a Windows executable; its host APIs, imports,
WASI profile, and data files must be supplied by the deployment host. The
matrix never promises cross-compilation merely because a target name is
listed: `host_supported`, target, architecture, and installed tool identity
must all pass for a build plan.

Verification is static and side-effect bounded. It checks artifact structure,
hashes, and an adjacent versioned sidecar when present; it does not launch the
artifact, exercise its runtime, install it, or validate external assets and
permissions. Release dry-runs are unsigned and local. MSIX/APPX wrappers are
deliberately unsigned, so Windows trust/install behavior is not proven; treat
them as packaging or internal-test artifacts until a separately signed release
process exists outside this repository.

Builds are offline by default. Dependency prefetch requires an approved,
hash-addressed lock, an explicit cache/mirror policy, and both network and
installation permissions. Diagnostics and analytics stay local, redact source
and environment data, and require an explicit export opt-in; no telemetry is
sent. `manifest migrate` imports legacy settings/profiles/history
idempotently, while `manifest rollback` and adjacent `.bak` files provide
recoverable project-state paths. See [File Locations](#-file-locations) for
the state, lock, cache, analytics, diagnostics, and recovery boundaries.

Project state is stored in the versioned `%APPDATA%\UniversalCompiler\universal-compiler.json`
manifest by default. A workspace manifest lives beside the project and is selected
with `--manifest`; it records settings, profiles, history, analytics metadata, and
an explicit `user` or `workspace` scope. `manifest migrate` imports legacy
`profiles.yaml`/`profiles.json`, `settings.json`, and `history.json` files
idempotently. Unknown fields and future schema versions fail closed, while a valid
`.bak` is used for recovery after an interrupted or invalid write. `init-profiles`
remains available for creating a legacy YAML file for older integrations.

Builds use a content/toolchain cache
next to the output and opt-in dependency prefetching (`--prefetch
--dependency-lock <approved-lock> --allow-network --allow-dependency-install`).
The lock must use `uc.dependencies.v1`, declare `approved: true`, an explicit
offline/online mirror and cache policy, SHA-256 identities for its lock inputs,
and package hashes where the package manager supports them. The default
source-adjacent name is `universal-compiler.lock.json`; online policies also
require an HTTPS or file mirror. Cache keys and artifact manifests record the
lock hash, dependency input hash, policy, and declared toolchain versions.
Without `--prefetch`, builds do not invoke package managers or network fetches.
Every compiler command is
launched without a shell with bounded timeout/output capture, a minimal
inherited environment, and redacted diagnostics. Static verification checks
the produced container or PE header without launching the artifact, so a GUI
build cannot steal focus or alter the active desktop. Post-build actions never
launch the compiled artifact.
Builds use an isolated staging directory and publish atomically only after
static verification; concurrent requests targeting the same output are locked,
and duplicate batch outputs are rejected before any compiler starts. Watch mode
debounces rapid source changes, and its stop event is passed through to the
compiler process tree.
Successful builds also emit a versioned `*.manifest.json` sidecar containing
source/config/toolchain identities, artifact SHA-256 and size, verification,
warnings, and explicit unsigned status. `verify` checks both artifact structure
and that sidecar when it is present.
Build JSON results use `uc.result.v1` and include a correlation ID, bounded
phase timings, cache status, exit classification, redacted command metadata,
artifact hashes, and a privacy-safe `uc.diagnostics.v1` record. Local JSONL
diagnostics are retained for 30 days/2,000 events by default and can be
disabled with `--no-diagnostics` or redirected with
`--diagnostics-path`; the `diagnostics --export` command requires an explicit
`--allow-telemetry` opt-in and writes locally without network access. Source
contents, full environment values, and unredacted command output are excluded
from exported diagnostics.
MSIX/APPX output is deliberately unsigned and is intended for packaging or
internal testing only.

`release` is a local dry-run evidence command. It copies selected artifacts,
rewrites copied sidecars to their bundle paths, emits `SHA256SUMS`, a CycloneDX
JSON SBOM, SLSA-shaped provenance metadata, an unsigned release manifest, and
a static verification report. `release verify` rechecks those hashes and
artifact structures. It never signs, publishes, uploads, or needs credentials.

The optional `vscode-extension/` package adds a **Universal Compiler: Build
Active File** command. Point its `universalCompiler.scriptPath` setting at the
repository's `UniversalCompiler.py` when the extension is installed locally.
The extension launches the core with an argv array, an explicit workspace cwd,
an allowlisted environment, and a cancellable progress operation; it never
constructs or sends a terminal command. Configure `profile`, `target`,
`architecture`, `verify`, `recordDiagnostics`, `diagnosticsPath`, and explicit
namespaced `adapters` in VS Code settings. Failed builds are parsed into the
Problems panel and structured result details remain in the output channel
without forcing it into focus.
`compatibility --json` is the authoritative, schema-versioned host and artifact
matrix. `list-toolchains --json` remains the lower-level capability registry
consumed by the CLI, both GUI shells, and the VS Code extension; it reports
lifecycle, availability, host/target/architecture constraints, required SDKs,
and a verified tool version when the installed tool exposes one.
Automatic selection excludes deprecated backends; `pkg` remains available only
when explicitly requested. The PowerShell GUI also delegates builds to this
Python CLI and consumes its versioned JSON request/result contract; it remains
useful as a Windows shell and explicit toolchain setup surface.
External backend adapters use the versioned `uc.adapter.v1` contract and are
disabled by default. Enable an installed, namespaced entry point explicitly
with repeatable `--adapter namespace.name` options (or the
`UC_ADAPTER_ALLOWLIST` environment variable); discovery is deterministic and
conflicts fail closed.

The repository contract gate is `.github/workflows/ci.yml`. It runs the full
fake-runner/backend-plan matrix, artifact and migration tests, lint, and mypy
on an isolated temporary root. Every generated workflow action is pinned to a
full commit SHA, and installed-tool availability is never used to skip core
contract tests.

### Drag & Drop

- **Single file**: Loads immediately for compilation
- **Multiple files**: Adds to batch queue for bulk compilation

### Build Profiles

Save your favorite settings as profiles:

| Profile | Console | Admin | Use Case |
|---------|---------|-------|----------|
| Default | No | No | GUI applications |
| Console App | Yes | No | Command-line tools |
| Admin Tool | Yes | Yes | System utilities |
| GUI Application | No | No | Windows apps |

### Post-Build Actions

| Action | Description |
|--------|-------------|
| None | Just compile |
| Open Output Folder | Opens Explorer to the EXE location |
| Copy to Folder | Copies EXE to a specified directory |

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Drag & Drop` | Load file(s) |
| `Browse Button` | Open file dialog |
| `▼ Button` | Recent files menu |
| `F5` | Start the current build |
| `Escape` | Request cancellation of the active Python-shell build |
| `Alt+B` | Open the source-file picker |
| `Ctrl+L` | Focus the build log |

## ♿ Accessibility and Localization

Both GUI shells load the shared `resources\i18n\catalog.json` catalog. Locale
selection is explicit setting/command-line value first, then `UC_LOCALE`,
`UNIVERSAL_COMPILER_LOCALE`, `LC_ALL`/`LANG`, and the Windows UI culture;
unsupported regional variants fall back to their language and finally English.
Catalog messages use named placeholders and one/other plural forms. Number,
binary-size, and timestamp formatting use catalog separators and patterns
without changing the process-wide system locale. Use `--locale es` for the
Python CLI or `-Locale es` for PowerShell.

The Python shell declares semantic names and roles for its interactive controls,
uses a deterministic tab order, shows a two-pixel keyboard focus treatment,
and provides the F5/Escape/Alt+B/Ctrl+L golden-path bindings. Escape passes a
cancellation event into the bounded core process policy. The WPF shell exposes
`AutomationProperties.Name`, native control roles, a focus visual style,
keyboard tab navigation, and a high-contrast palette. Native WPF UI Automation
peers are the assistive-technology surface; the Python shell's semantic
registry is the cross-platform fallback for Tk/customtkinter bridges.

The headless contract suite validates catalog fallback/plurals/formatting,
keyboard bindings, semantic role declarations, WPF automation names, focus
styles, high-contrast branches, and WCAG relative-luminance contrast ratios for
the normal and high-contrast palettes. Interactive screen-reader checks remain a
Windows UI Automation/manual acceptance step because launching a GUI is not
part of the noninteractive build/test suite.

---

## 📁 File Locations

| Item | Location |
|------|----------|
| Configuration | `%APPDATA%\UniversalCompiler\config.json` |
| Canonical project state | `%APPDATA%\UniversalCompiler\universal-compiler.json` |
| Workspace project state | `<project>\universal-compiler.json` |
| Dependency lock | `<project>\universal-compiler.lock.json` |
| Legacy Build Profiles | `%APPDATA%\UniversalCompiler\profiles.yaml` (imported on migration) |
| Legacy Compilation History | `%APPDATA%\UniversalCompiler\history.json` (imported on migration) |
| Local Analytics | `%APPDATA%\UniversalCompiler\analytics.sqlite3` |
| Recent Files | `%APPDATA%\UniversalCompiler\recent.json` |
| Legacy Settings | `%APPDATA%\UniversalCompiler\settings.json` (imported on migration) |
| Templates | `%APPDATA%\UniversalCompiler\Templates\` |
| Install Log | `%APPDATA%\UniversalCompiler\install.log` |
| State locks | Adjacent hidden `.<filename>.lock` files; released by the owning process |
| Recovery backups | Adjacent `<filename>.bak` files for user/project JSON/YAML state |

State under `%APPDATA%\UniversalCompiler` is private to the current Windows
user. Workspace manifests, dependency locks, and output-side caches remain
beside the selected project or output; analytics, diagnostics, and
installation logs remain private in the user profile. State writes use a
bounded cross-process lock and an
fsync-before-replace sequence; a valid `.bak` is restored after an interrupted
write. Cache files are disposable and can be deleted without losing profiles
or history.

Analytics uses SQLite WAL with a 10-second busy timeout, explicit immediate
writer transactions, and a validated atomic backup. Back up or recover it
without opening the GUI:

```powershell
python .\UniversalCompiler.py analytics --path .\analytics.sqlite3 --backup --json
python .\UniversalCompiler.py analytics --path .\analytics.sqlite3 --recover --json
```

The default analytics location is the per-user path above; `--path` is the
explicit project or test database path. Builds remain offline by default and
can disable analytics entirely with `--no-analytics`.

---

## 🎨 Themes

### Dark Theme (Default)
- Background: `#020617`
- Cards: `#0f172a`
- Accent: `#22c55e` (Green)
- Text: `#f8fafc`

### Light Theme
- Background: `#f8fafc`
- Cards: `#ffffff`
- Accent: `#16a34a` (Green)
- Text: `#0f172a`

Toggle themes with the 🌙 button in the header.

---

## 📄 Template Scripts

Universal Compiler includes "Hello World" templates for all supported languages:

```
%APPDATA%\UniversalCompiler\Templates\
├── HelloWorld.ps1    # PowerShell
├── HelloWorld.py     # Python
├── HelloWorld.bat    # Batch
├── HelloWorld.js     # Node.js
├── HelloWorld.cs     # C#
├── HelloWorld.go     # Go
├── HelloWorld.rb     # Ruby
├── HelloWorld.vbs    # VBScript
└── HelloWorld.ahk    # AutoHotkey
```

Access templates via the **"📄 Templates"** button.

---

## 🔍 Troubleshooting

### Common Issues

**"PS2EXE not found"**
```powershell
# Manual installation
Install-Module ps2exe -Scope CurrentUser -Force
```

**"PyInstaller not found"**
```bash
pip install pyinstaller
```

**"No active JavaScript backend"**
```bash
python .\UniversalCompiler.py list-toolchains --json
```
Install a supported Bun or Deno SDK and rerun the capability probe. The
archived `pkg` backend is available only as an explicit legacy override.

**Window appears cut off**
- The app now opens maximized by default
- Supports high-DPI displays (125%, 150%, 200%)

**Compilation fails**
1. Check the Build Log for errors
2. Click "Export Log" to save detailed output
3. Verify the compiler is installed via "Manage Deps"

### Reset Configuration

To reset all settings:
```powershell
Remove-Item "$env:APPDATA\UniversalCompiler" -Recurse -Force
```

---

## 🛠️ Command Line Usage

```powershell
# Basic usage
.\UniversalCompiler.ps1

# Skip setup wizard
.\UniversalCompiler.ps1 -SkipSetup

# Force re-run setup
.\UniversalCompiler.ps1 -ForceSetup -SetupMode Install

# Inspect setup without installing anything
.\UniversalCompiler.ps1 -SetupMode Diagnostic
.\UniversalCompiler.ps1 -SetupMode DryRun
```

---

## 📝 Changelog

### v2.1.0 — 2026-08-03
- ✨ Complete UI redesign with modern dark theme
- 🖱️ Drag & drop support
- 📋 Batch compilation
- 💾 Build profiles system
- 📁 Recent files tracking
- 🌙 Light/Dark theme toggle
- 🔔 Toast notifications
- 📊 Compilation history
- 📄 Template scripts
- ⚡ Post-build actions
- 📤 Export build logs
- 📏 Size estimation
- 💡 Tooltips
- 🖥️ DPI awareness
- 🎨 Styled dropdown menus

### v1.0 — 2026-04-13
- Initial release
- Basic compilation support
- Console-based setup

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- [PS2EXE](https://github.com/MScholtes/PS2EXE) - PowerShell to EXE compiler
- [PyInstaller](https://pyinstaller.org/) - Python to EXE compiler
- [Bun](https://bun.sh/) and [Deno](https://deno.com/) - JavaScript compilers
- [pkg](https://github.com/vercel/pkg) - Node.js to EXE compiler
- [Ocra](https://github.com/larsch/ocra) - Ruby to EXE compiler

---

<p align="center">
  <a href="#-universal-compiler">Back to top ⬆️</a>
</p>
