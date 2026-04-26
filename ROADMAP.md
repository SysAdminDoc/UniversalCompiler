# Roadmap

Forward-looking plans for Universal Compiler — script-to-EXE compiler with drag-and-drop GUI, supporting PS1, PY, BAT, JS, CS, GO, RB, VBS, AHK. v2.0 today.

## Planned Features

### Language Coverage
- Rust (`cargo build --release`) with optional UPX pass
- Lua (srlua / luastatic)
- Perl (PAR::Packer)
- Kotlin JVM (kotlinc-native / GraalVM)
- TypeScript via `bun build --compile`
- Python added: Nuitka option alongside PyInstaller (smaller, faster exes)
- PowerShell 7 support via PS2EXE-gui fork or custom launcher (current PS2EXE is 5.1-only)

### Build Pipeline
- Per-profile toolchain versions — pin PyInstaller/pkg/csc versions, save in profile YAML
- Parallel batch compilation with a configurable worker pool
- Build matrix (x86 / x64 / arm64) with per-arch toolchain detection
- Dependency prefetch — `pip install`, `npm install`, `go mod download` before the compile step
- Post-build verification — run `--version` / `-v` on the produced exe, attach a smoke-test log

### Signing & Distribution
- Azure Code Signing / DigiCert KeyLocker integration (HSM-backed signing, mandatory for new Microsoft Store entries)
- SignTool timestamping with fallback URLs
- SmartScreen reputation prefetch check (submit unsigned exe to MDAV for scan before publish)
- MSIX / APPX wrapping option for Windows Store distribution
- Auto-upload to GitHub Releases with `gh release upload`

### GUI
- Side-by-side "original script ↔ build log" with jump-to-error
- Build cache — skip recompile if source hash + toolchain unchanged
- Icon extractor — drag an exe in, extract icon as .ico for reuse
- Version string injection (`fileversion`, `productversion`, `companyname`) via VERSIONINFO resource
- Optional obfuscation (ConfuserEx for C#, PyArmor for Python, javascript-obfuscator for JS)

### Automation
- CLI mode: `uc build myscript.ps1 --profile Release --sign`
- Watch mode — recompile on save
- GitHub Actions template per language
- VSCode extension that invokes Universal Compiler for the active file

## Competitive Research

- **PS2EXE**: de-facto for PowerShell → EXE. Still 5.1-only and abandoned for years; our v2 PS7 path is the differentiator.
- **PyInstaller / Nuitka**: Python's two winners. Supporting both (Nuitka produces smaller, faster binaries) widens our appeal.
- **pkg / Bun.build**: Node's landscape shifted — Bun's `--compile` produces a single 30–80 MB exe with no runtime deps; worth migrating Node compiles away from pkg.
- **Inno Setup / NSIS**: installer space. Ship a "wrap exe in installer" post-step so users get both an exe and an installer in one build.

## Nice-to-Haves

- WebAssembly target for browser-runnable scripts
- Bytecode compile-only mode (.pyc bundle, .ps1xml) for speed without packaging
- Encrypted-string inliner for embedded API keys
- Linux/macOS cross-compile for Go / Rust / Bun targets
- Telemetry-free build analytics — local SQLite tracks sizes/times per profile, graphed over time
- Drag-to-sign — drop any existing exe on the GUI to sign/timestamp without rebuilding

## Open-Source Research (Round 2)

### Related OSS Projects
- https://github.com/brentvollebregt/auto-py-to-exe — de-facto PyInstaller GUI wrapper
- https://github.com/PySimpleGUI/psgcompiler — pluggable back-ends (PyInstaller, cx_Freeze)
- https://github.com/inject3r/pyinstaller-gui — cross-platform PyInstaller GUI
- https://github.com/MScholtes/PS2EXE — PowerShell → EXE encapsulator (5.x)
- https://github.com/MScholtes/Win-PS2EXE — C# WPF GUI for PS2EXE with drag-drop
- https://github.com/Nuitka/Nuitka — Python → C compiler for smaller/faster exes
- https://github.com/nullrequest/vanara — not relevant; use https://github.com/vercel/pkg — Node single-binary packager
- https://github.com/zeit/pkg — Node.js bytecode-embedded exe (archived but referenceable)
- https://github.com/oven-sh/bun — Bun's `bun build --compile` single-binary pipeline
- https://github.com/ahkscript/awesome-AutoHotkey — AHK compilation tooling references

### Features to Borrow
- Presets with virtualenv handling + advanced-option exposure (pyinstaller-gui PyQt5 variant)
- Drag-to-sign overlay: drop an existing exe to re-sign without rebuilding (mirrors the roadmap entry, already present in Win-PS2EXE drag-drop pattern)
- Multiple back-ends per language: PyInstaller + Nuitka + cx_Freeze for Python, with size/speed comparison view (psgcompiler)
- Live-command preview: build the actual shell command as the user toggles options (psgcompiler pattern)
- Profile save/load as JSON — shareable build recipes (auto-py-to-exe)
- Bun `--compile` + Deno `compile` + Node SEA (Single Executable Application) for modern JS targets
- Icon-injection with Lanczos resample for all required icon sizes (common PyInstaller GUI feature)
- UAC-manifest editor baked in (requestedExecutionLevel) for post-build elevation tagging
- Telemetry-free build analytics (local SQLite) — already in roadmap; borrow schema from psgcompiler's run-log
- Code-sign batch mode — queue multiple exes for signing with one password prompt (enterprise ask)

### Patterns & Architectures Worth Studying
- Back-end abstraction: each compiler (PyInstaller/Nuitka/cx_Freeze/PS2EXE/go build) is a driver with `build()`, `args()`, `postProcess()` methods
- Subprocess build runner with streamed stdout to embedded console panel — avoid hanging the GUI (psgcompiler pattern)
- Two-file distribution: standalone CLI `uc build` and GUI wrapper over the same engine (PS2EXE + Win-PS2EXE model)
- Per-toolchain PATH probe + auto-install assistant (winget / choco / scoop) — zero-config turnkey
- Post-build verification: run the produced exe in a sandbox, capture version/help output, attach to build log
