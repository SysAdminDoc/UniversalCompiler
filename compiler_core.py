"""Side-effect-free build engine and command line interface for Universal Compiler.

The GUI is intentionally kept out of this module.  The engine can therefore be
used by the CLI, tests, batch automation, and the graphical front end without
creating windows, changing the current desktop, or installing Python packages
as an import side effect.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APP_NAME = "Universal Compiler"
APP_VERSION = "2.0"


DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "Default": {
        "console": False,
        "admin": False,
        "single_file": True,
        "backend": "auto",
        "target": "native",
        "architecture": "native",
        "version": "1.0.0.0",
        "company": "",
        "copyright": "",
        "description": "",
        "product": "",
        "prefetch": False,
        "verify": True,
        "cache": True,
    },
    "Console App": {
        "console": True,
        "admin": False,
        "single_file": True,
        "backend": "auto",
        "target": "native",
        "architecture": "native",
        "version": "1.0.0.0",
        "company": "",
        "copyright": "",
        "description": "",
        "product": "",
        "prefetch": False,
        "verify": True,
        "cache": True,
    },
    "Admin Tool": {
        "console": True,
        "admin": True,
        "single_file": True,
        "backend": "auto",
        "target": "native",
        "architecture": "native",
        "version": "1.0.0.0",
        "company": "",
        "copyright": "",
        "description": "",
        "product": "",
        "prefetch": False,
        "verify": True,
        "cache": True,
    },
    "GUI Application": {
        "console": False,
        "admin": False,
        "single_file": True,
        "backend": "auto",
        "target": "native",
        "architecture": "native",
        "version": "1.0.0.0",
        "company": "",
        "copyright": "",
        "description": "",
        "product": "",
        "prefetch": False,
        "verify": True,
        "cache": True,
    },
}


EXTENSION_BACKENDS: dict[str, tuple[str, ...]] = {
    "ps1": ("ps2exe",),
    "py": ("pyinstaller", "nuitka"),
    "bat": ("iexpress",),
    "cmd": ("iexpress",),
    "js": ("bun", "pkg", "deno"),
    "ts": ("bun",),
    "vbs": ("iexpress",),
    "ahk": ("ahk2exe",),
    "cs": ("csc",),
    "go": ("go",),
    "rb": ("ocra",),
    "rs": ("rust",),
    "lua": ("srlua", "luastatic"),
    "pl": ("perl-pp",),
    "pm": ("perl-pp",),
    "kt": ("kotlin-native",),
    "kts": ("kotlin-native",),
}


BACKEND_NAMES: dict[str, str] = {
    "ps2exe": "PowerShell PS2EXE",
    "pyinstaller": "Python PyInstaller",
    "nuitka": "Python Nuitka",
    "iexpress": "Windows IExpress",
    "bun": "Bun compile",
    "pkg": "Node.js pkg",
    "deno": "Deno compile",
    "ahk2exe": "AutoHotkey Ahk2Exe",
    "csc": "C# CSC",
    "go": "Go build",
    "ocra": "Ruby Ocra",
    "rust": "Rust cargo/rustc",
    "srlua": "Lua srlua",
    "luastatic": "Lua luastatic",
    "perl-pp": "Perl PAR::Packer",
    "kotlin-native": "Kotlin/Native",
}


class BuildValidationError(ValueError):
    """Raised when a build request cannot be safely planned."""


@dataclass(frozen=True)
class BuildRequest:
    """All inputs that affect a reproducible build."""

    source: Path
    output: Path
    file_type: str | None = None
    backend: str = "auto"
    target: str = "native"
    architecture: str = "native"
    console: bool = False
    admin: bool = False
    single_file: bool = True
    icon: Path | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    profile_name: str = "Default"
    prefetch: bool = False
    verify: bool = True
    cache: bool = True
    force: bool = False
    extra_args: tuple[str, ...] = ()

    def normalized(self) -> BuildRequest:
        source = Path(self.source).expanduser()
        output = Path(self.output).expanduser()
        icon = Path(self.icon).expanduser() if self.icon else None
        file_type = (
            (self.file_type or detect_file_type(source) or "").lower().lstrip(".")
        )
        metadata = {str(k): str(v) for k, v in dict(self.metadata).items()}
        return BuildRequest(
            source=source,
            output=output,
            file_type=file_type or None,
            backend=(self.backend or "auto").lower(),
            target=(self.target or "native").lower(),
            architecture=(self.architecture or "native").lower(),
            console=bool(self.console),
            admin=bool(self.admin),
            single_file=bool(self.single_file),
            icon=icon,
            metadata=metadata,
            profile_name=self.profile_name,
            prefetch=bool(self.prefetch),
            verify=bool(self.verify),
            cache=bool(self.cache),
            force=bool(self.force),
            extra_args=tuple(str(value) for value in self.extra_args),
        )


@dataclass
class CommandResult:
    """Captured result from one non-shell command."""

    command: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part).strip()


@dataclass
class VerificationResult:
    """Static artifact verification result.

    Runtime execution is deliberately not part of automatic verification.  An
    arbitrary newly-built executable may create a window, request elevation, or
    affect the user's desktop.  Callers that have their own isolated execution
    service can attach a runtime command result separately.
    """

    passed: bool
    kind: str
    details: str
    runtime: CommandResult | None = None


@dataclass
class BuildResult:
    """Structured outcome suitable for CLI JSON, GUI logs, and history."""

    success: bool
    status: str
    request: BuildRequest
    output: Path
    backend: str | None = None
    commands: list[CommandResult] = field(default_factory=list)
    verification: VerificationResult | None = None
    source_hash: str = ""
    cache_key: str = ""
    message: str = ""
    duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["request"]["source"] = str(self.request.source)
        result["request"]["output"] = str(self.request.output)
        result["request"]["icon"] = (
            str(self.request.icon) if self.request.icon else None
        )
        result["request"]["metadata"] = dict(self.request.metadata)
        result["output"] = str(self.output)
        return result


@dataclass
class BuildPlan:
    """A command plus the files it may create outside the requested artifact."""

    command: tuple[str, ...]
    cwd: Path
    backend: str
    artifact_candidates: tuple[Path, ...] = ()
    cleanup_paths: tuple[Path, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)


def config_dir(environment: Mapping[str, str] | None = None) -> Path:
    """Return the per-user configuration directory without creating it."""

    env = environment or os.environ
    root = env.get("APPDATA") or env.get("LOCALAPPDATA") or str(Path.home())
    return Path(root) / "UniversalCompiler"


def profiles_path(environment: Mapping[str, str] | None = None) -> Path:
    return config_dir(environment) / "profiles.yaml"


def detect_file_type(source: os.PathLike[str] | str) -> str | None:
    """Return the normalized supported extension for a source path."""

    extension = Path(source).suffix.lower().lstrip(".")
    return extension if extension in EXTENSION_BACKENDS else None


def format_size(size: int) -> str:
    """Format a byte count for humans."""

    if size >= 1_073_741_824:
        return f"{size / 1_073_741_824:.1f} GB"
    if size >= 1_048_576:
        return f"{size / 1_048_576:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


def estimate_output_size(
    source: os.PathLike[str] | str, file_type: str | None = None
) -> str:
    """Return a conservative output-size estimate for the GUI."""

    path = Path(source)
    if not path.exists():
        return "Unknown"
    estimates = {
        "ps1": (5 * 1024 * 1024, 1.5),
        "py": (15 * 1024 * 1024, 2.0),
        "bat": (50 * 1024, 1.2),
        "cmd": (50 * 1024, 1.2),
        "js": (40 * 1024 * 1024, 1.5),
        "ts": (40 * 1024 * 1024, 1.5),
        "vbs": (50 * 1024, 1.2),
        "ahk": (1 * 1024 * 1024, 1.3),
        "cs": (10 * 1024, 1.1),
        "go": (2 * 1024 * 1024, 1.2),
        "rb": (20 * 1024 * 1024, 2.0),
        "rs": (2 * 1024 * 1024, 1.5),
        "lua": (500 * 1024, 1.2),
        "pl": (15 * 1024 * 1024, 1.5),
        "pm": (15 * 1024 * 1024, 1.5),
        "kt": (20 * 1024 * 1024, 1.5),
        "kts": (20 * 1024 * 1024, 1.5),
    }
    key = (file_type or detect_file_type(path) or "").lower()
    if key not in estimates:
        return "Unknown"
    base, multiplier = estimates[key]
    return format_size(int(base + path.stat().st_size * multiplier))


def load_json(path: os.PathLike[str] | str, default: Any = None) -> Any:
    """Load JSON safely, returning a caller-provided default on failure."""

    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return default if default is not None else {}


def save_json(path: os.PathLike[str] | str, value: Any) -> None:
    """Atomically save JSON while keeping a partially-written file recoverable."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, destination)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    if not text:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_.:/+@-]+", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _dump_profiles_yaml(profiles: Mapping[str, Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for name, profile in profiles.items():
        lines.append(f"{_yaml_scalar(name)}:")
        for key, value in profile.items():
            lines.append(f"  {key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def _parse_yaml_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "~"}:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        if value[0] == '"':
            try:
                return json.loads(value)
            except ValueError:
                pass
        return value[1:-1].replace("''", "'")
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _load_simple_yaml(text: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    current_name: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith("  "):
            if current is None or ":" not in raw_line:
                continue
            key, value = raw_line.strip().split(":", 1)
            current[key.strip()] = _parse_yaml_scalar(value)
            continue
        if ":" not in raw_line:
            continue
        name, value = raw_line.split(":", 1)
        current_name = str(_parse_yaml_scalar(name.strip()))
        current = {}
        result[current_name] = current
        if value.strip():
            result[current_name] = _parse_yaml_scalar(value)
            current = None
    return result


def load_profiles(
    path: os.PathLike[str] | str,
    defaults: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Load YAML profiles, with JSON and a dependency-free YAML fallback."""

    merged = {
        name: dict(profile) for name, profile in (defaults or DEFAULT_PROFILES).items()
    }
    source = Path(path)
    if not source.exists():
        return merged
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return merged
    loaded: Any = None
    try:
        import yaml  # type: ignore[import-not-found]

        loaded = yaml.safe_load(text)
    except (ImportError, AttributeError, ValueError):
        loaded = _load_simple_yaml(text)
    if not isinstance(loaded, Mapping):
        return merged
    for name, profile in loaded.items():
        if isinstance(profile, Mapping):
            base = dict(merged.get(str(name), {}))
            base.update({str(key): value for key, value in profile.items()})
            merged[str(name)] = base
    return merged


def save_profiles(
    path: os.PathLike[str] | str, profiles: Mapping[str, Mapping[str, Any]]
) -> None:
    """Write human-readable YAML without requiring PyYAML."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(_dump_profiles_yaml(profiles), encoding="utf-8")
    os.replace(temporary, destination)


def sha256_file(path: os.PathLike[str] | str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ps_quote(value: os.PathLike[str] | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def command_display(command: Sequence[str]) -> str:
    """Return a copy/paste-friendly display form without using a shell."""

    if os.name == "nt":
        return subprocess.list2cmdline([str(item) for item in command])
    return shlex.join([str(item) for item in command])


def run_command(
    command: Sequence[str],
    cwd: os.PathLike[str] | str | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> CommandResult:
    """Run one executable directly, with no shell and no visible console window."""

    normalized = tuple(str(item) for item in command)
    started = time.monotonic()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    if environment:
        env.update({str(key): str(value) for key, value in environment.items()})
    try:
        completed = subprocess.run(
            normalized,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
        return CommandResult(
            command=normalized,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_seconds=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired as error:
        stdout = (
            error.stdout.decode("utf-8", errors="replace")
            if isinstance(error.stdout, bytes)
            else (error.stdout or "")
        )
        stderr = (
            error.stderr.decode("utf-8", errors="replace")
            if isinstance(error.stderr, bytes)
            else (error.stderr or "")
        )
        return CommandResult(
            command=normalized,
            returncode=124,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
            timed_out=True,
        )
    except OSError as error:
        return CommandResult(
            command=normalized,
            returncode=127,
            stderr=str(error),
            duration_seconds=time.monotonic() - started,
        )


def _find_first(names: Iterable[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _powershell_executable() -> str | None:
    return _find_first(("pwsh", "powershell"))


def _windows_path_candidates(*parts: str) -> Iterable[Path]:
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            yield Path(root).joinpath(*parts)


def resolve_backend_executable(backend: str) -> str | None:
    """Resolve the executable for a backend without installing anything."""

    direct = {
        "pyinstaller": ("pyinstaller",),
        "nuitka": (),
        "iexpress": (),
        "bun": ("bun",),
        "pkg": ("pkg",),
        "deno": ("deno",),
        "csc": (),
        "go": ("go",),
        "ocra": ("ocra",),
        "rust": ("cargo", "rustc"),
        "srlua": ("srlua",),
        "luastatic": ("luastatic",),
        "perl-pp": ("pp",),
        "kotlin-native": ("kotlinc-native",),
    }
    if backend == "ps2exe":
        return _powershell_executable()
    if backend == "ahk2exe":
        candidates = list(
            _windows_path_candidates("AutoHotkey", "Compiler", "Ahk2Exe.exe")
        )
        candidates.extend(
            _windows_path_candidates("AutoHotkey", "v2", "Compiler", "Ahk2Exe.exe")
        )
        return next((str(path) for path in candidates if path.exists()), None)
    if backend == "iexpress":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        path = Path(windir) / "System32" / "iexpress.exe"
        return str(path) if path.exists() else None
    if backend == "csc":
        windir = os.environ.get("WINDIR", "C:\\Windows")
        candidates = (
            Path(windir) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
            Path(windir) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
        )
        return next((str(path) for path in candidates if path.exists()), None)
    if backend == "nuitka":
        return sys.executable if importlib.util.find_spec("nuitka") else None
    names = direct.get(backend)
    return _find_first(names) if names else None


def backend_status() -> dict[str, dict[str, Any]]:
    """Return deterministic, read-only availability information for every backend."""

    status: dict[str, dict[str, Any]] = {}
    for backend, name in BACKEND_NAMES.items():
        path = resolve_backend_executable(backend)
        status[backend] = {
            "backend": backend,
            "name": name,
            "available": path is not None,
            "executable": path,
            "extensions": [
                ext for ext, values in EXTENSION_BACKENDS.items() if backend in values
            ],
        }
    return status


def _target_for(backend: str, architecture: str) -> str:
    arch = architecture.lower()
    if arch in {"native", "auto"}:
        return "native"
    if backend in {"pkg", "bun"}:
        suffix = {"x64": "x64", "amd64": "x64", "x86": "x86", "arm64": "arm64"}.get(
            arch, arch
        )
        prefix = "node" if backend == "pkg" else "bun"
        return f"{prefix}-win-{suffix}" if backend == "pkg" else f"bun-windows-{suffix}"
    if backend == "deno":
        return {
            "x64": "x86_64-pc-windows-msvc",
            "amd64": "x86_64-pc-windows-msvc",
            "arm64": "aarch64-pc-windows-msvc",
        }.get(arch, arch)
    return arch


def _metadata(request: BuildRequest, key: str) -> str:
    return str(request.metadata.get(key, ""))


def _write_pyinstaller_version_file(path: Path, metadata: Mapping[str, str]) -> None:
    """Write the version resource consumed by PyInstaller."""

    def escaped(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    version = _normalize_version(metadata.get("version", "1.0.0.0"))
    lines = [
        "# UTF-8",
        "VSVersionInfo(",
        "  ffi=FixedFileInfo(",
        (
            f"    filevers={version}, prodvers={version}, mask=0x3f, flags=0x0, "
            "OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)"
        ),
        "  ),",
        "  kids=[",
        "    StringFileInfo([",
        "      StringTable('040904B0', [",
    ]
    values = {
        "CompanyName": metadata.get("company", ""),
        "FileDescription": metadata.get("description", ""),
        "FileVersion": metadata.get("version", "1.0.0.0"),
        "InternalName": metadata.get("product", "UniversalCompiler"),
        "OriginalFilename": metadata.get("product", "UniversalCompiler") + ".exe",
        "ProductName": metadata.get("product", "Universal Compiler"),
        "ProductVersion": metadata.get("version", "1.0.0.0"),
        "LegalCopyright": metadata.get("copyright", ""),
    }
    lines.extend(
        f'        StringStruct(u"{key}", u"{escaped(value)}"),'
        for key, value in values.items()
    )
    lines.extend(
        [
            "      ]),",
            "    ]),",
            "    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])",
            "  ]",
            ")",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _normalize_version(value: str) -> tuple[int, int, int, int]:
    parts = [int(part) if part.isdigit() else 0 for part in str(value).split(".")[:4]]
    return tuple((parts + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def _cargo_package_name(cargo_file: Path) -> str | None:
    try:
        text = cargo_file.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"(?ms)^\[package\]\s+.*?^name\s*=\s*[\"']([^\"']+)", text)
    return match.group(1) if match else None


def _find_cargo_project(source: Path) -> Path | None:
    if source.name.lower() == "cargo.toml" and source.exists():
        return source
    candidate = source.parent / "Cargo.toml"
    return candidate if candidate.exists() else None


class CompilerEngine:
    """Plan and execute compiler backends with cache and verification support."""

    def __init__(
        self,
        runner: Callable[..., CommandResult] = run_command,
        require_available: bool = True,
    ) -> None:
        self.runner = runner
        self.require_available = require_available

    def choose_backend(self, file_type: str, requested: str = "auto") -> str | None:
        choices = EXTENSION_BACKENDS.get(file_type.lower(), ())
        if requested != "auto":
            return (
                requested
                if requested in choices or requested in BACKEND_NAMES
                else None
            )
        for backend in choices:
            if resolve_backend_executable(backend):
                return backend
        return choices[0] if choices else None

    def _validate(
        self, request: BuildRequest, allow_missing_source: bool = False
    ) -> BuildRequest:
        normalized = request.normalized()
        if not normalized.file_type or normalized.file_type not in EXTENSION_BACKENDS:
            raise BuildValidationError(
                f"Unsupported source type: {normalized.source.suffix or '<none>'}"
            )
        if not allow_missing_source and not normalized.source.is_file():
            raise BuildValidationError(f"Source file not found: {normalized.source}")
        if normalized.icon and not normalized.icon.is_file():
            raise BuildValidationError(f"Icon file not found: {normalized.icon}")
        backend = self.choose_backend(normalized.file_type, normalized.backend)
        if not backend:
            raise BuildValidationError(f"No backend supports .{normalized.file_type}")
        if normalized.backend == "auto" or normalized.backend != backend:
            normalized = BuildRequest(**{**asdict(normalized), "backend": backend})
        if self.require_available and not resolve_backend_executable(backend):
            raise BuildValidationError(f"Compiler backend is not installed: {backend}")
        return normalized

    def _tool_identity(self, backend: str) -> dict[str, Any]:
        executable = resolve_backend_executable(backend)
        if not executable:
            return {"backend": backend, "executable": None}
        path = Path(executable)
        try:
            stat = path.stat()
            return {
                "backend": backend,
                "executable": str(path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        except OSError:
            return {"backend": backend, "executable": str(path)}

    def _cache_path(self, output: Path) -> Path:
        return output.with_name(output.name + ".uc-cache.json")

    def _cache_key(self, request: BuildRequest, source_hash: str, backend: str) -> str:
        return _canonical_hash(
            {
                "source": str(request.source.resolve()),
                "source_hash": source_hash,
                "file_type": request.file_type,
                "backend": backend,
                "target": request.target,
                "architecture": request.architecture,
                "console": request.console,
                "admin": request.admin,
                "single_file": request.single_file,
                "icon": str(request.icon.resolve()) if request.icon else None,
                "icon_hash": sha256_file(request.icon)
                if request.icon and request.icon.exists()
                else None,
                "metadata": dict(request.metadata),
                "extra_args": list(request.extra_args),
                "tool": self._tool_identity(backend),
            }
        )

    def _cache_hit(self, request: BuildRequest, key: str) -> bool:
        if request.force or not request.cache or not request.output.is_file():
            return False
        saved = load_json(self._cache_path(request.output), {})
        return isinstance(saved, Mapping) and saved.get("key") == key

    def _save_cache(
        self,
        request: BuildRequest,
        key: str,
        source_hash: str,
        backend: str,
        verification: VerificationResult | None,
    ) -> None:
        save_json(
            self._cache_path(request.output),
            {
                "key": key,
                "source_hash": source_hash,
                "backend": backend,
                "output": str(request.output),
                "created_at": datetime.now(UTC).isoformat(),
                "verification": asdict(verification) if verification else None,
            },
        )

    def plan(
        self, request: BuildRequest, allow_missing_source: bool = False
    ) -> BuildPlan:
        """Create a command plan.  This never executes a compiler."""

        request = self._validate(request, allow_missing_source=allow_missing_source)
        source = request.source.resolve()
        output = request.output.resolve()
        backend = request.backend
        executable = resolve_backend_executable(backend) or backend
        cleanup: list[Path] = []
        candidates: list[Path] = [output]
        environment: dict[str, str] = {}
        target = _target_for(backend, request.architecture)

        if backend == "ps2exe":
            args = [
                "Import-Module ps2exe;",
                f"Invoke-PS2EXE -InputFile {_ps_quote(source)} -OutputFile {_ps_quote(output)}",
            ]
            if request.icon:
                args.append(f"-IconFile {_ps_quote(request.icon)}")
            if request.admin:
                args.append("-RequireAdmin")
            if not request.console:
                args.append("-NoConsole")
            for key, flag in (
                ("product", "-Title"),
                ("version", "-Version"),
                ("company", "-Company"),
                ("copyright", "-Copyright"),
            ):
                if _metadata(request, key):
                    args.append(f"{flag} {_ps_quote(_metadata(request, key))}")
            command = (
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                " ".join(args),
            )
        elif backend == "pyinstaller":
            work = output.parent / ".uc-build" / output.stem
            if not allow_missing_source:
                work.mkdir(parents=True, exist_ok=True)
            cleanup.append(work)
            command_parts = [
                executable,
                "--noconfirm",
                "--clean",
                "--distpath",
                str(output.parent),
                "--workpath",
                str(work),
                "--specpath",
                str(work),
                "--name",
                output.stem,
            ]
            if request.single_file:
                command_parts.append("--onefile")
            if not request.console:
                command_parts.append("--noconsole")
            if request.icon:
                command_parts.extend(("--icon", str(request.icon)))
            if request.metadata:
                version_file = work / "version_info.txt"
                if not allow_missing_source:
                    _write_pyinstaller_version_file(version_file, request.metadata)
                command_parts.extend(("--version-file", str(version_file)))
            command_parts.extend((str(source), *request.extra_args))
            command = tuple(command_parts)
        elif backend == "nuitka":
            work = output.parent / ".uc-build" / output.stem
            if not allow_missing_source:
                work.mkdir(parents=True, exist_ok=True)
            cleanup.append(work)
            command_parts = [
                sys.executable,
                "-m",
                "nuitka",
                f"--output-dir={output.parent}",
                f"--output-filename={output.name}",
                "--onefile" if request.single_file else "--standalone",
                "--windows-console-mode=force"
                if request.console
                else "--windows-console-mode=disable",
            ]
            if request.admin:
                command_parts.append("--windows-uac-admin")
            if request.icon:
                command_parts.append(f"--windows-icon-from-ico={request.icon}")
            if _metadata(request, "product"):
                command_parts.append(f"--product-name={_metadata(request, 'product')}")
            if _metadata(request, "company"):
                command_parts.append(f"--company-name={_metadata(request, 'company')}")
            command_parts.extend((str(source), *request.extra_args))
            command = tuple(command_parts)
        elif backend in {"bun", "pkg", "deno"}:
            command_parts: list[str]
            if backend == "bun":
                command_parts = [
                    executable,
                    "build",
                    "--compile",
                    str(source),
                    "--outfile",
                    str(output),
                ]
                if target != "native":
                    command_parts.extend(("--target", target))
            elif backend == "pkg":
                command_parts = [
                    executable,
                    str(source),
                    "--target",
                    target if target != "native" else "node18-win-x64",
                    "--output",
                    str(output),
                ]
            else:
                command_parts = [executable, "compile", "--output", str(output)]
                if target != "native":
                    command_parts.extend(("--target", target))
                command_parts.append(str(source))
            command = tuple(command_parts + list(request.extra_args))
        elif backend == "iexpress":
            temp_dir = (
                Path(tempfile.gettempdir()) / "uc-iexpress-preview"
                if allow_missing_source
                else Path(tempfile.mkdtemp(prefix="uc-iexpress-"))
            )
            cleanup.append(temp_dir)
            source_copy = temp_dir / source.name
            if not allow_missing_source:
                shutil.copy2(source, source_copy)
            sed = temp_dir / "config.sed"
            if not allow_missing_source:
                sed.write_text(
                    "[Version]\nClass=IEXPRESS\nSEDVersion=3\n[Options]\n"
                    "PackagePurpose=InstallApp\nShowInstallProgramWindow=0\nHideExtractAnimation=1\n"
                    "UseLongFileName=1\nInsideCompressed=0\nCAB_FixedSize=0\nRebootMode=N\n"
                    f"TargetName={output}\nFriendlyName=Universal Compiler build\n"
                    f'AppLaunched=cmd /c "{source.name}"\nPostInstallCmd=<None>\n'
                    "SourceFiles=SourceFiles\n[Strings]\n[SourceFiles]\n"
                    f"SourceFiles0={temp_dir}\\\n[SourceFiles0]\n%FILE0%={source.name}\n",
                    encoding="utf-8",
                )
            command = (executable, "/N", "/Q", str(sed))
        elif backend == "csc":
            target_flag = "/target:exe" if request.console else "/target:winexe"
            command = (
                executable,
                target_flag,
                f"/out:{output}",
                *request.extra_args,
                str(source),
            )
        elif backend == "go":
            environment = {"GOOS": "windows"}
            if request.architecture not in {"native", "auto"}:
                environment["GOARCH"] = {
                    "x86": "386",
                    "x64": "amd64",
                    "amd64": "amd64",
                    "arm64": "arm64",
                }.get(request.architecture, request.architecture)
            command = (
                executable,
                "build",
                "-trimpath",
                "-o",
                str(output),
                *request.extra_args,
                str(source),
            )
        elif backend == "rust":
            cargo = _find_first(("cargo",))
            cargo_file = _find_cargo_project(source)
            if cargo and cargo_file:
                package = _cargo_package_name(cargo_file)
                if not package:
                    raise BuildValidationError(
                        f"Could not determine Cargo package name: {cargo_file}"
                    )
                command = (cargo, "build", "--release", *request.extra_args)
                candidate_dir = cargo_file.parent / "target" / "release"
                candidates.append(candidate_dir / f"{package}.exe")
            else:
                rustc = _find_first(("rustc",)) or executable
                command = (
                    rustc,
                    "--edition",
                    "2021",
                    "-O",
                    "-o",
                    str(output),
                    *request.extra_args,
                    str(source),
                )
        elif backend == "srlua":
            command = (executable, str(source), str(output), *request.extra_args)
        elif backend == "luastatic":
            command = (executable, str(source), "-o", str(output), *request.extra_args)
        elif backend == "perl-pp":
            command = (executable, "-o", str(output), *request.extra_args, str(source))
        elif backend == "kotlin-native":
            command = (
                executable,
                str(source),
                "-o",
                str(output.with_suffix("")),
                *request.extra_args,
            )
            candidates.append(output.with_suffix(""))
        elif backend == "ahk2exe":
            command_parts = [executable, "/in", str(source), "/out", str(output)]
            if request.icon:
                command_parts.extend(("/icon", str(request.icon)))
            command = tuple(command_parts + list(request.extra_args))
        elif backend == "ocra":
            command = (
                executable,
                str(source),
                "--output",
                str(output),
                *request.extra_args,
            )
        else:
            raise BuildValidationError(f"Unsupported backend: {backend}")
        return BuildPlan(
            command=command,
            cwd=source.parent,
            backend=backend,
            artifact_candidates=tuple(candidates),
            cleanup_paths=tuple(cleanup),
            environment=environment,
        )

    def prefetch_dependencies(self, request: BuildRequest) -> list[CommandResult]:
        """Run opt-in dependency prefetch commands discovered beside the source."""

        source = request.source.resolve()
        root = source.parent
        commands: list[tuple[Sequence[str], Path]] = []
        requirements = root / "requirements.txt"
        if requirements.exists() and request.file_type in {"py", "pyw"}:
            commands.append(
                (
                    (sys.executable, "-m", "pip", "install", "-r", str(requirements)),
                    root,
                )
            )
        package = root / "package.json"
        if package.exists() and request.file_type in {"js", "ts"}:
            manager = _find_first(("bun", "npm"))
            if manager:
                commands.append(((manager, "install"), root))
        go_mod = root / "go.mod"
        if go_mod.exists() and request.file_type == "go":
            go = _find_first(("go",))
            if go:
                commands.append(((go, "mod", "download"), root))
        cargo = root / "Cargo.toml"
        if cargo.exists() and request.file_type == "rs":
            cargo_exe = _find_first(("cargo",))
            if cargo_exe:
                commands.append(((cargo_exe, "fetch"), root))
        gemfile = root / "Gemfile"
        bundle = _find_first(("bundle",))
        if gemfile.exists() and bundle and request.file_type == "rb":
            commands.append(((bundle, "install"), root))
        return [self.runner(command, cwd=cwd) for command, cwd in commands]

    def build(self, request: BuildRequest) -> BuildResult:
        started = time.monotonic()
        try:
            normalized = self._validate(request)
            normalized.output.parent.mkdir(parents=True, exist_ok=True)
            source_hash = sha256_file(normalized.source)
            plan = self.plan(normalized)
            cache_key = self._cache_key(normalized, source_hash, plan.backend)
            if self._cache_hit(normalized, cache_key):
                verification = (
                    verify_artifact(normalized.output) if normalized.verify else None
                )
                if verification is None or verification.passed:
                    return BuildResult(
                        True,
                        "cache-hit",
                        normalized,
                        normalized.output,
                        plan.backend,
                        verification=verification,
                        source_hash=source_hash,
                        cache_key=cache_key,
                        message="Build cache hit",
                        duration_seconds=time.monotonic() - started,
                    )
            commands: list[CommandResult] = []
            if normalized.prefetch:
                commands.extend(self.prefetch_dependencies(normalized))
                failed_prefetch = next(
                    (result for result in commands if not result.success), None
                )
                if failed_prefetch:
                    return BuildResult(
                        False,
                        "failed",
                        normalized,
                        normalized.output,
                        plan.backend,
                        commands=commands,
                        source_hash=source_hash,
                        cache_key=cache_key,
                        message=f"Dependency prefetch failed: {failed_prefetch.output}",
                        duration_seconds=time.monotonic() - started,
                    )
            result = self.runner(
                plan.command, cwd=plan.cwd, environment=plan.environment
            )
            commands.append(result)
            if not result.success:
                return BuildResult(
                    False,
                    "failed",
                    normalized,
                    normalized.output,
                    plan.backend,
                    commands=commands,
                    source_hash=source_hash,
                    cache_key=cache_key,
                    message=result.output or "Compiler command failed",
                    duration_seconds=time.monotonic() - started,
                )
            if not normalized.output.exists():
                candidate = next(
                    (path for path in plan.artifact_candidates if path.is_file()), None
                )
                if candidate and candidate != normalized.output:
                    normalized.output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, normalized.output)
            verification = (
                verify_artifact(normalized.output) if normalized.verify else None
            )
            if normalized.verify and (verification is None or not verification.passed):
                detail = (
                    verification.details
                    if verification
                    else "No artifact verification result"
                )
                return BuildResult(
                    False,
                    "failed",
                    normalized,
                    normalized.output,
                    plan.backend,
                    commands=commands,
                    verification=verification,
                    source_hash=source_hash,
                    cache_key=cache_key,
                    message=f"Post-build verification failed: {detail}",
                    duration_seconds=time.monotonic() - started,
                )
            self._save_cache(
                normalized, cache_key, source_hash, plan.backend, verification
            )
            return BuildResult(
                True,
                "built",
                normalized,
                normalized.output,
                plan.backend,
                commands=commands,
                verification=verification,
                source_hash=source_hash,
                cache_key=cache_key,
                message="Build completed",
                duration_seconds=time.monotonic() - started,
            )
        except (BuildValidationError, OSError, ValueError) as error:
            output = Path(request.output)
            return BuildResult(
                False,
                "failed",
                request.normalized(),
                output,
                request.backend,
                message=str(error),
                duration_seconds=time.monotonic() - started,
            )
        finally:
            if "plan" in locals():
                for path in plan.cleanup_paths:
                    try:
                        if path.is_dir():
                            shutil.rmtree(path, ignore_errors=True)
                        elif path.exists():
                            path.unlink()
                    except OSError:
                        pass

    def build_batch(
        self, requests: Sequence[BuildRequest], workers: int = 1
    ) -> list[BuildResult]:
        """Compile independent requests in parallel while retaining input order."""

        if workers <= 1 or len(requests) <= 1:
            return [self.build(request) for request in requests]
        worker_count = max(1, min(int(workers), len(requests)))
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="uc-build"
        ) as pool:
            futures = [pool.submit(self.build, request) for request in requests]
            return [future.result() for future in futures]

    def watch(
        self,
        request: BuildRequest,
        interval: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> Iterable[BuildResult]:
        """Yield an initial build and subsequent builds when the source changes."""

        event = stop_event or threading.Event()
        previous: str | None = None
        while not event.is_set():
            try:
                current = sha256_file(request.source)
            except OSError:
                current = None
            if current and current != previous:
                previous = current
                yield self.build(request)
            event.wait(max(0.1, interval))


def verify_artifact(path: os.PathLike[str] | str) -> VerificationResult:
    """Verify the file/container signature without executing the artifact."""

    artifact = Path(path)
    if not artifact.is_file():
        return VerificationResult(False, "missing", f"Artifact not found: {artifact}")
    try:
        with artifact.open("rb") as handle:
            header = handle.read(4096)
    except OSError as error:
        return VerificationResult(False, "unreadable", str(error))
    if artifact.suffix.lower() == ".exe":
        if len(header) < 64 or header[:2] != b"MZ":
            return VerificationResult(
                False, "pe", "Executable does not have an MZ header"
            )
        pe_offset = int.from_bytes(header[0x3C:0x40], "little")
        if (
            pe_offset + 4 > len(header)
            or header[pe_offset : pe_offset + 4] != b"PE\0\0"
        ):
            return VerificationResult(
                False, "pe", "Executable does not have a valid PE signature"
            )
        return VerificationResult(
            True, "pe", f"Valid PE artifact ({format_size(artifact.stat().st_size)})"
        )
    if artifact.suffix.lower() in {".jar", ".zip"}:
        try:
            import zipfile

            passed = zipfile.is_zipfile(artifact)
        except OSError:
            passed = False
        return VerificationResult(
            passed,
            "zip",
            "Valid ZIP/JAR archive" if passed else "Invalid ZIP/JAR archive",
        )
    if artifact.suffix.lower() == ".wasm":
        passed = header[:4] == b"\x00asm"
        return VerificationResult(
            passed,
            "wasm",
            "Valid WebAssembly header" if passed else "Invalid WebAssembly header",
        )
    passed = artifact.stat().st_size > 0
    return VerificationResult(
        passed, "file", "Non-empty artifact" if passed else "Empty artifact"
    )


def parse_metadata(values: Sequence[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise BuildValidationError(f"Metadata must use key=value: {value}")
        key, item = value.split("=", 1)
        key = key.strip()
        if not key:
            raise BuildValidationError(f"Metadata key is empty: {value}")
        metadata[key] = item
    return metadata


def _profile_request(
    profile: Mapping[str, Any], args: argparse.Namespace, source: Path, output: Path
) -> BuildRequest:
    metadata = {
        key: str(profile.get(key, ""))
        for key in ("product", "version", "company", "copyright", "description")
        if profile.get(key, "") != ""
    }
    metadata.update(parse_metadata(args.metadata or []))
    return BuildRequest(
        source=source,
        output=output,
        backend=args.backend or str(profile.get("backend", "auto")),
        target=args.target or str(profile.get("target", "native")),
        architecture=args.architecture or str(profile.get("architecture", "native")),
        console=args.console
        if args.console is not None
        else bool(profile.get("console", False)),
        admin=args.admin
        if args.admin is not None
        else bool(profile.get("admin", False)),
        single_file=not args.no_single_file
        if args.no_single_file
        else bool(profile.get("single_file", True)),
        icon=Path(args.icon) if args.icon else None,
        metadata=metadata,
        profile_name=args.profile,
        prefetch=args.prefetch or bool(profile.get("prefetch", False)),
        verify=not args.no_verify
        if args.no_verify
        else bool(profile.get("verify", True)),
        cache=not args.no_cache if args.no_cache else bool(profile.get("cache", True)),
        force=args.force,
        extra_args=tuple(args.extra_arg or []),
    )


def _add_build_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", "-o", help="Output executable or artifact path")
    parser.add_argument(
        "--profile", default="Default", help="Profile name from profiles.yaml"
    )
    parser.add_argument("--profiles-file", help="Path to a YAML profiles file")
    parser.add_argument("--backend", help="Backend override, such as nuitka or bun")
    parser.add_argument("--target", help="Backend target triple or runtime target")
    parser.add_argument(
        "--architecture", "--arch", default=None, help="native, x86, x64, or arm64"
    )
    parser.add_argument(
        "--console", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument("--admin", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--no-single-file", action="store_true")
    parser.add_argument("--icon")
    parser.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument(
        "--prefetch",
        action="store_true",
        help="Install/fetch manifest dependencies before building",
    )
    parser.add_argument(
        "--verify",
        dest="verify_flag",
        action="store_true",
        help="Enable static artifact verification",
    )
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--extra-arg", action="append", default=[])


def create_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="UniversalCompiler", description="Build scripts into Windows executables."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build one source file")
    build_parser.add_argument("source")
    _add_build_options(build_parser)
    build_parser.add_argument(
        "--preview", action="store_true", help="Print the command without executing it"
    )
    build_parser.add_argument("--watch", action="store_true")
    build_parser.add_argument("--watch-interval", type=float, default=1.0)
    build_parser.add_argument("--json", action="store_true")

    batch_parser = subparsers.add_parser("batch", help="Build multiple source files")
    batch_parser.add_argument("sources", nargs="+")
    _add_build_options(batch_parser)
    batch_parser.add_argument("--jobs", type=int, default=1)
    batch_parser.add_argument("--output-dir")
    batch_parser.add_argument("--json", action="store_true")

    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect a source and its backend availability"
    )
    inspect_parser.add_argument("source")
    inspect_parser.add_argument("--json", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify", help="Verify an artifact without executing it"
    )
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser(
        "list-toolchains", help="List supported backends and availability"
    )
    list_parser.add_argument("--json", action="store_true")

    init_parser = subparsers.add_parser(
        "init-profiles", help="Create a starter YAML profile file"
    )
    init_parser.add_argument(
        "path",
        nargs="?",
        help="Destination, defaulting to the per-user config directory",
    )

    return parser


def _default_output(source: Path) -> Path:
    return source.with_suffix(".exe")


def _result_text(result: BuildResult) -> str:
    lines = [f"{result.status}: {result.output}"]
    if result.backend:
        lines.append(f"backend: {result.backend}")
    if result.message:
        lines.append(result.message)
    for command in result.commands:
        lines.append(f"$ {command_display(command.command)}")
        if command.output:
            lines.append(command.output)
    if result.verification:
        lines.append(
            "verification: "
            f"{'pass' if result.verification.passed else 'fail'} "
            f"({result.verification.details})"
        )
    return "\n".join(lines)


def cli_main(argv: Sequence[str] | None = None) -> int:
    parser = create_cli_parser()
    args = parser.parse_args(argv)
    if args.command == "list-toolchains":
        value = backend_status()
        print(
            json.dumps(value, indent=2)
            if args.json
            else "\n".join(
                f"{key}: {'available' if item['available'] else 'missing'}"
                for key, item in value.items()
            )
        )
        return 0
    if args.command == "init-profiles":
        destination = Path(args.path).expanduser() if args.path else profiles_path()
        save_profiles(destination, DEFAULT_PROFILES)
        print(destination)
        return 0
    if args.command == "verify":
        result = verify_artifact(args.artifact)
        print(
            json.dumps(asdict(result), indent=2, default=str)
            if args.json
            else f"{'PASS' if result.passed else 'FAIL'}: {result.details}"
        )
        return 0 if result.passed else 1
    if args.command == "inspect":
        source = Path(args.source).expanduser()
        file_type = detect_file_type(source)
        choices = EXTENSION_BACKENDS.get(file_type or "", ())
        value = {
            "source": str(source),
            "file_type": file_type,
            "estimated_size": estimate_output_size(source, file_type),
            "backends": {backend: backend_status()[backend] for backend in choices},
        }
        print(json.dumps(value, indent=2) if args.json else json.dumps(value, indent=2))
        return 0

    profile_file = (
        Path(args.profiles_file).expanduser()
        if getattr(args, "profiles_file", None)
        else profiles_path()
    )
    profiles = load_profiles(profile_file)
    profile = profiles.get(args.profile)
    if profile is None:
        parser.error(f"Profile not found: {args.profile}")
    engine = CompilerEngine()
    if args.command == "build":
        source = Path(args.source).expanduser()
        output = (
            Path(args.output).expanduser() if args.output else _default_output(source)
        )
        request = _profile_request(profile, args, source, output)
        if args.preview:
            try:
                plan = CompilerEngine(require_available=False).plan(
                    request,
                    allow_missing_source=True,
                )
            except BuildValidationError as error:
                print(f"ERROR: {error}", file=sys.stderr)
                return 2
            print(command_display(plan.command))
            return 0
        if args.watch:
            try:
                for result in engine.watch(request, interval=args.watch_interval):
                    print(
                        json.dumps(result.as_dict(), indent=2, default=str)
                        if args.json
                        else _result_text(result),
                        flush=True,
                    )
            except KeyboardInterrupt:
                return 0
            return 0
        result = engine.build(request)
        print(
            json.dumps(result.as_dict(), indent=2, default=str)
            if args.json
            else _result_text(result)
        )
        return 0 if result.success else 1
    if args.command == "batch":
        output_dir = Path(args.output_dir).expanduser() if args.output_dir else None
        requests: list[BuildRequest] = []
        for source_value in args.sources:
            source = Path(source_value).expanduser()
            output = (
                (output_dir / f"{source.stem}.exe")
                if output_dir
                else _default_output(source)
            )
            requests.append(_profile_request(profile, args, source, output))
        results = engine.build_batch(requests, workers=args.jobs)
        if args.json:
            print(
                json.dumps(
                    [result.as_dict() for result in results], indent=2, default=str
                )
            )
        else:
            print("\n\n".join(_result_text(result) for result in results))
        return 0 if all(result.success for result in results) else 1
    parser.error(f"Unsupported command: {args.command}")
    return 2


__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "BACKEND_NAMES",
    "BuildPlan",
    "BuildRequest",
    "BuildResult",
    "BuildValidationError",
    "CommandResult",
    "CompilerEngine",
    "DEFAULT_PROFILES",
    "EXTENSION_BACKENDS",
    "VerificationResult",
    "backend_status",
    "cli_main",
    "command_display",
    "config_dir",
    "detect_file_type",
    "estimate_output_size",
    "format_size",
    "load_json",
    "load_profiles",
    "profiles_path",
    "run_command",
    "save_json",
    "save_profiles",
    "sha256_file",
    "verify_artifact",
]
