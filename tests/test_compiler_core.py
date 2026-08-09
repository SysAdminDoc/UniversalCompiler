from __future__ import annotations

import json
import re
import sqlite3
import struct
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compiler_core
from compiler_core import (
    ADAPTER_API_VERSION,
    AdapterDescriptor,
    ANALYTICS_SCHEMA_VERSION,
    APP_VERSION,
    BuildAnalytics,
    BuildPlan,
    BuildValidationError,
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    BACKEND_CATALOG,
    BACKEND_ARTIFACT_POLICIES,
    CAPABILITY_SCHEMA_VERSION,
    COMPATIBILITY_KIND,
    COMPATIBILITY_SCHEMA_VERSION,
    DIAGNOSTICS_KIND,
    DIAGNOSTICS_SCHEMA_VERSION,
    DiagnosticsStore,
    DEFAULT_PROFILES,
    DEPENDENCY_LOCK_KIND,
    DEPENDENCY_LOCK_SCHEMA_VERSION,
    DEPENDENCY_POLICY_VERSION,
    BuildRequest,
    BuildResult,
    CommandResult,
    CompilerEngine,
    contrast_ratio,
    ExecutionPolicy,
    PROJECT_MANIFEST_SCHEMA_VERSION,
    REQUEST_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    cli_main,
    compatibility_matrix,
    compile_bytecode,
    detect_file_type,
    load_dependency_lock,
    format_size,
    github_actions_template,
    load_profiles,
    load_json,
    get_message_catalog,
    load_project_manifest,
    default_project_manifest,
    project_manifest_backup_path,
    project_manifest_path,
    release_bundle,
    adapter_diagnostics,
    backend_status,
    discover_adapters,
    rollback_project_manifest,
    resolve_locale,
    run_command,
    save_profiles,
    save_json,
    save_project_manifest,
    state_lock,
    update_project_manifest,
    validate_project_manifest,
    verify_artifact_manifest,
    verify_artifact,
    verify_release_bundle,
    wrap_msix,
)


def _fake_pe(path: Path) -> None:
    data = bytearray(0x400)
    data[:2] = b"MZ"
    data[0x3C:0x40] = (0x80).to_bytes(4, "little")
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x14C, 1, 0, 0, 0, 0xE0, 0x010F)
    struct.pack_into("<H", data, 0x98, 0x10B)
    data[0x178:0x17E] = b".text\0"
    struct.pack_into("<IIII", data, 0x180, 0x100, 0x1000, 0x200, 0x200)
    path.write_bytes(data)


def _planned_pyinstaller_output(command: tuple[str, ...] | list[str]) -> Path:
    return Path(command[command.index("--distpath") + 1]) / (
        Path(command[command.index("--name") + 1]).stem + ".exe"
    )


def _write_dependency_lock(tmp_path: Path) -> Path:
    dependency_file = tmp_path / "requirements.lock"
    dependency_file.write_text(
        "example-package==1.0.0 --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    lock = {
        "schema_version": DEPENDENCY_LOCK_SCHEMA_VERSION,
        "kind": DEPENDENCY_LOCK_KIND,
        "approved": True,
        "policy": {
            "version": DEPENDENCY_POLICY_VERSION,
            "network": "offline",
            "mirror": None,
            "cache_dir": ".uc-dependency-cache",
        },
        "lockfiles": {
            "py": {
                "path": dependency_file.name,
                "sha256": compiler_core.sha256_file(dependency_file),
                "manager": "pip",
            }
        },
        "toolchains": {"pyinstaller": {"version": "6.20.0"}},
    }
    lock_path = tmp_path / "universal-compiler.lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return lock_path


def test_detect_file_type_and_size() -> None:
    assert detect_file_type("script.TS") == "ts"
    assert detect_file_type("README.md") is None
    assert format_size(1024) == "1.0 KB"


def test_execution_policy_bounds_output_redacts_secrets_and_preserves_environment() -> None:
    result = run_command(
        (
            sys.executable,
            "-c",
            "import os, sys; sys.stdout.reconfigure(encoding='utf-8'); "
            "print(os.environ['UC_HOSTILE']); print('token=super-secret')",
        ),
        environment={"UC_HOSTILE": "spaces & quotes Ω"},
        policy=ExecutionPolicy(max_output_bytes=1024),
    )

    assert result.success is True
    assert "spaces & quotes Ω" in result.output
    assert "token=[REDACTED]" in result.output
    assert "super-secret" not in result.output


def test_command_diagnostics_are_correlated_and_do_not_export_output_or_environment() -> None:
    result = run_command(
        (sys.executable, "-c", "print('token=secret-value')"),
        correlation_id="00112233445566778899aabbccddeeff",
        phase="compile",
    )

    diagnostic = result.diagnostic_record()
    assert diagnostic["schema_version"] == DIAGNOSTICS_SCHEMA_VERSION
    assert diagnostic["kind"] == DIAGNOSTICS_KIND
    assert diagnostic["correlation_id"] == "00112233445566778899aabbccddeeff"
    assert diagnostic["phase"] == "compile"
    assert diagnostic["exit_classification"] == "success"
    assert "secret-value" not in json.dumps(diagnostic)
    assert "output" in diagnostic and "stdout_bytes" in diagnostic["output"]


