from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compiler_core
from compiler_core import (
    BuildAnalytics,
    DEFAULT_PROFILES,
    BuildRequest,
    CommandResult,
    CompilerEngine,
    cli_main,
    compile_bytecode,
    detect_file_type,
    format_size,
    github_actions_template,
    load_profiles,
    save_profiles,
    verify_artifact,
    wrap_msix,
)


def _fake_pe(path: Path) -> None:
    data = bytearray(256)
    data[:2] = b"MZ"
    data[0x3C:0x40] = (0x80).to_bytes(4, "little")
    data[0x80:0x84] = b"PE\0\0"
    path.write_bytes(data)


def test_detect_file_type_and_size() -> None:
    assert detect_file_type("script.TS") == "ts"
    assert detect_file_type("README.md") is None
    assert format_size(1024) == "1.0 KB"


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
        _fake_pe(output)
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
        _fake_pe(output)
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
        output = Path(command[command.index("--distpath") + 1]) / (
            Path(command[command.index("--name") + 1]).stem + ".exe"
        )
        _fake_pe(output)
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
        output = Path(command[command.index("--distpath") + 1]) / (
            Path(command[command.index("--name") + 1]).stem + ".exe"
        )
        _fake_pe(output)
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


def test_cli_list_toolchains_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["list-toolchains", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert "nuitka" in output
    assert "rs" in output["rust"]["extensions"]
    assert "wat" in output["wat2wasm"]["extensions"]


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
