"""Side-effect-free build engine and command line interface for Universal Compiler.

The GUI is intentionally kept out of this module.  The engine can therefore be
used by the CLI, tests, batch automation, and the graphical front end without
creating windows, changing the current desktop, or installing Python packages
as an import side effect.
"""

from __future__ import annotations

import argparse
import copy
import concurrent.futures
from contextlib import contextmanager
import hashlib
import importlib.metadata as importlib_metadata
import importlib.util
import json
import locale as _system_locale
import math
import os
import py_compile
import platform
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
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

_fcntl: Any
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows uses msvcrt.
    _fcntl = None

_msvcrt: Any
try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX uses fcntl.
    _msvcrt = None

APP_NAME = "Universal Compiler"
APP_VERSION = "2.1.0"
I18N_SCHEMA_VERSION = "uc.i18n.v1"
DEFAULT_LOCALE = "en"
I18N_CATALOG_FILENAME = "catalog.json"
REQUEST_SCHEMA_VERSION = "uc.request.v1"
RESULT_SCHEMA_VERSION = "uc.result.v1"
CAPABILITY_SCHEMA_VERSION = "uc.capability.v1"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "uc.artifact-manifest.v1"
PROJECT_MANIFEST_SCHEMA_VERSION = "uc.project.v1"
PROJECT_MANIFEST_KIND = "universal-compiler.project"
PROJECT_MANIFEST_FILENAME = "universal-compiler.json"
DEPENDENCY_LOCK_SCHEMA_VERSION = "uc.dependencies.v1"
DEPENDENCY_LOCK_KIND = "universal-compiler.dependencies"
DEPENDENCY_LOCK_FILENAME = "universal-compiler.lock.json"
DEPENDENCY_POLICY_VERSION = "uc.dependency-policy.v1"
RELEASE_SCHEMA_VERSION = "uc.release.v1"
RELEASE_KIND = "universal-compiler.release"
SBOM_SCHEMA_VERSION = "uc.sbom.v1"
PROVENANCE_SCHEMA_VERSION = "uc.provenance.v1"
DIAGNOSTICS_SCHEMA_VERSION = "uc.diagnostics.v1"
DIAGNOSTICS_KIND = "universal-compiler.diagnostics"
DEFAULT_DIAGNOSTICS_RETENTION_DAYS = 30
DEFAULT_DIAGNOSTICS_MAX_EVENTS = 2000
ADAPTER_API_VERSION = "uc.adapter.v1"
ADAPTER_ENTRY_POINT_GROUP = "universal_compiler.adapters"
ADAPTER_ALLOWLIST_ENV = "UC_ADAPTER_ALLOWLIST"
ADAPTER_POLICY_VERSION = "uc.adapter-policy.v1"


_BUILTIN_I18N_CATALOG: dict[str, Any] = {
    "schema_version": I18N_SCHEMA_VERSION,
    "default_locale": DEFAULT_LOCALE,
    "locales": {
        "en": {
            "messages": {
                "app.title": APP_NAME,
                "status.ready": "Ready",
                "status.compiling": "Compiling...",
                "status.cancelling": "Cancelling...",
                "status.complete": "Complete!",
                "status.failed": "Failed",
                "cli.error": "ERROR: {error}",
                "cli.warning": "WARNING: {warning}",
            },
            "plurals": {
                "queue.files": {"one": "{count} file", "other": "{count} files"}
            },
            "format": {
                "decimal_separator": ".",
                "group_separator": ",",
                "date_format": "%Y-%m-%d",
                "time_format": "%H:%M:%S",
            },
        }
    },
}


def normalize_locale(value: str | None) -> str:
    """Normalize a BCP-47/POSIX locale token for catalog lookup."""

    if not value:
        return ""
    token = str(value).strip().replace("_", "-")
    if not token or token.lower() in {"auto", "c", "posix"}:
        return ""
    parts = token.split("-")
    return "-".join(
        [parts[0].lower(), *[part.upper() if len(part) == 2 else part for part in parts[1:]]]
    )


def resolve_locale(
    preferred: str | None = None,
    environment: Mapping[str, str] | None = None,
    available: Iterable[str] | None = None,
) -> str:
    """Choose a catalog locale using explicit, environment, then OS settings.

    ``UC_LOCALE`` and ``UNIVERSAL_COMPILER_LOCALE`` are intentionally first-class
    overrides so CLI, Tk, and PowerShell launches can select the same locale
    without mutating the process-wide C/POSIX locale.
    """

    values = {normalize_locale(value) for value in (available or (DEFAULT_LOCALE,))}
    values.discard("")
    default = normalize_locale(DEFAULT_LOCALE) or DEFAULT_LOCALE
    if default not in values:
        values.add(default)
    env = os.environ if environment is None else environment
    candidates = [preferred]
    candidates.extend(
        env.get(name)
        for name in ("UC_LOCALE", "UNIVERSAL_COMPILER_LOCALE", "LC_ALL", "LANG")
    )
    try:
        candidates.append(_system_locale.getlocale()[0])
    except ValueError:
        pass
    for candidate in candidates:
        normalized = normalize_locale(candidate)
        if not normalized:
            continue
        if normalized in values:
            return normalized
        base = normalized.split("-", 1)[0]
        if base in values:
            return base
    return default


def _catalog_value(mapping: Any, key: str) -> Any:
    if isinstance(mapping, Mapping):
        return mapping.get(key)
    return None