def test_execution_policy_rejects_unapproved_tools_and_caps_output(tmp_path: Path) -> None:
    with pytest.raises(BuildValidationError):
        run_command(
            (sys.executable, "-c", "print('not allowed')"),
            policy=ExecutionPolicy(allowed_executable_roots=(tmp_path,)),
        )

    result = run_command(
        (sys.executable, "-c", "print('x' * 1000)"),
        policy=ExecutionPolicy(max_output_bytes=64),
    )

    assert result.success is True
    assert result.output_truncated is True
    assert "[output truncated by execution policy]" in result.stderr
    assert len(result.stdout.encode("utf-8")) <= 64

    with pytest.raises(BuildValidationError, match="network and install permission"):
        run_command((sys.executable, "-m", "pip", "install", "example-package"))


def test_execution_policy_times_out_and_cancels_processes() -> None:
    timed_out = run_command(
        (sys.executable, "-c", "import time; time.sleep(2)"),
        policy=ExecutionPolicy(timeout_seconds=0.2),
    )
    assert timed_out.timed_out is True
    assert timed_out.success is False
    assert timed_out.returncode == 124

    stop_event = threading.Event()
    holder: dict[str, CommandResult] = {}

    def run_cancellable() -> None:
        holder["result"] = run_command(
            (sys.executable, "-c", "import time; time.sleep(2)"),
            policy=ExecutionPolicy(timeout_seconds=5),
            stop_event=stop_event,
        )

    thread = threading.Thread(target=run_cancellable)
    thread.start()
    time.sleep(0.15)
    stop_event.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert holder["result"].cancelled is True
    assert holder["result"].returncode == 130


def test_prefetch_requires_explicit_network_and_install_permissions(tmp_path: Path) -> None:
    source = tmp_path / "script.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("example-package\n", encoding="utf-8")
    request = BuildRequest(
        source=source,
        output=tmp_path / "script.exe",
        file_type="py",
        backend="pyinstaller",
        prefetch=True,
    )

    with pytest.raises(BuildValidationError, match="allow-network"):
        CompilerEngine(require_available=False).prefetch_dependencies(request)


def test_approved_dependency_lock_requires_hashes_and_drives_offline_command(
    tmp_path: Path,
) -> None:
    source = tmp_path / "script.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    lock_path = _write_dependency_lock(tmp_path)
    snapshot = load_dependency_lock(lock_path, source_type="py", require_entry=True)
    assert snapshot["policy"]["network"] == "offline"
    assert snapshot["dependency"]["hashes_verified"] is True

    calls: list[tuple[str, ...]] = []

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        calls.append(tuple(command))
        return CommandResult(tuple(command), 0, stdout="prefetched")

    request = BuildRequest(
        source=source,
        output=tmp_path / "script.exe",
        file_type="py",
        backend="pyinstaller",
        prefetch=True,
        allow_network=True,
        allow_dependency_install=True,
        dependency_lockfile=lock_path,
    )
    result = CompilerEngine(
        runner=fake_runner,
        require_available=False,
    ).prefetch_dependencies(request, snapshot)

    assert result[0].success is True
    assert "--require-hashes" in calls[0]
    assert "--no-index" in calls[0]
    assert "--find-links" in calls[0]
    assert str(tmp_path / ".uc-dependency-cache") in calls[0]


def test_dependency_snapshot_changes_cache_identity_and_is_recorded(
    tmp_path: Path,
) -> None:
    source = tmp_path / "script.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    output = tmp_path / "script.exe"
    lock_path = _write_dependency_lock(tmp_path)
    request = BuildRequest(
        source=source,
        output=output,
        file_type="py",
        backend="pyinstaller",
        prefetch=True,
        allow_network=True,
        allow_dependency_install=True,
        dependency_lockfile=lock_path,
    ).normalized()
    engine = CompilerEngine(require_available=False)
    first_snapshot = engine._dependency_snapshot(request)
    first_key = engine._cache_key(request, compiler_core.sha256_file(source), "pyinstaller", first_snapshot)

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["toolchains"]["pyinstaller"]["version"] = "6.21.0"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    second_snapshot = engine._dependency_snapshot(request)
    second_key = engine._cache_key(request, compiler_core.sha256_file(source), "pyinstaller", second_snapshot)

    assert first_key != second_key
    assert first_snapshot["toolchains"]["pyinstaller"]["version"] == "6.20.0"
    assert second_snapshot["toolchains"]["pyinstaller"]["version"] == "6.21.0"

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        if "--distpath" in command:
            _fake_pe(_planned_pyinstaller_output(command))
        return CommandResult(tuple(command), 0)

    result = CompilerEngine(
        runner=fake_runner,
        require_available=False,
    ).build(request)
    assert result.success is True
    assert result.manifest is not None
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["dependencies"]["status"] == "locked"
    assert manifest["dependencies"]["lockfile_sha256"]
    assert manifest["dependencies"]["dependency"]["hashes_verified"] is True


def test_shells_delegate_to_versioned_core_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    powershell = (root / "UniversalCompiler.ps1").read_text(encoding="utf-8")
    extension = (root / "vscode-extension" / "extension.js").read_text(encoding="utf-8")

    assert "Invoke-CoreBuild" in powershell
    assert "UniversalCompiler.py" in powershell
    assert "Invoke-PS2EXE" not in powershell
    assert "spawn(" in extension
    assert "terminal.sendText" not in extension
    assert 'args.push("--json", "--no-analytics")' in extension
    assert '"list-toolchains", "--json"' in extension
    assert "Get-PreferredCapability" in powershell


