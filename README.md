# ⚡ Universal Compiler

<p align="center">
  <img src="https://img.shields.io/badge/version-2.1.0-green?style=for-the-badge" alt="Version 2.1.0">
  <img src="https://img.shields.io/badge/platform-Windows-blue?style=for-the-badge" alt="Windows">
  <img src="https://img.shields.io/badge/PowerShell-5.1+-purple?style=for-the-badge" alt="PowerShell 5.1+">
  <img src="https://img.shields.io/badge/license-MIT-orange?style=for-the-badge" alt="MIT License">
</p>

<p align="center">
  <b>A powerful, all-in-one script-to-EXE compiler</b>
</p>

<p align="center">
  Compile PowerShell, Python, Batch, Node.js, C#, Go, Ruby, VBScript, and AutoHotkey scripts into standalone Windows executables with just a few clicks.
</p>

---

## ✨ Features

### 🎯 Multi-Language Support
| Language | Extension | Compiler | Status |
|----------|-----------|----------|--------|
| PowerShell | `.ps1` | PS2EXE | ✅ Full Support |
| Python | `.py` | PyInstaller / Nuitka | ✅ Full Support |
| Batch | `.bat`, `.cmd` | IExpress | ✅ Full Support |
| Node.js | `.js` | Bun compile / Deno compile | Capability-gated; legacy pkg is explicit-only |
| TypeScript | `.ts` | Bun `build --compile` | ✅ Full Support |
| C# | `.cs` | CSC (.NET) | ✅ Full Support |
| Go | `.go` | go build | ✅ Full Support |
| Ruby | `.rb` | Ocra | ✅ Full Support |
| VBScript | `.vbs` | IExpress | ✅ Full Support |
| AutoHotkey | `.ahk` | Ahk2Exe | ✅ Full Support |
| Rust | `.rs` | Cargo / rustc | ✅ Full Support |
| Lua | `.lua` | srlua / luastatic | ✅ Full Support |
| Perl | `.pl`, `.pm` | PAR::Packer | ✅ Full Support |
| Kotlin | `.kt`, `.kts` | Kotlin/Native | ✅ Full Support |
| WebAssembly Text | `.wat` | wat2wasm | ✅ Full Support |

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

1. **Download** `UniversalCompiler.ps1`
2. **Right-click** → "Run with PowerShell"
3. **Install compilers explicitly** with `-ForceSetup` when you want the setup wizard; normal launches never download or install tools

### Requirements

- **Windows 10/11** (Windows 7/8 may work with limitations)
- **PowerShell 5.1+** (included with Windows 10+)
- **.NET Framework 4.5+** (included with Windows 10+)
- Python CLI builds do not install packages implicitly; install `customtkinter` explicitly only when launching the Python GUI

### Optional Dependencies

The explicitly launched setup wizard can install these for you:

| Compiler | For | Auto-Install |
|----------|-----|--------------|
| PS2EXE | PowerShell scripts | ✅ Yes |
| PyInstaller | Python scripts | ✅ Yes (requires Python) |
| Bun / Deno | JavaScript and TypeScript scripts | Manual/SDK-managed; capability-gated |
| pkg (legacy) | JavaScript scripts | ✅ Yes when explicitly requested |
| Go | Go scripts | ✅ Yes |
| Ruby + Ocra | Ruby scripts | ✅ Yes |
| AutoHotkey | AHK scripts | ✅ Yes |
| CSC | C# scripts | ✅ Built-in |
| IExpress | Batch/VBS | ✅ Built-in |

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

# Dependency installation is never implicit; both permissions are required
python .\UniversalCompiler.py build .\myscript.py --prefetch --allow-network --allow-dependency-install

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

# Inspect available toolchains or verify an artifact without running it
python .\UniversalCompiler.py list-toolchains
python .\UniversalCompiler.py list-toolchains --json
python .\UniversalCompiler.py inspect .\myscript.py --json
python .\UniversalCompiler.py verify .\dist\myscript.exe
python .\UniversalCompiler.py verify .\dist\myscript.exe.manifest.json
python .\UniversalCompiler.py extract-icon .\existing.exe --output .\icon.ico
python .\UniversalCompiler.py wrap-msix .\dist\myscript.exe --output .\dist\myscript.msix
python .\UniversalCompiler.py obfuscate .\myscript.py --method pyarmor --output .\obfuscated

# Generate a language-specific workflow template
python .\UniversalCompiler.py init-actions --language py
```

Profiles are stored in `%APPDATA%\UniversalCompiler\profiles.yaml`; use
`init-profiles` to create a starter file. Builds use a content/toolchain cache
next to the output and opt-in dependency prefetching (`--prefetch
--allow-network --allow-dependency-install`). Every compiler command is
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
MSIX/APPX output is deliberately unsigned and is intended for packaging or
internal testing only.

The optional `vscode-extension/` package adds a **Universal Compiler: Build
Active File** command. Point its `universalCompiler.scriptPath` setting at the
repository's `UniversalCompiler.py` when the extension is installed locally.
`list-toolchains --json` is the authoritative, schema-versioned capability
registry consumed by the CLI, both GUI shells, and the VS Code extension. It
reports lifecycle, availability, host/target/architecture constraints,
required SDKs, and a verified tool version when the installed tool exposes one.
Automatic selection excludes deprecated backends; `pkg` remains available only
when explicitly requested. The PowerShell GUI also delegates builds to this
Python CLI and consumes its versioned JSON request/result contract; it remains
useful as a Windows shell and explicit toolchain setup surface.

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

---

## 📁 File Locations

| Item | Location |
|------|----------|
| Configuration | `%APPDATA%\UniversalCompiler\config.json` |
| Build Profiles | `%APPDATA%\UniversalCompiler\profiles.yaml` |
| Compilation History | `%APPDATA%\UniversalCompiler\history.json` |
| Local Analytics | `%APPDATA%\UniversalCompiler\analytics.sqlite3` |
| Recent Files | `%APPDATA%\UniversalCompiler\recent.json` |
| Settings | `%APPDATA%\UniversalCompiler\settings.json` |
| Templates | `%APPDATA%\UniversalCompiler\Templates\` |
| Install Log | `%APPDATA%\UniversalCompiler\install.log` |

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
.\UniversalCompiler.ps1 -ForceSetup
```

---

## 📝 Changelog

### v2.1.0
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

### v1.0
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