class MessageCatalog:
    """Shared, dependency-free message and locale formatting catalog."""

    def __init__(
        self,
        locale_name: str | None = None,
        catalog_path: os.PathLike[str] | str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        source = (
            Path(catalog_path).expanduser()
            if catalog_path
            else Path(__file__).resolve().parent / "resources" / "i18n" / I18N_CATALOG_FILENAME
        )
        payload: Mapping[str, Any] = _BUILTIN_I18N_CATALOG
        try:
            loaded = json.loads(source.read_text(encoding="utf-8"))
            if (
                isinstance(loaded, Mapping)
                and loaded.get("schema_version") == I18N_SCHEMA_VERSION
                and isinstance(loaded.get("locales"), Mapping)
            ):
                payload = loaded
        except (OSError, ValueError, TypeError):
            pass
        self.source = source
        self.default_locale = normalize_locale(
            str(payload.get("default_locale", DEFAULT_LOCALE))
        ) or DEFAULT_LOCALE
        raw_locales = payload.get("locales", {})
        self.locales: dict[str, Mapping[str, Any]] = {
            normalize_locale(str(name)): value
            for name, value in raw_locales.items()
            if normalize_locale(str(name)) and isinstance(value, Mapping)
        }
        if self.default_locale not in self.locales:
            self.locales[DEFAULT_LOCALE] = _BUILTIN_I18N_CATALOG["locales"]["en"]
        self.locale = resolve_locale(
            locale_name,
            environment=environment,
            available=self.locales,
        )

    @property
    def available_locales(self) -> tuple[str, ...]:
        return tuple(sorted(self.locales))

    def _locale_data(self) -> Mapping[str, Any]:
        return self.locales.get(self.locale, self.locales[self.default_locale])

    def message(self, key: str, default: str | None = None, **values: Any) -> str:
        """Return a translated message, falling back to English and the key."""

        localized = _catalog_value(self._locale_data().get("messages"), key)
        fallback = _catalog_value(self.locales[self.default_locale].get("messages"), key)
        template = localized or fallback or default or key
        if not isinstance(template, str):
            template = str(template)
        try:
            return template.format(**values)
        except (IndexError, KeyError, ValueError):
            return template

    def plural(
        self,
        key: str,
        quantity: int | float,
        default: str | None = None,
        **values: Any,
    ) -> str:
        """Format a two-form plural message with an English fallback."""

        forms = _catalog_value(self._locale_data().get("plurals"), key)
        if not isinstance(forms, Mapping):
            forms = _catalog_value(
                self.locales[self.default_locale].get("plurals"), key
            )
        template = forms.get("one" if quantity == 1 else "other") if forms else None
        if not isinstance(template, str):
            template = default or key
        values.setdefault("count", quantity)
        return self.message_from_template(template, **values)

    @staticmethod
    def message_from_template(template: str, **values: Any) -> str:
        try:
            return template.format(**values)
        except (IndexError, KeyError, ValueError):
            return template

    def format_number(self, value: int | float, decimals: int = 0) -> str:
        """Format a number using catalog separators without changing global locale."""

        formatting = self._locale_data().get("format", {})
        decimal_separator = str(formatting.get("decimal_separator", "."))
        group_separator = str(formatting.get("group_separator", ","))
        rendered = f"{float(value):,.{max(0, int(decimals))}f}"
        integer, _, fraction = rendered.partition(".")
        integer = integer.replace(",", group_separator)
        return integer if not fraction else integer + decimal_separator + fraction

    def format_size(self, size: int) -> str:
        """Format bytes with locale-aware separators and stable binary units."""

        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(value) < 1024 or unit == "TB":
                decimals = 0 if unit == "B" else 1
                return f"{self.format_number(value, decimals)} {unit}"
            value /= 1024
        return f"{self.format_number(value, 1)} TB"

    def format_datetime(self, value: datetime) -> str:
        """Format a timestamp using the catalog's locale date/time pattern."""

        formatting = self._locale_data().get("format", {})
        date_format = str(formatting.get("date_format", "%Y-%m-%d"))
        time_format = str(formatting.get("time_format", "%H:%M:%S"))
        return value.strftime(f"{date_format} {time_format}")


def get_message_catalog(
    locale_name: str | None = None,
    catalog_path: os.PathLike[str] | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> MessageCatalog:
    """Return the shared catalog used by CLI and graphical shells."""

    return MessageCatalog(locale_name, catalog_path, environment)


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG relative-luminance contrast ratio for two hex colors."""

    def luminance(color: str) -> float:
        token = color.strip().lstrip("#")
        if len(token) == 3:
            token = "".join(character * 2 for character in token)
        if len(token) != 6:
            raise ValueError(f"Expected a six-digit hex color, got {color!r}")
        channels = [int(token[index : index + 2], 16) / 255 for index in (0, 2, 4)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter = max(luminance(foreground), luminance(background))
    darker = min(luminance(foreground), luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


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

DEFAULT_MANIFEST_SETTINGS: dict[str, Any] = {
    "theme": "Dark",
    "locale": "",
    "post_build_action": "None",
    "post_build_copy_path": "",
    "show_notifications": True,
    "auto_check_updates": True,
    "max_recent_files": 10,
    "max_history_items": 50,
    "default_profile": "Default",
}
PROJECT_MANIFEST_PROFILE_FIELDS = frozenset(
    {
        "console",
        "admin",
        "single_file",
        "backend",
        "target",
        "architecture",
        "version",
        "company",
        "copyright",
        "description",
        "product",
        "prefetch",
        "verify",
        "cache",
        "force",
        "extra_args",
        "toolchain_versions",
        "upx",
        "timeout_seconds",
        "max_output_bytes",
        "allow_network",
        "allow_dependency_install",
        "dependency_lockfile",
        "dependency_cache_dir",
        "dependency_mirror",
    }
)
PROJECT_MANIFEST_SETTINGS_FIELDS = frozenset(DEFAULT_MANIFEST_SETTINGS)
PROJECT_MANIFEST_HISTORY_FIELDS = frozenset(
    {"timestamp", "source", "output", "type", "success", "profile", "size", "backend", "message", "manifest"}
)
PROJECT_MANIFEST_ANALYTICS_FIELDS = frozenset({"enabled", "scope", "database"})


BACKEND_CATALOG: dict[str, dict[str, Any]] = {
    "ps2exe": {
        "name": "PowerShell PS2EXE", "extensions": ("ps1",), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native", "x86", "x64", "arm64"), "required_sdks": ("PowerShell",),
    },
    "pyinstaller": {
        "name": "Python PyInstaller", "extensions": ("py", "pyw"), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native", "x86", "x64", "arm64"), "required_sdks": ("Python",),
    },
    "nuitka": {
        "name": "Python Nuitka", "extensions": ("py", "pyw"), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native", "x86", "x64", "arm64"), "required_sdks": ("Python", "C compiler"),
    },
    "iexpress": {
        "name": "Windows IExpress", "extensions": ("bat", "cmd", "vbs"), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native",), "required_sdks": ("Windows",),
    },
    "bun": {
        "name": "Bun compile", "extensions": ("js", "ts"), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows", "linux", "darwin"),
        "architectures": ("native", "x86", "x64", "arm64"), "required_sdks": ("Bun",),
    },
    "deno": {
        "name": "Deno compile", "extensions": ("js",), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows", "linux", "darwin"),
        "architectures": ("native", "x86", "x64", "arm64"), "required_sdks": ("Deno",),
    },
    "pkg": {
        "name": "Node.js pkg", "extensions": ("js",), "status": "deprecated",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows", "linux", "darwin"),
        "architectures": ("native", "x86", "x64", "arm64"), "required_sdks": ("Node.js",),
    },
    "ahk2exe": {
        "name": "AutoHotkey Ahk2Exe", "extensions": ("ahk",), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native",), "required_sdks": ("AutoHotkey",),
    },
    "csc": {
        "name": "C# CSC", "extensions": ("cs",), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native", "x86", "x64", "arm64"), "required_sdks": (".NET Framework",),
    },
    "go": {
        "name": "Go build", "extensions": ("go",), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows", "linux", "darwin"),
        "architectures": ("native", "x86", "x64", "amd64", "arm64"), "required_sdks": ("Go",),
    },
    "ocra": {
        "name": "Ruby Ocra", "extensions": ("rb",), "status": "experimental",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native",), "required_sdks": ("Ruby",),
    },
    "rust": {
        "name": "Rust cargo/rustc", "extensions": ("rs",), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows", "linux", "darwin"),
        "architectures": ("native", "x86", "x64", "amd64", "arm64"), "required_sdks": ("Rust",),
    },
    "srlua": {
        "name": "Lua srlua", "extensions": ("lua",), "status": "experimental",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native",), "required_sdks": ("srlua",),
    },
    "luastatic": {
        "name": "Lua luastatic", "extensions": ("lua",), "status": "experimental",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native",), "required_sdks": ("luastatic",),
    },
    "perl-pp": {
        "name": "Perl PAR::Packer", "extensions": ("pl", "pm"), "status": "experimental",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native",), "required_sdks": ("Perl",),
    },
    "kotlin-native": {
        "name": "Kotlin/Native", "extensions": ("kt", "kts"), "status": "experimental",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows", "linux", "darwin"),
        "architectures": ("native", "x86", "x64", "arm64"), "required_sdks": ("Kotlin/Native",),
    },
    "wat2wasm": {
        "name": "WebAssembly wat2wasm", "extensions": ("wat",), "status": "stable",
        "host_platforms": ("windows",), "target_platforms": ("native", "wasm"),
        "architectures": ("native",), "required_sdks": ("WABT",),
    },
    "upx": {
        "name": "UPX compressor", "extensions": (), "status": "optional",
        "host_platforms": ("windows",), "target_platforms": ("native", "windows"),
        "architectures": ("native",), "required_sdks": ("UPX",),
    },
}

EXTENSION_BACKENDS: dict[str, tuple[str, ...]] = {}
for _backend_name, _backend_spec in BACKEND_CATALOG.items():
    for _extension in _backend_spec["extensions"]:
        EXTENSION_BACKENDS.setdefault(_extension, ())
        EXTENSION_BACKENDS[_extension] += (_backend_name,)

BACKEND_NAMES: dict[str, str] = {
    backend: str(spec["name"]) for backend, spec in BACKEND_CATALOG.items()
}

OBFUSCATOR_NAMES: dict[str, str] = {
    "pyarmor": "PyArmor Python obfuscator",
    "javascript-obfuscator": "JavaScript Obfuscator",
    "confuserex": "ConfuserEx .NET obfuscator",
}


class BuildValidationError(ValueError):
    """Raised when a build request cannot be safely planned."""


class StateLockError(BuildValidationError):
    """Raised when a local state file cannot be locked within the bound."""


DEFAULT_EXECUTION_TIMEOUT_SECONDS = 15 * 60
DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
DEFAULT_STATE_LOCK_TIMEOUT_SECONDS = 15.0
STATE_LOCK_POLL_SECONDS = 0.05
ANALYTICS_BUSY_TIMEOUT_MS = 10_000
ANALYTICS_SCHEMA_VERSION = 1
MAX_CLEANUP_PATHS = 32
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

_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()
_BUILD_LOCKS: dict[str, threading.RLock] = {}


def _state_lock_key(path: os.PathLike[str] | str) -> str:
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = Path(os.path.abspath(candidate))
    return os.path.normcase(str(candidate))


def _state_thread_lock(path: os.PathLike[str] | str) -> threading.RLock:
    key = _state_lock_key(path)
    with _STATE_LOCKS_GUARD:
        lock = _STATE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STATE_LOCKS[key] = lock
        return lock


@contextmanager
def state_lock(
    path: os.PathLike[str] | str,
    timeout: float = DEFAULT_STATE_LOCK_TIMEOUT_SECONDS,
) -> Iterator[Path]:
    """Coordinate state writers across threads and processes.

    The lock file is deliberately retained beside the state file. OS-level
    locks are released when the owning process exits, so a stale lock file
    never becomes a false permanent lock and remains discoverable for support.
    """

    destination = Path(path).expanduser()
    lock_path = destination.with_name(f".{destination.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _state_thread_lock(lock_path)
    if not thread_lock.acquire(timeout=max(0.0, float(timeout))):
        raise StateLockError(f"Timed out waiting for state lock: {lock_path}")
    handle = None
    deadline = time.monotonic() + max(0.0, float(timeout))
    try:
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        while True:
            try:
                handle.seek(0)
                if os.name == "nt" and _msvcrt is not None:
                    _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
                elif _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                else:  # pragma: no cover - supported runtimes have one backend.
                    raise StateLockError("No supported process lock implementation")
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise StateLockError(
                        f"Timed out waiting for state lock: {lock_path}"
                    ) from error
                time.sleep(STATE_LOCK_POLL_SECONDS)
        try:
            yield lock_path
        finally:
            try:
                handle.seek(0)
                if os.name == "nt" and _msvcrt is not None:
                    _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
                elif _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        if handle is not None:
            handle.close()
        thread_lock.release()
_BUILD_LOCKS_GUARD = threading.Lock()


def _output_lock_key(path: os.PathLike[str] | str) -> str:
    """Return a case-insensitive identity for a build output path."""

    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = Path(os.path.abspath(candidate))
    return os.path.normcase(str(candidate))


def _output_lock(path: os.PathLike[str] | str) -> threading.RLock:
    """Return a process-wide lock so separate engines cannot publish together."""

    key = _output_lock_key(path)
    with _BUILD_LOCKS_GUARD:
        lock = _BUILD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _BUILD_LOCKS[key] = lock
        return lock


def _cleanup_paths(paths: Iterable[Path]) -> None:
    """Remove only bounded, build-owned temporary paths."""

    unique: list[Path] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        key = _output_lock_key(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
        if len(unique) >= MAX_CLEANUP_PATHS:
            break
    for path in reversed(unique):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError:
            pass


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


def new_correlation_id() -> str:
    """Create a local correlation token without including user or host data."""

    return uuid.uuid4().hex


def _normalize_correlation_id(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{32}", candidate):
        return candidate
    try:
        return uuid.UUID(candidate).hex
    except (ValueError, AttributeError):
        pass
    return new_correlation_id()


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
    dependency_lockfile: Path | None = None
    dependency_cache_dir: Path | None = None
    dependency_mirror: str | None = None
    correlation_id: str = field(default_factory=new_correlation_id, compare=False)
    cancel_event: threading.Event | None = field(
        default=None, repr=False, compare=False
    )

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
            dependency_lockfile=(
                Path(self.dependency_lockfile).expanduser()
                if self.dependency_lockfile
                else None
            ),
            dependency_cache_dir=(
                Path(self.dependency_cache_dir).expanduser()
                if self.dependency_cache_dir
                else None
            ),
            dependency_mirror=(
                str(self.dependency_mirror).strip()
                if self.dependency_mirror
                else None
            ),
            correlation_id=_normalize_correlation_id(self.correlation_id),
            cancel_event=self.cancel_event,
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
    correlation_id: str = ""
    phase: str = "command"

    def __post_init__(self) -> None:
        self.correlation_id = _normalize_correlation_id(self.correlation_id)
        self.phase = str(self.phase or "command").strip().lower() or "command"

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part).strip()

    @property
    def exit_classification(self) -> str:
        if self.cancelled:
            return "cancelled"
        if self.timed_out:
            return "timed-out"
        if self.returncode == 0:
            return "success"
        if self.returncode < 0:
            return "signal"
        return "failed"

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        redacted = redact_command(self.command)
        result["command"] = list(redacted)
        result["stdout"] = redact_text(self.stdout)
        result["stderr"] = redact_text(self.stderr)
        result["exit_classification"] = self.exit_classification
        result["command_metadata"] = {
            "executable": Path(redacted[0]).name if redacted else "",
            "argument_count": max(0, len(redacted) - 1),
            "argv": list(redacted),
            "redacted": True,
        }
        return result

    def diagnostic_record(self, correlation_id: str | None = None) -> dict[str, Any]:
        redacted = redact_command(self.command)
        return {
            "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
            "kind": DIAGNOSTICS_KIND,
            "correlation_id": _normalize_correlation_id(
                correlation_id or self.correlation_id
            ),
            "phase": self.phase,
            "duration_seconds": round(max(0.0, float(self.duration_seconds)), 6),
            "returncode": self.returncode,
            "exit_classification": self.exit_classification,
            "command": {
                "executable": Path(redacted[0]).name if redacted else "",
                "argument_count": max(0, len(redacted) - 1),
                "option_names": [
                    item.split("=", 1)[0]
                    for item in redacted[1:]
                    if item.startswith("-")
                ],
                "redacted": True,
            },
            "output": {
                "stdout_bytes": len(self.stdout.encode("utf-8", errors="replace")),
                "stderr_bytes": len(self.stderr.encode("utf-8", errors="replace")),
                "truncated": self.output_truncated,
            },
        }


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
    phase_timings: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.phase_timings:
            timings: dict[str, float] = {
                "total": round(max(0.0, float(self.duration_seconds)), 6)
            }
            command_seconds = sum(
                max(0.0, float(command.duration_seconds)) for command in self.commands
            )
            if self.commands:
                timings["commands"] = round(command_seconds, 6)
                for command in self.commands:
                    phase = command.phase or "command"
                    timings[phase] = round(
                        timings.get(phase, 0.0)
                        + max(0.0, float(command.duration_seconds)),
                        6,
                    )
            self.phase_timings = timings

    @property
    def correlation_id(self) -> str:
        return _normalize_correlation_id(self.request.correlation_id)

    @property
    def cache_status(self) -> str:
        if self.status == "cache-hit":
            return "hit"
        if not self.request.cache:
            return "disabled"
        if self.cache_key:
            return "miss"
        return "not-requested"

    def artifact_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        if re.fullmatch(r"[0-9a-f]{64}", self.source_hash):
            hashes["source"] = self.source_hash
        for name, path in (("output", self.output), ("manifest", self.manifest)):
            if path and Path(path).is_file():
                try:
                    hashes[name] = sha256_file(path)
                except OSError:
                    continue
        return hashes

    def diagnostic_record(self) -> dict[str, Any]:
        return {
            "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
            "kind": DIAGNOSTICS_KIND,
            "correlation_id": self.correlation_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "status": self.status,
            "success": self.success,
            "backend": self.backend,
            "target": self.request.target,
            "architecture": self.request.architecture,
            "exit_classification": (
                self.commands[-1].exit_classification
                if self.commands
                else ("success" if self.success else "failed")
            ),
            "phase_timings": {
                str(name): round(max(0.0, float(seconds)), 6)
                for name, seconds in self.phase_timings.items()
            },
            "cache": {"status": self.cache_status},
            "artifacts": {"sha256": self.artifact_hashes()},
            "commands": [
                command.diagnostic_record(self.correlation_id)
                for command in self.commands
            ],
        }

    def as_dict(self) -> dict[str, Any]:
        serializable_request = replace(self.request, cancel_event=None)
        result = asdict(replace(self, request=serializable_request))
        result["schema_version"] = RESULT_SCHEMA_VERSION
        result["correlation_id"] = self.correlation_id
        result["cache_status"] = self.cache_status
        result["artifact_hashes"] = self.artifact_hashes()
        result["exit_classification"] = self.diagnostic_record()["exit_classification"]
        result["request"]["source"] = str(self.request.source)
        result["request"]["schema_version"] = REQUEST_SCHEMA_VERSION
        result["request"]["output"] = str(self.request.output)
        result["request"].pop("cancel_event", None)
        result["request"]["icon"] = (
            str(self.request.icon) if self.request.icon else None
        )
        result["request"]["dependency_lockfile"] = (
            str(self.request.dependency_lockfile)
            if self.request.dependency_lockfile
            else None
        )
        result["request"]["dependency_cache_dir"] = (
            str(self.request.dependency_cache_dir)
            if self.request.dependency_cache_dir
            else None
        )
        result["request"]["metadata"] = dict(self.request.metadata)
        result["request"]["extra_args"] = list(redact_command(self.request.extra_args))
        result["output"] = str(self.output)
        result["manifest"] = str(self.manifest) if self.manifest else None
        result["message"] = redact_text(str(result.get("message", "")))
        result["commands"] = [
            command.as_dict()
            for command in self.commands
        ]
        result["diagnostics"] = self.diagnostic_record()
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


class BackendAdapter(Protocol):
    """Versioned external adapter shape consumed by the core engine."""

    api_version: str
    namespace: str
    name: str
    extensions: tuple[str, ...]
    lifecycle: str
    host_platforms: tuple[str, ...]
    target_platforms: tuple[str, ...]
    architectures: tuple[str, ...]
    required_sdks: tuple[str, ...]
    detector: Callable[[Path], bool] | None
    tool_identity: Callable[[], Mapping[str, Any]] | None
    diagnostics: Callable[[CommandResult], Mapping[str, Any]] | None

    def detect(self, source: Path) -> bool: ...

    def plan(
        self, request: BuildRequest, context: Mapping[str, Any]
    ) -> BuildPlan: ...


@dataclass(frozen=True)
class AdapterDescriptor:
    """Validated adapter metadata and the side-effect-free planning hook."""

    api_version: str
    namespace: str
    name: str
    extensions: tuple[str, ...]
    lifecycle: str = "experimental"
    host_platforms: tuple[str, ...] = ("windows",)
    target_platforms: tuple[str, ...] = ("native",)
    architectures: tuple[str, ...] = ("native",)
    required_sdks: tuple[str, ...] = ()
    planner: Callable[[BuildRequest, Mapping[str, Any]], BuildPlan] | None = None
    detector: Callable[[Path], bool] | None = field(default=None, compare=False, repr=False)
    tool_identity: Callable[[], Mapping[str, Any]] | None = field(
        default=None, compare=False, repr=False
    )
    diagnostics: Callable[[CommandResult], Mapping[str, Any]] | None = field(
        default=None, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.api_version != ADAPTER_API_VERSION:
            raise BuildValidationError(
                f"Unsupported adapter API {self.api_version!r}; expected {ADAPTER_API_VERSION}"
            )
        identifier_pattern = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
        if not identifier_pattern.fullmatch(self.namespace) or not identifier_pattern.fullmatch(self.name):
            raise BuildValidationError("Adapter namespace and name must be lowercase identifiers")
        if self.namespace != "builtin" and self.planner is None:
            raise BuildValidationError("External adapters must provide a planner")
        normalized_extensions = tuple(
            str(extension).lower().lstrip(".") for extension in self.extensions
        )
        if not normalized_extensions:
            raise BuildValidationError("Adapters must declare at least one source extension")
        object.__setattr__(self, "extensions", normalized_extensions)

    @property
    def backend_id(self) -> str:
        return self.name if self.namespace == "builtin" else f"{self.namespace}.{self.name}"

    @property
    def external(self) -> bool:
        return self.namespace != "builtin"

    def plan(
        self, request: BuildRequest, context: Mapping[str, Any]
    ) -> BuildPlan:
        """Invoke the adapter planner through the versioned adapter surface."""

        if self.planner is None:
            raise BuildValidationError(f"Adapter has no planner: {self.backend_id}")
        return self.planner(request, context)

    def detect(self, source: Path) -> bool:
        """Detect a source through the adapter's optional content hook."""

        if self.detector is None:
            return source.suffix.lower().lstrip(".") in self.extensions
        return bool(self.detector(source))


def _builtin_adapter_descriptors() -> tuple[AdapterDescriptor, ...]:
    return tuple(
        AdapterDescriptor(
            api_version=ADAPTER_API_VERSION,
            namespace="builtin",
            name=backend,
            extensions=tuple(str(value) for value in spec["extensions"]),
            lifecycle=str(spec["status"]),
            host_platforms=tuple(str(value) for value in spec["host_platforms"]),
            target_platforms=tuple(str(value) for value in spec["target_platforms"]),
            architectures=tuple(str(value) for value in spec["architectures"]),
            required_sdks=tuple(str(value) for value in spec.get("required_sdks", ())),
        )
        for backend, spec in BACKEND_CATALOG.items()
        if spec["extensions"]
    )


def _adapter_allowlist(allowlist: Sequence[str] | None) -> frozenset[str]:
    if allowlist is None:
        raw = os.environ.get(ADAPTER_ALLOWLIST_ENV, "")
        allowlist = tuple(value.strip() for value in raw.split(",") if value.strip())
    return frozenset(str(value).strip().lower() for value in allowlist if str(value).strip())


def discover_adapters(
    allowlist: Sequence[str] | None = None,
    entry_points: Sequence[Any] | None = None,
) -> tuple[AdapterDescriptor, ...]:
    """Discover only explicitly allowlisted, namespaced entry-point adapters."""

    builtins = _builtin_adapter_descriptors()
    allowed = _adapter_allowlist(allowlist)
    if not allowed:
        return builtins
    if entry_points is None:
        try:
            selected = importlib_metadata.entry_points()
            if hasattr(selected, "select"):
                entry_points = tuple(selected.select(group=ADAPTER_ENTRY_POINT_GROUP))
            else:
                legacy_groups: Any = selected
                entry_points = tuple(
                    item
                    for item in legacy_groups.get(ADAPTER_ENTRY_POINT_GROUP, ())
                )
        except Exception as error:
            raise BuildValidationError(f"Could not inspect adapter entry points: {error}") from error
    candidates = sorted(
        (entry for entry in entry_points if str(getattr(entry, "name", "")).lower() in allowed),
        key=lambda entry: str(getattr(entry, "name", "")).lower(),
    )
    found = {str(getattr(entry, "name", "")).lower() for entry in candidates}
    missing = sorted(allowed - found)
    if missing:
        raise BuildValidationError(
            f"Allowlisted adapter entry point(s) are unavailable: {', '.join(missing)}"
        )
    adapters = list(builtins)
    identifiers = {adapter.backend_id for adapter in adapters}
    for entry in candidates:
        entry_name = str(getattr(entry, "name", "")).lower()
        try:
            loaded = entry.load()
            adapter = loaded() if callable(loaded) and not isinstance(loaded, AdapterDescriptor) else loaded
        except Exception as error:
            raise BuildValidationError(
                f"Could not load allowlisted adapter {entry_name}: {error}"
            ) from error
        if not isinstance(adapter, AdapterDescriptor):
            raise BuildValidationError(
                f"Adapter {entry_name} did not return an AdapterDescriptor"
            )
        if not adapter.external or adapter.backend_id != entry_name:
            raise BuildValidationError(
                f"Adapter {entry_name} must use its namespaced descriptor id"
            )
        if adapter.backend_id in identifiers:
            raise BuildValidationError(f"Conflicting adapter identifier: {adapter.backend_id}")
        identifiers.add(adapter.backend_id)
        adapters.append(adapter)
    return tuple(adapters)


def adapter_catalog(
    adapters: Sequence[AdapterDescriptor] | None = None,
) -> dict[str, AdapterDescriptor]:
    """Return deterministic backend-id to adapter metadata mappings."""

    selected = tuple(adapters) if adapters is not None else discover_adapters()
    catalog: dict[str, AdapterDescriptor] = {}
    for adapter in selected:
        if adapter.backend_id in catalog:
            raise BuildValidationError(f"Conflicting adapter identifier: {adapter.backend_id}")
        catalog[adapter.backend_id] = adapter
    return catalog


def _detect_adapter_file_type(
    adapters: Mapping[str, AdapterDescriptor], source: Path
) -> str | None:
    matches: list[AdapterDescriptor] = []
    for adapter in adapters.values():
        if adapter.detector is None:
            continue
        try:
            if adapter.detect(source):
                matches.append(adapter)
        except Exception as error:
            raise BuildValidationError(
                f"Adapter {adapter.backend_id} detector failed: {error}"
            ) from error
    if len(matches) > 1:
        names = ", ".join(adapter.backend_id for adapter in matches)
        raise BuildValidationError(f"Ambiguous adapter detection for {source}: {names}")
    return matches[0].extensions[0] if matches else None


def _redact_diagnostic_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _redact_diagnostic_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_diagnostic_value(item) for item in value]
    if isinstance(value, Path):
        return redact_text(str(value))
    if isinstance(value, str):
        return redact_text(value)
    return value


def adapter_diagnostics(
    adapter: AdapterDescriptor, command: CommandResult
) -> dict[str, Any]:
    """Normalize an adapter diagnostic callback without exposing raw secrets."""

    if adapter.diagnostics is None:
        return {
            "adapter": adapter.backend_id,
            "success": command.success,
            "returncode": command.returncode,
            "output": redact_text(command.output),
        }
    try:
        redacted = _redact_diagnostic_value(adapter.diagnostics(command))
        if not isinstance(redacted, dict):
            raise ValueError("diagnostics callback must return a mapping")
        value = redacted
    except Exception as error:
        return {
            "adapter": adapter.backend_id,
            "success": False,
            "error": redact_text(str(error)),
        }
    value["adapter"] = adapter.backend_id
    return value


@dataclass(frozen=True)
class ManifestLoadResult:
    """Result of loading, recovering, or migrating a project manifest."""

    manifest: dict[str, Any]
    migrated: bool = False
    recovered: bool = False
    warnings: tuple[str, ...] = ()
    source_paths: tuple[Path, ...] = ()


def config_dir(environment: Mapping[str, str] | None = None) -> Path:
    """Return the per-user configuration directory without creating it."""

    env = environment or os.environ
    root = env.get("APPDATA") or env.get("LOCALAPPDATA") or str(Path.home())
    return Path(root) / "UniversalCompiler"


def profiles_path(environment: Mapping[str, str] | None = None) -> Path:
    return config_dir(environment) / "profiles.yaml"


def project_manifest_path(
    scope: str = "user",
    workspace: os.PathLike[str] | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the explicit per-user or workspace-local manifest path."""

    normalized_scope = str(scope).lower()
    if normalized_scope == "user":
        return config_dir(environment) / PROJECT_MANIFEST_FILENAME
    if normalized_scope == "workspace":
        root = Path(workspace or Path.cwd()).expanduser().resolve()
        return root / PROJECT_MANIFEST_FILENAME
    raise BuildValidationError(
        f"Unknown manifest scope {scope!r}; expected 'user' or 'workspace'"
    )


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


def _atomic_write_bytes(destination: Path, value: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _atomic_backup(destination: Path) -> Path | None:
    if not destination.is_file():
        return None
    backup = project_manifest_backup_path(destination)
    temporary = backup.with_name(
        f".{backup.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        shutil.copy2(destination, temporary)
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, backup)
        return backup
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def load_json(
    path: os.PathLike[str] | str,
    default: Any = None,
    *,
    recoverable: bool = False,
) -> Any:
    """Load JSON, optionally restoring a valid ``.bak`` after interruption."""

    destination = Path(path).expanduser()
    try:
        with destination.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        if not recoverable:
            return default if default is not None else {}
    backup = project_manifest_backup_path(destination)
    try:
        with state_lock(destination):
            try:
                with destination.open("r", encoding="utf-8-sig") as handle:
                    return json.load(handle)
            except (OSError, ValueError, TypeError):
                with backup.open("r", encoding="utf-8-sig") as handle:
                    recovered = json.load(handle)
                _atomic_write_bytes(
                    destination,
                    json.dumps(recovered, indent=2, sort_keys=True).encode("utf-8"),
                )
                return recovered
    except (OSError, ValueError, TypeError, StateLockError):
        return default if default is not None else {}


def save_json(
    path: os.PathLike[str] | str,
    value: Any,
    *,
    recoverable: bool = False,
) -> None:
    """Atomically save JSON under a cross-process lock.

    ``recoverable=True`` keeps the previous valid file at ``<name>.bak``;
    caches and generated reports intentionally omit that backup.
    """

    destination = Path(path).expanduser()
    encoded = json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
    with state_lock(destination):
        if recoverable:
            _atomic_backup(destination)
        _atomic_write_bytes(destination, encoded)


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
    *,
    recoverable: bool = True,
) -> dict[str, dict[str, Any]]:
    """Load YAML profiles, recovering from a valid adjacent backup if needed."""

    merged = {
        name: dict(profile) for name, profile in (defaults or DEFAULT_PROFILES).items()
    }
    source = Path(path)
    candidates = [source]
    backup = project_manifest_backup_path(source)
    if recoverable and backup.is_file():
        candidates.append(backup)
    loaded: Any = None
    recovered_text: str | None = None
    source_valid = False
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        try:
            import yaml  # type: ignore[import-not-found, import-untyped]

            loaded = yaml.safe_load(text)
        except (ImportError, AttributeError, ValueError):
            loaded = _load_simple_yaml(text)
        except Exception:
            try:
                loaded = _load_simple_yaml(text)
            except Exception:
                loaded = None
        if isinstance(loaded, Mapping):
            if candidate == source:
                source_valid = True
            elif candidate == backup and not source_valid:
                recovered_text = text
            break
    if recovered_text is not None:
        with state_lock(source):
            _atomic_write_bytes(source, recovered_text.encode("utf-8"))
    if not isinstance(loaded, Mapping):
        return merged
    for name, profile in loaded.items():
        if isinstance(profile, Mapping):
            base = dict(merged.get(str(name), {}))
            base.update({str(key): value for key, value in profile.items()})
            merged[str(name)] = base
    return merged


def save_profiles(
    path: os.PathLike[str] | str,
    profiles: Mapping[str, Mapping[str, Any]],
    *,
    recoverable: bool = True,
) -> None:
    """Atomically write human-readable YAML with optional recovery backup."""

    destination = Path(path).expanduser()
    encoded = _dump_profiles_yaml(profiles).encode("utf-8")
    with state_lock(destination):
        if recoverable:
            _atomic_backup(destination)
        _atomic_write_bytes(destination, encoded)


_PROJECT_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "scope",
        "workspace",
        "settings",
        "profiles",
        "history",
        "analytics",
    }
)
_PROJECT_MANIFEST_WORKSPACE_FIELDS = frozenset({"root", "name"})


def _manifest_unknown_fields(
    value: Mapping[str, Any], allowed: Iterable[str], location: str
) -> None:
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise BuildValidationError(
            f"Unknown manifest field(s) at {location}: {', '.join(unknown)}"
        )


def _manifest_mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BuildValidationError(f"Manifest field {location} must be an object")
    return value


def default_project_manifest(
    scope: str = "user", workspace: os.PathLike[str] | str | None = None
) -> dict[str, Any]:
    """Return a complete manifest with explicit ownership scope metadata."""

    normalized_scope = str(scope).lower()
    if normalized_scope not in {"user", "workspace"}:
        raise BuildValidationError(
            f"Unknown manifest scope {scope!r}; expected 'user' or 'workspace'"
        )
    workspace_root = (
        str(Path(workspace or Path.cwd()).expanduser().resolve())
        if normalized_scope == "workspace"
        else None
    )
    return {
        "schema_version": PROJECT_MANIFEST_SCHEMA_VERSION,
        "kind": PROJECT_MANIFEST_KIND,
        "scope": normalized_scope,
        "workspace": {"root": workspace_root, "name": None},
        "settings": copy.deepcopy(DEFAULT_MANIFEST_SETTINGS),
        "profiles": copy.deepcopy(DEFAULT_PROFILES),
        "history": [],
        "analytics": {
            "enabled": True,
            "scope": "user",
            "database": "analytics.sqlite3",
        },
    }


def validate_project_manifest(
    manifest: Mapping[str, Any],
    expected_scope: str | None = None,
) -> dict[str, Any]:
    """Strictly validate and normalize a project manifest.

    Unknown fields are errors. Missing optional sections receive their v1
    defaults, while a newer schema is rejected as forward-incompatible.
    """

    source = _manifest_mapping(manifest, "<root>")
    _manifest_unknown_fields(source, _PROJECT_MANIFEST_FIELDS, "<root>")
    schema_version = source.get("schema_version")
    if schema_version != PROJECT_MANIFEST_SCHEMA_VERSION:
        if isinstance(schema_version, str) and schema_version.startswith("uc.project.v"):
            raise BuildValidationError(
                f"Forward-incompatible project manifest schema {schema_version}; "
                f"this build supports {PROJECT_MANIFEST_SCHEMA_VERSION}"
            )
        raise BuildValidationError(
            f"Unsupported project manifest schema {schema_version!r}; "
            f"expected {PROJECT_MANIFEST_SCHEMA_VERSION}"
        )
    if source.get("kind") != PROJECT_MANIFEST_KIND:
        raise BuildValidationError(
            f"Unsupported project manifest kind {source.get('kind')!r}"
        )
    scope = str(source.get("scope", "")).lower()
    if scope not in {"user", "workspace"}:
        raise BuildValidationError("Manifest scope must be 'user' or 'workspace'")
    if expected_scope and scope != str(expected_scope).lower():
        raise BuildValidationError(
            f"Manifest scope {scope!r} does not match requested scope {expected_scope!r}"
        )
    normalized = default_project_manifest(scope)
    normalized["workspace"] = dict(
        _manifest_mapping(source.get("workspace", normalized["workspace"]), "workspace")
    )
    _manifest_unknown_fields(
        normalized["workspace"], _PROJECT_MANIFEST_WORKSPACE_FIELDS, "workspace"
    )
    workspace_root = normalized["workspace"].get("root")
    if workspace_root is not None and not isinstance(workspace_root, str):
        raise BuildValidationError("Manifest field workspace.root must be a string or null")
    workspace_name = normalized["workspace"].get("name")
    if workspace_name is not None and not isinstance(workspace_name, str):
        raise BuildValidationError("Manifest field workspace.name must be a string or null")

    settings = _manifest_mapping(
        source.get("settings", normalized["settings"]), "settings"
    )
    _manifest_unknown_fields(settings, PROJECT_MANIFEST_SETTINGS_FIELDS, "settings")
    normalized["settings"].update(dict(settings))
    for key, default in DEFAULT_MANIFEST_SETTINGS.items():
        value = normalized["settings"][key]
        if not isinstance(value, type(default)):
            raise BuildValidationError(
                f"Manifest field settings.{key} has invalid type"
            )

    profiles = _manifest_mapping(
        source.get("profiles", normalized["profiles"]), "profiles"
    )
    normalized_profiles: dict[str, dict[str, Any]] = {}
    for name, raw_profile in profiles.items():
        profile = _manifest_mapping(raw_profile, f"profiles.{name}")
        _manifest_unknown_fields(
            profile, PROJECT_MANIFEST_PROFILE_FIELDS, f"profiles.{name}"
        )
        base = copy.deepcopy(DEFAULT_PROFILES.get(str(name), {}))
        base.update(dict(profile))
        if not isinstance(base.get("toolchain_versions", {}), Mapping):
            raise BuildValidationError(
                f"Manifest field profiles.{name}.toolchain_versions must be an object"
            )
        if not isinstance(base.get("extra_args", []), (list, tuple)):
            raise BuildValidationError(
                f"Manifest field profiles.{name}.extra_args must be an array"
            )
        for path_key in (
            "dependency_lockfile",
            "dependency_cache_dir",
            "dependency_mirror",
        ):
            if base.get(path_key) is not None and not isinstance(base[path_key], str):
                raise BuildValidationError(
                    f"Manifest field profiles.{name}.{path_key} must be a string or null"
                )
        base["extra_args"] = [str(item) for item in base.get("extra_args", [])]
        base["toolchain_versions"] = {
            str(key): str(value)
            for key, value in dict(base.get("toolchain_versions", {})).items()
        }
        normalized_profiles[str(name)] = base
    if not normalized_profiles:
        normalized_profiles = copy.deepcopy(DEFAULT_PROFILES)
    normalized["profiles"] = normalized_profiles

    history = source.get("history", normalized["history"])
    if not isinstance(history, list):
        raise BuildValidationError("Manifest field history must be an array")
    normalized_history: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(history):
        entry = _manifest_mapping(raw_entry, f"history[{index}]")
        _manifest_unknown_fields(entry, PROJECT_MANIFEST_HISTORY_FIELDS, f"history[{index}]")
        normalized_history.append(dict(entry))
    normalized["history"] = normalized_history

    analytics = _manifest_mapping(
        source.get("analytics", normalized["analytics"]), "analytics"
    )
    _manifest_unknown_fields(analytics, PROJECT_MANIFEST_ANALYTICS_FIELDS, "analytics")
    normalized["analytics"].update(dict(analytics))
    if not isinstance(normalized["analytics"]["enabled"], bool):
        raise BuildValidationError("Manifest field analytics.enabled must be boolean")
    if not isinstance(normalized["analytics"]["scope"], str):
        raise BuildValidationError("Manifest field analytics.scope must be a string")
    if not isinstance(normalized["analytics"]["database"], str):
        raise BuildValidationError("Manifest field analytics.database must be a string")
    return normalized


def project_manifest_backup_path(path: os.PathLike[str] | str) -> Path:
    """Return the recoverable backup path for a project manifest."""

    destination = Path(path).expanduser()
    return destination.with_name(f"{destination.name}.bak")


def save_project_manifest(
    path: os.PathLike[str] | str, manifest: Mapping[str, Any]
) -> Path:
    """Lock, validate, back up, and atomically save a project manifest."""

    destination = Path(path).expanduser()
    normalized = validate_project_manifest(manifest)
    encoded = json.dumps(normalized, indent=2, sort_keys=True).encode("utf-8")
    with state_lock(destination):
        _atomic_backup(destination)
        _atomic_write_bytes(destination, encoded)
    return destination


def update_project_manifest(
    path: os.PathLike[str] | str,
    updater: Callable[[dict[str, Any]], Mapping[str, Any] | None],
    expected_scope: str | None = None,
) -> ManifestLoadResult:
    """Apply one manifest-field update while holding the cross-process lock."""

    destination = Path(path).expanduser()
    if not destination.is_file():
        migrate_project_manifest(destination, scope=expected_scope or "user")
    with state_lock(destination):
        if destination.is_file():
            try:
                current = validate_project_manifest(
                    json.loads(destination.read_text(encoding="utf-8-sig")),
                    expected_scope=expected_scope,
                )
            except (OSError, ValueError, BuildValidationError) as error:
                backup = project_manifest_backup_path(destination)
                try:
                    current = validate_project_manifest(
                        json.loads(backup.read_text(encoding="utf-8-sig")),
                        expected_scope=expected_scope,
                    )
                    _atomic_write_bytes(
                        destination,
                        json.dumps(current, indent=2, sort_keys=True).encode("utf-8"),
                    )
                except (OSError, ValueError, BuildValidationError) as backup_error:
                    raise BuildValidationError(
                        f"Could not load project manifest {destination}: {error}"
                    ) from backup_error
        else:
            current = default_project_manifest(
                expected_scope or "user",
                destination.parent if (expected_scope or "user") == "workspace" else None,
            )
        candidate = copy.deepcopy(current)
        updated = updater(candidate)
        normalized = validate_project_manifest(
            updated if updated is not None else candidate,
            expected_scope=expected_scope,
        )
        _atomic_backup(destination)
        _atomic_write_bytes(
            destination, json.dumps(normalized, indent=2, sort_keys=True).encode("utf-8")
        )
    return ManifestLoadResult(normalized)


def rollback_project_manifest(path: os.PathLike[str] | str) -> Path:
    """Restore the last valid manifest backup without deleting the backup."""

    destination = Path(path).expanduser()
    backup = project_manifest_backup_path(destination)
    try:
        candidate = json.loads(backup.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise BuildValidationError(
            f"Manifest backup is unavailable or invalid: {backup}"
        ) from error
    normalized = validate_project_manifest(candidate)
    with state_lock(destination):
        _atomic_write_bytes(
            destination, json.dumps(normalized, indent=2, sort_keys=True).encode("utf-8")
        )
    return destination


def _legacy_paths_for_manifest(destination: Path) -> tuple[Path, ...]:
    return tuple(
        destination.parent / name
        for name in ("profiles.yaml", "profiles.yml", "profiles.json", "settings.json", "history.json")
    )


_LEGACY_MANIFEST_KEY_MAP = {
    "console": "console",
    "admin": "admin",
    "singlefile": "single_file",
    "single_file": "single_file",
    "backend": "backend",
    "target": "target",
    "architecture": "architecture",
    "version": "version",
    "company": "company",
    "copyright": "copyright",
    "description": "description",
    "product": "product",
    "prefetch": "prefetch",
    "verify": "verify",
    "cache": "cache",
    "force": "force",
    "extraargs": "extra_args",
    "extra_args": "extra_args",
    "toolchainversions": "toolchain_versions",
    "toolchain_versions": "toolchain_versions",
    "upx": "upx",
    "timeoutseconds": "timeout_seconds",
    "timeout_seconds": "timeout_seconds",
    "maxoutputbytes": "max_output_bytes",
    "max_output_bytes": "max_output_bytes",
    "allownetwork": "allow_network",
    "allow_network": "allow_network",
    "allowdependencyinstall": "allow_dependency_install",
    "allow_dependency_install": "allow_dependency_install",
    "dependencylockfile": "dependency_lockfile",
    "dependency_lockfile": "dependency_lockfile",
    "dependencycachedir": "dependency_cache_dir",
    "dependency_cache_dir": "dependency_cache_dir",
    "dependencymirror": "dependency_mirror",
    "dependency_mirror": "dependency_mirror",
    "theme": "theme",
    "postbuildaction": "post_build_action",
    "post_build_action": "post_build_action",
    "postbuildcopypath": "post_build_copy_path",
    "post_build_copy_path": "post_build_copy_path",
    "shownotifications": "show_notifications",
    "show_notifications": "show_notifications",
    "autocheckupdates": "auto_check_updates",
    "auto_check_updates": "auto_check_updates",
    "maxrecentfiles": "max_recent_files",
    "max_recent_files": "max_recent_files",
    "maxhistoryitems": "max_history_items",
    "max_history_items": "max_history_items",
    "defaultprofile": "default_profile",
    "default_profile": "default_profile",
}


def _normalize_legacy_mapping(
    value: Mapping[str, Any], allowed: Iterable[str]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    normalized: dict[str, Any] = {}
    ignored: list[str] = []
    allowed_values = set(allowed)
    allowed_lower = {str(value).lower(): str(value) for value in allowed_values}
    for raw_key, raw_value in value.items():
        key_text = str(raw_key)
        lowered = key_text.lower().replace("-", "")
        key = _LEGACY_MANIFEST_KEY_MAP.get(
            lowered, allowed_lower.get(lowered, key_text)
        )
        if key in allowed_values:
            normalized[key] = raw_value
        else:
            ignored.append(key_text)
    return normalized, tuple(ignored)


def migrate_project_manifest(
    destination: os.PathLike[str] | str,
    legacy_paths: Sequence[os.PathLike[str] | str] | None = None,
    scope: str = "user",
) -> ManifestLoadResult:
    """Import currently supported YAML/JSON state into the canonical schema."""

    destination_path = Path(destination).expanduser()
    if destination_path.exists():
        return load_project_manifest(destination_path, expected_scope=scope)
    manifest = default_project_manifest(scope, destination_path.parent)
    candidates = tuple(
        Path(path).expanduser()
        for path in (legacy_paths or _legacy_paths_for_manifest(destination_path))
    )
    sources: list[Path] = []
    warnings: list[str] = []
    for source in candidates:
        if not source.is_file() or source == destination_path:
            continue
        try:
            if source.suffix.lower() in {".yaml", ".yml"}:
                imported_profiles = load_profiles(source, {})
                if imported_profiles:
                    manifest["profiles"] = {
                        str(name): _normalize_legacy_mapping(
                            profile, PROJECT_MANIFEST_PROFILE_FIELDS
                        )[0]
                        for name, profile in imported_profiles.items()
                        if isinstance(profile, Mapping)
                    }
            else:
                loaded = json.loads(source.read_text(encoding="utf-8-sig"))
                if source.name.lower().startswith("profiles") and isinstance(loaded, Mapping):
                    manifest["profiles"] = {
                        str(name): _normalize_legacy_mapping(
                            profile, PROJECT_MANIFEST_PROFILE_FIELDS
                        )[0]
                        for name, profile in loaded.items()
                        if isinstance(profile, Mapping)
                    }
                elif source.name.lower().startswith("settings") and isinstance(loaded, Mapping):
                    imported_settings, _ = _normalize_legacy_mapping(
                        loaded, PROJECT_MANIFEST_SETTINGS_FIELDS
                    )
                    manifest["settings"].update(imported_settings)
                elif source.name.lower().startswith("history") and isinstance(loaded, list):
                    manifest["history"] = [
                        _normalize_legacy_mapping(
                            entry, PROJECT_MANIFEST_HISTORY_FIELDS
                        )[0]
                        for entry in loaded
                        if isinstance(entry, Mapping)
                    ]
                elif isinstance(loaded, Mapping):
                    candidate = dict(loaded)
                    if "profiles" in candidate and isinstance(candidate["profiles"], Mapping):
                        manifest["profiles"] = {
                            str(name): _normalize_legacy_mapping(
                                profile, PROJECT_MANIFEST_PROFILE_FIELDS
                            )[0]
                            for name, profile in candidate["profiles"].items()
                            if isinstance(profile, Mapping)
                        }
                    if "settings" in candidate and isinstance(candidate["settings"], Mapping):
                        imported_settings, _ = _normalize_legacy_mapping(
                            candidate["settings"], PROJECT_MANIFEST_SETTINGS_FIELDS
                        )
                        manifest["settings"].update(imported_settings)
                    if "history" in candidate and isinstance(candidate["history"], list):
                        manifest["history"] = [
                            _normalize_legacy_mapping(
                                entry, PROJECT_MANIFEST_HISTORY_FIELDS
                            )[0]
                            for entry in candidate["history"]
                            if isinstance(entry, Mapping)
                        ]
            sources.append(source)
        except (OSError, ValueError, BuildValidationError) as error:
            warnings.append(f"Could not import {source.name}: {error}")
    normalized = validate_project_manifest(manifest, expected_scope=scope)
    if sources:
        save_project_manifest(destination_path, normalized)
        return ManifestLoadResult(
            normalized,
            migrated=True,
            warnings=tuple(warnings),
            source_paths=tuple(sources),
        )
    return ManifestLoadResult(normalized, warnings=tuple(warnings))


def load_project_manifest(
    path: os.PathLike[str] | str,
    expected_scope: str | None = None,
    legacy_paths: Sequence[os.PathLike[str] | str] | None = None,
) -> ManifestLoadResult:
    """Load a canonical manifest, recovering from backup or importing legacy state."""

    destination = Path(path).expanduser()
    scope = expected_scope or "user"
    if destination.is_file():
        try:
            loaded = json.loads(destination.read_text(encoding="utf-8-sig"))
            normalized = validate_project_manifest(loaded, expected_scope=expected_scope)
            return ManifestLoadResult(normalized)
        except (OSError, ValueError, BuildValidationError) as error:
            backup = project_manifest_backup_path(destination)
            if backup.is_file():
                try:
                    backup_loaded = json.loads(backup.read_text(encoding="utf-8-sig"))
                    recovered = validate_project_manifest(
                        backup_loaded, expected_scope=expected_scope
                    )
                    rollback_project_manifest(destination)
                    return ManifestLoadResult(
                        recovered,
                        recovered=True,
                        warnings=(f"Recovered invalid manifest from {backup.name}",),
                        source_paths=(backup,),
                    )
                except (OSError, ValueError, BuildValidationError):
                    pass
            raise BuildValidationError(
                f"Could not load project manifest {destination}: {error}"
            ) from error
    return migrate_project_manifest(destination, legacy_paths, scope=scope)


def sha256_file(path: os.PathLike[str] | str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


_DEPENDENCY_LOCK_FIELDS = frozenset(
    {"schema_version", "kind", "approved", "policy", "lockfiles", "toolchains"}
)
_DEPENDENCY_POLICY_FIELDS = frozenset(
    {"version", "network", "mirror", "cache_dir"}
)
_DEPENDENCY_LOCKFILE_FIELDS = frozenset({"path", "sha256", "manager"})
_DEPENDENCY_TOOLCHAIN_FIELDS = frozenset({"version", "sha256"})
_DEPENDENCY_MANAGERS = {
    "py": frozenset({"pip"}),
    "pyw": frozenset({"pip"}),
    "js": frozenset({"npm", "bun"}),
    "ts": frozenset({"npm", "bun"}),
    "go": frozenset({"go"}),
    "rs": frozenset({"cargo"}),
    "rb": frozenset({"bundle"}),
}


def _dependency_unknown_fields(
    value: Mapping[str, Any], allowed: Iterable[str], location: str
) -> None:
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise BuildValidationError(
            f"Unknown dependency lock field(s) at {location}: {', '.join(unknown)}"
        )


def _dependency_sha256(value: Any, location: str) -> str:
    text = str(value).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise BuildValidationError(
            f"Dependency lock field {location} must be a SHA-256 hex digest"
        )
    return text


def _dependency_has_hashes(manager: str, path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_bytes().decode("latin-1")
    if manager == "pip":
        return bool(re.search(r"--hash\s*=\s*sha(?:256|384|512):[0-9a-f]+", text, re.I))
    if manager in {"npm", "bun"}:
        if path.suffix.lower() == ".json":
            try:
                json.loads(text)
            except ValueError as error:
                raise BuildValidationError(
                    f"Dependency lock file is not valid JSON: {path}"
                ) from error
        return bool(re.search(r'"(?:integrity|checksum|sha(?:256|512))"\s*:', text, re.I))
    if manager == "go":
        return "h1:" in text
    if manager == "cargo":
        return bool(re.search(r"^\s*checksum\s*=", text, re.M))
    # Bundler lock files pin the complete dependency graph but do not carry
    # package hashes in the lock format, so the lockfile hash is the identity.
    return True


def dependency_lock_path(
    source: os.PathLike[str] | str, lockfile: os.PathLike[str] | str | None = None
) -> Path:
    """Return the explicit lockfile or the source-adjacent default path."""

    if lockfile:
        return Path(lockfile).expanduser()
    return Path(source).expanduser().resolve().parent / DEPENDENCY_LOCK_FILENAME


def load_dependency_lock(
    path: os.PathLike[str] | str,
    source_type: str | None = None,
    cache_dir: os.PathLike[str] | str | None = None,
    mirror: str | None = None,
    require_entry: bool = False,
) -> dict[str, Any]:
    """Validate an approved, hash-addressed dependency lock and snapshot it."""

    lock_path = Path(path).expanduser().resolve()
    if not lock_path.is_file():
        raise BuildValidationError(
            f"Approved dependency lock file not found: {lock_path}"
        )
    try:
        loaded = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise BuildValidationError(
            f"Could not read dependency lock file {lock_path}: {error}"
        ) from error
    if not isinstance(loaded, Mapping):
        raise BuildValidationError("Dependency lock must be a JSON object")
    _dependency_unknown_fields(loaded, _DEPENDENCY_LOCK_FIELDS, "<root>")
    if loaded.get("schema_version") != DEPENDENCY_LOCK_SCHEMA_VERSION:
        raise BuildValidationError(
            f"Unsupported dependency lock schema {loaded.get('schema_version')!r}; "
            f"expected {DEPENDENCY_LOCK_SCHEMA_VERSION}"
        )
    if loaded.get("kind") != DEPENDENCY_LOCK_KIND:
        raise BuildValidationError(
            f"Unsupported dependency lock kind {loaded.get('kind')!r}"
        )
    if loaded.get("approved") is not True:
        raise BuildValidationError(
            "Dependency prefetch requires an approved dependency lock"
        )

    raw_policy = loaded.get("policy")
    if not isinstance(raw_policy, Mapping):
        raise BuildValidationError("Dependency lock policy must be an object")
    _dependency_unknown_fields(raw_policy, _DEPENDENCY_POLICY_FIELDS, "policy")
    if raw_policy.get("version") != DEPENDENCY_POLICY_VERSION:
        raise BuildValidationError(
            f"Unsupported dependency policy {raw_policy.get('version')!r}; "
            f"expected {DEPENDENCY_POLICY_VERSION}"
        )
    network = str(raw_policy.get("network", "")).lower()
    if network not in {"offline", "online"}:
        raise BuildValidationError(
            "Dependency lock policy.network must be 'offline' or 'online'"
        )
    raw_mirror = raw_policy.get("mirror")
    if raw_mirror is not None and not isinstance(raw_mirror, str):
        raise BuildValidationError("Dependency lock policy.mirror must be a string or null")
    effective_mirror = str(mirror or raw_mirror or "").strip() or None
    if network == "online" and not effective_mirror:
        raise BuildValidationError(
            "Online dependency policy requires an explicit package mirror"
        )
    if network == "offline" and mirror:
        raise BuildValidationError(
            "An online mirror cannot override an offline dependency policy"
        )
    if effective_mirror and not effective_mirror.startswith(("https://", "file://")):
        raise BuildValidationError(
            "Dependency mirror must use https:// or file://"
        )
    raw_cache = raw_policy.get("cache_dir")
    if not isinstance(raw_cache, str) or not raw_cache.strip():
        raise BuildValidationError(
            "Dependency lock policy requires an explicit cache_dir"
        )
    effective_cache = Path(cache_dir).expanduser() if cache_dir else Path(raw_cache)
    if not effective_cache.is_absolute():
        effective_cache = lock_path.parent / effective_cache
    effective_cache = effective_cache.resolve()
    policy = {
        "version": DEPENDENCY_POLICY_VERSION,
        "network": network,
        "mirror": effective_mirror,
        "cache_dir": str(effective_cache),
    }

    raw_lockfiles = loaded.get("lockfiles")
    if not isinstance(raw_lockfiles, Mapping):
        raise BuildValidationError("Dependency lock lockfiles must be an object")
    selected: dict[str, Any] | None = None
    selected_type = (source_type or "").lower().lstrip(".")
    if selected_type == "pyw":
        selected_type = "py"
    if selected_type:
        raw_entry = raw_lockfiles.get(selected_type)
        if not isinstance(raw_entry, Mapping):
            if require_entry:
                raise BuildValidationError(
                    f"Dependency lock has no entry for source type .{selected_type}"
                )
        else:
            _dependency_unknown_fields(raw_entry, _DEPENDENCY_LOCKFILE_FIELDS, f"lockfiles.{selected_type}")
            relative_path = raw_entry.get("path")
            if not isinstance(relative_path, str) or not relative_path.strip():
                raise BuildValidationError(
                    f"Dependency lock entry .{selected_type}.path is required"
                )
            dependency_path = Path(relative_path).expanduser()
            if not dependency_path.is_absolute():
                dependency_path = lock_path.parent / dependency_path
            dependency_path = dependency_path.resolve()
            if not dependency_path.is_file():
                raise BuildValidationError(
                    f"Dependency lock input is missing: {dependency_path}"
                )
            expected_hash = _dependency_sha256(
                raw_entry.get("sha256"), f"lockfiles.{selected_type}.sha256"
            )
            actual_hash = sha256_file(dependency_path)
            if actual_hash != expected_hash:
                raise BuildValidationError(
                    f"Dependency lock hash mismatch for {dependency_path}: "
                    f"expected {expected_hash}, got {actual_hash}"
                )
            manager = str(raw_entry.get("manager", "")).lower()
            allowed_managers = _DEPENDENCY_MANAGERS.get(selected_type, frozenset())
            if manager not in allowed_managers:
                expected = ", ".join(sorted(allowed_managers)) or "none"
                raise BuildValidationError(
                    f"Unsupported dependency manager {manager!r} for .{selected_type}; "
                    f"expected {expected}"
                )
            if manager != "bundle" and not _dependency_has_hashes(manager, dependency_path):
                raise BuildValidationError(
                    f"Dependency lock input has no verifiable package hashes: {dependency_path}"
                )
            selected = {
                "path": str(dependency_path),
                "sha256": actual_hash,
                "manager": manager,
                "hashes_verified": manager == "bundle"
                or _dependency_has_hashes(manager, dependency_path),
            }

    raw_toolchains = loaded.get("toolchains", {})
    if not isinstance(raw_toolchains, Mapping):
        raise BuildValidationError("Dependency lock toolchains must be an object")
    toolchains: dict[str, dict[str, str]] = {}
    for name, raw_toolchain in raw_toolchains.items():
        if isinstance(raw_toolchain, str):
            raw_toolchain = {"version": raw_toolchain}
        if not isinstance(raw_toolchain, Mapping):
            raise BuildValidationError(f"Dependency lock toolchain {name} must be an object")
        _dependency_unknown_fields(raw_toolchain, _DEPENDENCY_TOOLCHAIN_FIELDS, f"toolchains.{name}")
        version = raw_toolchain.get("version")
        if not isinstance(version, str) or not version.strip():
            raise BuildValidationError(f"Dependency lock toolchain {name} needs a version")
        record = {"version": version.strip()}
        if raw_toolchain.get("sha256") is not None:
            record["sha256"] = _dependency_sha256(
                raw_toolchain["sha256"], f"toolchains.{name}.sha256"
            )
        toolchains[str(name)] = record

    return {
        "schema_version": DEPENDENCY_LOCK_SCHEMA_VERSION,
        "kind": DEPENDENCY_LOCK_KIND,
        "status": "locked",
        "approved": True,
        "lockfile": str(lock_path),
        "lockfile_sha256": sha256_file(lock_path),
        "source_type": selected_type or None,
        "dependency": selected,
        "policy": policy,
        "toolchains": toolchains,
    }


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
    correlation_id: str | None = None,
    phase: str = "command",
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
            correlation_id=correlation_id or "",
            phase=phase,
        )
    except OSError as error:
        return CommandResult(
            command=normalized,
            returncode=127,
            stderr=str(error),
            duration_seconds=time.monotonic() - started,
            correlation_id=correlation_id or "",
            phase=phase,
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


def _verified_backend_version(backend: str) -> str | None:
    """Probe a CLI tool's version without installing or invoking build inputs."""

    executable = resolve_backend_executable(backend)
    if not executable or backend in {"iexpress", "ahk2exe", "csc"}:
        return None
    command: tuple[str, ...]
    if backend == "nuitka":
        command = (sys.executable, "-m", "nuitka", "--version")
    elif backend == "ps2exe":
        command = (
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "(Get-Module -ListAvailable ps2exe | Sort-Object Version -Descending | Select-Object -First 1 -ExpandProperty Version)",
        )
    else:
        command = (executable, "--version")
    try:
        result = run_command(command, timeout=10)
    except (BuildValidationError, OSError, ValueError):
        return None
    if not result.success:
        return None
    first_line = next((line.strip() for line in result.output.splitlines() if line.strip()), "")
    return redact_text(first_line) or None


def backend_status(
    adapters: Sequence[AdapterDescriptor] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the schema-versioned capability and availability catalog."""

    status: dict[str, dict[str, Any]] = {}
    host_platform = "windows" if os.name == "nt" else sys.platform
    for backend, spec in BACKEND_CATALOG.items():
        path = resolve_backend_executable(backend)
        status[backend] = {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "backend": backend,
            "name": spec["name"],
            "lifecycle": spec["status"],
            "available": path is not None,
            "executable": path,
            "extensions": list(spec["extensions"]),
            "host_platforms": list(spec["host_platforms"]),
            "host_supported": host_platform in spec["host_platforms"],
            "target_platforms": list(spec["target_platforms"]),
            "architectures": list(spec["architectures"]),
            "required_sdks": list(spec["required_sdks"]),
            "default": backend != "pkg",
            "verified_version": _verified_backend_version(backend),
        }
    for adapter in adapter_catalog(adapters).values():
        if not adapter.external:
            continue
        identity: Mapping[str, Any] = {}
        if adapter.tool_identity is not None:
            try:
                identity = dict(adapter.tool_identity())
            except Exception:
                identity = {}
        status[adapter.backend_id] = {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "backend": adapter.backend_id,
            "name": adapter.name,
            "namespace": adapter.namespace,
            "lifecycle": adapter.lifecycle,
            "available": bool(identity.get("available", True)),
            "executable": identity.get("executable"),
            "extensions": list(adapter.extensions),
            "host_platforms": list(adapter.host_platforms),
            "host_supported": host_platform in adapter.host_platforms,
            "target_platforms": list(adapter.target_platforms),
            "architectures": list(adapter.architectures),
            "required_sdks": list(adapter.required_sdks),
            "default": adapter.lifecycle != "deprecated",
            "verified_version": identity.get("version"),
            "adapter_api": adapter.api_version,
            "adapter_policy": ADAPTER_POLICY_VERSION,
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
        adapters: Sequence[AdapterDescriptor] | None = None,
    ) -> None:
        self.runner = runner
        self.require_available = require_available
        self.policy = policy or ExecutionPolicy()
        self.adapters = adapter_catalog(adapters)

    def _policy_for_request(self, request: BuildRequest) -> ExecutionPolicy:
        return self.policy.for_request(request)

    def _run(
        self,
        request: BuildRequest,
        command: Sequence[str],
        cwd: os.PathLike[str] | str | None = None,
        environment: Mapping[str, str] | None = None,
        phase: str = "command",
    ) -> CommandResult:
        """Run a compiler command through the configured execution policy."""

        policy = self._policy_for_request(request)
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "environment": environment,
            "timeout": policy.timeout_seconds,
            "correlation_id": request.correlation_id,
            "phase": phase,
        }
        if self.runner is run_command:
            kwargs["policy"] = policy
            kwargs["stop_event"] = request.cancel_event
        else:
            kwargs.pop("correlation_id", None)
            kwargs.pop("phase", None)
        result = self.runner(command, **kwargs)
        return replace(
            result,
            correlation_id=request.correlation_id,
            phase=phase,
        )

    def choose_backend(self, file_type: str, requested: str = "auto") -> str | None:
        normalized_type = file_type.lower().lstrip(".")
        choices = tuple(
            adapter.backend_id
            for adapter in self.adapters.values()
            if normalized_type in adapter.extensions
        )
        if requested != "auto":
            return requested if requested in choices else None
        auto_choices = tuple(
            backend
            for backend in choices
            if self.adapters[backend].lifecycle != "deprecated"
        )
        for backend in auto_choices:
            adapter = self.adapters[backend]
            available = resolve_backend_executable(backend) is not None
            if adapter.external and adapter.tool_identity is not None:
                try:
                    available = bool(dict(adapter.tool_identity()).get("available", True))
                except Exception:
                    available = False
            if available:
                return backend
        return auto_choices[0] if auto_choices else None

    def _validate_capability(self, request: BuildRequest, backend: str) -> None:
        adapter = self.adapters.get(backend)
        if adapter is None:
            raise BuildValidationError(f"Backend is not in the capability catalog: {backend}")
        host_platform = "windows" if os.name == "nt" else sys.platform
        if host_platform not in adapter.host_platforms:
            raise BuildValidationError(
                f"Backend {backend} is not supported on host platform {host_platform}"
            )
        target = request.target.lower()
        target_platforms = adapter.target_platforms
        if target not in {"native", "auto"} and not (
            target in target_platforms
            or any(
                f"-{platform}-" in target
                or target.startswith(f"{platform}-")
                or target.endswith(f"-{platform}")
                for platform in target_platforms
                if platform not in {"native", "wasm"}
            )
        ):
            raise BuildValidationError(
                f"Backend {backend} does not support target {request.target}; "
                f"supported targets: {', '.join(target_platforms)}"
            )
        architecture = request.architecture.lower()
        architectures = adapter.architectures
        if architecture not in {"native", "auto"} and architecture not in architectures:
            raise BuildValidationError(
                f"Backend {backend} does not support architecture {request.architecture}; "
                f"supported architectures: {', '.join(architectures)}"
            )

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
            result = self._run(request, command, phase="toolchain-probe")
            if not result.success or str(expected) not in result.output:
                actual = result.output.splitlines()[0] if result.output else "unknown"
                raise BuildValidationError(
                    f"Toolchain pin mismatch for {backend}: expected {expected}, got {actual}"
                )

    def _validate(
        self, request: BuildRequest, allow_missing_source: bool = False
    ) -> BuildRequest:
        normalized = request.normalized()
        if not normalized.file_type:
            suffix = normalized.source.suffix.lower().lstrip(".")
            adapter_extensions = {
                extension
                for adapter in self.adapters.values()
                for extension in adapter.extensions
            }
            if suffix in adapter_extensions:
                normalized = replace(normalized, file_type=suffix)
            else:
                detected_type = _detect_adapter_file_type(self.adapters, normalized.source)
                if detected_type:
                    normalized = replace(normalized, file_type=detected_type)
        supported_extensions = {
            extension
            for adapter in self.adapters.values()
            for extension in adapter.extensions
        }
        if not normalized.file_type or normalized.file_type not in supported_extensions:
            raise BuildValidationError(
                f"Unsupported source type: {normalized.source.suffix or '<none>'}"
            )
        if not allow_missing_source and not normalized.source.is_file():
            raise BuildValidationError(f"Source file not found: {normalized.source}")
        if normalized.icon and not normalized.icon.is_file():
            raise BuildValidationError(f"Icon file not found: {normalized.icon}")
        backend = self.choose_backend(normalized.file_type, normalized.backend)
        if not backend:
            if normalized.backend != "auto":
                raise BuildValidationError(
                    f"Backend {normalized.backend} is not compatible with .{normalized.file_type}"
                )
            raise BuildValidationError(f"No backend supports .{normalized.file_type}")
        self._validate_capability(normalized, backend)
        if normalized.backend == "auto" or normalized.backend != backend:
            normalized = replace(normalized, backend=backend)
        if self.require_available:
            adapter = self.adapters[backend]
            available = resolve_backend_executable(backend) is not None
            if adapter.external and adapter.tool_identity is not None:
                try:
                    available = bool(dict(adapter.tool_identity()).get("available", True))
                except Exception:
                    available = False
            if not available:
                raise BuildValidationError(f"Compiler backend is not installed: {backend}")
        if not allow_missing_source and normalized.toolchain_versions:
            self._validate_toolchain_versions(normalized)
        return normalized

    def _tool_identity(self, backend: str) -> dict[str, Any]:
        adapter = self.adapters.get(backend)
        if adapter and adapter.external and adapter.tool_identity is not None:
            try:
                return {
                    "backend": backend,
                    **{
                        str(key): redact_text(str(value))
                        for key, value in dict(adapter.tool_identity()).items()
                    },
                }
            except Exception as error:
                return {"backend": backend, "error": redact_text(str(error))}
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

    def _dependency_snapshot(self, request: BuildRequest) -> dict[str, Any]:
        lock_path = dependency_lock_path(
            request.source, request.dependency_lockfile
        )
        if not lock_path.is_file() and not request.prefetch and not request.dependency_lockfile:
            return {
                "schema_version": DEPENDENCY_LOCK_SCHEMA_VERSION,
                "status": "not-requested",
                "policy_version": DEPENDENCY_POLICY_VERSION,
            }
        snapshot = load_dependency_lock(
            lock_path,
            source_type=request.file_type,
            cache_dir=request.dependency_cache_dir,
            mirror=request.dependency_mirror,
            require_entry=request.prefetch,
        )
        if request.prefetch and snapshot["policy"]["network"] == "online" and not request.allow_network:
            raise BuildValidationError(
                "Online dependency policy requires explicit --allow-network"
            )
        return snapshot

    def _cache_key(
        self,
        request: BuildRequest,
        source_hash: str,
        backend: str,
        dependency_snapshot: Mapping[str, Any],
    ) -> str:
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
                "dependency_snapshot": dependency_snapshot,
                "policy_version": DEPENDENCY_POLICY_VERSION,
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
        dependency_snapshot: Mapping[str, Any],
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
                "dependency_snapshot": dict(dependency_snapshot),
            },
        )

    def _write_artifact_manifest(
        self,
        request: BuildRequest,
        plan: BuildPlan,
        source_hash: str,
        cache_key: str,
        verification: VerificationResult | None,
        dependency_snapshot: Mapping[str, Any],
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
            "dependencies": dict(dependency_snapshot),
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
        adapter = self.adapters[backend]
        if adapter.external:
            try:
                adapter_plan = adapter.plan(
                    request,
                    {
                        "api_version": ADAPTER_API_VERSION,
                        "backend": adapter.backend_id,
                        "source": source,
                        "output": output,
                        "engine": self,
                    },
                )
            except (BuildValidationError, OSError, ValueError):
                raise
            except Exception as error:
                raise BuildValidationError(
                    f"Adapter {adapter.backend_id} planner failed: {error}"
                ) from error
            if not isinstance(adapter_plan, BuildPlan):
                raise BuildValidationError(
                    f"Adapter {adapter.backend_id} planner did not return BuildPlan"
                )
            if adapter_plan.backend != adapter.backend_id or not adapter_plan.command:
                raise BuildValidationError(
                    f"Adapter {adapter.backend_id} returned an invalid BuildPlan"
                )
            if len(adapter_plan.cleanup_paths) > MAX_CLEANUP_PATHS:
                raise BuildValidationError(
                    f"Adapter {adapter.backend_id} returned too many cleanup paths"
                )
            return adapter_plan
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

    def prefetch_dependencies(
        self,
        request: BuildRequest,
        dependency_snapshot: Mapping[str, Any] | None = None,
    ) -> list[CommandResult]:
        """Run one approved, hash-addressed dependency operation."""

        if not request.allow_network or not request.allow_dependency_install:
            raise BuildValidationError(
                "Dependency prefetch requires both --allow-network and "
                "--allow-dependency-install"
            )
        source = request.source.resolve()
        root = source.parent
        snapshot = dependency_snapshot or self._dependency_snapshot(request)
        dependency = snapshot.get("dependency")
        if not isinstance(dependency, Mapping):
            raise BuildValidationError(
                f"Dependency lock has no usable entry for .{request.file_type}"
            )
        manager = str(dependency.get("manager", "")).lower()
        lock_input = Path(str(dependency["path"]))
        cache_dir = Path(str(snapshot["policy"]["cache_dir"]))
        cache_dir.mkdir(parents=True, exist_ok=True)
        offline = snapshot["policy"]["network"] == "offline"
        environment: dict[str, str] = {}

        if manager == "pip":
            command: tuple[str, ...] = (
                sys.executable,
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--no-deps",
            )
            if offline:
                command += ("--no-index", "--find-links", str(cache_dir))
            else:
                command += ("--index-url", str(snapshot["policy"]["mirror"]))
            command += ("-r", str(lock_input))
        elif manager in {"npm", "bun"}:
            executable = _find_first((manager,))
            if not executable:
                raise BuildValidationError(
                    f"Required dependency manager is unavailable: {manager}"
                )
            if lock_input.parent != root:
                raise BuildValidationError(
                    f"{manager} lock input must be beside the source: {lock_input}"
                )
            command = (executable, "ci", "--ignore-scripts", "--no-audit", "--no-fund")
            if manager == "bun":
                command = (executable, "install", "--frozen-lockfile")
                if offline:
                    command += ("--offline",)
            elif offline:
                command += ("--offline",)
            else:
                command += ("--registry", str(snapshot["policy"]["mirror"]))
            command += ("--cache", str(cache_dir))
        elif manager == "go":
            executable = _find_first(("go",))
            if not executable:
                raise BuildValidationError("Required dependency manager is unavailable: go")
            command = (executable, "mod", "download")
            environment["GOMODCACHE"] = str(cache_dir)
            environment["GOPROXY"] = "off" if offline else str(snapshot["policy"]["mirror"])
            if offline:
                environment["GOSUMDB"] = "off"
        elif manager == "cargo":
            executable = _find_first(("cargo",))
            if not executable:
                raise BuildValidationError("Required dependency manager is unavailable: cargo")
            command = (executable, "fetch", "--locked")
            if offline:
                command += ("--offline",)
            environment["CARGO_HOME"] = str(cache_dir)
        elif manager == "bundle":
            executable = _find_first(("bundle",))
            if not executable:
                raise BuildValidationError("Required dependency manager is unavailable: bundle")
            command = (executable, "install", "--deployment", "--path", str(cache_dir))
            if offline:
                command += ("--local",)
            else:
                environment["BUNDLE_MIRROR__HTTPS://RUBYGEMS__ORG"] = str(
                    snapshot["policy"]["mirror"]
                )
        else:
            raise BuildValidationError(f"Unsupported dependency manager: {manager}")
        return [
            self._run(
                request,
                command,
                cwd=root,
                environment=environment,
                phase="dependency-prefetch",
            )
        ]

    def _staged_request(self, request: BuildRequest) -> tuple[BuildRequest, Path]:
        """Create a per-build staging directory on the output volume."""

        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", request.output.stem)[:32] or "artifact"
        stage_root = Path(
            tempfile.mkdtemp(
                prefix=f".uc-stage-{stem}-",
                dir=str(request.output.parent),
            )
        )
        return replace(request, output=stage_root / request.output.name), stage_root

    def _build_locked(self, request: BuildRequest, started: float) -> BuildResult:
        plan: BuildPlan | None = None
        stage_root: Path | None = None
        try:
            normalized = self._validate(request)
            normalized = replace(
                normalized,
                source=normalized.source.resolve(),
                output=normalized.output.resolve(),
            )
            normalized.output.parent.mkdir(parents=True, exist_ok=True)
            if normalized.cancel_event and normalized.cancel_event.is_set():
                return BuildResult(
                    False,
                    "cancelled",
                    normalized,
                    normalized.output,
                    normalized.backend,
                    message="Build cancelled before execution",
                    duration_seconds=time.monotonic() - started,
                )
            dependency_snapshot = self._dependency_snapshot(normalized)
            source_hash = sha256_file(normalized.source)
            cache_key = self._cache_key(
                normalized,
                source_hash,
                normalized.backend,
                dependency_snapshot,
            )
            if self._cache_hit(normalized, cache_key):
                verification = (
                    verify_artifact(normalized.output) if normalized.verify else None
                )
                if verification is None or verification.passed:
                    manifest_path = artifact_manifest_path(normalized.output)
                    if not manifest_path.is_file():
                        cache_plan = BuildPlan(
                            command=(),
                            cwd=normalized.source.parent,
                            backend=normalized.backend,
                        )
                        manifest_path = self._write_artifact_manifest(
                            normalized,
                            cache_plan,
                            source_hash,
                            cache_key,
                            verification,
                            dependency_snapshot,
                        )
                    return BuildResult(
                        True,
                        "cache-hit",
                        normalized,
                        normalized.output,
                        normalized.backend,
                        verification=verification,
                        source_hash=source_hash,
                        cache_key=cache_key,
                        message="Build cache hit",
                        duration_seconds=time.monotonic() - started,
                        manifest=manifest_path,
                    )

            staged_request, stage_root = self._staged_request(normalized)
            plan = self.plan(staged_request)
            commands: list[CommandResult] = []
            if normalized.prefetch:
                commands.extend(
                    self.prefetch_dependencies(normalized, dependency_snapshot)
                )
                failed_prefetch = next(
                    (result for result in commands if not result.success), None
                )
                if failed_prefetch:
                    status = "cancelled" if failed_prefetch.cancelled else "failed"
                    return BuildResult(
                        False,
                        status,
                        normalized,
                        normalized.output,
                        plan.backend,
                        commands=commands,
                        source_hash=source_hash,
                        cache_key=cache_key,
                        message=(
                            "Build cancelled during dependency prefetch"
                            if failed_prefetch.cancelled
                            else f"Dependency prefetch failed: {failed_prefetch.output}"
                        ),
                        duration_seconds=time.monotonic() - started,
                    )
            result = self._run(
                staged_request,
                plan.command,
                cwd=plan.cwd,
                environment=plan.environment,
                phase="compile",
            )
            commands.append(result)
            if not result.success:
                status = "cancelled" if result.cancelled else "failed"
                return BuildResult(
                    False,
                    status,
                    normalized,
                    normalized.output,
                    plan.backend,
                    commands=commands,
                    source_hash=source_hash,
                    cache_key=cache_key,
                    message=(
                        "Build cancelled"
                        if result.cancelled
                        else result.output or "Compiler command failed"
                    ),
                    duration_seconds=time.monotonic() - started,
                )
            if normalized.cancel_event and normalized.cancel_event.is_set():
                return BuildResult(
                    False,
                    "cancelled",
                    normalized,
                    normalized.output,
                    plan.backend,
                    commands=commands,
                    source_hash=source_hash,
                    cache_key=cache_key,
                    message="Build cancelled before publication",
                    duration_seconds=time.monotonic() - started,
                )
            if not staged_request.output.exists():
                candidate = next(
                    (path for path in plan.artifact_candidates if path.is_file()), None
                )
                if candidate and candidate != staged_request.output:
                    shutil.copy2(candidate, staged_request.output)
            if not staged_request.output.is_file():
                return BuildResult(
                    False,
                    "failed",
                    normalized,
                    normalized.output,
                    plan.backend,
                    commands=commands,
                    source_hash=source_hash,
                    cache_key=cache_key,
                    message="Compiler completed without producing an artifact",
                    duration_seconds=time.monotonic() - started,
                )
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
                    staged_request,
                    (upx, "--best", "--lzma", str(staged_request.output)),
                    cwd=staged_request.output.parent,
                    phase="postprocess",
                )
                commands.append(compression)
                if not compression.success:
                    status = "cancelled" if compression.cancelled else "failed"
                    return BuildResult(
                        False,
                        status,
                        normalized,
                        normalized.output,
                        plan.backend,
                        commands=commands,
                        source_hash=source_hash,
                        cache_key=cache_key,
                        message=(
                            "Build cancelled during UPX compression"
                            if compression.cancelled
                            else f"UPX compression failed: {compression.output}"
                        ),
                        duration_seconds=time.monotonic() - started,
                    )

            publication_verification = verify_artifact(staged_request.output)
            if not publication_verification.passed:
                return BuildResult(
                    False,
                    "failed",
                    normalized,
                    normalized.output,
                    plan.backend,
                    commands=commands,
                    verification=publication_verification,
                    source_hash=source_hash,
                    cache_key=cache_key,
                    message=(
                        "Artifact verification failed before publication: "
                        f"{publication_verification.details}"
                    ),
                    duration_seconds=time.monotonic() - started,
                )
            if normalized.cancel_event and normalized.cancel_event.is_set():
                return BuildResult(
                    False,
                    "cancelled",
                    normalized,
                    normalized.output,
                    plan.backend,
                    commands=commands,
                    verification=publication_verification,
                    source_hash=source_hash,
                    cache_key=cache_key,
                    message="Build cancelled before publication",
                    duration_seconds=time.monotonic() - started,
                )
            os.replace(staged_request.output, normalized.output)
            verification = publication_verification if normalized.verify else None
            self._save_cache(
                normalized,
                cache_key,
                source_hash,
                plan.backend,
                verification,
                dependency_snapshot,
            )
            manifest_path = self._write_artifact_manifest(
                normalized,
                plan,
                source_hash,
                cache_key,
                verification,
                dependency_snapshot,
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
            output = Path(request.output).expanduser()
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
            cleanup = list(plan.cleanup_paths) if plan else []
            if stage_root:
                cleanup.append(stage_root)
            _cleanup_paths(cleanup)

    def build(self, request: BuildRequest) -> BuildResult:
        """Build under an output lock and publish only a verified staged artifact."""

        started = time.monotonic()
        normalized = request.normalized()
        with _output_lock(normalized.output):
            return self._build_locked(request, started)

    def build_batch(
        self, requests: Sequence[BuildRequest], workers: int = 1
    ) -> list[BuildResult]:
        """Compile independent requests in parallel while retaining input order."""

        normalized_requests = [request.normalized() for request in requests]
        output_groups: dict[str, list[int]] = {}
        for index, request in enumerate(normalized_requests):
            output_groups.setdefault(_output_lock_key(request.output), []).append(index)
        collisions = [
            (key, indexes) for key, indexes in output_groups.items() if len(indexes) > 1
        ]
        if collisions:
            details = "; ".join(
                f"{normalized_requests[indexes[0]].output} ({len(indexes)} requests)"
                for _, indexes in collisions
            )
            return [
                BuildResult(
                    False,
                    "collision",
                    request,
                    request.output,
                    request.backend,
                    message=f"Output collision detected before execution: {details}",
                )
                for request in normalized_requests
            ]
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

        return self.build_batch(
            self.matrix_requests(request, architectures),
            workers=workers,
        )

    def matrix_requests(
        self,
        request: BuildRequest,
        architectures: Sequence[str],
    ) -> list[BuildRequest]:
        """Expand a matrix without executing it, preserving collision checks."""

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
        return requests

    def watch(
        self,
        request: BuildRequest,
        interval: float = 1.0,
        stop_event: threading.Event | None = None,
        debounce: float = 0.35,
    ) -> Iterable[BuildResult]:
        """Yield an initial build and coalesced, cancellable source changes."""

        poll_interval = max(0.05, float(interval))
        debounce_seconds = max(0.05, float(debounce))
        event = stop_event or request.cancel_event or threading.Event()
        watched_request = replace(request, cancel_event=event)
        previous: str | None = None
        observed: str | None = None
        pending_since: float | None = None
        while not event.is_set():
            try:
                current = sha256_file(watched_request.source)
            except OSError:
                current = None
            now = time.monotonic()
            if current != observed:
                observed = current
                pending_since = (
                    now - debounce_seconds if previous is None else now
                )
            if (
                current
                and current != previous
                and pending_since is not None
                and now - pending_since >= debounce_seconds
            ):
                result = self.build(watched_request)
                previous = current
                pending_since = None
                yield result
            event.wait(poll_interval)


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
        with manifest_path.open("r", encoding="utf-8-sig") as handle:
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


def _release_source_materials(
    source_root: Path, excluded_root: Path | None = None
) -> list[dict[str, Any]]:
    if not source_root.is_dir():
        raise BuildValidationError(f"Release source root is not a directory: {source_root}")
    excluded = excluded_root.resolve() if excluded_root else None
    ignored_parts = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    materials: list[dict[str, Any]] = []
    for candidate in sorted(source_root.rglob("*")):
        if not candidate.is_file() or any(part in ignored_parts for part in candidate.parts):
            continue
        resolved = candidate.resolve()
        if excluded and _path_is_within(resolved, excluded):
            continue
        relative = candidate.relative_to(source_root).as_posix()
        materials.append(
            {
                "uri": f"workspace:{relative}",
                "digest": {"sha256": sha256_file(candidate)},
                "size_bytes": candidate.stat().st_size,
            }
        )
        if len(materials) > 8192:
            raise BuildValidationError("Release source root contains too many files")
    return materials


def _release_file_record(path: Path, kind: str) -> dict[str, Any]:
    return {
        "name": path.name,
        "kind": kind,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def release_bundle(
    artifacts: Sequence[os.PathLike[str] | str],
    output_dir: os.PathLike[str] | str,
    source_root: os.PathLike[str] | str | None = None,
    version: str = APP_VERSION,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Create unsigned local release evidence without publishing or signing."""

    if not artifacts:
        raise BuildValidationError("Release requires at least one --artifact")
    release_version = str(version).strip()
    if not release_version:
        raise BuildValidationError("Release version must not be empty")
    inputs = [Path(value).expanduser().resolve() for value in artifacts]
    names = [path.name for path in inputs]
    if len(set(names)) != len(names):
        raise BuildValidationError("Release artifacts must have unique file names")
    for path in inputs:
        if not path.is_file():
            raise BuildValidationError(f"Release artifact not found: {path}")

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    artifact_records: list[dict[str, Any]] = []
    verification_records: list[dict[str, Any]] = []
    copied_files: list[Path] = []
    for input_path in inputs:
        input_verification = verify_artifact(input_path)
        target = destination / input_path.name
        if input_path != target:
            shutil.copy2(input_path, target)
        else:
            target = input_path
        copied_files.append(target)
        sidecar = artifact_manifest_path(input_path)
        target_sidecar = artifact_manifest_path(target)
        if sidecar.is_file():
            try:
                sidecar_value = json.loads(sidecar.read_text(encoding="utf-8-sig"))
                if isinstance(sidecar_value, Mapping):
                    sidecar_value = copy.deepcopy(dict(sidecar_value))
                    artifact_value = dict(sidecar_value.get("artifact", {}))
                    artifact_value["path"] = str(target)
                    sidecar_value["artifact"] = artifact_value
                    save_json(target_sidecar, sidecar_value)
                else:
                    shutil.copy2(sidecar, target_sidecar)
            except (OSError, ValueError, TypeError):
                shutil.copy2(sidecar, target_sidecar)
            copied_files.append(target_sidecar)
        bundle_verification = verify_artifact(target)
        artifact_records.append(
            {
                "name": target.name,
                "sha256": sha256_file(target),
                "size_bytes": target.stat().st_size,
                "verification": asdict(bundle_verification),
                "unsigned": True,
            }
        )
        verification_records.append(
            {
                "artifact": target.name,
                "input": asdict(input_verification),
                "bundle": asdict(bundle_verification),
                "passed": input_verification.passed and bundle_verification.passed,
            }
        )

    report_path = destination / "verification-report.json"
    report = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "kind": "universal-compiler.verification-report",
        "passed": all(record["passed"] for record in verification_records),
        "artifacts": verification_records,
    }
    save_json(report_path, report)

    release_files = [path for path in copied_files if path.is_file()]
    sbom_components = [
        {
            "type": "file",
            "name": path.name,
            "hashes": [{"alg": "SHA-256", "content": sha256_file(path)}],
        }
        for path in release_files
    ]
    sbom_path = destination / "sbom.cdx.json"
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{_canonical_hash([item['name'] for item in artifact_records])[:32]}",
        "version": 1,
        "schema_version": SBOM_SCHEMA_VERSION,
        "metadata": {
            "component": {
                "type": "application",
                "name": APP_NAME,
                "version": release_version,
            }
        },
        "components": sbom_components,
    }
    save_json(sbom_path, sbom)

    root = Path(source_root).expanduser().resolve() if source_root else None
    materials = _release_source_materials(root, destination) if root else []
    for record in artifact_records:
        materials.append(
            {
                "uri": f"artifact:{record['name']}",
                "digest": {"sha256": record["sha256"]},
            }
        )
    provenance_path = destination / "provenance.json"
    provenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "kind": "https://slsa.dev/provenance/v1",
        "build_type": "universal-compiler.release-dry-run.v1",
        "builder": {
            "name": APP_NAME,
            "version": APP_VERSION,
            "python": platform.python_version(),
            "platform": sys.platform,
        },
        "invocation": {
            "version": release_version,
            "dry_run": bool(dry_run),
            "unsigned": True,
            "artifacts": [record["name"] for record in artifact_records],
        },
        "metadata": {
            "repository_revision": os.environ.get("GITHUB_SHA"),
            "source_digest": _canonical_hash(materials) if materials else None,
        },
        "materials": materials,
    }
    save_json(provenance_path, provenance)

    checksummed = release_files + [report_path, sbom_path, provenance_path]
    checksum_path = destination / "SHA256SUMS"
    checksum_path.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in sorted(checksummed, key=lambda item: item.name)),
        encoding="utf-8",
    )
    release_files.extend([report_path, sbom_path, provenance_path])
    release_manifest = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "kind": RELEASE_KIND,
        "version": release_version,
        "dry_run": bool(dry_run),
        "unsigned": True,
        "signature": {"status": "unsigned", "signed": False},
        "artifacts": artifact_records,
        "files": [_release_file_record(path, "evidence") for path in release_files],
        "checksums": {
            "file": checksum_path.name,
            "sha256": sha256_file(checksum_path),
        },
        "sbom": sbom_path.name,
        "provenance": provenance_path.name,
        "verification_report": report_path.name,
    }
    release_path = destination / "release.json"
    save_json(release_path, release_manifest)
    return {
        "passed": bool(report["passed"]),
        "release": str(release_path),
        "output_dir": str(destination),
        "manifest": release_manifest,
        "verification": report,
    }