def test_powershell_toolchain_acquisition_is_pinned_and_explicit() -> None:
    root = Path(__file__).resolve().parents[1]
    powershell = (root / "UniversalCompiler.ps1").read_text(encoding="utf-8")

    assert "uc.toolchain-acquisition.v1" in powershell
    assert "Invoke-ToolchainAcquisition" in powershell
    assert "Get-FileHash" in powershell
    assert "RequiredVersion" in powershell
    assert "--exact" in powershell
    assert "if ($SetupMode -eq 'Diagnostic')" in powershell
    assert "WebClient" not in powershell
    assert "DownloadFile" not in powershell
    assert "master.zip" not in powershell
    assert "if ($ForceSetup -and -not $SkipSetup)" in powershell
    assert "Install-Module $($definition.Package) -RequiredVersion" in powershell
    assert "Invoke-WithStateLock" in powershell
    assert "Write-AtomicText" in powershell
    assert '("." + [IO.Path]::GetFileName($destination) + ".lock")' in powershell


def test_localization_catalog_has_fallback_plural_and_locale_formatting() -> None:
    catalog = get_message_catalog("es-MX")

    assert catalog.locale == "es"
    assert catalog.message("source.file") == "📁 Archivo de origen"
    assert catalog.message("actions.from_exe") == "From EXE"
    assert catalog.plural("queue.files", 1) == "1 archivo"
    assert catalog.plural("queue.files", 3) == "3 archivos"
    assert catalog.format_number(12345.6, 1) == "12.345,6"
    assert catalog.format_size(1536) == "1,5 KB"
    assert catalog.format_datetime(datetime(2026, 8, 8, 13, 4, 5, tzinfo=UTC)) == (
        "08/08/2026 13:04:05"
    )
    assert contrast_ratio("#f8fafc", "#020617") >= 4.5
    assert contrast_ratio("#ffffff", "#000000") == 21.0
    assert resolve_locale("fr-CA", environment={}, available=("en", "es")) == "en"


def test_gui_accessibility_and_localization_contracts_are_declared() -> None:
    root = Path(__file__).resolve().parents[1]
    python_shell = (root / "UniversalCompiler.py").read_text(encoding="utf-8")
    powershell = (root / "UniversalCompiler.ps1").read_text(encoding="utf-8")
    catalog = json.loads(
        (root / "resources" / "i18n" / "catalog.json").read_text(encoding="utf-8")
    )

    assert catalog["schema_version"] == "uc.i18n.v1"
    assert {"en", "es"} <= set(catalog["locales"])
    assert "plural" in json.dumps(catalog["locales"]["en"])
    assert "get_message_catalog" in python_shell
    assert 'self.root.bind("<F5>"' in python_shell
    assert 'self.root.bind("<Escape>"' in python_shell
    assert 'self.root.bind("<Control-l>"' in python_shell
    assert "_register_accessible" in python_shell
    assert "high_contrast_enabled" in python_shell
    assert "AutomationProperties.Name" in powershell
    assert "KeyboardNavigation.TabNavigation" in powershell
    assert "FocusVisual" in powershell
    assert "HighContrast" in powershell
    assert "Get-LocalizedMessage" in powershell
    assert "[string]$Locale" in powershell


def test_profiles_round_trip_without_extra_dependency(tmp_path: Path) -> None:
    path = tmp_path / "profiles.yaml"
    profiles = dict(DEFAULT_PROFILES)
    profiles["Release Profile"] = {
        "backend": "nuitka",
        "console": True,
        "single_file": True,
    }
    profiles["Pinned Profile"] = {
        "toolchain_versions": {"pyinstaller": "6.20.0"},
        "upx": True,
    }

    save_profiles(path, profiles)
    loaded = load_profiles(path)

    assert loaded["Release Profile"]["backend"] == "nuitka"
    assert loaded["Release Profile"]["console"] is True
    assert loaded["Default"]["single_file"] is True
    assert loaded["Pinned Profile"]["toolchain_versions"]["pyinstaller"] == "6.20.0"


