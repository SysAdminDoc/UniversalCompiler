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
import math
import os
import py_compile
import re
import shlex
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APP_NAME = "Universal Compiler"
APP_VERSION = "2.1.0"
REQUEST_SCHEMA_VERSION = "uc.request.v1"
RESULT_SCHEMA_VERSION = "uc.result.v1"
CAPABILITY_SCHEMA_VERSION = "uc.capability.v1"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "uc.artifact-manifest.v1"


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
        "toolchain_versions": {},
        "upx": False,
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
        "toolchain_versions": {},
        "upx": False,
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
        "toolchain_versions": {},
        "upx": False,
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
        "toolchain_versions": {},
        "upx": False,
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
    "wat": ("wat2wasm",),
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
    "upx": "UPX compressor",
    "wat2wasm": "WebAssembly wat2wasm",
}

OBFUSCATOR_NAMES: dict[str, str] = {
    "pyarmor": "PyArmor Python obfuscator",
    "javascript-obfuscator": "JavaScript Obfuscator",
    "confuserex": "ConfuserEx .NET obfuscator",
}


class BuildValidationError(ValueError):
    """Raised when a build request cannot be safely planned."""


DEFAULT_EXECUTION_TIMEOUT_SECONDS = 15 * 60
DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
DEFAULT_INHERITED_ENVIRONMENT = (
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
    "VIRTUAL_ENV",
    "WINDIR",
)
_SENSITIVE_ARGUMENT_NAMES = frozenset(
    {
        "--api-key",
        "--apikey",
        "--password",
        "--secret",
        "--token",
        "--access-token",
        "--client-secret",
    }
)
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(\b(?:api[_-]?key|password|secret|token)\b\s*[=:]\s*)([^\s,;]+)"
)


def _default_executable_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    try:
        roots.append(Path(sys.executable).resolve().parent)
    except OSError:
        pass
    path_value = os.environ.get("PATH") or os.environ.get("Path") or ""
    for value in path_value.split(os.pathsep):
        if value:
            try:
                roots.append(Path(value).expanduser().resolve())
            except OSError:
                continue
    unique: dict[str, Path] = {}
    for root in roots:
        unique.setdefault(os.path.normcase(str(root)), root)
    return tuple(unique.values())


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _dependency_install_requested(command: Sequence[str]) -> bool:
    """Recognize common package-manager mutations before process creation."""

    values = [str(item).strip().lower() for item in command]
    if not values:
        return False
    executable = Path(values[0]).stem
    if executable in {"pip", "pip3", "pipx"}:
        return any(value in {"install", "download", "wheel"} for value in values[1:])
    if executable in {"npm", "pnpm", "yarn", "bun", "gem"}:
        return any(value in {"install", "i", "add", "update", "upgrade", "fetch"} for value in values[1:])
    if executable == "go":
        return len(values) > 1 and values[1] == "mod" and any(
            value in {"download", "tidy", "vendor"} for value in values[2:]
        )
    if executable == "cargo":
        return len(values) > 1 and values[1] in {"fetch", "update"}
    if executable in {"python", "python3", "py", "pwsh", "powershell"}:
        command_text = " ".join(values)
        return bool(
            re.search(
                r"(?:-m\s+pip\s+(?:install|download|wheel)|"
                r"install-module|invoke-(?:webrequest|restmethod)|downloadfile|"
                r"start-bitstransfer)",
                command_text,
            )
        )
    return False


@dataclass(frozen=True)
class ExecutionPolicy:
    """Boundaries applied to every compiler/tool subprocess.

    The policy is intentionally restrictive by default: it inherits only a
    small, toolchain-relevant environment, caps runtime/output, and does not
    authorize network access or dependency installation.  Network/install
    permissions are checked by dependency-prefetch callers; this class does
    not pretend to sandbox arbitrary compiler behavior.
    """

    timeout_seconds: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    allow_network: bool = False
    allow_dependency_install: bool = False
    inherited_environment: tuple[str, ...] = DEFAULT_INHERITED_ENVIRONMENT
    allowed_executable_roots: tuple[Path, ...] = field(
        default_factory=_default_executable_roots
    )

    def __post_init__(self) -> None:
        timeout = float(self.timeout_seconds)
        output_limit = int(self.max_output_bytes)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Execution timeout must be a finite positive number")
        if output_limit <= 0:
            raise ValueError("Maximum command output must be positive")
        if self.allow_dependency_install and not self.allow_network:
            raise ValueError(
                "Dependency installation requires explicit network permission"
            )
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "max_output_bytes", output_limit)
        object.__setattr__(
            self,
            "inherited_environment",
            tuple(str(key).upper() for key in self.inherited_environment),
        )
        object.__setattr__(
            self,
            "allowed_executable_roots",
            tuple(Path(root).expanduser() for root in self.allowed_executable_roots),
        )

    def for_request(self, request: BuildRequest) -> ExecutionPolicy:
        """Return this policy with request-scoped limits and permissions."""

        return replace(
            self,
            timeout_seconds=(
                self.timeout_seconds
                if request.timeout_seconds is None
                else request.timeout_seconds
            ),
            max_output_bytes=(
                self.max_output_bytes
                if request.max_output_bytes is None
                else request.max_output_bytes
            ),
            allow_network=self.allow_network or request.allow_network,
            allow_dependency_install=(
                self.allow_dependency_install or request.allow_dependency_install
            ),
        )

    def environment(self, overrides: Mapping[str, str] | None = None) -> dict[str, str]:
        """Build a minimal inherited environment plus explicit overrides."""

        allowed = set(self.inherited_environment)
        result = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in allowed
        }
        for key, value in (overrides or {}).items():
            normalized_key = str(key)
            normalized_value = str(value)
            if "\x00" in normalized_key or "\x00" in normalized_value:
                raise BuildValidationError("Environment values cannot contain NUL bytes")
            result[normalized_key] = normalized_value
        return result

    def validate_command(
        self,
        command: Sequence[str],
        cwd: os.PathLike[str] | str | None = None,
    ) -> tuple[str, ...]:
        """Validate argv/cwd and return the normalized command tuple."""

        normalized = tuple(str(item) for item in command)
        if not normalized:
            raise BuildValidationError("Cannot execute an empty command")
        if any("\x00" in item for item in normalized):
            raise BuildValidationError("Command arguments cannot contain NUL bytes")
        executable = Path(normalized[0]).expanduser()
        if not executable.is_absolute():
            resolved = shutil.which(normalized[0])
            if not resolved:
                raise BuildValidationError(f"Executable not found: {normalized[0]}")
            executable = Path(resolved)
        if self.allowed_executable_roots and not any(
            _path_is_within(executable, root)
            for root in self.allowed_executable_roots
        ):
            roots = ", ".join(str(root) for root in self.allowed_executable_roots)
            raise BuildValidationError(
                f"Executable is outside the execution policy roots: {executable} ({roots})"
            )
        if cwd is not None and not Path(cwd).is_dir():
            raise BuildValidationError(f"Working directory not found: {cwd}")
        return normalized


def redact_command(command: Sequence[str]) -> tuple[str, ...]:
    """Mask values following common secret-bearing command options."""

    redacted: list[str] = []
    mask_next = False
    for raw_value in command:
        value = str(raw_value)
        option = value.split("=", 1)[0].lower()
        if mask_next:
            redacted.append("[REDACTED]")
            mask_next = False
        elif option in _SENSITIVE_ARGUMENT_NAMES:
            if "=" in value:
                redacted.append(f"{option}=[REDACTED]")
            else:
                redacted.append(value)
                mask_next = True
        else:
            redacted.append(value)
    return tuple(redacted)