def verify_release_bundle(path: os.PathLike[str] | str) -> dict[str, Any]:
    """Recheck release hashes, unsigned metadata, and static artifact structure."""

    release_path = Path(path).expanduser().resolve()
    if release_path.is_dir():
        release_path = release_path / "release.json"
    try:
        release = json.loads(release_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise BuildValidationError(f"Could not read release manifest: {error}") from error
    if not isinstance(release, Mapping) or release.get("schema_version") != RELEASE_SCHEMA_VERSION:
        raise BuildValidationError("Unsupported release manifest schema")
    signature = release.get("signature")
    if (
        not isinstance(signature, Mapping)
        or signature.get("status") != "unsigned"
        or release.get("unsigned") is not True
    ):
        raise BuildValidationError("Release manifest must explicitly declare unsigned status")
    root = release_path.parent
    failures: list[str] = []
    for record in release.get("files", []):
        file_path = root / str(record.get("name", ""))
        if not file_path.is_file() or sha256_file(file_path) != record.get("sha256"):
            failures.append(f"file hash mismatch: {file_path.name}")
    checksum_record = release.get("checksums", {})
    checksum_path = root / str(checksum_record.get("file", "SHA256SUMS"))
    if not checksum_path.is_file() or sha256_file(checksum_path) != checksum_record.get("sha256"):
        failures.append("checksum file hash mismatch")
    elif checksum_path.is_file():
        for line in checksum_path.read_text(encoding="utf-8").splitlines():
            if "  " not in line:
                failures.append("malformed checksum record")
                continue
            expected, name = line.split("  ", 1)
            candidate = root / name
            if not candidate.is_file() or sha256_file(candidate) != expected:
                failures.append(f"checksum mismatch: {name}")
    artifact_results: list[dict[str, Any]] = []
    for artifact in release.get("artifacts", []):
        artifact_path = root / str(artifact.get("name", ""))
        result = verify_artifact(artifact_path)
        artifact_results.append({"artifact": artifact_path.name, **asdict(result)})
        if not result.passed:
            failures.append(f"artifact verification failed: {artifact_path.name}")
    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "passed": not failures,
        "release": str(release_path),
        "failures": failures,
        "artifacts": artifact_results,
    }


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
    """Local SQLite analytics with WAL, bounded busy waits, and backups.

    Writers take an immediate transaction under the shared state lock. SQLite
    WAL keeps readers available while one writer commits; ``backup`` and
    ``recover`` provide the explicit copy/restore policy used by the CLI.
    """

    def __init__(self, path: os.PathLike[str] | str | None = None) -> None:
        self.path = (
            Path(path).expanduser() if path else config_dir() / "analytics.sqlite3"
        )
        self.backup_path = self.path.with_name(f"{self.path.name}.bak")

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=ANALYTICS_BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout={ANALYTICS_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
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
            connection.execute(f"PRAGMA user_version={ANALYTICS_SCHEMA_VERSION}")
            return connection
        except BaseException:
            connection.close()
            raise

    def record(self, result: BuildResult) -> None:
        size = 0
        try:
            size = result.output.stat().st_size
        except OSError:
            pass
        with state_lock(self.path):
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
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
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def backup(self, destination: os.PathLike[str] | str | None = None) -> Path:
        """Create an atomic SQLite backup, defaulting to ``analytics.sqlite3.bak``."""

        output = Path(destination).expanduser() if destination else self.backup_path
        if output.resolve() == self.path.resolve():
            raise BuildValidationError("Analytics backup must differ from its database")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(
            f".{output.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        with state_lock(self.path):
            source = self._connect()
            target = None
            try:
                target = sqlite3.connect(
                    temporary,
                    timeout=ANALYTICS_BUSY_TIMEOUT_MS / 1000,
                    isolation_level=None,
                )
                target.execute(f"PRAGMA busy_timeout={ANALYTICS_BUSY_TIMEOUT_MS}")
                source.backup(target, pages=100, sleep=STATE_LOCK_POLL_SECONDS)
                target.execute("PRAGMA synchronous=NORMAL")
                target.commit()
                target.close()
                target = None
                with temporary.open("r+b") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, output)
            finally:
                source.close()
                if target is not None:
                    target.close()
                try:
                    temporary.unlink()
                except OSError:
                    pass
        return output

    def recover(self, backup: os.PathLike[str] | str | None = None) -> Path:
        """Restore a validated SQLite backup atomically into the live database."""

        source_path = Path(backup).expanduser() if backup else self.backup_path
        if not source_path.is_file():
            raise BuildValidationError(f"Analytics backup is unavailable: {source_path}")
        try:
            connection = sqlite3.connect(source_path, timeout=ANALYTICS_BUSY_TIMEOUT_MS / 1000)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise BuildValidationError(f"Analytics backup is invalid: {source_path}") from error
        if not integrity or integrity[0] != "ok":
            raise BuildValidationError(f"Analytics backup failed integrity check: {source_path}")
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        with state_lock(self.path):
            try:
                shutil.copy2(source_path, temporary)
                with temporary.open("r+b") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                try:
                    temporary.unlink()
                except OSError:
                    pass
        return self.path

    def summary(self) -> dict[str, Any]:
        connection = self._connect()
        try:
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
        finally:
            connection.close()
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
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT timestamp, source, output, backend, profile, success,
                       status, size_bytes, duration_seconds
                FROM builds ORDER BY id DESC LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            connection.close()
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


class DiagnosticsStore:
    """Local JSONL diagnostics with bounded, explicit retention.

    Records contain hashes and redacted command metadata only. No network
    export is performed; ``export`` requires an explicit opt-in flag and
    writes a sanitized bundle to a caller-selected local path.
    """

    def __init__(
        self,
        path: os.PathLike[str] | str | None = None,
        retention_days: int = DEFAULT_DIAGNOSTICS_RETENTION_DAYS,
        max_events: int = DEFAULT_DIAGNOSTICS_MAX_EVENTS,
    ) -> None:
        self.path = (
            Path(path).expanduser()
            if path
            else config_dir() / "diagnostics.jsonl"
        )
        self.retention_days = int(retention_days)
        self.max_events = int(max_events)
        if self.retention_days < 1:
            raise BuildValidationError("Diagnostics retention must be at least one day")
        if self.max_events < 1:
            raise BuildValidationError("Diagnostics max events must be positive")

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        records: list[dict[str, Any]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return records
        for line in lines:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(value, Mapping)
                and value.get("schema_version") == DIAGNOSTICS_SCHEMA_VERSION
                and value.get("kind") == DIAGNOSTICS_KIND
            ):
                records.append(dict(value))
        return records

    def _rewrite_unlocked(self, records: Sequence[Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
            + "\n"
            for record in records
        )
        _atomic_write_bytes(self.path, payload.encode("utf-8"))

    def _rewrite(self, records: Sequence[Mapping[str, Any]]) -> None:
        with state_lock(self.path):
            self._rewrite_unlocked(records)

    def _retained(self, records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC).timestamp() - self.retention_days * 86400
        retained: list[dict[str, Any]] = []
        for record in records:
            timestamp = record.get("timestamp")
            try:
                parsed = datetime.fromisoformat(str(timestamp)).timestamp()
            except (TypeError, ValueError):
                parsed = datetime.now(UTC).timestamp()
            if parsed >= cutoff:
                retained.append(dict(record))
        return retained[-self.max_events :]

    def append(self, record: Mapping[str, Any]) -> None:
        if record.get("schema_version") != DIAGNOSTICS_SCHEMA_VERSION:
            raise BuildValidationError("Unsupported diagnostics schema")
        if record.get("kind") != DIAGNOSTICS_KIND:
            raise BuildValidationError("Unsupported diagnostics kind")
        sanitized = _redact_diagnostic_value(dict(record))
        if not isinstance(sanitized, dict):
            raise BuildValidationError("Diagnostics record must be an object")
        sanitized["correlation_id"] = _normalize_correlation_id(
            str(sanitized.get("correlation_id", ""))
        )
        with state_lock(self.path):
            records = self._retained([*self._read(), sanitized])
            self._rewrite_unlocked(records)

    def record(self, result: BuildResult) -> None:
        self.append(result.diagnostic_record())

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._retained(self._read())[-max(1, int(limit)) :]

    def export(
        self, destination: os.PathLike[str] | str, opt_in: bool = False
    ) -> Path:
        if not opt_in:
            raise BuildValidationError(
                "Diagnostics export requires explicit telemetry opt-in"
            )
        output = Path(destination).expanduser()
        save_json(
            output,
            {
                "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
                "kind": DIAGNOSTICS_KIND,
                "exported_at": datetime.now(UTC).isoformat(),
                "events": self.recent(self.max_events),
                "telemetry": {"opt_in": True, "network": False},
            },
        )
        return output


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
            "      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0",
            "        with:",
            "          python-version: '3.12'",
            "      - name: Install locked Python toolchain",
            "        run: python -m pip install --require-hashes --no-deps --no-index --find-links .uc-dependency-cache -r requirements.lock",
        ]
    elif extension in {"js", "ts"}:
        setup = [
            "      - uses: oven-sh/setup-bun@735343b667d3e6f658f44d0eca948eb6282f2b76 # v2.0.2",
            "      - run: bun install --frozen-lockfile --offline",
        ]
    elif extension == "go":
        setup = [
            "      - uses: actions/setup-go@d35c59abb061a4a6fb18e82ac0862c26744d6ab5 # v5.5.0",
            "        with:",
            "          go-version: stable",
        ]
    elif extension == "rs":
        setup = [
            "      - uses: actions-rust-lang/setup-rust-toolchain@2fcdc490d667999e01ddbbf0f2823181beef6b39 # v1.15.0",
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
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
{setup_text}
      - name: Build source
        shell: pwsh
        run: python .\\UniversalCompiler.py build $env:UC_SOURCE --output dist\\artifact.exe --verify --no-analytics
        env:
          UC_SOURCE: ${{{{ vars.UC_SOURCE || 'src/main.{extension}' }}}}
      - name: Upload artifact
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
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
        dependency_lockfile=(
            Path(args.dependency_lockfile or profile["dependency_lockfile"])
            if args.dependency_lockfile or profile.get("dependency_lockfile")
            else None
        ),
        dependency_cache_dir=(
            Path(args.dependency_cache_dir or profile["dependency_cache_dir"])
            if args.dependency_cache_dir or profile.get("dependency_cache_dir")
            else None
        ),
        dependency_mirror=(
            args.dependency_mirror or profile.get("dependency_mirror")
        ),
        correlation_id=args.correlation_id or new_correlation_id(),
    )


def _add_build_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", "-o", help="Output executable or artifact path")
    parser.add_argument(
        "--manifest",
        help="Versioned project manifest supplying settings and profiles",
    )
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
        "--dependency-lock",
        "--lockfile",
        dest="dependency_lockfile",
        help=(
            "Approved uc.dependencies.v1 lock file; defaults to "
            "universal-compiler.lock.json beside the source"
        ),
    )
    parser.add_argument(
        "--dependency-cache-dir",
        help="Override the lock file's explicit dependency cache directory",
    )
    parser.add_argument(
        "--dependency-mirror",
        help="Override the lock file's explicit package mirror",
    )
    parser.add_argument(
        "--correlation-id",
        help="Optional 32-character or UUID correlation id for structured diagnostics",
    )
    parser.add_argument(
        "--diagnostics-path",
        help="Local JSONL diagnostics path (default: per-user diagnostics.jsonl)",
    )
    parser.add_argument(
        "--diagnostics-retention-days",
        type=int,
        default=DEFAULT_DIAGNOSTICS_RETENTION_DAYS,
        help="Local diagnostics retention window",
    )
    parser.add_argument(
        "--no-diagnostics",
        action="store_true",
        help="Disable local structured diagnostics recording",
    )
    parser.add_argument(
        "--adapter",
        action="append",
        default=[],
        help=(
            "Explicitly allow an installed namespaced adapter entry point "
            "(repeatable; external adapters are disabled by default)"
        ),
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
    parser.add_argument(
        "--locale",
        help="Message locale (for example: en, es); defaults to UC_LOCALE/system locale",
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
    build_parser.add_argument("--watch-debounce", type=float, default=0.35)
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
    inspect_parser.add_argument("--adapter", action="append", default=[])
    inspect_parser.add_argument("--json", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify", help="Verify an artifact without executing it"
    )
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser(
        "list-toolchains", help="List supported backends and availability"
    )
    list_parser.add_argument("--adapter", action="append", default=[])
    list_parser.add_argument("--json", action="store_true")

    init_parser = subparsers.add_parser(
        "init-profiles", help="Create a starter YAML profile file"
    )
    init_parser.add_argument(
        "path",
        nargs="?",
        help="Destination, defaulting to the per-user config directory",
    )

    manifest_parser = subparsers.add_parser(
        "manifest", help="Show, initialize, migrate, or roll back project state"
    )
    manifest_parser.add_argument(
        "action",
        nargs="?",
        choices=("show", "init", "migrate", "rollback"),
        default="show",
    )
    manifest_parser.add_argument("--path")
    manifest_parser.add_argument(
        "--scope", choices=("user", "workspace"), default="user"
    )
    manifest_parser.add_argument("--workspace")
    manifest_parser.add_argument("--json", action="store_true")

    release_parser = subparsers.add_parser(
        "release", help="Create or verify an unsigned local release evidence bundle"
    )
    release_parser.add_argument(
        "action",
        nargs="?",
        choices=("dry-run", "verify"),
        default="dry-run",
    )
    release_parser.add_argument("--artifact", action="append", default=[])
    release_parser.add_argument("--output-dir", default="release")
    release_parser.add_argument("--source-root")
    release_parser.add_argument("--version", default=APP_VERSION)
    release_parser.add_argument("--path")
    release_parser.add_argument("--json", action="store_true")

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
        "analytics", help="Show, back up, or recover local build analytics"
    )
    analytics_parser.add_argument("--path")
    analytics_parser.add_argument("--recent", type=int, default=0)
    analytics_parser.add_argument(
        "--backup",
        nargs="?",
        const="",
        metavar="PATH",
        help="Create an atomic SQLite backup (default: <database>.bak)",
    )
    analytics_parser.add_argument(
        "--recover",
        nargs="?",
        const="",
        metavar="PATH",
        help="Restore a validated SQLite backup (default: <database>.bak)",
    )
    analytics_parser.add_argument("--json", action="store_true")

    diagnostics_parser = subparsers.add_parser(
        "diagnostics", help="Show or explicitly export local structured diagnostics"
    )
    diagnostics_parser.add_argument("--path")
    diagnostics_parser.add_argument(
        "--retention-days", type=int, default=DEFAULT_DIAGNOSTICS_RETENTION_DAYS
    )
    diagnostics_parser.add_argument("--recent", type=int, default=10)
    diagnostics_parser.add_argument(
        "--export", dest="export_path", help="Write a sanitized local diagnostics bundle"
    )
    diagnostics_parser.add_argument(
        "--allow-telemetry",
        action="store_true",
        help="Explicitly opt in to diagnostics export (no network is used)",
    )
    diagnostics_parser.add_argument("--json", action="store_true")

    return parser


def _default_output(source: Path) -> Path:
    return source.with_suffix(".exe")


def _cli_error(catalog: MessageCatalog, error: BaseException) -> None:
    print(
        catalog.message("cli.error", "ERROR: {error}", error=redact_text(str(error))),
        file=sys.stderr,
    )


def _cli_warning(catalog: MessageCatalog, warning: str) -> None:
    print(
        catalog.message("cli.warning", "WARNING: {warning}", warning=warning),
        file=sys.stderr,
    )


def _result_text(result: BuildResult, catalog: MessageCatalog | None = None) -> str:
    messages = catalog or get_message_catalog()
    status = messages.message(f"status.{result.status}", result.status)
    lines = [f"{status}: {result.output}"]
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
    catalog = get_message_catalog(args.locale)
    if args.command == "list-toolchains":
        try:
            status_value = backend_status(
                discover_adapters(args.adapter or None)
            )
        except BuildValidationError as error:
            _cli_error(catalog, error)
            return 1
        print(
            json.dumps(status_value, indent=2)
            if args.json
            else "\n".join(
                f"{key}: {'available' if item['available'] else 'missing'}"
                for key, item in status_value.items()
            )
        )
        return 0
    if args.command == "manifest":
        destination = (
            Path(args.path).expanduser()
            if args.path
            else project_manifest_path(args.scope, args.workspace)
        )
        try:
            if args.action == "init":
                manifest_result = ManifestLoadResult(
                    validate_project_manifest(
                        default_project_manifest(args.scope, args.workspace),
                        expected_scope=args.scope,
                    )
                )
                save_project_manifest(destination, manifest_result.manifest)
            elif args.action == "rollback":
                rollback_project_manifest(destination)
                manifest_result = load_project_manifest(
                    destination, expected_scope=args.scope
                )
            else:
                manifest_result = load_project_manifest(
                    destination,
                    expected_scope=args.scope if args.path is None else None,
                )
        except (BuildValidationError, OSError, ValueError) as error:
            _cli_error(catalog, error)
            return 1
        for warning in manifest_result.warnings:
            _cli_warning(catalog, warning)
        if args.json:
            print(json.dumps(manifest_result.manifest, indent=2, default=str))
        else:
            print(destination)
            print(
                f"schema={manifest_result.manifest['schema_version']} "
                f"scope={manifest_result.manifest['scope']} "
                f"migrated={manifest_result.migrated} "
                f"recovered={manifest_result.recovered}"
            )
        return 0
    if args.command == "release":
        try:
            if args.action == "verify":
                if not args.path:
                    raise BuildValidationError(
                        "release verify requires --path to release.json or its directory"
                    )
                release_result = verify_release_bundle(args.path)
            else:
                release_result = release_bundle(
                    args.artifact,
                    args.output_dir,
                    source_root=args.source_root,
                    version=args.version,
                    dry_run=True,
                )
        except (BuildValidationError, OSError, ValueError) as error:
            _cli_error(catalog, error)
            return 1
        print(
            json.dumps(release_result, indent=2, default=str)
            if args.json
            else (
                f"{'PASS' if release_result['passed'] else 'FAIL'}: "
                f"{release_result.get('release', release_result.get('output_dir', ''))}"
            )
        )
        return 0 if release_result["passed"] else 1
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
            _cli_error(catalog, error)
            return 1
        bytecode_value = {"source": args.source, "output": str(output)}
        print(json.dumps(bytecode_value, indent=2) if args.json else str(output))
        return 0
    if args.command == "extract-icon":
        try:
            output = extract_icon(args.executable, args.output)
        except (BuildValidationError, OSError) as error:
            _cli_error(catalog, error)
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
            _cli_error(catalog, error)
            return 1
        print(output)
        return 0
    if args.command == "obfuscate":
        try:
            output = obfuscate_source(args.source, args.method, args.output)
        except (BuildValidationError, OSError) as error:
            _cli_error(catalog, error)
            return 1
        print(output)
        return 0
    if args.command == "analytics":
        analytics_store = BuildAnalytics(args.path)
        try:
            analytics_value: dict[str, Any] = {}
            if args.recover is not None:
                recovery_source = args.recover or None
                analytics_value["recovered"] = str(
                    analytics_store.recover(recovery_source)
                )
            if args.backup is not None:
                backup_destination = args.backup or None
                analytics_value["backup"] = str(
                    analytics_store.backup(backup_destination)
                )
            analytics_value.update(analytics_store.summary())
            if args.recent:
                analytics_value["recent"] = analytics_store.recent(args.recent)
        except (BuildValidationError, OSError, sqlite3.DatabaseError) as error:
            _cli_error(catalog, error)
            return 1
        print(
            json.dumps(analytics_value, indent=2, default=str)
            if args.json
            else json.dumps(analytics_value, indent=2, default=str)
        )
        return 0
    if args.command == "diagnostics":
        try:
            diagnostics_store = DiagnosticsStore(
                args.path,
                retention_days=args.retention_days,
            )
            export_path = (
                diagnostics_store.export(args.export_path, opt_in=args.allow_telemetry)
                if args.export_path
                else None
            )
        except (BuildValidationError, OSError, ValueError) as error:
            _cli_error(catalog, error)
            return 1
        diagnostics_value = {
            "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
            "kind": DIAGNOSTICS_KIND,
            "path": str(diagnostics_store.path),
            "retention_days": diagnostics_store.retention_days,
            "events": diagnostics_store.recent(args.recent),
            "export": str(export_path) if export_path else None,
            "telemetry": {"opt_in": bool(args.allow_telemetry), "network": False},
        }
        print(json.dumps(diagnostics_value, indent=2, default=str))
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
        adapters = discover_adapters(args.adapter or None)
        file_type = detect_file_type(source)
        if file_type is None:
            suffix = source.suffix.lower().lstrip(".")
            if any(suffix in adapter.extensions for adapter in adapters):
                file_type = suffix
            else:
                detected_type = _detect_adapter_file_type(adapter_catalog(adapters), source)
                if detected_type:
                    file_type = detected_type
        choices = tuple(
            adapter.backend_id
            for adapter in adapters
            if file_type and file_type in adapter.extensions
        )
        status_catalog = backend_status(adapters)
        inspect_value = {
            "source": str(source),
            "file_type": file_type,
            "estimated_size": estimate_output_size(source, file_type),
            "backends": {backend: status_catalog[backend] for backend in choices},
        }
        print(json.dumps(inspect_value, indent=2) if args.json else json.dumps(inspect_value, indent=2))
        return 0

    profile_file = (
        Path(args.profiles_file).expanduser()
        if getattr(args, "profiles_file", None)
        else profiles_path()
    )
    try:
        if getattr(args, "manifest", None):
            manifest_result = load_project_manifest(
                Path(args.manifest).expanduser()
            )
            profiles = manifest_result.manifest["profiles"]
            for warning in manifest_result.warnings:
                _cli_warning(catalog, warning)
        else:
            profiles = load_profiles(profile_file)
    except (BuildValidationError, OSError, ValueError) as error:
        _cli_error(catalog, error)
        return 1
    profile = profiles.get(args.profile)
    if profile is None:
        parser.error(f"Profile not found: {args.profile}")
    try:
        adapters = discover_adapters(getattr(args, "adapter", None) or None)
    except BuildValidationError as error:
        _cli_error(catalog, error)
        return 1
    engine = CompilerEngine(adapters=adapters)
    build_analytics: BuildAnalytics | None = (
        None if args.no_analytics else BuildAnalytics()
    )
    try:
        build_diagnostics: DiagnosticsStore | None = (
            None
            if args.no_diagnostics
            else DiagnosticsStore(
                args.diagnostics_path,
                retention_days=args.diagnostics_retention_days,
            )
        )
    except (BuildValidationError, OSError, ValueError) as error:
        _cli_error(catalog, error)
        return 1
    if args.command == "build":
        source = Path(args.source).expanduser()
        output = (
            Path(args.output).expanduser() if args.output else _default_output(source)
        )
        request = _profile_request(profile, args, source, output)
        if args.preview:
            try:
                plan = CompilerEngine(
                    require_available=False,
                    adapters=adapters,
                ).plan(
                    request,
                    allow_missing_source=True,
                )
            except BuildValidationError as error:
                _cli_error(catalog, error)
                return 2
            print(command_display(plan.command))
            return 0
        if args.watch:
            try:
                for result in engine.watch(
                    request,
                    interval=args.watch_interval,
                    debounce=args.watch_debounce,
                ):
                    if build_analytics:
                        build_analytics.record(result)
                    if build_diagnostics:
                        build_diagnostics.record(result)
                    print(
                        json.dumps(result.as_dict(), indent=2, default=str)
                        if args.json
                        else _result_text(result, catalog),
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
            if build_diagnostics:
                for matrix_result in results:
                    build_diagnostics.record(matrix_result)
            if args.json:
                print(
                    json.dumps(
                        [result.as_dict() for result in results],
                        indent=2,
                        default=str,
                    )
                )
            else:
                print("\n\n".join(_result_text(result, catalog) for result in results))
            return 0 if all(result.success for result in results) else 1
        build_result = engine.build(request)
        if build_analytics:
            build_analytics.record(build_result)
        if build_diagnostics:
            build_diagnostics.record(build_result)
        print(
            json.dumps(build_result.as_dict(), indent=2, default=str)
            if args.json
            else _result_text(build_result, catalog)
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
            matrix_requests = [
                matrix_request
                for request in requests
                for matrix_request in engine.matrix_requests(request, args.matrix)
            ]
            results = engine.build_batch(matrix_requests, workers=args.jobs)
        else:
            results = engine.build_batch(requests, workers=args.jobs)
        if build_analytics:
            for batch_result in results:
                build_analytics.record(batch_result)
        if build_diagnostics:
            for batch_result in results:
                build_diagnostics.record(batch_result)
        if args.json:
            print(
                json.dumps(
                    [result.as_dict() for result in results], indent=2, default=str
                )
            )
        else:
            print("\n\n".join(_result_text(result, catalog) for result in results))
        return 0 if all(result.success for result in results) else 1
    parser.error(f"Unsupported command: {args.command}")
    return 2


__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "DEFAULT_LOCALE",
    "I18N_CATALOG_FILENAME",
    "I18N_SCHEMA_VERSION",
    "ADAPTER_ALLOWLIST_ENV",
    "ADAPTER_API_VERSION",
    "ADAPTER_ENTRY_POINT_GROUP",
    "ADAPTER_POLICY_VERSION",
    "ANALYTICS_SCHEMA_VERSION",
    "AdapterDescriptor",
    "BackendAdapter",
    "ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "BACKEND_NAMES",
    "BACKEND_CATALOG",
    "BuildAnalytics",
    "BuildPlan",
    "BuildRequest",
    "BuildResult",
    "BuildValidationError",
    "StateLockError",
    "CAPABILITY_SCHEMA_VERSION",
    "CommandResult",
    "CompilerEngine",
    "DIAGNOSTICS_KIND",
    "DIAGNOSTICS_SCHEMA_VERSION",
    "DiagnosticsStore",
    "PROVENANCE_SCHEMA_VERSION",
    "RELEASE_KIND",
    "RELEASE_SCHEMA_VERSION",
    "SBOM_SCHEMA_VERSION",
    "DEPENDENCY_LOCK_FILENAME",
    "DEPENDENCY_LOCK_KIND",
    "DEPENDENCY_LOCK_SCHEMA_VERSION",
    "DEPENDENCY_POLICY_VERSION",
    "DEFAULT_MANIFEST_SETTINGS",
    "DEFAULT_PROFILES",
    "DEFAULT_EXECUTION_TIMEOUT_SECONDS",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "ExecutionPolicy",
    "EXTENSION_BACKENDS",
    "ManifestLoadResult",
    "MessageCatalog",
    "VerificationResult",
    "backend_status",
    "artifact_manifest_path",
    "adapter_catalog",
    "adapter_diagnostics",
    "cli_main",
    "command_display",
    "compile_bytecode",
    "config_dir",
    "contrast_ratio",
    "detect_file_type",
    "dependency_lock_path",
    "discover_adapters",
    "estimate_output_size",
    "extract_icon",
    "format_size",
    "get_message_catalog",
    "new_correlation_id",
    "normalize_locale",
    "github_actions_template",
    "obfuscate_source",
    "load_json",
    "load_dependency_lock",
    "load_project_manifest",
    "load_profiles",
    "default_project_manifest",
    "migrate_project_manifest",
    "project_manifest_backup_path",
    "project_manifest_path",
    "profiles_path",
    "parse_toolchain_versions",
    "run_command",
    "PROJECT_MANIFEST_FILENAME",
    "PROJECT_MANIFEST_KIND",
    "PROJECT_MANIFEST_SCHEMA_VERSION",
    "REQUEST_SCHEMA_VERSION",
    "redact_command",
    "redact_text",
    "release_bundle",
    "resolve_locale",
    "RESULT_SCHEMA_VERSION",
    "save_json",
    "save_project_manifest",
    "save_profiles",
    "sha256_file",
    "state_lock",
    "update_project_manifest",
    "rollback_project_manifest",
    "validate_project_manifest",
    "verify_artifact_manifest",
    "verify_artifact",
    "verify_release_bundle",
    "wrap_msix",
]