def test_project_manifest_migrates_legacy_yaml_json_and_is_idempotent(
    tmp_path: Path,
) -> None:
    profiles = tmp_path / "profiles.yaml"
    profiles.write_text(
        "Default:\n  Console: true\n  SingleFile: true\n  Backend: pyinstaller\n",
        encoding="utf-8",
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"Theme": "Light", "MaxHistoryItems": 7}), encoding="utf-8"
    )
    (tmp_path / "history.json").write_text(
        json.dumps(
            [
                {
                    "Timestamp": "2026-08-08T00:00:00Z",
                    "Source": "main.py",
                    "Output": "main.exe",
                    "Type": "py",
                    "Success": True,
                    "Profile": "Default",
                    "Size": 42,
                }
            ]
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "universal-compiler.json"

    first = load_project_manifest(manifest_path, expected_scope="user")
    second = load_project_manifest(manifest_path, expected_scope="user")

    assert first.migrated is True
    assert second.migrated is False
    assert first.manifest["schema_version"] == PROJECT_MANIFEST_SCHEMA_VERSION
    assert first.manifest["profiles"]["Default"]["console"] is True
    assert first.manifest["settings"]["theme"] == "Light"
    assert first.manifest["settings"]["max_history_items"] == 7
    assert first.manifest["history"][0]["source"] == "main.py"
    assert second.manifest == first.manifest


def test_project_manifest_strict_validation_scopes_and_forward_error(
    tmp_path: Path,
) -> None:
    manifest = default_project_manifest("workspace", tmp_path)
    assert project_manifest_path("workspace", tmp_path).parent == tmp_path
    assert validate_project_manifest(manifest, expected_scope="workspace")["scope"] == "workspace"

    unknown = dict(manifest)
    unknown["future_field"] = True
    with pytest.raises(BuildValidationError, match="Unknown manifest field"):
        validate_project_manifest(unknown)

    future = dict(manifest)
    future["schema_version"] = "uc.project.v99"
    with pytest.raises(BuildValidationError, match="Forward-incompatible"):
        validate_project_manifest(future)


def test_project_manifest_backup_recovery_and_rollback(tmp_path: Path) -> None:
    path = tmp_path / "universal-compiler.json"
    original = default_project_manifest("user")
    save_project_manifest(path, original)
    updated = default_project_manifest("user")
    updated["settings"]["theme"] = "Light"
    save_project_manifest(path, updated)

    backup = project_manifest_backup_path(path)
    assert backup.is_file()
    path.write_text("{ invalid", encoding="utf-8")
    recovered = load_project_manifest(path, expected_scope="user")
    assert recovered.recovered is True
    assert recovered.manifest["settings"]["theme"] == "Dark"

    rollback_project_manifest(path)
    rolled_back = load_project_manifest(path, expected_scope="user")
    assert rolled_back.manifest["settings"]["theme"] == "Dark"


def test_user_state_is_recoverable_and_manifest_updates_do_not_lose_fields(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "settings.json"
    save_json(state_path, {"value": 1}, recoverable=True)
    save_json(state_path, {"value": 2}, recoverable=True)
    with state_lock(state_path):
        assert state_path.with_name(".settings.json.lock").is_file()
    assert state_path.with_name("settings.json.bak").is_file()
    state_path.write_text("{ interrupted", encoding="utf-8")
    assert load_json(state_path, {}, recoverable=True) == {"value": 1}
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"value": 1}

    profiles_path = tmp_path / "profiles.yaml"
    save_profiles(profiles_path, DEFAULT_PROFILES)
    changed_profiles = dict(DEFAULT_PROFILES)
    changed_profiles["Default"] = dict(DEFAULT_PROFILES["Default"], backend="nuitka")
    save_profiles(profiles_path, changed_profiles)
    profiles_path.write_text("Default: [interrupted", encoding="utf-8")
    assert load_profiles(profiles_path)["Default"]["backend"] == "auto"
    profiles_path.unlink()
    assert load_profiles(profiles_path)["Default"]["backend"] == "auto"
    assert profiles_path.is_file()

    manifest_path = tmp_path / "manifest.json"
    save_project_manifest(manifest_path, default_project_manifest("user"))
    barrier = threading.Barrier(2)

    def update_theme(manifest: dict[str, object]) -> dict[str, object]:
        manifest["settings"]["theme"] = "Light"  # type: ignore[index]
        return manifest

    def update_history_limit(manifest: dict[str, object]) -> dict[str, object]:
        manifest["settings"]["max_history_items"] = 7  # type: ignore[index]
        return manifest

    def run_update(updater):
        barrier.wait()
        return update_project_manifest(manifest_path, updater, expected_scope="user")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run_update, (update_theme, update_history_limit)))
    merged = load_project_manifest(manifest_path, expected_scope="user").manifest
    assert merged["settings"]["theme"] == "Light"
    assert merged["settings"]["max_history_items"] == 7


