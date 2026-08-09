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
- Emit versioned artifact manifests with source/toolchain/config identities,
  hashes, sizes, verification results, warnings, and explicit unsigned status;
  strengthen PE, archive, MSIX/APPX, and WebAssembly structural validation.
- Add a schema-versioned backend capability catalog consumed by the CLI, both
  GUI shells, and the VS Code extension, with lifecycle, host/target/
  architecture constraints, SDK requirements, and verified tool versions;
  deprecated pkg is no longer selected automatically.
- Add per-output build locking, isolated staging, atomic post-verification
  publication, duplicate-output rejection, cancellation propagation, bounded
  cleanup, and debounced watch rebuilds.
- Route Python GUI and setup-worker results through bounded queues drained by
  the Tk event loop so background threads no longer mutate widgets directly.
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
- Add a versioned `uc.project.v1` manifest with strict validation, explicit
  user/workspace scopes, legacy YAML/JSON migration, atomic backups, rollback,
  and recovery support across the CLI and GUI shells.
- Add the versioned `uc.adapter.v1` backend/plugin boundary with deterministic,
  namespaced, allowlisted entry-point discovery, capability metadata, planner
  contracts, tool identity, and redacted adapter diagnostics; external adapters
  remain disabled unless explicitly enabled.
- Add correlated `uc.diagnostics.v1` build/command records with phase timings,
  cache and exit classifications, artifact hashes, redacted command metadata,
  bounded local JSONL retention, and explicit opt-in local export without
  network telemetry or source/environment leakage.
- Replace the VS Code terminal integration with a cancellable argv child
  process using explicit cwd/trusted-workspace checks and an allowlisted
  environment; add profile/target/architecture/verification settings, bounded
  capture, structured notifications, and source diagnostics without focus theft.
- Require approved, hash-addressed dependency locks for prefetch, enforce
  explicit offline/online mirror and cache policies, and record dependency and
  toolchain snapshots in cache metadata and artifact manifests.
- Add a pinned Windows CI contract gate with isolated temporary-root cleanup,
  full backend planning coverage, artifact/migration/cancellation tests, and
  lint/type checks; generated workflow actions now use commit SHAs.
- Add a local `release` dry-run and verification command that emits unsigned
  artifacts, SHA-256 checksums, CycloneDX SBOM data, SLSA-shaped provenance,
  and a static verification report without credentials or publishing.
- Replace mutable setup downloads and direct installer launches with a pinned,
  package-manager-only acquisition catalog, explicit dry-run/manual/diagnostic
  modes, offline SHA-256 verification, bounded provenance records, and no
  implicit setup during script startup.

## [v0.1.0] - %Y->- (HEAD -> main, origin/main, origin/HEAD)

- Create UniversalCompiler.py
- Rename Universalcompiler.ps1 to UniversalCompiler.ps1
- Create Universalcompiler.ps1
- Changed: Update README.md
