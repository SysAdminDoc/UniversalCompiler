# Changelog

All notable changes to UniversalCompiler will be documented in this file.

## [v2.1.0] - 2026-08-03

- Add a bounded, offline-by-default execution policy with executable-root
  validation, minimal environments, timeouts, cancellation, process cleanup,
  output caps, dependency-install gates, and redacted diagnostics across the
  Python engine, legacy shells, and VS Code launcher.
- Make post-build actions unable to launch compiled artifacts and require an
  explicit setup action before downloading or installing toolchains.
- Make the Python CLI/core the build authority for the Python GUI, PowerShell
  GUI, and VS Code extension, with versioned request, result, and capability
  metadata and shared structured error handling.
- Add a side-effect-free Python build engine and CLI with YAML profiles, backend
  previews, cache-aware builds, dependency prefetching, parallel batch builds,
  watch mode, and static post-build verification.
- Add Nuitka, Bun, Deno, TypeScript, Rust, Lua, Perl, and Kotlin/Native backend
  planning to the shared engine and GUI backend selector.
- Add YAML toolchain pins, architecture matrix builds, optional UPX compression,
  bytecode-only compilation, local SQLite analytics, language-specific GitHub
  Actions templates, and a VS Code command extension.
- Add side-by-side source/log diagnostics with error-line navigation and
  executable icon extraction for reuse in later builds.
- Add unsigned MSIX/APPX wrapping, WebAssembly text compilation, and explicit
  opt-in obfuscator command support.
- Add platform/architecture target mapping for Go, Rust, Bun, Deno, and pkg
  cross-build plans.

## [v0.1.0] - %Y->- (HEAD -> main, origin/main, origin/HEAD)

- Create UniversalCompiler.py
- Rename Universalcompiler.ps1 to UniversalCompiler.ps1
- Create Universalcompiler.ps1
- Changed: Update README.md
