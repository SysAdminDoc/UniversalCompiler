from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compiler_core import (
    DEFAULT_PROFILES,
    BuildRequest,
    CommandResult,
    CompilerEngine,
    cli_main,
    detect_file_type,
    format_size,
    load_profiles,
    save_profiles,
    verify_artifact,
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

    save_profiles(path, profiles)
    loaded = load_profiles(path)

    assert loaded["Release Profile"]["backend"] == "nuitka"
    assert loaded["Release Profile"]["console"] is True
    assert loaded["Default"]["single_file"] is True


def test_static_artifact_verification(tmp_path: Path) -> None:
    artifact = tmp_path / "sample.exe"
    _fake_pe(artifact)
    result = verify_artifact(artifact)
    assert result.passed is True
    assert result.kind == "pe"

    artifact.write_bytes(b"not an executable")
    assert verify_artifact(artifact).passed is False


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


def test_cli_list_toolchains_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["list-toolchains", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert "nuitka" in output
    assert "rs" in output["rust"]["extensions"]


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