def redact_text(value: str) -> str:
    """Mask simple key/value secrets in captured diagnostics."""

    return _SENSITIVE_TEXT_PATTERN.sub(r"\1[REDACTED]", value)


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
    toolchain_versions: Mapping[str, str] = field(default_factory=dict)
    upx: bool = False
    timeout_seconds: float | None = None
    max_output_bytes: int | None = None
    allow_network: bool = False
    allow_dependency_install: bool = False

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
            toolchain_versions={
                str(key): str(value)
                for key, value in dict(self.toolchain_versions).items()
            },
            upx=bool(self.upx),
            timeout_seconds=(
                float(self.timeout_seconds)
                if self.timeout_seconds is not None
                else None
            ),
            max_output_bytes=(
                int(self.max_output_bytes)
                if self.max_output_bytes is not None
                else None
            ),
            allow_network=bool(self.allow_network),
            allow_dependency_install=bool(self.allow_dependency_install),
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
    cancelled: bool = False
    output_truncated: bool = False

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled

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
    manifest: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schema_version"] = RESULT_SCHEMA_VERSION
        result["request"]["source"] = str(self.request.source)
        result["request"]["schema_version"] = REQUEST_SCHEMA_VERSION
        result["request"]["output"] = str(self.request.output)
        result["request"]["icon"] = (
            str(self.request.icon) if self.request.icon else None
        )
        result["request"]["metadata"] = dict(self.request.metadata)
        result["request"]["extra_args"] = list(redact_command(self.request.extra_args))
        result["output"] = str(self.output)
        result["manifest"] = str(self.manifest) if self.manifest else None
        result["message"] = redact_text(str(result.get("message", "")))
        result["commands"] = [
            {
                **asdict(command),
                "command": list(redact_command(command.command)),
                "stdout": redact_text(command.stdout),
                "stderr": redact_text(command.stderr),
            }
            for command in self.commands
        ]
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
        "wat": (100 * 1024, 1.2),
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
            if isinstance(value, Mapping):
                if value:
                    lines.append(f"  {key}:")
                    for nested_key, nested_value in value.items():
                        lines.append(f"    {nested_key}: {_yaml_scalar(nested_value)}")
                else:
                    lines.append(f"  {key}: {{}}")
            else:
                lines.append(f"  {key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


def _parse_yaml_scalar(value: str) -> Any:
    value = value.strip()
    if value == "{}":
        return {}
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
    nested: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent >= 4:
            if nested is None or ":" not in raw_line:
                continue
            key, value = raw_line.strip().split(":", 1)
            nested[key.strip()] = _parse_yaml_scalar(value)
            continue
        if indent == 2:
            if current is None or ":" not in raw_line:
                continue
            key, value = raw_line.strip().split(":", 1)
            if value.strip():
                current[key.strip()] = _parse_yaml_scalar(value)
                nested = None
            else:
                nested = {}
                current[key.strip()] = nested
            continue
        if ":" not in raw_line:
            continue
        name, value = raw_line.split(":", 1)
        current_name = str(_parse_yaml_scalar(name.strip()))
        current = {}
        result[current_name] = current
        nested = None
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
        import yaml  # type: ignore[import-not-found, import-untyped]

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


def command_display(command: Sequence[str], redact: bool = False) -> str:
    """Return a copy/paste-friendly display form without using a shell."""

    values = redact_command(command) if redact else tuple(str(item) for item in command)
    if os.name == "nt":
        return subprocess.list2cmdline(list(values))
    return shlex.join(list(values))


def _capture_output(
    stream: Any,
    limit: int,
    buffer: bytearray,
    truncated: list[bool],
    budget: list[int],
    budget_lock: threading.Lock,
) -> None:
    """Drain one process stream without allowing unbounded memory growth."""

    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return
            with budget_lock:
                remaining = max(limit - budget[0], 0)
                if remaining > 0:
                    accepted = chunk[:remaining]
                    buffer.extend(accepted)
                    budget[0] += len(accepted)
                if len(chunk) > remaining:
                    truncated[0] = True
    except (OSError, ValueError):
        return


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate a process and its Windows descendants without a shell."""

    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill:
            try:
                subprocess.run(
                    (taskkill, "/PID", str(process.pid), "/T", "/F"),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
    try:
        process.kill()
    except OSError:
        pass


def run_command(
    command: Sequence[str],
    cwd: os.PathLike[str] | str | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: float | None = None,
    policy: ExecutionPolicy | None = None,
    stop_event: threading.Event | None = None,
) -> CommandResult:
    """Run one bounded executable directly, with no shell or console window."""

    effective_policy = policy or ExecutionPolicy()
    normalized = effective_policy.validate_command(command, cwd)
    if _dependency_install_requested(normalized) and not (
        effective_policy.allow_network and effective_policy.allow_dependency_install
    ):
        raise BuildValidationError(
            "Dependency installation requires both network and install permission"
        )
    effective_timeout = effective_policy.timeout_seconds
    if timeout is not None:
        effective_timeout = min(float(timeout), effective_timeout)
    if not math.isfinite(effective_timeout) or effective_timeout <= 0:
        raise BuildValidationError("Execution timeout must be a finite positive number")
    started = time.monotonic()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    env = effective_policy.environment(environment)
    try:
        process = subprocess.Popen(
            normalized,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=creationflags,
        )
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        stdout_truncated = [False]
        stderr_truncated = [False]
        output_budget = [0]
        output_budget_lock = threading.Lock()
        readers = [
            threading.Thread(
                target=_capture_output,
                args=(
                    process.stdout,
                    effective_policy.max_output_bytes,
                    stdout_buffer,
                    stdout_truncated,
                    output_budget,
                    output_budget_lock,
                ),
                daemon=True,
            ),
            threading.Thread(
                target=_capture_output,
                args=(
                    process.stderr,
                    effective_policy.max_output_bytes,
                    stderr_buffer,
                    stderr_truncated,
                    output_budget,
                    output_budget_lock,
                ),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        cancelled = False
        deadline = started + effective_timeout
        while process.poll() is None:
            if stop_event is not None and stop_event.is_set():
                cancelled = True
                _terminate_process_tree(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _terminate_process_tree(process)
                break
            time.sleep(0.05)
        returncode = process.wait()
        for reader in readers:
            reader.join(timeout=5)
        stdout = bytes(stdout_buffer).decode("utf-8", errors="replace")
        stderr = bytes(stderr_buffer).decode("utf-8", errors="replace")
        output_truncated = stdout_truncated[0] or stderr_truncated[0]
        if output_truncated:
            stderr = f"{stderr}\n[output truncated by execution policy]".strip()
        if timed_out:
            returncode = 124
        elif cancelled:
            returncode = 130
        return CommandResult(
            command=normalized,
            returncode=returncode,
            stdout=redact_text(stdout),
            stderr=redact_text(stderr),
            duration_seconds=time.monotonic() - started,
            timed_out=timed_out,
            cancelled=cancelled,
            output_truncated=output_truncated,
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
        "upx": ("upx",),
        "wat2wasm": ("wat2wasm",),
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
        csc_candidates = (
            Path(windir) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
            Path(windir) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
        )
        return next((str(path) for path in csc_candidates if path.exists()), None)
    if backend == "nuitka":
        return sys.executable if importlib.util.find_spec("nuitka") else None
    names = direct.get(backend)
    return _find_first(names) if names else None


def resolve_obfuscator(method: str) -> str | None:
    """Resolve an optional obfuscator without installing or modifying source."""

    normalized = method.lower()
    if normalized == "pyarmor":
        return _find_first(("pyarmor",))
    if normalized == "javascript-obfuscator":
        return _find_first(("javascript-obfuscator", "js-obfuscator"))
    if normalized == "confuserex":
        direct = _find_first(("Confuser.CLI.exe", "Confuser.CLI"))
        if direct:
            return direct
        for root in _windows_path_candidates("ConfuserEx"):
            candidate = root / "Confuser.CLI.exe"
            if candidate.exists():
                return str(candidate)
    return None


def backend_status() -> dict[str, dict[str, Any]]:
    """Return deterministic, read-only availability information for every backend."""

    status: dict[str, dict[str, Any]] = {}
    for backend, name in BACKEND_NAMES.items():
        path = resolve_backend_executable(backend)
        status[backend] = {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "backend": backend,
            "name": name,
            "available": path is not None,
            "executable": path,
            "extensions": [
                ext for ext, values in EXTENSION_BACKENDS.items() if backend in values
            ],
        }
    return status


def _target_for(backend: str, architecture: str, platform: str = "native") -> str:
    arch = architecture.lower()
    platform = platform.lower()
    if platform in {"native", "auto"}:
        platform = "windows"
    if arch in {"native", "auto"} and platform == "windows":
        return "native"
    if arch in {"native", "auto"}:
        arch = "x64"
    if backend in {"pkg", "bun"}:
        suffix = {"x64": "x64", "amd64": "x64", "x86": "x86", "arm64": "arm64"}.get(
            arch, arch
        )
        if backend == "pkg":
            return f"node18-{platform[:3]}-{suffix}"
        return f"bun-{platform}-{suffix}"
    if backend == "deno":
        prefix = {
            "windows": "pc-windows-msvc",
            "linux": "unknown-linux-gnu",
            "darwin": "apple-darwin",
        }.get(platform, platform)
        architecture_name = {
            "x86": "i686",
            "x64": "x86_64",
            "amd64": "x86_64",
            "arm64": "aarch64",
        }.get(arch, arch)
        return {
            "native": "native",
        }.get(arch, f"{architecture_name}-{prefix}")
    if backend == "rust":
        if "-" in platform:
            return platform
        suffix = {
            "windows": "pc-windows-msvc",
            "linux": "unknown-linux-gnu",
            "darwin": "apple-darwin",
        }.get(platform, platform)
        architecture_name = {
            "x86": "i686",
            "x64": "x86_64",
            "amd64": "x86_64",
            "arm64": "aarch64",
        }.get(arch, arch)
        return {
            "native": "native",
        }.get(arch, f"{architecture_name}-{suffix}")
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
        policy: ExecutionPolicy | None = None,
    ) -> None:
        self.runner = runner
        self.require_available = require_available
        self.policy = policy or ExecutionPolicy()

    def _policy_for_request(self, request: BuildRequest) -> ExecutionPolicy:
        return self.policy.for_request(request)

    def _run(
        self,
        request: BuildRequest,
        command: Sequence[str],
        cwd: os.PathLike[str] | str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Run a compiler command through the configured execution policy."""

        policy = self._policy_for_request(request)
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "environment": environment,
            "timeout": policy.timeout_seconds,
        }
        if self.runner is run_command:
            kwargs["policy"] = policy
        return self.runner(command, **kwargs)

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

    def _version_command(self, backend: str) -> tuple[str, ...] | None:
        executable = resolve_backend_executable(backend)
        if not executable:
            return None
        if backend == "nuitka":
            return (sys.executable, "-m", "nuitka", "--version")
        if backend == "ps2exe":
            return (
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-Module -ListAvailable ps2exe | Sort-Object Version -Descending | Select-Object -First 1 -ExpandProperty Version)",
            )
        if backend == "csc":
            return (executable, "/version")
        return (executable, "--version")

    def _validate_toolchain_versions(self, request: BuildRequest) -> None:
        for backend, expected in request.toolchain_versions.items():
            command = self._version_command(backend)
            if not command:
                raise BuildValidationError(
                    f"Pinned toolchain is not installed: {backend} ({expected})"
                )
            result = self._run(request, command)
            if not result.success or str(expected) not in result.output:
                actual = result.output.splitlines()[0] if result.output else "unknown"
                raise BuildValidationError(
                    f"Toolchain pin mismatch for {backend}: expected {expected}, got {actual}"
                )

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
        if not allow_missing_source and normalized.toolchain_versions:
            self._validate_toolchain_versions(normalized)
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
                "toolchain_versions": dict(request.toolchain_versions),
                "upx": request.upx,
                "tool": self._tool_identity(backend),
            }
        )

    def _cache_hit(self, request: BuildRequest, key: str) -> bool:
        if request.force or not request.cache or not request.output.is_file():
            return False
        saved = load_json(self._cache_path(request.output), {})
        if not isinstance(saved, Mapping) or saved.get("key") != key:
            return False
        manifest = artifact_manifest_path(request.output)
        return not (
            request.verify
            and manifest.is_file()
            and not verify_artifact_manifest(manifest, request.output).passed
        )

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

    def _write_artifact_manifest(
        self,
        request: BuildRequest,
        plan: BuildPlan,
        source_hash: str,
        cache_key: str,
        verification: VerificationResult | None,
    ) -> Path:
        """Atomically emit the identity and verification record for a build."""

        output = request.output.resolve()
        stat = output.stat()
        warnings: list[str] = []
        if not request.verify:
            warnings.append("Static artifact verification was disabled")
        policy = self._policy_for_request(request)
        manifest = {
            "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "source": {
                "path": str(request.source.resolve()),
                "sha256": source_hash,
                "file_type": request.file_type,
            },
            "request": {
                "schema_version": REQUEST_SCHEMA_VERSION,
                "backend": plan.backend,
                "target": request.target,
                "architecture": request.architecture,
                "console": request.console,
                "admin": request.admin,
                "single_file": request.single_file,
                "profile": request.profile_name,
                "icon": str(request.icon.resolve()) if request.icon else None,
                "metadata": {
                    str(key): redact_text(str(value))
                    for key, value in request.metadata.items()
                },
                "extra_args": list(redact_command(request.extra_args)),
                "toolchain_versions": dict(request.toolchain_versions),
                "upx": request.upx,
            },
            "toolchain": {
                "backend": self._tool_identity(plan.backend),
                "pins": dict(request.toolchain_versions),
            },
            "policy": {
                "timeout_seconds": policy.timeout_seconds,
                "max_output_bytes": policy.max_output_bytes,
                "allow_network": request.allow_network,
                "allow_dependency_install": request.allow_dependency_install,
            },
            "artifact": {
                "path": str(output),
                "output_type": verification.kind
                if verification
                else output.suffix.lower().lstrip(".") or "file",
                "sha256": sha256_file(output),
                "size_bytes": stat.st_size,
            },
            "verification": (
                {
                    "passed": verification.passed,
                    "kind": verification.kind,
                    "details": redact_text(verification.details),
                }
                if verification
                else {"passed": False, "kind": "disabled", "details": "Not requested"}
            ),
            "signature": {
                "status": "unsigned",
                "signed": False,
                "signing_implemented": False,
            },
            "warnings": warnings,
            "result": {"schema_version": RESULT_SCHEMA_VERSION, "status": "built"},
            "cache_key": cache_key,
        }
        destination = artifact_manifest_path(output)
        save_json(destination, manifest)
        return destination

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
        target = _target_for(backend, request.architecture, request.target)
        command: tuple[str, ...]

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
            pyinstaller_command_parts = [
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
                pyinstaller_command_parts.append("--onefile")
            if not request.console:
                pyinstaller_command_parts.append("--noconsole")
            if request.icon:
                pyinstaller_command_parts.extend(("--icon", str(request.icon)))
            if request.metadata:
                version_file = work / "version_info.txt"
                if not allow_missing_source:
                    _write_pyinstaller_version_file(version_file, request.metadata)
                pyinstaller_command_parts.extend(("--version-file", str(version_file)))
            pyinstaller_command_parts.extend((str(source), *request.extra_args))
            command = tuple(pyinstaller_command_parts)
        elif backend == "nuitka":
            work = output.parent / ".uc-build" / output.stem
            if not allow_missing_source:
                work.mkdir(parents=True, exist_ok=True)
            cleanup.append(work)
            nuitka_command_parts = [
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
                nuitka_command_parts.append("--windows-uac-admin")
            if request.icon:
                nuitka_command_parts.append(f"--windows-icon-from-ico={request.icon}")
            if _metadata(request, "product"):
                nuitka_command_parts.append(f"--product-name={_metadata(request, 'product')}")
            if _metadata(request, "company"):
                nuitka_command_parts.append(f"--company-name={_metadata(request, 'company')}")
            if _metadata(request, "version"):
                nuitka_command_parts.extend(
                    (
                        f"--file-version={_metadata(request, 'version')}",
                        f"--product-version={_metadata(request, 'version')}",
                    )
                )
            if _metadata(request, "description"):
                nuitka_command_parts.append(
                    f"--file-description={_metadata(request, 'description')}"
                )
            if _metadata(request, "copyright"):
                nuitka_command_parts.append(f"--copyright={_metadata(request, 'copyright')}")
            nuitka_command_parts.extend((str(source), *request.extra_args))
            command = tuple(nuitka_command_parts)
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
            goos = (
                request.target
                if request.target not in {"native", "auto"}
                else "windows"
            )
            environment = {"GOOS": goos}
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
                cargo_args = [cargo, "build", "--release"]
                if target != "native":
                    cargo_args.extend(("--target", target))
                command = tuple(cargo_args + list(request.extra_args))
                candidate_dir = cargo_file.parent / "target"
                if target != "native":
                    candidate_dir /= target
                candidate_dir /= "release"
                candidates.append(candidate_dir / f"{package}.exe")
            else:
                rustc = _find_first(("rustc",)) or executable
                rustc_args = [rustc, "--edition", "2021", "-O"]
                if target != "native":
                    rustc_args.extend(("--target", target))
                command = tuple(
                    rustc_args + ["-o", str(output), *request.extra_args, str(source)]
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
        elif backend == "wat2wasm":
            command = (executable, str(source), "-o", str(output), *request.extra_args)
        elif backend == "ahk2exe":
            ahk_command_parts = [executable, "/in", str(source), "/out", str(output)]
            if request.icon:
                ahk_command_parts.extend(("/icon", str(request.icon)))
            command = tuple(ahk_command_parts + list(request.extra_args))
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

        if not request.allow_network or not request.allow_dependency_install:
            raise BuildValidationError(
                "Dependency prefetch requires both --allow-network and "
                "--allow-dependency-install"
            )

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
        return [self._run(request, command, cwd=cwd) for command, cwd in commands]

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
                    manifest_path = artifact_manifest_path(normalized.output.resolve())
                    if not manifest_path.is_file():
                        manifest_path = self._write_artifact_manifest(
                            normalized,
                            plan,
                            source_hash,
                            cache_key,
                            verification,
                        )
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
                        manifest=manifest_path,
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
            result = self._run(
                normalized,
                plan.command,
                cwd=plan.cwd,
                environment=plan.environment,
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
            if normalized.upx:
                upx = resolve_backend_executable("upx")
                if not upx:
                    return BuildResult(
                        False,
                        "failed",
                        normalized,
                        normalized.output,
                        plan.backend,
                        commands=commands,
                        source_hash=source_hash,
                        cache_key=cache_key,
                        message="UPX was requested but is not installed",
                        duration_seconds=time.monotonic() - started,
                    )
                compression = self._run(
                    normalized,
                    (upx, "--best", "--lzma", str(normalized.output)),
                    cwd=normalized.output.parent,
                )
                commands.append(compression)
                if not compression.success:
                    return BuildResult(
                        False,
                        "failed",
                        normalized,
                        normalized.output,
                        plan.backend,
                        commands=commands,
                        source_hash=source_hash,
                        cache_key=cache_key,
                        message=f"UPX compression failed: {compression.output}",
                        duration_seconds=time.monotonic() - started,
                    )
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
            manifest_path = self._write_artifact_manifest(
                normalized,
                plan,
                source_hash,
                cache_key,
                verification,
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
                manifest=manifest_path,
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

    def build_matrix(
        self,
        request: BuildRequest,
        architectures: Sequence[str],
        workers: int = 1,
    ) -> list[BuildResult]:
        """Build one source for each requested architecture."""

        values: list[str] = []
        for architecture in architectures:
            normalized = str(architecture).lower()
            if normalized and normalized not in values:
                values.append(normalized)
        if not values:
            raise BuildValidationError("At least one matrix architecture is required")
        requests: list[BuildRequest] = []
        for architecture in values:
            suffix = f"-{architecture}"
            output = request.output.with_name(
                f"{request.output.stem}{suffix}{request.output.suffix}"
            )
            requests.append(replace(request, output=output, architecture=architecture))
        return self.build_batch(requests, workers=workers)

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


def artifact_manifest_path(path: os.PathLike[str] | str) -> Path:
    """Return the sidecar manifest path for an artifact."""

    artifact = Path(path).expanduser()
    return artifact.with_name(f"{artifact.name}.manifest.json")


def _verify_pe_structure(artifact: Path, header: bytes) -> VerificationResult:
    size = artifact.stat().st_size
    if len(header) < 64 or header[:2] != b"MZ":
        return VerificationResult(False, "pe", "Executable does not have an MZ header")
    pe_offset = int.from_bytes(header[0x3C:0x40], "little")
    if pe_offset < 64 or pe_offset + 24 > size:
        return VerificationResult(False, "pe", "PE header offset is outside the artifact")
    try:
        with artifact.open("rb") as handle:
            handle.seek(pe_offset)
            signature = handle.read(4)
            coff = handle.read(20)
            if signature != b"PE\0\0" or len(coff) != 20:
                return VerificationResult(False, "pe", "Executable does not have a valid PE signature")
            machine, sections, _, _, _, optional_size, _ = struct.unpack(
                "<HHIIIHH", coff
            )
            if machine == 0 or sections < 1 or sections > 96:
                return VerificationResult(False, "pe", "PE COFF header has invalid machine or section count")
            if optional_size < 64 or optional_size > 4096:
                return VerificationResult(False, "pe", "PE optional header size is invalid")
            optional = handle.read(optional_size)
            if len(optional) != optional_size or struct.unpack_from("<H", optional)[0] not in {0x10B, 0x20B}:
                return VerificationResult(False, "pe", "PE optional header is invalid")
            section_table_end = pe_offset + 4 + 20 + optional_size + sections * 40
            if section_table_end > size:
                return VerificationResult(False, "pe", "PE section table exceeds artifact size")
            section_table = handle.read(sections * 40)
            for index in range(sections):
                section = section_table[index * 40 : (index + 1) * 40]
                raw_size, raw_offset = struct.unpack_from("<II", section, 16)
                if raw_size and (raw_offset < section_table_end or raw_offset + raw_size > size):
                    return VerificationResult(False, "pe", f"PE section {index + 1} exceeds artifact bounds")
    except (OSError, struct.error) as error:
        return VerificationResult(False, "pe", f"Could not read PE structure: {error}")
    return VerificationResult(True, "pe", f"Valid PE artifact ({format_size(size)})")


def _verify_zip_structure(artifact: Path) -> VerificationResult:
    try:
        with zipfile.ZipFile(artifact) as archive:
            names = archive.namelist()
            if not names or archive.testzip() is not None:
                return VerificationResult(False, "zip", "Archive is empty or contains a corrupt member")
            if any(
                name.startswith(("/", "\\")) or ".." in Path(name).parts
                for name in names
            ):
                return VerificationResult(False, "zip", "Archive contains an unsafe member path")
            suffix = artifact.suffix.lower()
            if suffix in {".msix", ".appx"}:
                required = {"AppxManifest.xml", "App.exe"}
                if not required.issubset(names):
                    return VerificationResult(False, "msix", "MSIX/APPX is missing its manifest or application")
                root = ET.fromstring(archive.read("AppxManifest.xml"))
                if not root.tag.endswith("Package") or not any(
                    element.tag.endswith("Identity") for element in root.iter()
                ):
                    return VerificationResult(False, "msix", "MSIX/APPX manifest has no Package/Identity structure")
                return VerificationResult(True, "msix", "Valid unsigned MSIX/APPX structure")
    except (OSError, zipfile.BadZipFile, ET.ParseError) as error:
        return VerificationResult(False, "zip", f"Invalid archive structure: {error}")
    return VerificationResult(True, "zip", "Valid ZIP/JAR archive structure")


def _read_wasm_unsigned_leb(handle: Any, remaining: int) -> tuple[int, int] | None:
    value = 0
    shift = 0
    for count in range(5):
        byte = handle.read(1)
        if not byte:
            return None
        remaining -= 1
        value |= (byte[0] & 0x7F) << shift
        if not byte[0] & 0x80:
            return value, count + 1
        shift += 7
    return None


def _verify_wasm_structure(artifact: Path) -> VerificationResult:
    try:
        size = artifact.stat().st_size
        with artifact.open("rb") as handle:
            if handle.read(8) != b"\x00asm\x01\x00\x00\x00":
                return VerificationResult(False, "wasm", "Invalid WebAssembly magic or version")
            consumed = 8
            while consumed < size:
                section_id = handle.read(1)
                if not section_id or section_id[0] > 12:
                    return VerificationResult(False, "wasm", "Invalid WebAssembly section identifier")
                consumed += 1
                length = _read_wasm_unsigned_leb(handle, size - consumed)
                if length is None:
                    return VerificationResult(False, "wasm", "Invalid WebAssembly section length")
                section_size, length_bytes = length
                consumed += length_bytes
                if section_size > size - consumed:
                    return VerificationResult(False, "wasm", "WebAssembly section exceeds artifact bounds")
                handle.seek(section_size, os.SEEK_CUR)
                consumed += section_size
    except OSError as error:
        return VerificationResult(False, "wasm", f"Could not read WebAssembly structure: {error}")
    return VerificationResult(True, "wasm", "Valid WebAssembly module structure")


def _verify_artifact_structure(path: os.PathLike[str] | str) -> VerificationResult:
    artifact = Path(path).expanduser()
    if not artifact.is_file():
        return VerificationResult(False, "missing", f"Artifact not found: {artifact}")
    try:
        with artifact.open("rb") as handle:
            header = handle.read(4096)
    except OSError as error:
        return VerificationResult(False, "unreadable", str(error))
    suffix = artifact.suffix.lower()
    if suffix == ".exe":
        return _verify_pe_structure(artifact, header)
    if suffix in {".jar", ".zip", ".msix", ".appx"}:
        return _verify_zip_structure(artifact)
    if suffix == ".wasm":
        return _verify_wasm_structure(artifact)
    size = artifact.stat().st_size
    return VerificationResult(True, "file", "Non-empty artifact" if size > 0 else "Empty artifact") if size > 0 else VerificationResult(False, "file", "Empty artifact")


def verify_artifact_manifest(
    path: os.PathLike[str] | str,
    artifact: os.PathLike[str] | str | None = None,
) -> VerificationResult:
    """Validate a versioned sidecar manifest and the artifact it identifies."""

    manifest_path = Path(path).expanduser()
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as error:
        return VerificationResult(False, "manifest", f"Could not read artifact manifest: {error}")
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != ARTIFACT_MANIFEST_SCHEMA_VERSION:
        return VerificationResult(False, "manifest", "Unsupported artifact manifest schema")
    for field_name in (
        "source",
        "request",
        "toolchain",
        "artifact",
        "verification",
        "signature",
        "warnings",
    ):
        if field_name not in manifest:
            return VerificationResult(False, "manifest", f"Manifest is missing {field_name}")
    source_record = manifest.get("source")
    request_record = manifest.get("request")
    toolchain_record = manifest.get("toolchain")
    verification_record = manifest.get("verification")
    if (
        not isinstance(source_record, Mapping)
        or not isinstance(request_record, Mapping)
        or not isinstance(toolchain_record, Mapping)
        or not isinstance(verification_record, Mapping)
        or not isinstance(manifest.get("warnings"), list)
    ):
        return VerificationResult(False, "manifest", "Manifest sections have invalid types")
    if not re.fullmatch(r"[0-9a-f]{64}", str(source_record.get("sha256", ""))):
        return VerificationResult(False, "manifest", "Manifest source identity is invalid")
    if request_record.get("schema_version") != REQUEST_SCHEMA_VERSION:
        return VerificationResult(False, "manifest", "Manifest request schema is unsupported")
    artifact_record = manifest.get("artifact")
    if not isinstance(artifact_record, Mapping):
        return VerificationResult(False, "manifest", "Manifest has no artifact record")
    recorded_path = Path(str(artifact_record.get("path", "")))
    if not recorded_path.is_absolute():
        recorded_path = manifest_path.parent / recorded_path
    actual_path = Path(artifact).expanduser() if artifact is not None else recorded_path
    try:
        if recorded_path.resolve() != actual_path.resolve():
            return VerificationResult(False, "manifest", "Manifest artifact path does not match the requested artifact")
    except OSError as error:
        return VerificationResult(False, "manifest", f"Could not resolve manifest artifact path: {error}")
    if not actual_path.is_file():
        return VerificationResult(False, "manifest", "Manifest artifact is missing")
    expected_size = artifact_record.get("size_bytes")
    expected_hash = str(artifact_record.get("sha256", ""))
    if not isinstance(expected_size, int) or expected_size != actual_path.stat().st_size:
        return VerificationResult(False, "manifest", "Manifest artifact size does not match")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or sha256_file(actual_path) != expected_hash:
        return VerificationResult(False, "manifest", "Manifest artifact SHA-256 does not match")
    signature = manifest.get("signature")
    if not isinstance(signature, Mapping) or signature.get("status") != "unsigned" or signature.get("signed") is not False:
        return VerificationResult(False, "manifest", "Manifest does not explicitly declare unsigned status")
    structure = _verify_artifact_structure(actual_path)
    if not structure.passed:
        return VerificationResult(False, "manifest", f"Manifest artifact structure failed: {structure.details}")
    return VerificationResult(True, "manifest", "Artifact manifest, hash, and structure verified")


def verify_artifact(path: os.PathLike[str] | str) -> VerificationResult:
    """Verify artifact structure and any adjacent versioned manifest."""

    artifact = Path(path).expanduser()
    structure = _verify_artifact_structure(artifact)
    if not structure.passed:
        return structure
    manifest = artifact_manifest_path(artifact)
    if manifest.is_file():
        manifest_result = verify_artifact_manifest(manifest, artifact)
        if not manifest_result.passed:
            return VerificationResult(False, structure.kind, manifest_result.details)
        return VerificationResult(True, structure.kind, f"{structure.details}; manifest verified")
    return structure


def compile_bytecode(
    source: os.PathLike[str] | str,
    output: os.PathLike[str] | str | None = None,
) -> Path:
    """Compile a Python source file to an explicit .pyc without packaging it."""

    source_path = Path(source).expanduser()
    if source_path.suffix.lower() != ".py":
        raise BuildValidationError(
            "Bytecode compile-only mode currently supports Python .py sources"
        )
    output_path = (
        Path(output).expanduser() if output else source_path.with_suffix(".pyc")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(
        str(source_path),
        cfile=str(output_path),
        doraise=True,
    )
    return output_path


def extract_icon(
    executable: os.PathLike[str] | str,
    output: os.PathLike[str] | str | None = None,
) -> Path:
    """Extract the associated Windows icon through a hidden .NET call."""

    source_path = Path(executable).expanduser()
    if not source_path.is_file() or source_path.suffix.lower() != ".exe":
        raise BuildValidationError(f"Windows executable not found: {source_path}")
    output_path = (
        Path(output).expanduser() if output else source_path.with_suffix(".ico")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    powershell = _powershell_executable()
    if not powershell:
        raise BuildValidationError("PowerShell is required for icon extraction")
    script = " ".join(
        [
            "$ErrorActionPreference='Stop';",
            "Add-Type -AssemblyName System.Drawing;",
            f"$icon=[System.Drawing.Icon]::ExtractAssociatedIcon({_ps_quote(source_path)});",
            "if($null -eq $icon){throw 'No associated icon found'};",
            f"$stream=[IO.File]::Open({_ps_quote(output_path)},[IO.FileMode]::Create);",
            "try{$icon.Save($stream)}finally{$stream.Dispose();$icon.Dispose()};",
        ]
    )
    result = run_command(
        (powershell, "-NoProfile", "-NonInteractive", "-Command", script)
    )
    if not result.success or not output_path.is_file():
        raise BuildValidationError(result.output or "Icon extraction failed")
    return output_path


def wrap_msix(
    executable: os.PathLike[str] | str,
    output: os.PathLike[str] | str,
    display_name: str = "Universal Compiler Application",
    publisher: str = "CN=UniversalCompiler",
    version: str = "1.0.0.0",
) -> Path:
    """Package an executable as an explicitly unsigned MSIX/APPX archive."""

    from xml.sax.saxutils import escape

    source_path = Path(executable).expanduser()
    if not source_path.is_file() or source_path.suffix.lower() != ".exe":
        raise BuildValidationError(f"Windows executable not found: {source_path}")
    output_path = Path(output).expanduser()
    if output_path.suffix.lower() not in {".msix", ".appx"}:
        raise BuildValidationError("MSIX output must end in .msix or .appx")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    identity = re.sub(r"[^A-Za-z0-9.\-]", "", display_name)[:50] or "UniversalCompiler"
    safe_version = ".".join(str(version).split(".")[:4])
    if len(safe_version.split(".")) != 4:
        safe_version = "1.0.0.0"
    manifest = f"""<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">
  <Identity Name="{escape(identity)}" Publisher="{escape(publisher)}" Version="{escape(safe_version)}" />
  <Properties>
    <DisplayName>{escape(display_name)}</DisplayName>
    <PublisherDisplayName>{escape(publisher)}</PublisherDisplayName>
    <Description>{escape(display_name)}</Description>
    <Logo>Assets/StoreLogo.png</Logo>
  </Properties>
  <Resources>
    <Resource Language="en-us" />
  </Resources>
  <Applications>
    <Application Id="App" Executable="App.exe" EntryPoint="Windows.FullTrustApplication" />
  </Applications>
  <Capabilities>
    <Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"""
    makeappx = _find_first(("makeappx", "makeappx.exe"))
    with tempfile.TemporaryDirectory(prefix="uc-msix-") as directory:
        package_dir = Path(directory)
        shutil.copy2(source_path, package_dir / "App.exe")
        assets = package_dir / "Assets"
        assets.mkdir()
        assets.joinpath("StoreLogo.png").write_bytes(
            bytes.fromhex(
                "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
                "0000000D49444154789C6360F8CF00000003000101F9D5C2A00000000049454E44AE426082"
            )
        )
        (package_dir / "AppxManifest.xml").write_text(manifest, encoding="utf-8")
        if makeappx:
            result = run_command(
                (
                    makeappx,
                    "pack",
                    "/d",
                    str(package_dir),
                    "/p",
                    str(output_path),
                    "/o",
                )
            )
            if not result.success or not output_path.is_file():
                raise BuildValidationError(result.output or "MSIX packaging failed")
        else:
            with zipfile.ZipFile(
                output_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.write(package_dir / "App.exe", "App.exe")
                archive.write(package_dir / "AppxManifest.xml", "AppxManifest.xml")
                archive.write(assets / "StoreLogo.png", "Assets/StoreLogo.png")
    verification = _verify_artifact_structure(output_path)
    if not verification.passed:
        raise BuildValidationError(f"Unsigned MSIX/APPX validation failed: {verification.details}")
    return output_path


def obfuscate_source(
    source: os.PathLike[str] | str,
    method: str,
    output: os.PathLike[str] | str,
) -> Path:
    """Run an opt-in obfuscator into a separate destination."""

    source_path = Path(source).expanduser()
    output_path = Path(output).expanduser()
    normalized = method.lower()
    if not source_path.is_file():
        raise BuildValidationError(f"Source file not found: {source_path}")
    executable = resolve_obfuscator(normalized)
    if not executable:
        raise BuildValidationError(f"Obfuscator is not installed: {normalized}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command: tuple[str, ...]
    if normalized == "pyarmor":
        output_path.mkdir(parents=True, exist_ok=True)
        command = (executable, "gen", "--output", str(output_path), str(source_path))
        expected = output_path / source_path.name
    elif normalized == "javascript-obfuscator":
        command = (executable, str(source_path), "--output", str(output_path))
        expected = output_path
    elif normalized == "confuserex":
        if source_path.suffix.lower() != ".crproj":
            raise BuildValidationError(
                "ConfuserEx requires a .crproj project file as its source"
            )
        command = (executable, str(source_path))
        expected = output_path
    else:
        raise BuildValidationError(f"Unsupported obfuscator: {method}")
    result = run_command(command, cwd=source_path.parent)
    if not result.success:
        raise BuildValidationError(result.output or "Obfuscation failed")
    if not expected.exists():
        raise BuildValidationError(f"Obfuscator did not create: {expected}")
    return expected


class BuildAnalytics:
    """Local-only build timing and size history backed by SQLite."""

    def __init__(self, path: os.PathLike[str] | str | None = None) -> None:
        self.path = (
            Path(path).expanduser() if path else config_dir() / "analytics.sqlite3"
        )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS builds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                output TEXT NOT NULL,
                file_type TEXT,
                backend TEXT,
                profile TEXT,
                success INTEGER NOT NULL,
                status TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                duration_seconds REAL NOT NULL
            )
            """
        )
        connection.commit()
        return connection

    def record(self, result: BuildResult) -> None:
        size = 0
        try:
            size = result.output.stat().st_size
        except OSError:
            pass
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO builds (
                    timestamp, source, output, file_type, backend, profile,
                    success, status, size_bytes, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    str(result.request.source),
                    str(result.output),
                    result.request.file_type,
                    result.backend,
                    result.request.profile_name,
                    int(result.success),
                    result.status,
                    size,
                    result.duration_seconds,
                ),
            )

    def summary(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(success), 0),
                       COALESCE(SUM(size_bytes), 0),
                       COALESCE(AVG(duration_seconds), 0)
                FROM builds
                """
            ).fetchone()
            by_backend = connection.execute(
                """
                SELECT COALESCE(backend, 'unknown'), COUNT(*), SUM(success)
                FROM builds GROUP BY backend ORDER BY backend
                """
            ).fetchall()
        total, successful, bytes_built, average_seconds = row or (0, 0, 0, 0)
        return {
            "database": str(self.path),
            "total_builds": int(total),
            "successful_builds": int(successful),
            "bytes_built": int(bytes_built),
            "average_duration_seconds": float(average_seconds),
            "by_backend": {
                str(backend): {"builds": int(count), "successes": int(successes)}
                for backend, count, successes in by_backend
            },
        }

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT timestamp, source, output, backend, profile, success,
                       status, size_bytes, duration_seconds
                FROM builds ORDER BY id DESC LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        keys = (
            "timestamp",
            "source",
            "output",
            "backend",
            "profile",
            "success",
            "status",
            "size_bytes",
            "duration_seconds",
        )
        return [dict(zip(keys, row)) for row in rows]


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


def parse_toolchain_versions(values: Sequence[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise BuildValidationError(
                f"Toolchain pins must use backend=version: {value}"
            )
        backend, version = value.split("=", 1)
        backend = backend.strip().lower()
        if not backend or not version.strip():
            raise BuildValidationError(f"Toolchain pin is incomplete: {value}")
        versions[backend] = version.strip()
    return versions


def github_actions_template(file_type: str) -> str:
    """Return a minimal workflow that invokes the repository's CLI."""

    extension = file_type.lower().lstrip(".")
    if extension not in EXTENSION_BACKENDS:
        raise BuildValidationError(f"No GitHub Actions template for: {file_type}")
    setup: list[str]
    if extension == "py":
        setup = [
            "      - uses: actions/setup-python@v5",
            "        with:",
            "          python-version: '3.12'",
            "      - run: python -m pip install pyinstaller",
        ]
    elif extension in {"js", "ts"}:
        setup = [
            "      - uses: oven-sh/setup-bun@v2",
            "      - run: bun install --frozen-lockfile",
        ]
    elif extension == "go":
        setup = [
            "      - uses: actions/setup-go@v5",
            "        with:",
            "          go-version: stable",
        ]
    elif extension == "rs":
        setup = [
            "      - uses: actions-rust-lang/setup-rust-toolchain@v1",
            "        with:",
            "          toolchain: stable",
        ]
    else:
        setup = []
    setup_text = "\n".join(setup)
    return f"""name: Universal Compiler ({extension})

on:
  workflow_dispatch:
  push:
    paths:
      - '**/*.{extension}'
      - 'UniversalCompiler.py'
      - 'compiler_core.py'

jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
{setup_text}
      - name: Build source
        shell: pwsh
        run: python .\\UniversalCompiler.py build $env:UC_SOURCE --output dist\\artifact.exe --verify --no-analytics
        env:
          UC_SOURCE: ${{{{ vars.UC_SOURCE || 'src/main.{extension}' }}}}
      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: universal-compiler-{extension}
          path: dist/*.exe
"""


def _profile_request(
    profile: Mapping[str, Any], args: argparse.Namespace, source: Path, output: Path
) -> BuildRequest:
    metadata = {
        key: str(profile.get(key, ""))
        for key in ("product", "version", "company", "copyright", "description")
        if profile.get(key, "") != ""
    }
    metadata.update(parse_metadata(args.metadata or []))
    toolchain_versions = {
        str(key): str(value)
        for key, value in dict(profile.get("toolchain_versions") or {}).items()
    }
    toolchain_versions.update(parse_toolchain_versions(args.tool_version or []))
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
        verify=(
            True
            if args.verify_flag
            else not args.no_verify
            if args.no_verify
            else bool(profile.get("verify", True))
        ),
        cache=not args.no_cache if args.no_cache else bool(profile.get("cache", True)),
        force=args.force,
        extra_args=tuple(args.extra_arg or []),
        toolchain_versions=toolchain_versions,
        upx=args.upx or bool(profile.get("upx", False)),
        timeout_seconds=(
            args.timeout_seconds
            if args.timeout_seconds is not None
            else profile.get("timeout_seconds")
        ),
        max_output_bytes=(
            args.max_output_bytes
            if args.max_output_bytes is not None
            else profile.get("max_output_bytes")
        ),
        allow_network=args.allow_network or bool(profile.get("allow_network", False)),
        allow_dependency_install=(
            args.allow_dependency_install
            or bool(profile.get("allow_dependency_install", False))
        ),
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
        "--timeout",
        dest="timeout_seconds",
        type=float,
        metavar="SECONDS",
        help=f"Maximum time per external command (default: {DEFAULT_EXECUTION_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument(
        "--max-output-bytes",
        type=int,
        help=f"Maximum captured stdout/stderr per command (default: {DEFAULT_MAX_OUTPUT_BYTES})",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Allow network-capable dependency prefetch when explicitly requested",
    )
    parser.add_argument(
        "--allow-dependency-install",
        action="store_true",
        help="Allow dependency installation during --prefetch",
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
    parser.add_argument(
        "--tool-version",
        action="append",
        default=[],
        metavar="BACKEND=VERSION",
        help="Require a toolchain version (repeatable)",
    )
    parser.add_argument(
        "--upx", action="store_true", help="Compress the output with UPX"
    )
    parser.add_argument(
        "--matrix",
        nargs="+",
        metavar="ARCH",
        help="Build a matrix for x86, x64, and/or arm64",
    )
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--no-analytics", action="store_true")


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

    actions_parser = subparsers.add_parser(
        "init-actions", help="Create a GitHub Actions workflow for a source type"
    )
    actions_parser.add_argument(
        "--language", required=True, choices=sorted(EXTENSION_BACKENDS)
    )
    actions_parser.add_argument(
        "--output", default=".github/workflows/universal-compiler.yml"
    )

    bytecode_parser = subparsers.add_parser(
        "bytecode", help="Compile a Python source file to .pyc"
    )
    bytecode_parser.add_argument("source")
    bytecode_parser.add_argument("--output", "-o")
    bytecode_parser.add_argument("--json", action="store_true")

    icon_parser = subparsers.add_parser(
        "extract-icon", help="Extract a Windows executable icon as .ico"
    )
    icon_parser.add_argument("executable")
    icon_parser.add_argument("--output", "-o")

    msix_parser = subparsers.add_parser(
        "wrap-msix", help="Create an unsigned MSIX/APPX package around an EXE"
    )
    msix_parser.add_argument("executable")
    msix_parser.add_argument("--output", "-o", required=True)
    msix_parser.add_argument("--display-name", default="Universal Compiler Application")
    msix_parser.add_argument("--publisher", default="CN=UniversalCompiler")
    msix_parser.add_argument("--version", default="1.0.0.0")

    obfuscate_parser = subparsers.add_parser(
        "obfuscate", help="Run an opt-in source obfuscator into a separate path"
    )
    obfuscate_parser.add_argument("source")
    obfuscate_parser.add_argument(
        "--method", required=True, choices=sorted(OBFUSCATOR_NAMES)
    )
    obfuscate_parser.add_argument("--output", "-o", required=True)

    analytics_parser = subparsers.add_parser(
        "analytics", help="Show local build timing and size analytics"
    )
    analytics_parser.add_argument("--path")
    analytics_parser.add_argument("--recent", type=int, default=0)
    analytics_parser.add_argument("--json", action="store_true")

    return parser


def _default_output(source: Path) -> Path:
    return source.with_suffix(".exe")


def _result_text(result: BuildResult) -> str:
    lines = [f"{result.status}: {result.output}"]
    if result.backend:
        lines.append(f"backend: {result.backend}")
    if result.message:
        lines.append(redact_text(result.message))
    for command in result.commands:
        lines.append(f"$ {command_display(command.command, redact=True)}")
        if command.output:
            lines.append(redact_text(command.output))
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
        status_value = backend_status()
        print(
            json.dumps(status_value, indent=2)
            if args.json
            else "\n".join(
                f"{key}: {'available' if item['available'] else 'missing'}"
                for key, item in status_value.items()
            )
        )
        return 0
    if args.command == "init-profiles":
        destination = Path(args.path).expanduser() if args.path else profiles_path()
        save_profiles(destination, DEFAULT_PROFILES)
        print(destination)
        return 0
    if args.command == "init-actions":
        destination = Path(args.output).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            github_actions_template(args.language),
            encoding="utf-8",
        )
        print(destination)
        return 0
    if args.command == "bytecode":
        try:
            output = compile_bytecode(args.source, args.output)
        except (BuildValidationError, OSError, py_compile.PyCompileError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        bytecode_value = {"source": args.source, "output": str(output)}
        print(json.dumps(bytecode_value, indent=2) if args.json else str(output))
        return 0
    if args.command == "extract-icon":
        try:
            output = extract_icon(args.executable, args.output)
        except (BuildValidationError, OSError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print(output)
        return 0
    if args.command == "wrap-msix":
        try:
            output = wrap_msix(
                args.executable,
                args.output,
                display_name=args.display_name,
                publisher=args.publisher,
                version=args.version,
            )
        except (BuildValidationError, OSError, zipfile.BadZipFile) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print(output)
        return 0
    if args.command == "obfuscate":
        try:
            output = obfuscate_source(args.source, args.method, args.output)
        except (BuildValidationError, OSError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print(output)
        return 0
    if args.command == "analytics":
        analytics_store = BuildAnalytics(args.path)
        analytics_value: dict[str, Any] = analytics_store.summary()
        if args.recent:
            analytics_value["recent"] = analytics_store.recent(args.recent)
        print(
            json.dumps(analytics_value, indent=2, default=str)
            if args.json
            else json.dumps(analytics_value, indent=2, default=str)
        )
        return 0
    if args.command == "verify":
        verification_result = (
            verify_artifact_manifest(args.artifact)
            if Path(args.artifact).name.endswith(".manifest.json")
            else verify_artifact(args.artifact)
        )
        print(
            json.dumps(asdict(verification_result), indent=2, default=str)
            if args.json
            else f"{'PASS' if verification_result.passed else 'FAIL'}: {verification_result.details}"
        )
        return 0 if verification_result.passed else 1
    if args.command == "inspect":
        source = Path(args.source).expanduser()
        file_type = detect_file_type(source)
        choices = EXTENSION_BACKENDS.get(file_type or "", ())
        inspect_value = {
            "source": str(source),
            "file_type": file_type,
            "estimated_size": estimate_output_size(source, file_type),
            "backends": {backend: backend_status()[backend] for backend in choices},
        }
        print(json.dumps(inspect_value, indent=2) if args.json else json.dumps(inspect_value, indent=2))
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
    build_analytics: BuildAnalytics | None = (
        None if args.no_analytics else BuildAnalytics()
    )
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
                    if build_analytics:
                        build_analytics.record(result)
                    print(
                        json.dumps(result.as_dict(), indent=2, default=str)
                        if args.json
                        else _result_text(result),
                        flush=True,
                    )
            except KeyboardInterrupt:
                return 0
            return 0
        if args.matrix:
            results = engine.build_matrix(request, args.matrix, workers=args.jobs)
            if build_analytics:
                for matrix_result in results:
                    build_analytics.record(matrix_result)
            if args.json:
                print(
                    json.dumps(
                        [result.as_dict() for result in results],
                        indent=2,
                        default=str,
                    )
                )
            else:
                print("\n\n".join(_result_text(result) for result in results))
            return 0 if all(result.success for result in results) else 1
        build_result = engine.build(request)
        if build_analytics:
            build_analytics.record(build_result)
        print(
            json.dumps(build_result.as_dict(), indent=2, default=str)
            if args.json
            else _result_text(build_result)
        )
        return 0 if build_result.success else 1
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
        if args.matrix:
            results = [
                result
                for request in requests
                for result in engine.build_matrix(
                    request,
                    args.matrix,
                    workers=args.jobs,
                )
            ]
        else:
            results = engine.build_batch(requests, workers=args.jobs)
        if build_analytics:
            for batch_result in results:
                build_analytics.record(batch_result)
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
    "ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "BACKEND_NAMES",
    "BuildAnalytics",
    "BuildPlan",
    "BuildRequest",
    "BuildResult",
    "BuildValidationError",
    "CAPABILITY_SCHEMA_VERSION",
    "CommandResult",
    "CompilerEngine",
    "DEFAULT_PROFILES",
    "DEFAULT_EXECUTION_TIMEOUT_SECONDS",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "ExecutionPolicy",
    "EXTENSION_BACKENDS",
    "VerificationResult",
    "backend_status",
    "artifact_manifest_path",
    "cli_main",
    "command_display",
    "compile_bytecode",
    "config_dir",
    "detect_file_type",
    "estimate_output_size",
    "extract_icon",
    "format_size",
    "github_actions_template",
    "obfuscate_source",
    "load_json",
    "load_profiles",
    "profiles_path",
    "parse_toolchain_versions",
    "run_command",
    "REQUEST_SCHEMA_VERSION",
    "redact_command",
    "redact_text",
    "RESULT_SCHEMA_VERSION",
    "save_json",
    "save_profiles",
    "sha256_file",
    "verify_artifact_manifest",
    "verify_artifact",
    "wrap_msix",
]
