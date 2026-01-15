# ⚡ Universal Compiler

<p align="center">
  <img src="https://img.shields.io/badge/version-2.0-green?style=for-the-badge" alt="Version 2.0">
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
| Python | `.py` | PyInstaller | ✅ Full Support |
| Batch | `.bat`, `.cmd` | IExpress | ✅ Full Support |
| Node.js | `.js` | pkg | ✅ Full Support |
| C# | `.cs` | CSC (.NET) | ✅ Full Support |
| Go | `.go` | go build | ✅ Full Support |
| Ruby | `.rb` | Ocra | ✅ Full Support |
| VBScript | `.vbs` | IExpress | ✅ Full Support |
| AutoHotkey | `.ahk` | Ahk2Exe | ✅ Full Support |

### 🚀 Key Features

- **🖱️ Drag & Drop** - Simply drag files onto the window to compile
- **📋 Batch Compilation** - Compile multiple scripts at once
- **💾 Build Profiles** - Save and load compilation presets
- **📁 Recent Files** - Quick access to recently compiled scripts
- **🌙 Dark/Light Theme** - Toggle between themes for comfortable viewing
- **🔐 Code Signing** - Sign executables with PFX certificates
- **🔔 Toast Notifications** - Get notified when builds complete
- **📊 Compilation History** - Track all your previous builds
- **📄 Template Scripts** - Pre-made "Hello World" for all languages
- **⚡ Post-Build Actions** - Auto-run, open folder, or copy after build
- **📤 Export Build Log** - Save detailed logs for troubleshooting
- **📏 Size Estimation** - See estimated output size before compiling
- **💡 Tooltips** - Hover for helpful explanations
- **🖥️ DPI Aware** - Sharp rendering on high-DPI displays

---

## 📸 Screenshots

### Main Interface
```
┌─────────────────────────────────────────────────────────────────┐
│ ⚡ Universal Compiler v2.0                    [🌙 Theme] [⚙]    │
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
3. **First Run**: The setup wizard will guide you through installing compilers

### Requirements

- **Windows 10/11** (Windows 7/8 may work with limitations)
- **PowerShell 5.1+** (included with Windows 10+)
- **.NET Framework 4.5+** (included with Windows 10+)

### Optional Dependencies

The setup wizard can automatically install these for you:

| Compiler | For | Auto-Install |
|----------|-----|--------------|
| PS2EXE | PowerShell scripts | ✅ Yes |
| PyInstaller | Python scripts | ✅ Yes (requires Python) |
| pkg | Node.js scripts | ✅ Yes (requires Node.js) |
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
| Run Executable | Launches the compiled EXE |
| Copy to Folder | Copies EXE to a specified directory |

### Code Signing

To sign your executables:

1. Go to **Settings**
2. Set your **Certificate Path** (`.pfx` file)
3. Enter **Certificate Password**
4. Check **"Code Sign"** in Build Options

---

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
| Build Profiles | `%APPDATA%\UniversalCompiler\profiles.json` |
| Compilation History | `%APPDATA%\UniversalCompiler\history.json` |
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

**"pkg not found"**
```bash
npm install -g pkg
```

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

### v2.0
- ✨ Complete UI redesign with modern dark theme
- 🖱️ Drag & drop support
- 📋 Batch compilation
- 💾 Build profiles system
- 📁 Recent files tracking
- 🌙 Light/Dark theme toggle
- 🔐 Code signing support
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
- [pkg](https://github.com/vercel/pkg) - Node.js to EXE compiler
- [Ocra](https://github.com/larsch/ocra) - Ruby to EXE compiler

---

<p align="center">
  <a href="#-universal-compiler">Back to top ⬆️</a>
</p>
