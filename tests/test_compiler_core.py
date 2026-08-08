from __future__ import annotations

import json
import struct
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compiler_core
from compiler_core import (
    BuildAnalytics,
    BuildValidationError,
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    BACKEND_CATALOG,
    CAPABILITY_SCHEMA_VERSION,
    DEFAULT_PROFILES,
    BuildRequest,
    BuildResult,
    CommandResult,
    CompilerEngine,
    ExecutionPolicy,
    REQUEST_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    cli_main,
    compile_bytecode,
    detect_file_type,
    format_size,
    github_actions_template,
    load_profiles,
    run_command,
    save_profiles,
    verify_artifact_manifest,
    verify_artifact,
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


def test_static_artifact_verification(tmp_path: Path) -> None:
    artifact = tmp_path / "sample.exe"
    _fake_pe(artifact)
    result = verify_artifact(artifact)
    assert result.passed is True
    assert result.kind == "pe"

    artifact.write_bytes(b"not an executable")
    assert verify_artifact(artifact).passed is False


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


def test_github_actions_template_is_language_specific(tmp_path: Path) -> None:
    workflow = github_actions_template("py")
    assert "setup-python" in workflow
    assert "--no-analytics" in workflow

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