def test_manifest_cli_init_and_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "project.json"
    assert cli_main(["manifest", "init", "--path", str(path), "--json"]) == 0
    capsys.readouterr()
    assert cli_main(["manifest", "show", "--path", str(path), "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["schema_version"] == PROJECT_MANIFEST_SCHEMA_VERSION
    assert shown["profiles"]["Default"]["backend"] == "auto"


def test_static_artifact_verification(tmp_path: Path) -> None:
    artifact = tmp_path / "sample.exe"
    _fake_pe(artifact)
    result = verify_artifact(artifact)
    assert result.passed is True
    assert result.kind == "pe"

    artifact.write_bytes(b"not an executable")
    assert verify_artifact(artifact).passed is False


def test_release_dry_run_emits_hashes_sbom_provenance_and_report(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "sample.exe"
    _fake_pe(artifact)
    source = tmp_path / "source.py"
    source.write_text("print('release')\n", encoding="utf-8")
    destination = tmp_path / "release"

    result = release_bundle(
        [artifact], destination, source_root=tmp_path, version="2.1.0"
    )

    assert result["passed"] is True
    assert result["manifest"]["unsigned"] is True
    assert (destination / "SHA256SUMS").is_file()
    sbom = json.loads((destination / "sbom.cdx.json").read_text(encoding="utf-8"))
    provenance = json.loads(
        (destination / "provenance.json").read_text(encoding="utf-8")
    )
    assert sbom["bomFormat"] == "CycloneDX"
    assert any(component["name"] == "sample.exe" for component in sbom["components"])
    assert provenance["invocation"]["dry_run"] is True
    assert provenance["invocation"]["unsigned"] is True
    assert verify_release_bundle(destination)["passed"] is True


def test_wasm_structure_verification_checks_version_and_sections(tmp_path: Path) -> None:
    artifact = tmp_path / "module.wasm"
    artifact.write_bytes(b"\x00asm\x01\x00\x00\x00\x00\x01\x00")
    assert verify_artifact(artifact).passed is True

    artifact.write_bytes(b"\x00asm\x01\x00\x00\x00\x01\x05\x00")
    assert verify_artifact(artifact).passed is False


def test_unsigned_msix_contains_manifest_and_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "app.exe"
    package = tmp_path / "app.msix"
    _fake_pe(executable)
    monkeypatch.setattr(compiler_core, "_find_first", lambda names: None)

    result = wrap_msix(executable, package, display_name="Test App")

    assert result == package
    assert verify_artifact(package).passed is True
    with zipfile.ZipFile(package) as archive:
        assert {"App.exe", "AppxManifest.xml", "Assets/StoreLogo.png"} <= set(
            archive.namelist()
        )
        assert "Test App" in archive.read("AppxManifest.xml").decode("utf-8")


def test_bytecode_compile_only(tmp_path: Path) -> None:
    source = tmp_path / "bytecode.py"
    output = tmp_path / "bytecode.pyc"
    source.write_text("value = 42\n", encoding="utf-8")

    result = compile_bytecode(source, output)

    assert result == output
    assert output.read_bytes()[:4] != b""


def test_local_analytics_records_summary(tmp_path: Path) -> None:
    source = tmp_path / "analytics.py"
    source.write_text("print(1)\n", encoding="utf-8")
    output = tmp_path / "analytics.exe"

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        _fake_pe(_planned_pyinstaller_output(command))
        return CommandResult(tuple(command), 0)

    result = CompilerEngine(
        runner=fake_runner,
        require_available=False,
    ).build(BuildRequest(source=source, output=output, backend="pyinstaller"))
    analytics = BuildAnalytics(tmp_path / "analytics.sqlite3")
    analytics.record(result)

    summary = analytics.summary()
    assert summary["total_builds"] == 1
    assert summary["successful_builds"] == 1
    assert analytics.recent(1)[0]["backend"] == "pyinstaller"

    connection = sqlite3.connect(analytics.path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == ANALYTICS_SCHEMA_VERSION
    finally:
        connection.close()
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(analytics.record, (result, result, result)))
    assert analytics.summary()["total_builds"] == 4

    backup = analytics.backup()
    assert backup == analytics.backup_path
    assert backup.is_file()
    for sidecar in (
        analytics.path.with_name(f"{analytics.path.name}-wal"),
        analytics.path.with_name(f"{analytics.path.name}-shm"),
    ):
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
    analytics.path.write_bytes(b"interrupted database")
    with pytest.raises(sqlite3.DatabaseError):
        analytics.summary()
    analytics.recover()
    assert analytics.summary()["total_builds"] == 4


def test_diagnostics_store_has_bounded_local_retention_and_opt_in_export(
    tmp_path: Path,
) -> None:
    request = BuildRequest(
        source=tmp_path / "private-source.py",
        output=tmp_path / "artifact.exe",
        backend="pyinstaller",
    )
    result = BuildResult(
        True,
        "built",
        request,
        request.output,
        backend="pyinstaller",
        source_hash="a" * 64,
        duration_seconds=0.25,
    )
    store = DiagnosticsStore(
        tmp_path / "diagnostics.jsonl",
        retention_days=30,
        max_events=2,
    )
    for _ in range(3):
        store.record(result)

    assert len(store.recent(10)) == 2
    raw = store.path.read_text(encoding="utf-8")
    assert raw.count("\n") == 2
    assert "private-source.py" not in raw
    with pytest.raises(BuildValidationError, match="telemetry opt-in"):
        store.export(tmp_path / "export.json")

    exported = store.export(tmp_path / "export.json", opt_in=True)
    exported_value = json.loads(exported.read_text(encoding="utf-8"))
    assert exported_value["telemetry"] == {"opt_in": True, "network": False}
    assert len(exported_value["events"]) == 2


def test_engine_builds_and_uses_cache(tmp_path: Path) -> None:
    source = tmp_path / "hello.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    output = tmp_path / "out.exe"
    calls: list[tuple[str, ...]] = []

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        calls.append(tuple(command))
        _fake_pe(_planned_pyinstaller_output(command))
        return CommandResult(tuple(command), 0, stdout="built")

    engine = CompilerEngine(runner=fake_runner, require_available=False)
    request = BuildRequest(
        source=source, output=output, backend="pyinstaller", verify=True
    )

    first = engine.build(request)
    second = engine.build(request)

    assert first.success is True
    assert first.status == "built"
    assert second.success is True
    assert second.status == "cache-hit"
    assert len(calls) == 1
    assert (tmp_path / "out.exe.uc-cache.json").is_file()
    serialized = first.as_dict()
    assert serialized["schema_version"] == RESULT_SCHEMA_VERSION
    assert serialized["request"]["schema_version"] == REQUEST_SCHEMA_VERSION
    assert serialized["correlation_id"] == first.request.correlation_id
    assert serialized["cache_status"] == "miss"
    assert serialized["phase_timings"]["commands"] == 0
    assert serialized["diagnostics"]["artifacts"]["sha256"]["output"]
    assert first.manifest is not None and first.manifest.is_file()
    manifest = json.loads(first.manifest.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == ARTIFACT_MANIFEST_SCHEMA_VERSION
    assert manifest["signature"]["status"] == "unsigned"
    assert verify_artifact_manifest(first.manifest, output).passed is True
    output.write_bytes(b"tampered")
    assert verify_artifact_manifest(first.manifest, output).passed is False


def test_build_publishes_verified_staging_output_atomically(tmp_path: Path) -> None:
    source = tmp_path / "atomic.py"
    source.write_text("print(1)\n", encoding="utf-8")
    output = tmp_path / "atomic.exe"
    calls: list[tuple[str, ...]] = []

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        calls.append(tuple(command))
        _fake_pe(_planned_pyinstaller_output(command))
        return CommandResult(tuple(command), 0)

    result = CompilerEngine(runner=fake_runner, require_available=False).build(
        BuildRequest(source=source, output=output, backend="pyinstaller")
    )

    assert result.success is True
    assert output.is_file()
    assert any(".uc-stage-" in argument for argument in calls[0])
    assert list(tmp_path.glob(".uc-stage-*")) == []


def test_failed_staged_verification_does_not_replace_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "atomic-failure.py"
    source.write_text("print(1)\n", encoding="utf-8")
    output = tmp_path / "atomic-failure.exe"
    original = b"previous artifact"
    output.write_bytes(original)

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        _planned_pyinstaller_output(command).write_bytes(b"invalid artifact")
        return CommandResult(tuple(command), 0)

    result = CompilerEngine(runner=fake_runner, require_available=False).build(
        BuildRequest(source=source, output=output, backend="pyinstaller")
    )

    assert result.success is False
    assert result.verification is not None
    assert output.read_bytes() == original
    assert list(tmp_path.glob(".uc-stage-*")) == []


def test_build_cancellation_token_prevents_execution_and_serializes_cleanly(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cancel.py"
    source.write_text("print(1)\n", encoding="utf-8")
    event = threading.Event()
    event.set()
    calls: list[tuple[str, ...]] = []

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        calls.append(tuple(command))
        return CommandResult(tuple(command), 0)

    request = BuildRequest(
        source=source,
        output=tmp_path / "cancel.exe",
        backend="pyinstaller",
        cancel_event=event,
    )
    result = CompilerEngine(runner=fake_runner, require_available=False).build(request)

    assert result.status == "cancelled"
    assert calls == []
    assert "cancel_event" not in result.as_dict()["request"]


def test_batch_rejects_duplicate_outputs_before_execution(tmp_path: Path) -> None:
    requests = []
    for index in range(2):
        source = tmp_path / f"duplicate-{index}.py"
        source.write_text("print(1)\n", encoding="utf-8")
        requests.append(
            BuildRequest(
                source=source,
                output=tmp_path / "same.exe",
                backend="pyinstaller",
            )
        )
    calls: list[tuple[str, ...]] = []

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        calls.append(tuple(command))
        return CommandResult(tuple(command), 0)

    results = CompilerEngine(runner=fake_runner, require_available=False).build_batch(
        requests, workers=2
    )

    assert [result.status for result in results] == ["collision", "collision"]
    assert all("collision" in result.message.lower() for result in results)
    assert calls == []


def test_watch_coalesces_quick_source_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "watch.py"
    source.write_text("one\n", encoding="utf-8")
    event = threading.Event()
    request = BuildRequest(
        source=source,
        output=tmp_path / "watch.exe",
        backend="pyinstaller",
    )
    engine = CompilerEngine(require_available=False)
    builds: list[str] = []

    def fake_build(build_request: BuildRequest) -> BuildResult:
        builds.append(source.read_text(encoding="utf-8"))
        return BuildResult(
            True,
            "built",
            build_request,
            build_request.output,
            backend="pyinstaller",
        )

    monkeypatch.setattr(engine, "build", fake_build)
    watcher = engine.watch(request, interval=0.02, debounce=0.08, stop_event=event)
    first = next(watcher)
    source.write_text("two\n", encoding="utf-8")
    source.write_text("three\n", encoding="utf-8")
    second = next(watcher)
    event.set()
    watcher.close()

    assert first.success is True
    assert second.success is True
    assert builds == ["one\n", "three\n"]


def test_engine_batch_preserves_input_order(tmp_path: Path) -> None:
    requests = []
    for name in ("one.py", "two.py", "three.py"):
        source = tmp_path / name
        source.write_text("print(1)\n", encoding="utf-8")
        requests.append(
            BuildRequest(
                source=source,
                output=tmp_path / f"{source.stem}.exe",
                backend="pyinstaller",
            )
        )

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        _fake_pe(_planned_pyinstaller_output(command))
        return CommandResult(tuple(command), 0)

    results = CompilerEngine(runner=fake_runner, require_available=False).build_batch(
        requests, workers=2
    )
    assert [result.output.name for result in results] == [
        "one.exe",
        "two.exe",
        "three.exe",
    ]
    assert all(result.success for result in results)


def test_engine_matrix_suffixes_outputs(tmp_path: Path) -> None:
    source = tmp_path / "matrix.py"
    source.write_text("print(1)\n", encoding="utf-8")

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        _fake_pe(_planned_pyinstaller_output(command))
        return CommandResult(tuple(command), 0)

    request = BuildRequest(
        source=source,
        output=tmp_path / "matrix.exe",
        backend="pyinstaller",
    )
    results = CompilerEngine(
        runner=fake_runner,
        require_available=False,
    ).build_matrix(request, ["x86", "x64"], workers=2)

    assert [result.output.name for result in results] == [
        "matrix-x86.exe",
        "matrix-x64.exe",
    ]
    assert all(result.success for result in results)


def test_cross_target_plan_sets_platform_environment(tmp_path: Path) -> None:
    source = tmp_path / "main.go"
    request = BuildRequest(
        source=source,
        output=tmp_path / "main",
        backend="go",
        target="linux",
        architecture="x64",
    )

    plan = CompilerEngine(require_available=False).plan(
        request,
        allow_missing_source=True,
    )

    assert plan.environment["GOOS"] == "linux"
    assert plan.environment["GOARCH"] == "amd64"


def test_capability_registry_rejects_incompatible_backend_and_target(tmp_path: Path) -> None:
    engine = CompilerEngine(require_available=False)
    source = tmp_path / "main.py"

    with pytest.raises(BuildValidationError, match=r"not compatible with \.py"):
        engine.plan(
            BuildRequest(source=source, output=tmp_path / "main.exe", backend="go"),
            allow_missing_source=True,
        )
    with pytest.raises(BuildValidationError, match="does not support target linux"):
        engine.plan(
            BuildRequest(
                source=source,
                output=tmp_path / "main.exe",
                backend="pyinstaller",
                target="linux",
            ),
            allow_missing_source=True,
        )
    assert engine.choose_backend("js") != "pkg"
    assert BACKEND_CATALOG["pkg"]["status"] == "deprecated"


def test_portable_artifact_contract_records_assets_permissions_and_targets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.js"
    source.write_text("import './dynamic-module.js';\n", encoding="utf-8")
    asset = tmp_path / "runtime-data.json"
    asset.write_text('{"enabled":true}\n', encoding="utf-8")

    def fake_runner(command, cwd=None, environment=None, timeout=None):
        if "--outfile" in command:
            _fake_pe(Path(command[command.index("--outfile") + 1]))
        return CommandResult(tuple(command), 0)

    request = BuildRequest(
        source=source,
        output=tmp_path / "main.exe",
        backend="bun",
        assets=(asset,),
        permissions=("read", "net"),
        extra_args=("--external", "dynamic-module.js"),
    )
    result = CompilerEngine(runner=fake_runner, require_available=False).build(request)
    assert result.success is True
    assert result.manifest is not None
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["artifact"]["family"] == "bun"
    assert manifest["artifact"]["policy"]["type"] == "platform-executable"
    assert manifest["artifact"]["declared_permissions"] == ["read", "net"]
    assert manifest["artifact"]["declared_assets"] == [str(asset.resolve())]
    assert manifest["request"]["assets"][0]["sha256"] == compiler_core.sha256_file(asset)
    assert "dynamic-module.js" in manifest["request"]["extra_args"]
    assert verify_artifact_manifest(result.manifest, result.output).passed

    engine = CompilerEngine(require_available=False)
    with pytest.raises(BuildValidationError, match="does not support target wasi"):
        engine.plan(
            BuildRequest(
                source=source,
                output=tmp_path / "main.exe",
                backend="bun",
                target="wasi",
            ),
            allow_missing_source=True,
        )
    wasm_plan = engine.plan(
        BuildRequest(
            source=tmp_path / "module.wat",
            output=tmp_path / "module.wasm",
            backend="wat2wasm",
            target="wasi",
        ),
        allow_missing_source=True,
    )
    assert wasm_plan.backend == "wat2wasm"
    assert "-o" in wasm_plan.command
    sea_plan = engine.plan(
        BuildRequest(
            source=source,
            output=tmp_path / "main.exe",
            backend="node-sea",
        ),
        allow_missing_source=True,
    )
    assert len(sea_plan.post_commands) == 1
    assert "NODE_SEA_BLOB" in sea_plan.post_commands[0]


_BACKEND_PLAN_CASES = tuple(
    (extension, backend)
    for backend, spec in BACKEND_CATALOG.items()
    if spec["extensions"]
    for extension in spec["extensions"][:1]
)


@pytest.mark.parametrize(
    ("extension", "backend"),
    _BACKEND_PLAN_CASES,
    ids=[f"{backend}-{extension}" for extension, backend in _BACKEND_PLAN_CASES],
)
def test_every_catalog_backend_has_a_side_effect_free_plan_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
    backend: str,
) -> None:
    monkeypatch.setattr(compiler_core, "resolve_backend_executable", lambda name: name)
    source = tmp_path / f"input.{extension}"
    source.write_text("contract fixture\n", encoding="utf-8")
    plan = CompilerEngine(require_available=False).plan(
        BuildRequest(
            source=source,
            output=tmp_path
            / {
                "python-zipapp": "artifact.pyz",
                "pex": "artifact.pex",
                "wat2wasm": "artifact.wasm",
            }.get(backend, "artifact.exe"),
            file_type=extension,
            backend=backend,
        ),
        allow_missing_source=True,
    )

    assert plan.backend == backend
    assert plan.command
    assert plan.cwd == source.resolve().parent


def test_allowlisted_namespaced_external_adapter_contract(tmp_path: Path) -> None:
    def sample_planner(request: BuildRequest, context: dict[str, object]) -> BuildPlan:
        return BuildPlan(
            command=(sys.executable, "-c", "print('sample adapter')"),
            cwd=request.source.parent,
            backend=str(context["backend"]),
            artifact_candidates=(request.output,),
            cleanup_paths=(),
        )

    def sample_identity() -> dict[str, object]:
        return {"available": True, "executable": sys.executable, "version": "1.0"}

    sample = AdapterDescriptor(
        api_version=ADAPTER_API_VERSION,
        namespace="sample",
        name="text",
        extensions=("txt",),
        planner=sample_planner,
        tool_identity=sample_identity,
        diagnostics=lambda command: {
            "classification": "sample",
            "success": command.success,
            "token": "token=secret-value",
        },
    )

    class EntryPoint:
        name = "sample.text"

        def load(self):
            return sample

    adapters = discover_adapters(["sample.text"], [EntryPoint()])
    engine = CompilerEngine(require_available=False, adapters=adapters)
    source = tmp_path / "input.txt"
    source.write_text("sample\n", encoding="utf-8")
    plan = engine.plan(
        BuildRequest(source=source, output=tmp_path / "sample.out"),
        allow_missing_source=True,
    )

    assert engine.choose_backend("txt") == "sample.text"
    assert plan.backend == "sample.text"
    assert backend_status(adapters)["sample.text"]["adapter_api"] == ADAPTER_API_VERSION
    diagnostic = adapter_diagnostics(
        sample, CommandResult(("sample",), 0, stdout="ok")
    )
    assert diagnostic["classification"] == "sample"
    assert diagnostic["token"] == "token=[REDACTED]"


def test_external_adapter_detector_selects_extensionless_source(tmp_path: Path) -> None:
    adapter = AdapterDescriptor(
        api_version=ADAPTER_API_VERSION,
        namespace="sample",
        name="detector",
        extensions=("sample",),
        detector=lambda source: source.name == "extensionless",
        planner=lambda request, context: BuildPlan(
            command=("sample-detector",),
            cwd=request.source.parent,
            backend=str(context["backend"]),
        ),
    )
    source = tmp_path / "extensionless"
    plan = CompilerEngine(
        require_available=False,
        adapters=(adapter,),
    ).plan(
        BuildRequest(source=source, output=tmp_path / "sample.out"),
        allow_missing_source=True,
    )

    assert plan.backend == "sample.detector"


def test_external_adapter_discovery_is_conflict_safe_and_disabled_by_default() -> None:
    class EntryPoint:
        name = "sample.text"

        def load(self):
            return AdapterDescriptor(
                api_version=ADAPTER_API_VERSION,
                namespace="sample",
                name="text",
                extensions=("txt",),
                planner=lambda request, context: BuildPlan(
                    command=("sample",),
                    cwd=request.source.parent,
                    backend=str(context["backend"]),
                ),
            )

    assert all(not adapter.external for adapter in discover_adapters(entry_points=[EntryPoint()]))
    with pytest.raises(BuildValidationError, match="Conflicting adapter"):
        discover_adapters(
            ["sample.text"],
            [EntryPoint(), EntryPoint()],
        )


def test_auto_selection_never_falls_back_to_deprecated_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        compiler_core,
        "resolve_backend_executable",
        lambda backend: "pkg.exe" if backend == "pkg" else None,
    )
    assert CompilerEngine(require_available=False).choose_backend("js") == "bun"


def test_cli_list_toolchains_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["list-toolchains", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert "nuitka" in output
    assert "rs" in output["rust"]["extensions"]
    assert "wat" in output["wat2wasm"]["extensions"]
    assert output["pyinstaller"]["schema_version"] == CAPABILITY_SCHEMA_VERSION
    assert output["pyinstaller"]["target_platforms"]
    assert "required_sdks" in output["pyinstaller"]
    assert "verified_version" in output["pyinstaller"]


def test_compatibility_matrix_and_cli_json_are_schema_versioned(
    capsys: pytest.CaptureFixture[str],
) -> None:
    matrix = compatibility_matrix()
    assert matrix["schema_version"] == COMPATIBILITY_SCHEMA_VERSION
    assert matrix["kind"] == COMPATIBILITY_KIND
    assert matrix["host_platform"]
    entries = {entry["backend"]: entry for entry in matrix["entries"]}
    assert entries["pyinstaller"]["artifact"]["type"] == "windows-pe"
    assert entries["wat2wasm"]["artifact"]["type"] == "wasm-module"
    assert entries["python-zipapp"]["artifact"]["type"] == "python-zipapp"
    assert entries["pex"]["artifact"]["type"] == "python-pex"
    assert entries["node-sea"]["artifact"]["type"] == "platform-executable"
    assert entries["node-sea"]["default"] is False
    assert entries["pkg"]["lifecycle"] == "deprecated"
    assert entries["pkg"]["default"] is False
    assert set(BACKEND_CATALOG) <= set(BACKEND_ARTIFACT_POLICIES)
    for entry in entries.values():
        assert {
            "type",
            "runtime",
            "assets",
            "verification",
        } <= set(entry["artifact"])

    assert cli_main(["compatibility", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == COMPATIBILITY_SCHEMA_VERSION
    assert {entry["backend"] for entry in output["entries"]} >= {
        "pyinstaller",
        "wat2wasm",
    }


def test_version_source_and_release_metadata_are_synchronized() -> None:
    root = Path(__file__).resolve().parents[1]
    version_document = json.loads(
        (root / "version.json").read_text(encoding="utf-8")
    )
    assert version_document["version"] == APP_VERSION
    assert version_document["schema_version"] == "uc.version.v1"
    assert json.loads(
        (root / "vscode-extension" / "package.json").read_text(encoding="utf-8")
    )["version"] == APP_VERSION

    readme = (root / "README.md").read_text(encoding="utf-8")
    assert f"version-{APP_VERSION}-green" in readme
    assert f"alt=\"Version {APP_VERSION}\"" in readme
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [v{APP_VERSION}] - " in changelog
    assert not re.search(r"## \[v[^]]+\] - (?:%Y|.*->)", changelog)

    python_shell = (root / "UniversalCompiler.py").read_text(encoding="utf-8")
    powershell = (root / "UniversalCompiler.ps1").read_text(encoding="utf-8")
    assert "APP_VERSION = CORE_APP_VERSION" in python_shell
    assert "version.json" in powershell
    assert "$script:AppVersion = \"2.1.0\"" not in powershell


def test_github_actions_template_is_language_specific(tmp_path: Path) -> None:
    workflow = github_actions_template("py")
    assert "setup-python" in workflow
    assert "--no-analytics" in workflow
    assert "--require-hashes" in workflow
    for line in workflow.splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40}(?:\s|#|$)", line)

    ci_workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in ci_workflow
    for line in ci_workflow.splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40}(?:\s|#|$)", line)

    destination = tmp_path / ".github" / "workflow.yml"
    assert (
        cli_main(
            [
                "init-actions",
                "--language",
                "ts",
                "--output",
                str(destination),
            ]
        )
        == 0
    )
    assert "setup-bun" in destination.read_text(encoding="utf-8")


def test_cli_preview_does_not_execute(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "preview.py"
    source.write_text("print('preview')\n", encoding="utf-8")
    profiles = tmp_path / "profiles.yaml"
    save_profiles(profiles, DEFAULT_PROFILES)

    assert (
        cli_main(
            [
                "build",
                str(source),
                "--profiles-file",
                str(profiles),
                "--backend",
                "nuitka",
                "--preview",
            ]
        )
        == 0
    )
    assert "nuitka" in capsys.readouterr().out.lower()
