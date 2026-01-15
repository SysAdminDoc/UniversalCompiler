#!/usr/bin/env python3
"""
Universal Compiler v2.0 - Python Edition
A powerful, all-in-one script-to-EXE compiler with a modern dark-themed GUI

Compiles PowerShell, Python, Batch, Node.js, C#, Go, Ruby, VBScript, 
and AutoHotkey scripts into standalone Windows executables.
"""

import os
import sys
import json
import shutil
import subprocess
import threading
import ctypes
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any
import tkinter as tk
from tkinter import filedialog, messagebox

# Optional drag & drop support
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False
    print("Note: tkinterdnd2 not installed. Drag & drop disabled.")
    print("Install with: pip install tkinterdnd2")

try:
    import customtkinter as ctk
except ImportError:
    print("Installing customtkinter...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "customtkinter"])
    import customtkinter as ctk

try:
    from PIL import Image, ImageTk
except ImportError:
    print("Installing Pillow...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
    from PIL import Image, ImageTk

# ============================================================================
# CONSTANTS & CONFIGURATION
# ============================================================================

APP_NAME = "Universal Compiler"
APP_VERSION = "2.0"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "UniversalCompiler"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROFILES_FILE = CONFIG_DIR / "profiles.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
RECENT_FILE = CONFIG_DIR / "recent.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
TEMPLATES_DIR = CONFIG_DIR / "Templates"
LOG_FILE = CONFIG_DIR / "install.log"

# Ensure config directory exists
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================================
# THEME DEFINITIONS
# ============================================================================

THEMES = {
    "Dark": {
        "bg": "#020617",
        "card": "#0f172a",
        "card_hover": "#1e293b",
        "border": "#1e293b",
        "input": "#0f172a",
        "green": "#22c55e",
        "green_hover": "#16a34a",
        "blue": "#60a5fa",
        "red": "#ef4444",
        "yellow": "#eab308",
        "text1": "#f8fafc",
        "text2": "#94a3b8",
        "text3": "#64748b",
        "log_bg": "#0a0f1a",
    },
    "Light": {
        "bg": "#f8fafc",
        "card": "#ffffff",
        "card_hover": "#f1f5f9",
        "border": "#e2e8f0",
        "input": "#ffffff",
        "green": "#16a34a",
        "green_hover": "#15803d",
        "blue": "#3b82f6",
        "red": "#dc2626",
        "yellow": "#ca8a04",
        "text1": "#0f172a",
        "text2": "#475569",
        "text3": "#94a3b8",
        "log_bg": "#f1f5f9",
    }
}

# ============================================================================
# DEFAULT SETTINGS & PROFILES
# ============================================================================

DEFAULT_SETTINGS = {
    "theme": "Dark",
    "post_build_action": "None",
    "post_build_copy_path": "",
    "show_notifications": True,
    "auto_check_updates": True,
    "max_recent_files": 10,
    "max_history_items": 50,
    "default_profile": "Default",
    "signing_cert_path": "",
    "signing_cert_password": "",
}

DEFAULT_PROFILES = {
    "Default": {
        "console": False,
        "admin": False,
        "single_file": True,
        "version": "1.0.0.0",
        "company": "",
        "copyright": "",
        "description": "",
        "product": "",
    },
    "Console App": {
        "console": True,
        "admin": False,
        "single_file": True,
        "version": "1.0.0.0",
        "company": "",
        "copyright": "",
        "description": "",
        "product": "",
    },
    "Admin Tool": {
        "console": True,
        "admin": True,
        "single_file": True,
        "version": "1.0.0.0",
        "company": "",
        "copyright": "",
        "description": "",
        "product": "",
    },
    "GUI Application": {
        "console": False,
        "admin": False,
        "single_file": True,
        "version": "1.0.0.0",
        "company": "",
        "copyright": "",
        "description": "",
        "product": "",
    },
}

# ============================================================================
# COMPILER DEFINITIONS
# ============================================================================

COMPILERS = {
    "ps1": {"name": "PowerShell", "compiler": "PS2EXE", "desc": "PowerShell Script"},
    "py": {"name": "Python", "compiler": "PyInstaller", "desc": "Python Script"},
    "bat": {"name": "Batch", "compiler": "IExpress", "desc": "Batch Script"},
    "cmd": {"name": "Command", "compiler": "IExpress", "desc": "Command Script"},
    "js": {"name": "Node.js", "compiler": "pkg", "desc": "JavaScript"},
    "vbs": {"name": "VBScript", "compiler": "IExpress", "desc": "VBScript"},
    "ahk": {"name": "AutoHotkey", "compiler": "Ahk2Exe", "desc": "AutoHotkey"},
    "cs": {"name": "C#", "compiler": "CSC", "desc": "C# Source"},
    "go": {"name": "Go", "compiler": "go build", "desc": "Go Source"},
    "rb": {"name": "Ruby", "compiler": "Ocra", "desc": "Ruby Script"},
}

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def load_json(filepath: Path, default: Any = None) -> Any:
    """Load JSON file with fallback to default."""
    try:
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default if default is not None else {}


def save_json(filepath: Path, data: Any) -> None:
    """Save data to JSON file."""
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving {filepath}: {e}")


def format_size(size: int) -> str:
    """Format file size in human-readable format."""
    if size >= 1_073_741_824:
        return f"{size / 1_073_741_824:.1f} GB"
    elif size >= 1_048_576:
        return f"{size / 1_048_576:.1f} MB"
    elif size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


def estimate_output_size(source_file: str, file_type: str) -> str:
    """Estimate the output EXE size based on file type."""
    if not os.path.exists(source_file):
        return "Unknown"
    
    source_size = os.path.getsize(source_file)
    estimates = {
        "ps1": (5 * 1024 * 1024, 1.5),   # ~5MB base + 1.5x
        "py": (15 * 1024 * 1024, 2),      # ~15MB base + 2x
        "bat": (50 * 1024, 1.2),          # ~50KB base
        "cmd": (50 * 1024, 1.2),
        "js": (40 * 1024 * 1024, 1.5),    # ~40MB base (Node.js)
        "vbs": (50 * 1024, 1.2),
        "ahk": (1 * 1024 * 1024, 1.3),    # ~1MB base
        "cs": (10 * 1024, 1.1),           # Small .NET overhead
        "go": (2 * 1024 * 1024, 1.2),     # ~2MB base
        "rb": (20 * 1024 * 1024, 2),      # ~20MB base (Ruby)
    }
    
    if file_type in estimates:
        base, multiplier = estimates[file_type]
        return format_size(int(base + source_size * multiplier))
    return "Unknown"


def log_message(message: str) -> None:
    """Write message to log file."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def run_command(cmd: List[str], cwd: Optional[str] = None) -> tuple:
    """Run a command and return (success, output)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def which(program: str) -> Optional[str]:
    """Find program in PATH."""
    return shutil.which(program)


def show_notification(title: str, message: str) -> None:
    """Show Windows toast notification."""
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(title, message, duration=5, threaded=True)
    except ImportError:
        try:
            # Fallback to Windows balloon notification
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
        except Exception:
            pass
    except Exception:
        pass


# ============================================================================
# SETTINGS MANAGEMENT
# ============================================================================

class Settings:
    """Application settings manager."""
    
    def __init__(self):
        self._settings = DEFAULT_SETTINGS.copy()
        self.load()
    
    def load(self) -> None:
        """Load settings from file."""
        saved = load_json(SETTINGS_FILE, {})
        for key, value in saved.items():
            if key in self._settings:
                self._settings[key] = value
    
    def save(self) -> None:
        """Save settings to file."""
        save_json(SETTINGS_FILE, self._settings)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get setting value."""
        return self._settings.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set setting value."""
        self._settings[key] = value
        self.save()
    
    @property
    def theme(self) -> str:
        return self._settings["theme"]
    
    @theme.setter
    def theme(self, value: str):
        self._settings["theme"] = value
        self.save()


# ============================================================================
# RECENT FILES MANAGEMENT
# ============================================================================

class RecentFiles:
    """Recent files manager."""
    
    def __init__(self, max_items: int = 10):
        self.max_items = max_items
        self._files: List[str] = []
        self.load()
    
    def load(self) -> None:
        """Load recent files from disk."""
        saved = load_json(RECENT_FILE, [])
        self._files = [f for f in saved if os.path.exists(f)][:self.max_items]
    
    def save(self) -> None:
        """Save recent files to disk."""
        save_json(RECENT_FILE, self._files)
    
    def add(self, filepath: str) -> None:
        """Add file to recent list."""
        if filepath in self._files:
            self._files.remove(filepath)
        self._files.insert(0, filepath)
        self._files = self._files[:self.max_items]
        self.save()
    
    def get_all(self) -> List[str]:
        """Get all recent files."""
        return self._files.copy()


# ============================================================================
# BUILD PROFILES MANAGEMENT
# ============================================================================

class BuildProfiles:
    """Build profiles manager."""
    
    def __init__(self):
        self._profiles = DEFAULT_PROFILES.copy()
        self.load()
    
    def load(self) -> None:
        """Load profiles from disk."""
        saved = load_json(PROFILES_FILE, {})
        for name, profile in saved.items():
            self._profiles[name] = profile
    
    def save(self) -> None:
        """Save profiles to disk."""
        save_json(PROFILES_FILE, self._profiles)
    
    def get(self, name: str) -> Optional[Dict]:
        """Get profile by name."""
        return self._profiles.get(name)
    
    def set(self, name: str, profile: Dict) -> None:
        """Save or update profile."""
        self._profiles[name] = profile
        self.save()
    
    def names(self) -> List[str]:
        """Get all profile names."""
        return list(self._profiles.keys())


# ============================================================================
# COMPILATION HISTORY
# ============================================================================

class CompilationHistory:
    """Compilation history manager."""
    
    def __init__(self, max_items: int = 50):
        self.max_items = max_items
        self._history: List[Dict] = []
        self.load()
    
    def load(self) -> None:
        """Load history from disk."""
        self._history = load_json(HISTORY_FILE, [])
    
    def save(self) -> None:
        """Save history to disk."""
        save_json(HISTORY_FILE, self._history)
    
    def add(self, source: str, output: str, file_type: str, 
            success: bool, profile: str, size: int) -> None:
        """Add compilation to history."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "output": output,
            "type": file_type,
            "success": success,
            "profile": profile,
            "size": size,
        }
        self._history.insert(0, entry)
        self._history = self._history[:self.max_items]
        self.save()
    
    def get_all(self) -> List[Dict]:
        """Get all history entries."""
        return self._history.copy()


# ============================================================================
# TEMPLATE SCRIPTS
# ============================================================================

TEMPLATES = {
    "HelloWorld.ps1": '''# PowerShell Hello World
param([string]$Name = "World")
Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show("Hello, $Name!", "Hello", "OK", "Information")
''',
    "HelloWorld.py": '''# Python Hello World
import tkinter as tk
from tkinter import messagebox

root = tk.Tk()
root.withdraw()
messagebox.showinfo("Hello", "Hello, World!")
root.destroy()
''',
    "HelloWorld.bat": '''@echo off
echo Hello, World!
pause
''',
    "HelloWorld.js": '''// Node.js Hello World
console.log("Hello, World!");
''',
    "HelloWorld.cs": '''using System;
using System.Windows.Forms;

class Program {
    [STAThread]
    static void Main() {
        MessageBox.Show("Hello, World!", "Hello");
    }
}
''',
    "HelloWorld.go": '''package main

import "fmt"

func main() {
    fmt.Println("Hello, World!")
}
''',
    "HelloWorld.rb": '''# Ruby Hello World
puts "Hello, World!"
''',
    "HelloWorld.vbs": '''MsgBox "Hello, World!", vbInformation, "Hello"
''',
    "HelloWorld.ahk": '''MsgBox, Hello, World!
''',
}


def initialize_templates() -> None:
    """Create template files if they don't exist."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    for filename, content in TEMPLATES.items():
        filepath = TEMPLATES_DIR / filename
        if not filepath.exists():
            filepath.write_text(content, encoding="utf-8")


# ============================================================================
# DEPENDENCY CHECKER
# ============================================================================

class DependencyChecker:
    """Check and install compiler dependencies."""
    
    @staticmethod
    def check_ps2exe() -> bool:
        """Check if PS2EXE is available."""
        try:
            result = subprocess.run(
                ["powershell", "-Command", "Get-Module -ListAvailable ps2exe"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return "ps2exe" in result.stdout.lower()
        except Exception:
            return False
    
    @staticmethod
    def check_pyinstaller() -> bool:
        """Check if PyInstaller is available."""
        return which("pyinstaller") is not None
    
    @staticmethod
    def check_pkg() -> bool:
        """Check if pkg is available."""
        return which("pkg") is not None
    
    @staticmethod
    def check_go() -> bool:
        """Check if Go is available."""
        if which("go"):
            return True
        go_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Go/bin/go.exe"
        return go_path.exists()
    
    @staticmethod
    def check_ruby() -> bool:
        """Check if Ruby/Ocra is available."""
        return which("ocra") is not None
    
    @staticmethod
    def check_ahk() -> bool:
        """Check if AutoHotkey compiler is available."""
        paths = [
            Path(os.environ.get("ProgramFiles", "")) / "AutoHotkey/Compiler/Ahk2Exe.exe",
            Path(os.environ.get("ProgramFiles", "")) / "AutoHotkey/v2/Compiler/Ahk2Exe.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "AutoHotkey/Compiler/Ahk2Exe.exe",
        ]
        return any(p.exists() for p in paths)
    
    @staticmethod
    def check_csc() -> bool:
        """Check if CSC is available."""
        windir = os.environ.get("WINDIR", "C:\\Windows")
        paths = [
            Path(windir) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe",
            Path(windir) / "Microsoft.NET/Framework/v4.0.30319/csc.exe",
        ]
        return any(p.exists() for p in paths)
    
    @staticmethod
    def check_iexpress() -> bool:
        """Check if IExpress is available."""
        windir = os.environ.get("WINDIR", "C:\\Windows")
        return (Path(windir) / "System32/iexpress.exe").exists()
    
    @classmethod
    def check_compiler(cls, file_type: str) -> bool:
        """Check if compiler for file type is available."""
        checkers = {
            "ps1": cls.check_ps2exe,
            "py": cls.check_pyinstaller,
            "bat": cls.check_iexpress,
            "cmd": cls.check_iexpress,
            "js": cls.check_pkg,
            "vbs": cls.check_iexpress,
            "ahk": cls.check_ahk,
            "cs": cls.check_csc,
            "go": cls.check_go,
            "rb": cls.check_ruby,
        }
        checker = checkers.get(file_type)
        return checker() if checker else False
    
    @classmethod
    def get_all_status(cls) -> Dict[str, Dict]:
        """Get status of all dependencies."""
        return {
            "PS2EXE": {"name": "PS2EXE", "desc": "PowerShell (.ps1)", "installed": cls.check_ps2exe(), "size": "~2 MB"},
            "PyInstaller": {"name": "PyInstaller", "desc": "Python (.py)", "installed": cls.check_pyinstaller(), "size": "~15 MB"},
            "pkg": {"name": "pkg", "desc": "Node.js (.js)", "installed": cls.check_pkg(), "size": "~50 MB"},
            "Go": {"name": "Go", "desc": "Go (.go)", "installed": cls.check_go(), "size": "~150 MB"},
            "Ruby": {"name": "Ruby+Ocra", "desc": "Ruby (.rb)", "installed": cls.check_ruby(), "size": "~120 MB"},
            "AutoHotkey": {"name": "AutoHotkey", "desc": "AHK (.ahk)", "installed": cls.check_ahk(), "size": "~5 MB"},
            "CSC": {"name": "CSC", "desc": "C# (.cs)", "installed": cls.check_csc(), "size": "Built-in", "builtin": True},
            "IExpress": {"name": "IExpress", "desc": "Batch/VBS", "installed": cls.check_iexpress(), "size": "Built-in", "builtin": True},
        }


# ============================================================================
# COMPILERS
# ============================================================================

class Compiler:
    """Handle compilation for various script types."""
    
    @staticmethod
    def compile_ps1(source: str, output: str, icon: Optional[str] = None,
                    admin: bool = False, no_console: bool = True,
                    metadata: Optional[Dict] = None) -> tuple:
        """Compile PowerShell script using PS2EXE."""
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-Command"]
        
        ps_cmd = f'Invoke-PS2EXE -InputFile "{source}" -OutputFile "{output}"'
        if icon and os.path.exists(icon):
            ps_cmd += f' -IconFile "{icon}"'
        if admin:
            ps_cmd += " -RequireAdmin"
        if no_console:
            ps_cmd += " -NoConsole"
        if metadata:
            if metadata.get("product"):
                ps_cmd += f' -Title "{metadata["product"]}"'
            if metadata.get("version"):
                ps_cmd += f' -Version "{metadata["version"]}"'
            if metadata.get("company"):
                ps_cmd += f' -Company "{metadata["company"]}"'
            if metadata.get("copyright"):
                ps_cmd += f' -Copyright "{metadata["copyright"]}"'
        
        cmd.append(ps_cmd)
        return run_command(cmd)
    
    @staticmethod
    def compile_py(source: str, output: str, icon: Optional[str] = None,
                   one_file: bool = True, console: bool = False) -> tuple:
        """Compile Python script using PyInstaller."""
        output_dir = os.path.dirname(output)
        output_name = os.path.splitext(os.path.basename(output))[0]
        
        cmd = ["pyinstaller", "--distpath", output_dir, "--name", output_name, "--noconfirm"]
        if one_file:
            cmd.append("--onefile")
        if not console:
            cmd.append("--noconsole")
        if icon and os.path.exists(icon):
            cmd.extend(["--icon", icon])
        cmd.append(source)
        
        return run_command(cmd)
    
    @staticmethod
    def compile_batch(source: str, output: str) -> tuple:
        """Compile Batch/VBS script using IExpress."""
        import tempfile
        
        temp_dir = tempfile.mkdtemp()
        try:
            # Copy source to temp
            src_name = os.path.basename(source)
            shutil.copy(source, os.path.join(temp_dir, src_name))
            
            # Create SED file
            sed_content = f"""[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
RebootMode=N
TargetName={output}
FriendlyName=App
AppLaunched=cmd /c "{src_name}"
PostInstallCmd=<None>
SourceFiles=SourceFiles
[Strings]
[SourceFiles]
SourceFiles0={temp_dir}\\
[SourceFiles0]
%FILE0%={src_name}
"""
            sed_file = os.path.join(temp_dir, "config.sed")
            with open(sed_file, "w") as f:
                f.write(sed_content)
            
            windir = os.environ.get("WINDIR", "C:\\Windows")
            iexpress = os.path.join(windir, "System32", "iexpress.exe")
            return run_command([iexpress, "/N", "/Q", sed_file])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    @staticmethod
    def compile_js(source: str, output: str) -> tuple:
        """Compile Node.js script using pkg."""
        cmd = ["pkg", source, "--target", "node18-win-x64", "--output", output]
        return run_command(cmd)
    
    @staticmethod
    def compile_cs(source: str, output: str) -> tuple:
        """Compile C# source using CSC."""
        windir = os.environ.get("WINDIR", "C:\\Windows")
        csc_paths = [
            os.path.join(windir, "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe"),
            os.path.join(windir, "Microsoft.NET", "Framework", "v4.0.30319", "csc.exe"),
        ]
        csc = next((p for p in csc_paths if os.path.exists(p)), None)
        if not csc:
            return False, "CSC compiler not found"
        return run_command([csc, f"/out:{output}", source])
    
    @staticmethod
    def compile_go(source: str, output: str) -> tuple:
        """Compile Go source using go build."""
        go_exe = which("go")
        if not go_exe:
            go_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Go/bin/go.exe"
            if go_path.exists():
                go_exe = str(go_path)
        if not go_exe:
            return False, "Go compiler not found"
        return run_command([go_exe, "build", "-o", output, source], cwd=os.path.dirname(source))
    
    @staticmethod
    def compile_ahk(source: str, output: str, icon: Optional[str] = None) -> tuple:
        """Compile AutoHotkey script using Ahk2Exe."""
        ahk_paths = [
            Path(os.environ.get("ProgramFiles", "")) / "AutoHotkey/Compiler/Ahk2Exe.exe",
            Path(os.environ.get("ProgramFiles", "")) / "AutoHotkey/v2/Compiler/Ahk2Exe.exe",
        ]
        ahk = next((str(p) for p in ahk_paths if p.exists()), None)
        if not ahk:
            return False, "AutoHotkey compiler not found"
        
        cmd = [ahk, "/in", source, "/out", output]
        if icon and os.path.exists(icon):
            cmd.extend(["/icon", icon])
        return run_command(cmd)
    
    @staticmethod
    def compile_rb(source: str, output: str) -> tuple:
        """Compile Ruby script using Ocra."""
        return run_command(["ocra", source, "--output", output])
    
    @classmethod
    def compile(cls, source: str, output: str, file_type: str,
                icon: Optional[str] = None, admin: bool = False,
                console: bool = False, single_file: bool = True,
                metadata: Optional[Dict] = None) -> tuple:
        """Compile source file based on type."""
        compilers = {
            "ps1": lambda: cls.compile_ps1(source, output, icon, admin, not console, metadata),
            "py": lambda: cls.compile_py(source, output, icon, single_file, console),
            "bat": lambda: cls.compile_batch(source, output),
            "cmd": lambda: cls.compile_batch(source, output),
            "js": lambda: cls.compile_js(source, output),
            "vbs": lambda: cls.compile_batch(source, output),
            "ahk": lambda: cls.compile_ahk(source, output, icon),
            "cs": lambda: cls.compile_cs(source, output),
            "go": lambda: cls.compile_go(source, output),
            "rb": lambda: cls.compile_rb(source, output),
        }
        
        compiler = compilers.get(file_type)
        if compiler:
            return compiler()
        return False, f"Unsupported file type: {file_type}"


# ============================================================================
# SETUP WINDOW
# ============================================================================

class SetupWindow(ctk.CTkToplevel):
    """Dependency setup window."""
    
    def __init__(self, parent, theme: Dict):
        super().__init__(parent)
        self.theme = theme
        self.title("Universal Compiler - Setup")
        self.geometry("600x550")
        self.resizable(False, False)
        
        # Center window
        self.update_idletasks()
        x = (self.winfo_screenwidth() - 600) // 2
        y = (self.winfo_screenheight() - 550) // 2
        self.geometry(f"600x550+{x}+{y}")
        
        self.configure(fg_color=theme["bg"])
        self.checkboxes: Dict[str, ctk.CTkCheckBox] = {}
        self.completed = False
        
        self._create_ui()
        
        # Make modal
        self.transient(parent)
        self.grab_set()
    
    def _create_ui(self):
        # Header
        header = ctk.CTkFrame(self, fg_color=self.theme["card"], corner_radius=0)
        header.pack(fill="x", padx=0, pady=0)
        
        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(padx=20, pady=15)
        
        ctk.CTkLabel(
            title_frame, text="⚡ Universal Compiler", 
            font=("Segoe UI", 20, "bold"),
            text_color=self.theme["text1"]
        ).pack(side="left")
        
        ctk.CTkLabel(
            title_frame, text="v2.0",
            font=("Segoe UI", 10),
            text_color=self.theme["text3"]
        ).pack(side="left", padx=(10, 0), pady=(8, 0))
        
        ctk.CTkLabel(
            header, text="Select compilers to install",
            font=("Segoe UI", 11),
            text_color=self.theme["text2"]
        ).pack(padx=20, pady=(0, 15))
        
        # Dependency list
        deps_frame = ctk.CTkScrollableFrame(
            self, fg_color=self.theme["bg"],
            height=350
        )
        deps_frame.pack(fill="both", expand=True, padx=15, pady=10)
        
        deps = DependencyChecker.get_all_status()
        for key, dep in deps.items():
            self._create_dep_card(deps_frame, key, dep)
        
        # Progress bar (hidden initially)
        self.progress_frame = ctk.CTkFrame(self, fg_color=self.theme["log_bg"])
        self.progress_label = ctk.CTkLabel(
            self.progress_frame, text="Installing...",
            text_color=self.theme["text2"], font=("Segoe UI", 11)
        )
        self.progress_label.pack(padx=15, pady=(10, 5))
        self.progress_bar = ctk.CTkProgressBar(
            self.progress_frame, fg_color=self.theme["border"],
            progress_color=self.theme["green"]
        )
        self.progress_bar.pack(padx=15, pady=(0, 10), fill="x")
        self.progress_bar.set(0)
        
        # Bottom buttons
        bottom = ctk.CTkFrame(self, fg_color=self.theme["card"])
        bottom.pack(fill="x", side="bottom")
        
        btn_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_frame.pack(pady=15)
        
        self.skip_btn = ctk.CTkButton(
            btn_frame, text="Skip", width=100,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._on_skip
        )
        self.skip_btn.pack(side="left", padx=5)
        
        self.install_btn = ctk.CTkButton(
            btn_frame, text="Install Selected", width=140,
            fg_color=self.theme["green"],
            hover_color=self.theme["green_hover"],
            text_color=self.theme["bg"],
            font=("Segoe UI", 12, "bold"),
            command=self._on_install
        )
        self.install_btn.pack(side="left", padx=5)
    
    def _create_dep_card(self, parent, key: str, dep: Dict):
        card = ctk.CTkFrame(parent, fg_color=self.theme["card"], corner_radius=8)
        card.pack(fill="x", pady=4)
        
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)
        
        # Checkbox
        var = ctk.BooleanVar(value=not dep["installed"] and key == "PS2EXE")
        cb = ctk.CTkCheckBox(
            inner, text="", variable=var,
            fg_color=self.theme["green"],
            hover_color=self.theme["green_hover"],
            border_color=self.theme["border"],
            width=24, height=24
        )
        
        if dep["installed"] or dep.get("builtin"):
            cb.configure(state="disabled")
            var.set(False)
        
        cb.pack(side="left")
        self.checkboxes[key] = cb
        
        # Info
        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=(10, 0))
        
        name_frame = ctk.CTkFrame(info, fg_color="transparent")
        name_frame.pack(anchor="w")
        
        ctk.CTkLabel(
            name_frame, text=dep["name"],
            font=("Segoe UI", 13, "bold"),
            text_color=self.theme["text1"]
        ).pack(side="left")
        
        if dep["installed"]:
            badge = ctk.CTkLabel(
                name_frame, text="✓ Installed",
                font=("Segoe UI", 9),
                text_color=self.theme["green"],
                fg_color="#166534",
                corner_radius=3
            )
            badge.pack(side="left", padx=(8, 0))
        elif dep.get("builtin"):
            badge = ctk.CTkLabel(
                name_frame, text="Built-in",
                font=("Segoe UI", 9),
                text_color=self.theme["blue"],
                fg_color="#1e3a5f",
                corner_radius=3
            )
            badge.pack(side="left", padx=(8, 0))
        
        ctk.CTkLabel(
            info, text=dep["desc"],
            font=("Segoe UI", 10),
            text_color=self.theme["text2"]
        ).pack(anchor="w")
        
        # Size
        ctk.CTkLabel(
            inner, text=dep["size"],
            font=("Segoe UI", 10),
            text_color=self.theme["text3"]
        ).pack(side="right")
    
    def _on_skip(self):
        self.completed = True
        self.destroy()
    
    def _on_install(self):
        selected = [k for k, cb in self.checkboxes.items() 
                    if cb.cget("state") != "disabled" and cb.get()]
        
        if not selected:
            self.completed = True
            self.destroy()
            return
        
        self.install_btn.configure(state="disabled")
        self.skip_btn.configure(state="disabled")
        self.progress_frame.pack(fill="x", before=self.winfo_children()[-1])
        
        def install_thread():
            for i, dep in enumerate(selected):
                self.progress_label.configure(text=f"Installing {dep}...")
                self.progress_bar.set((i + 1) / len(selected))
                self.update()
                
                # Install logic here (placeholder)
                if dep == "PS2EXE":
                    run_command(["powershell", "-Command", 
                                "Install-Module ps2exe -Scope CurrentUser -Force"])
                elif dep == "PyInstaller":
                    run_command([sys.executable, "-m", "pip", "install", "pyinstaller"])
                
            self.progress_label.configure(text="Complete!")
            self.after(1000, self._finish)
        
        threading.Thread(target=install_thread, daemon=True).start()
    
    def _finish(self):
        self.completed = True
        self.destroy()


# ============================================================================
# MAIN APPLICATION
# ============================================================================

class UniversalCompiler:
    """Main application class."""
    
    def __init__(self):
        # Initialize managers
        self.settings = Settings()
        self.recent_files = RecentFiles(self.settings.get("max_recent_files", 10))
        self.profiles = BuildProfiles()
        self.history = CompilationHistory(self.settings.get("max_history_items", 50))
        
        # State
        self.source_file: Optional[str] = None
        self.file_type: Optional[str] = None
        self.compiling = False
        self.batch_queue: List[str] = []
        
        # Initialize templates
        initialize_templates()
        
        # Setup DPI awareness
        self._setup_dpi()
        
        # Create window
        self._create_window()
    
    def _setup_dpi(self):
        """Enable DPI awareness."""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    
    def _create_window(self):
        """Create main application window."""
        # Use TkinterDnD for drag & drop support if available
        self._has_dnd = False
        if HAS_DND:
            try:
                self.root = TkinterDnD.Tk()
                self._has_dnd = True
            except Exception:
                self.root = tk.Tk()
        else:
            self.root = tk.Tk()
        
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("1200x900")
        self.root.minsize(800, 600)
        
        # Maximize on start
        self.root.state("zoomed")
        
        # Get theme colors
        self.theme = THEMES[self.settings.theme]
        
        # Configure CustomTkinter
        ctk.set_appearance_mode("dark" if self.settings.theme == "Dark" else "light")
        ctk.set_default_color_theme("green")
        
        # Set background color using standard tk config
        self.root.configure(bg=self.theme["bg"])
        
        self._create_ui()
        self._setup_drag_drop()
    
    def _create_ui(self):
        """Create all UI elements."""
        # Main container
        main = ctk.CTkFrame(self.root, fg_color=self.theme["bg"])
        main.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Header
        self._create_header(main)
        
        # Content area
        content = ctk.CTkFrame(main, fg_color="transparent")
        content.pack(fill="both", expand=True, pady=(15, 0))
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(0, weight=1)
        
        # Left panel (scrollable)
        left_scroll = ctk.CTkScrollableFrame(content, fg_color="transparent")
        left_scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 15))
        
        self._create_source_section(left_scroll)
        self._create_output_section(left_scroll)
        self._create_options_section(left_scroll)
        self._create_postbuild_section(left_scroll)
        self._create_metadata_section(left_scroll)
        
        # Right panel
        right = ctk.CTkFrame(content, fg_color="transparent", width=320)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_propagate(False)
        
        self._create_queue_section(right)
        self._create_log_section(right)
        self._create_actions_section(right)
        
        # Footer
        ctk.CTkLabel(
            main,
            text="Universal Compiler v2.0 • Drag & Drop • Batch Build • Profiles • Code Signing",
            font=("Segoe UI", 9),
            text_color=self.theme["text3"]
        ).pack(pady=(10, 0))
    
    def _create_header(self, parent):
        """Create header section."""
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x")
        
        # Title
        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left")
        
        ctk.CTkLabel(
            title_frame, text="⚡",
            font=("Segoe UI", 26),
            text_color=self.theme["green"]
        ).pack(side="left")
        
        ctk.CTkLabel(
            title_frame, text="Universal Compiler",
            font=("Segoe UI", 24, "bold"),
            text_color=self.theme["text1"]
        ).pack(side="left", padx=(10, 0))
        
        ctk.CTkLabel(
            title_frame, text="v2.0",
            font=("Segoe UI", 10),
            text_color=self.theme["text3"]
        ).pack(side="left", padx=(8, 0), pady=(10, 0))
        
        # Subtitle
        ctk.CTkLabel(
            title_frame, text="Drag files here or browse to compile",
            font=("Segoe UI", 11),
            text_color=self.theme["text2"]
        ).pack(anchor="w", padx=(36, 0))
        
        # Header buttons
        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.pack(side="right")
        
        self.theme_btn = ctk.CTkButton(
            btn_frame, text="🌙" if self.settings.theme == "Dark" else "☀️",
            width=40, height=40,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._toggle_theme
        )
        self.theme_btn.pack(side="left", padx=(0, 8))
        
        ctk.CTkButton(
            btn_frame, text="⚙",
            width=40, height=40,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._show_settings
        ).pack(side="left")
    
    def _create_source_section(self, parent):
        """Create source file section."""
        card = ctk.CTkFrame(parent, fg_color=self.theme["card"], corner_radius=8)
        card.pack(fill="x", pady=(0, 12))
        
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)
        
        # Title
        ctk.CTkLabel(
            inner, text="📁 Source File",
            font=("Segoe UI", 13, "bold"),
            text_color=self.theme["text1"]
        ).pack(anchor="w", pady=(0, 10))
        
        # File input row
        input_row = ctk.CTkFrame(inner, fg_color="transparent")
        input_row.pack(fill="x")
        
        self.source_entry = ctk.CTkEntry(
            input_row, height=38,
            fg_color=self.theme["input"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"],
            state="readonly"
        )
        self.source_entry.pack(side="left", fill="x", expand=True)
        
        ctk.CTkButton(
            input_row, text="Browse", width=80,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._browse_source
        ).pack(side="left", padx=(8, 0))
        
        self.recent_btn = ctk.CTkButton(
            input_row, text="▼", width=40,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._show_recent_menu
        )
        self.recent_btn.pack(side="left", padx=(4, 0))
        
        # Info panel (hidden initially)
        self.info_frame = ctk.CTkFrame(inner, fg_color=self.theme["border"], corner_radius=6)
        
        info_inner = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        info_inner.pack(fill="x", padx=12, pady=10)
        
        # Info grid
        info_grid = ctk.CTkFrame(info_inner, fg_color="transparent")
        info_grid.pack(fill="x")
        
        labels = [
            ("Type:", "type_label", self.theme["blue"]),
            ("Size:", "size_label", self.theme["text2"]),
            ("Compiler:", "compiler_label", self.theme["green"]),
            ("Est. Output:", "est_label", self.theme["yellow"]),
            ("Status:", "status_label", self.theme["green"]),
        ]
        
        row = 0
        col = 0
        for label_text, attr_name, color in labels:
            ctk.CTkLabel(
                info_grid, text=label_text,
                font=("Segoe UI", 10),
                text_color=self.theme["text3"]
            ).grid(row=row, column=col * 2, sticky="w", padx=(0, 5))
            
            lbl = ctk.CTkLabel(
                info_grid, text="-",
                font=("Segoe UI", 10, "bold"),
                text_color=color
            )
            lbl.grid(row=row, column=col * 2 + 1, sticky="w", padx=(0, 20))
            setattr(self, attr_name, lbl)
            
            col += 1
            if col > 1:
                col = 0
                row += 1
        
        # Icon preview (hidden initially)
        self.icon_preview_frame = ctk.CTkFrame(inner, fg_color=self.theme["border"], corner_radius=6)
        self.icon_preview_label = ctk.CTkLabel(
            self.icon_preview_frame, text="",
            font=("Segoe UI", 10),
            text_color=self.theme["text2"]
        )
        self.icon_preview_label.pack(padx=10, pady=8)
    
    def _create_output_section(self, parent):
        """Create output section."""
        card = ctk.CTkFrame(parent, fg_color=self.theme["card"], corner_radius=8)
        card.pack(fill="x", pady=(0, 12))
        
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)
        
        ctk.CTkLabel(
            inner, text="📤 Output",
            font=("Segoe UI", 13, "bold"),
            text_color=self.theme["text1"]
        ).pack(anchor="w", pady=(0, 10))
        
        # Output name and directory
        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 10))
        
        # Output name
        name_frame = ctk.CTkFrame(row1, fg_color="transparent")
        name_frame.pack(side="left", fill="x", expand=True, padx=(0, 8))
        
        ctk.CTkLabel(
            name_frame, text="Output Name",
            font=("Segoe UI", 10),
            text_color=self.theme["text2"]
        ).pack(anchor="w", pady=(0, 4))
        
        self.output_name_entry = ctk.CTkEntry(
            name_frame, height=38,
            fg_color=self.theme["input"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"]
        )
        self.output_name_entry.pack(fill="x")
        
        # Output directory
        dir_frame = ctk.CTkFrame(row1, fg_color="transparent")
        dir_frame.pack(side="left", fill="x", expand=True)
        
        ctk.CTkLabel(
            dir_frame, text="Output Directory",
            font=("Segoe UI", 10),
            text_color=self.theme["text2"]
        ).pack(anchor="w", pady=(0, 4))
        
        dir_input = ctk.CTkFrame(dir_frame, fg_color="transparent")
        dir_input.pack(fill="x")
        
        self.output_dir_entry = ctk.CTkEntry(
            dir_input, height=38,
            fg_color=self.theme["input"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"],
            state="readonly"
        )
        self.output_dir_entry.pack(side="left", fill="x", expand=True)
        
        ctk.CTkButton(
            dir_input, text="...", width=40,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._browse_output_dir
        ).pack(side="left", padx=(4, 0))
        
        # Icon
        ctk.CTkLabel(
            inner, text="Custom Icon",
            font=("Segoe UI", 10),
            text_color=self.theme["text2"]
        ).pack(anchor="w", pady=(0, 4))
        
        icon_row = ctk.CTkFrame(inner, fg_color="transparent")
        icon_row.pack(fill="x")
        
        self.icon_entry = ctk.CTkEntry(
            icon_row, height=38,
            fg_color=self.theme["input"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"],
            state="readonly"
        )
        self.icon_entry.pack(side="left", fill="x", expand=True)
        
        ctk.CTkButton(
            icon_row, text="Browse", width=80,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._browse_icon
        ).pack(side="left", padx=(4, 0))
        
        ctk.CTkButton(
            icon_row, text="✕", width=40,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._clear_icon
        ).pack(side="left", padx=(4, 0))
    
    def _create_options_section(self, parent):
        """Create build options section."""
        card = ctk.CTkFrame(parent, fg_color=self.theme["card"], corner_radius=8)
        card.pack(fill="x", pady=(0, 12))
        
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)
        
        # Header with profile selector
        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))
        
        ctk.CTkLabel(
            header, text="🔧 Build Options",
            font=("Segoe UI", 13, "bold"),
            text_color=self.theme["text1"]
        ).pack(side="left")
        
        ctk.CTkLabel(
            header, text="Profile:",
            font=("Segoe UI", 10),
            text_color=self.theme["text3"]
        ).pack(side="left", padx=(20, 5))
        
        self.profile_combo = ctk.CTkComboBox(
            header, width=140,
            values=self.profiles.names(),
            fg_color=self.theme["input"],
            border_color=self.theme["border"],
            button_color=self.theme["border"],
            button_hover_color=self.theme["card_hover"],
            dropdown_fg_color=self.theme["card"],
            dropdown_hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._on_profile_change
        )
        self.profile_combo.set(self.settings.get("default_profile", "Default"))
        self.profile_combo.pack(side="left")
        
        ctk.CTkButton(
            header, text="💾", width=32,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._save_profile
        ).pack(side="left", padx=(4, 0))
        
        # Checkboxes
        checks = ctk.CTkFrame(inner, fg_color="transparent")
        checks.pack(fill="x")
        
        left_checks = ctk.CTkFrame(checks, fg_color="transparent")
        left_checks.pack(side="left", fill="x", expand=True)
        
        self.console_var = ctk.BooleanVar()
        self.console_check = ctk.CTkCheckBox(
            left_checks, text="Console Application",
            variable=self.console_var,
            fg_color=self.theme["green"],
            hover_color=self.theme["green_hover"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"]
        )
        self.console_check.pack(anchor="w", pady=3)
        
        self.admin_var = ctk.BooleanVar()
        self.admin_check = ctk.CTkCheckBox(
            left_checks, text="Require Administrator",
            variable=self.admin_var,
            fg_color=self.theme["green"],
            hover_color=self.theme["green_hover"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"]
        )
        self.admin_check.pack(anchor="w", pady=3)
        
        self.single_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            left_checks, text="Single File",
            variable=self.single_var,
            fg_color=self.theme["green"],
            hover_color=self.theme["green_hover"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"]
        ).pack(anchor="w", pady=3)
        
        right_checks = ctk.CTkFrame(checks, fg_color="transparent")
        right_checks.pack(side="left", fill="x", expand=True)
        
        self.sign_var = ctk.BooleanVar()
        ctk.CTkCheckBox(
            right_checks, text="Code Sign",
            variable=self.sign_var,
            fg_color=self.theme["green"],
            hover_color=self.theme["green_hover"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"]
        ).pack(anchor="w", pady=3)
        
        self.notify_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            right_checks, text="Notify on Complete",
            variable=self.notify_var,
            fg_color=self.theme["green"],
            hover_color=self.theme["green_hover"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"]
        ).pack(anchor="w", pady=3)
    
    def _create_postbuild_section(self, parent):
        """Create post-build action section."""
        card = ctk.CTkFrame(parent, fg_color=self.theme["card"], corner_radius=8)
        card.pack(fill="x", pady=(0, 12))
        
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)
        
        ctk.CTkLabel(
            inner, text="⚡ Post-Build Action",
            font=("Segoe UI", 13, "bold"),
            text_color=self.theme["text1"]
        ).pack(anchor="w", pady=(0, 10))
        
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")
        
        self.postbuild_combo = ctk.CTkComboBox(
            row, width=180,
            values=["None", "Open Output Folder", "Run Executable", "Copy to Folder..."],
            fg_color=self.theme["input"],
            border_color=self.theme["border"],
            button_color=self.theme["border"],
            button_hover_color=self.theme["card_hover"],
            dropdown_fg_color=self.theme["card"],
            dropdown_hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._on_postbuild_change
        )
        self.postbuild_combo.set("None")
        self.postbuild_combo.pack(side="left")
        
        self.postbuild_path_entry = ctk.CTkEntry(
            row, width=200, height=32,
            fg_color=self.theme["input"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"]
        )
        
        self.postbuild_path_btn = ctk.CTkButton(
            row, text="...", width=40,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._browse_postbuild_path
        )
    
    def _create_metadata_section(self, parent):
        """Create metadata section."""
        card = ctk.CTkFrame(parent, fg_color=self.theme["card"], corner_radius=8)
        card.pack(fill="x", pady=(0, 12))
        
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)
        
        ctk.CTkLabel(
            inner, text="📝 Metadata",
            font=("Segoe UI", 13, "bold"),
            text_color=self.theme["text1"]
        ).pack(anchor="w", pady=(0, 10))
        
        # Row 1: Product & Version
        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 8))
        
        for label, attr, default in [("Product Name", "product_entry", ""), ("Version", "version_entry", "1.0.0.0")]:
            frame = ctk.CTkFrame(row1, fg_color="transparent")
            frame.pack(side="left", fill="x", expand=True, padx=(0, 8) if attr == "product_entry" else 0)
            
            ctk.CTkLabel(frame, text=label, font=("Segoe UI", 10), text_color=self.theme["text2"]).pack(anchor="w", pady=(0, 4))
            entry = ctk.CTkEntry(frame, height=36, fg_color=self.theme["input"], border_color=self.theme["border"], text_color=self.theme["text1"])
            entry.insert(0, default)
            entry.pack(fill="x")
            setattr(self, attr, entry)
        
        # Row 2: Company & Copyright
        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 8))
        
        for label, attr in [("Company", "company_entry"), ("Copyright", "copyright_entry")]:
            frame = ctk.CTkFrame(row2, fg_color="transparent")
            frame.pack(side="left", fill="x", expand=True, padx=(0, 8) if attr == "company_entry" else 0)
            
            ctk.CTkLabel(frame, text=label, font=("Segoe UI", 10), text_color=self.theme["text2"]).pack(anchor="w", pady=(0, 4))
            entry = ctk.CTkEntry(frame, height=36, fg_color=self.theme["input"], border_color=self.theme["border"], text_color=self.theme["text1"])
            entry.pack(fill="x")
            setattr(self, attr, entry)
        
        # Description
        ctk.CTkLabel(inner, text="Description", font=("Segoe UI", 10), text_color=self.theme["text2"]).pack(anchor="w", pady=(0, 4))
        self.desc_entry = ctk.CTkTextbox(
            inner, height=60,
            fg_color=self.theme["input"],
            border_color=self.theme["border"],
            text_color=self.theme["text1"]
        )
        self.desc_entry.pack(fill="x")
    
    def _create_queue_section(self, parent):
        """Create batch queue section."""
        card = ctk.CTkFrame(parent, fg_color=self.theme["card"], corner_radius=8)
        card.pack(fill="x", pady=(0, 10))
        
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=12)
        
        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x", pady=(0, 8))
        
        ctk.CTkLabel(
            header, text="📋 Batch Queue",
            font=("Segoe UI", 12, "bold"),
            text_color=self.theme["text1"]
        ).pack(side="left")
        
        self.queue_count_label = ctk.CTkLabel(
            header, text=" (0)",
            font=("Segoe UI", 12),
            text_color=self.theme["text3"]
        )
        self.queue_count_label.pack(side="left")
        
        self.queue_listbox = ctk.CTkTextbox(
            inner, height=70,
            fg_color=self.theme["log_bg"],
            text_color=self.theme["text2"],
            font=("Consolas", 10)
        )
        self.queue_listbox.pack(fill="x")
        self.queue_listbox.configure(state="disabled")
        
        btn_row = ctk.CTkFrame(inner, fg_color="transparent")
        btn_row.pack(fill="x", pady=(8, 0))
        
        ctk.CTkButton(
            btn_row, text="+ Add", width=60,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            font=("Segoe UI", 10),
            command=self._add_to_queue
        ).pack(side="left")
        
        ctk.CTkButton(
            btn_row, text="Clear", width=60,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            font=("Segoe UI", 10),
            command=self._clear_queue
        ).pack(side="left", padx=(4, 0))
    
    def _create_log_section(self, parent):
        """Create build log section."""
        card = ctk.CTkFrame(parent, fg_color=self.theme["card"], corner_radius=8)
        card.pack(fill="both", expand=True, pady=(0, 10))
        
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=12)
        
        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x", pady=(0, 8))
        
        ctk.CTkLabel(
            header, text="📜 Build Log",
            font=("Segoe UI", 12, "bold"),
            text_color=self.theme["text1"]
        ).pack(side="left")
        
        ctk.CTkButton(
            header, text="Clear", width=50,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            font=("Segoe UI", 9),
            height=24,
            command=self._clear_log
        ).pack(side="left", padx=(8, 0))
        
        ctk.CTkButton(
            header, text="Export", width=50,
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            font=("Segoe UI", 9),
            height=24,
            command=self._export_log
        ).pack(side="left", padx=(4, 0))
        
        self.log_text = ctk.CTkTextbox(
            inner,
            fg_color=self.theme["log_bg"],
            text_color=self.theme["text2"],
            font=("Consolas", 10)
        )
        self.log_text.pack(fill="both", expand=True)
    
    def _create_actions_section(self, parent):
        """Create actions section."""
        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(fill="x")
        
        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(
            actions,
            fg_color=self.theme["border"],
            progress_color=self.theme["green"],
            height=6
        )
        self.progress_bar.set(0)
        
        # Status label
        self.status_label = ctk.CTkLabel(
            actions, text="Ready",
            font=("Segoe UI", 10),
            text_color=self.theme["text3"]
        )
        self.status_label.pack(pady=(0, 8))
        
        # Main buttons row
        btn_row1 = ctk.CTkFrame(actions, fg_color="transparent")
        btn_row1.pack(fill="x")
        
        ctk.CTkButton(
            btn_row1, text="Manage Deps",
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._show_setup
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        
        self.compile_btn = ctk.CTkButton(
            btn_row1, text="⚡ Compile",
            fg_color=self.theme["green"],
            hover_color=self.theme["green_hover"],
            text_color=self.theme["bg"],
            font=("Segoe UI", 12, "bold"),
            command=self._compile,
            state="disabled"
        )
        self.compile_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))
        
        # Compile all button (hidden initially)
        self.compile_all_btn = ctk.CTkButton(
            actions, text="Compile All in Queue",
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._compile_all
        )
        
        # Secondary buttons
        btn_row2 = ctk.CTkFrame(actions, fg_color="transparent")
        btn_row2.pack(fill="x", pady=(8, 0))
        
        ctk.CTkButton(
            btn_row2, text="📄 Templates",
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._open_templates
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        
        ctk.CTkButton(
            btn_row2, text="📊 History",
            fg_color=self.theme["border"],
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            command=self._show_history
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))
    
    def _setup_drag_drop(self):
        """Setup drag and drop handling."""
        if not self._has_dnd or not HAS_DND:
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
        except Exception as e:
            print(f"Drag & drop setup failed: {e}")
    
    # ========================================================================
    # EVENT HANDLERS
    # ========================================================================
    
    def _on_drop(self, event):
        """Handle file drop."""
        files = self.root.tk.splitlist(event.data)
        if len(files) == 1:
            self._load_file(files[0])
        else:
            for f in files:
                self.batch_queue.append(f)
            self._update_queue_display()
            self.log(f"Added {len(files)} files to queue")
    
    def _browse_source(self):
        """Browse for source file."""
        filetypes = [
            ("All Scripts", "*.ps1 *.py *.bat *.cmd *.js *.vbs *.ahk *.cs *.go *.rb"),
            ("All Files", "*.*")
        ]
        filepath = filedialog.askopenfilename(filetypes=filetypes)
        if filepath:
            self._load_file(filepath)
    
    def _show_recent_menu(self):
        """Show recent files menu."""
        recent = self.recent_files.get_all()
        if not recent:
            messagebox.showinfo("Recent Files", "No recent files")
            return
        
        menu = tk.Menu(self.root, tearoff=0, bg=self.theme["card"], fg=self.theme["text1"])
        for filepath in recent:
            menu.add_command(
                label=os.path.basename(filepath),
                command=lambda f=filepath: self._load_file(f)
            )
        
        # Position menu below button
        x = self.recent_btn.winfo_rootx()
        y = self.recent_btn.winfo_rooty() + self.recent_btn.winfo_height()
        menu.post(x, y)
    
    def _browse_output_dir(self):
        """Browse for output directory."""
        dirpath = filedialog.askdirectory()
        if dirpath:
            self.output_dir_entry.configure(state="normal")
            self.output_dir_entry.delete(0, "end")
            self.output_dir_entry.insert(0, dirpath)
            self.output_dir_entry.configure(state="readonly")
    
    def _browse_icon(self):
        """Browse for icon file."""
        filepath = filedialog.askopenfilename(filetypes=[("Icons", "*.ico"), ("All Files", "*.*")])
        if filepath:
            self.icon_entry.configure(state="normal")
            self.icon_entry.delete(0, "end")
            self.icon_entry.insert(0, filepath)
            self.icon_entry.configure(state="readonly")
            
            # Show preview
            self.icon_preview_label.configure(text=f"🖼️ {os.path.basename(filepath)}")
            self.icon_preview_frame.pack(fill="x", pady=(10, 0))
    
    def _clear_icon(self):
        """Clear selected icon."""
        self.icon_entry.configure(state="normal")
        self.icon_entry.delete(0, "end")
        self.icon_entry.configure(state="readonly")
        self.icon_preview_frame.pack_forget()
    
    def _on_profile_change(self, profile_name: str):
        """Handle profile selection change."""
        profile = self.profiles.get(profile_name)
        if profile:
            self.console_var.set(profile.get("console", False))
            self.admin_var.set(profile.get("admin", False))
            self.single_var.set(profile.get("single_file", True))
            self.version_entry.delete(0, "end")
            self.version_entry.insert(0, profile.get("version", "1.0.0.0"))
            self.company_entry.delete(0, "end")
            self.company_entry.insert(0, profile.get("company", ""))
            self.copyright_entry.delete(0, "end")
            self.copyright_entry.insert(0, profile.get("copyright", ""))
            self.product_entry.delete(0, "end")
            self.product_entry.insert(0, profile.get("product", ""))
    
    def _save_profile(self):
        """Save current settings as profile."""
        name = self.profile_combo.get()
        if not name:
            name = "Custom"
        
        profile = {
            "console": self.console_var.get(),
            "admin": self.admin_var.get(),
            "single_file": self.single_var.get(),
            "version": self.version_entry.get(),
            "company": self.company_entry.get(),
            "copyright": self.copyright_entry.get(),
            "product": self.product_entry.get(),
            "description": self.desc_entry.get("1.0", "end-1c"),
        }
        
        self.profiles.set(name, profile)
        
        # Update combo if new profile
        if name not in self.profile_combo.cget("values"):
            values = list(self.profile_combo.cget("values")) + [name]
            self.profile_combo.configure(values=values)
        
        self.log(f"Profile '{name}' saved", "success")
    
    def _on_postbuild_change(self, value: str):
        """Handle post-build action change."""
        if value == "Copy to Folder...":
            self.postbuild_path_entry.pack(side="left", padx=(8, 0))
            self.postbuild_path_btn.pack(side="left", padx=(4, 0))
        else:
            self.postbuild_path_entry.pack_forget()
            self.postbuild_path_btn.pack_forget()
    
    def _browse_postbuild_path(self):
        """Browse for post-build copy path."""
        dirpath = filedialog.askdirectory()
        if dirpath:
            self.postbuild_path_entry.delete(0, "end")
            self.postbuild_path_entry.insert(0, dirpath)
    
    def _toggle_theme(self):
        """Toggle between dark and light theme."""
        new_theme = "Light" if self.settings.theme == "Dark" else "Dark"
        self.settings.theme = new_theme
        messagebox.showinfo("Theme Changed", f"Theme changed to {new_theme}. Please restart the app.")
    
    def _show_settings(self):
        """Show settings dialog."""
        messagebox.showinfo(
            "Settings",
            f"Theme: {self.settings.theme}\n"
            f"Notifications: {self.settings.get('show_notifications')}\n"
            f"Recent Files: {self.settings.get('max_recent_files')}\n"
            f"History Items: {self.settings.get('max_history_items')}"
        )
    
    def _show_setup(self):
        """Show setup window."""
        setup = SetupWindow(self.root, self.theme)
        self.root.wait_window(setup)
        
        # Refresh compiler status
        if self.file_type:
            available = DependencyChecker.check_compiler(self.file_type)
            self.status_label.configure(
                text="Ready" if available else "Compiler not installed",
                text_color=self.theme["green"] if available else self.theme["red"]
            )
            self.compile_btn.configure(state="normal" if available else "disabled")
    
    def _add_to_queue(self):
        """Add files to batch queue."""
        filetypes = [
            ("All Scripts", "*.ps1 *.py *.bat *.cmd *.js *.vbs *.ahk *.cs *.go *.rb"),
            ("All Files", "*.*")
        ]
        files = filedialog.askopenfilenames(filetypes=filetypes)
        for f in files:
            if f not in self.batch_queue:
                self.batch_queue.append(f)
        self._update_queue_display()
    
    def _clear_queue(self):
        """Clear batch queue."""
        self.batch_queue.clear()
        self._update_queue_display()
    
    def _update_queue_display(self):
        """Update queue display."""
        self.queue_listbox.configure(state="normal")
        self.queue_listbox.delete("1.0", "end")
        for f in self.batch_queue:
            self.queue_listbox.insert("end", f"{os.path.basename(f)}\n")
        self.queue_listbox.configure(state="disabled")
        
        self.queue_count_label.configure(text=f" ({len(self.batch_queue)})")
        
        if self.batch_queue:
            self.compile_all_btn.pack(fill="x", pady=(8, 0))
        else:
            self.compile_all_btn.pack_forget()
    
    def _clear_log(self):
        """Clear build log."""
        self.log_text.delete("1.0", "end")
    
    def _export_log(self):
        """Export build log."""
        if not self.source_file:
            return
        
        log_content = self.log_text.get("1.0", "end-1c")
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        basename = os.path.splitext(os.path.basename(self.source_file))[0]
        
        desktop = Path.home() / "Desktop"
        log_path = desktop / f"BuildLog_{basename}_{timestamp}.txt"
        
        header = f"""================================================================================
Universal Compiler v{APP_VERSION} - Build Log
================================================================================
Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Source: {self.source_file}
================================================================================

"""
        
        log_path.write_text(header + log_content, encoding="utf-8")
        self.log(f"Log exported to: {log_path}", "success")
        os.startfile(str(desktop))
    
    def _open_templates(self):
        """Open templates folder."""
        os.startfile(str(TEMPLATES_DIR))
    
    def _show_history(self):
        """Show compilation history."""
        history = self.history.get_all()
        if not history:
            messagebox.showinfo("History", "No compilation history")
            return
        
        msg = ""
        for h in history[:10]:
            status = "✓" if h["success"] else "✗"
            source = os.path.basename(h["source"])
            msg += f"[{status}] {source}\n"
        
        messagebox.showinfo("Recent Builds", msg)
    
    # ========================================================================
    # FILE LOADING
    # ========================================================================
    
    def _load_file(self, filepath: str):
        """Load source file."""
        if not os.path.exists(filepath):
            self.log(f"File not found: {filepath}", "error")
            return
        
        self.source_file = filepath
        self.recent_files.add(filepath)
        
        # Update source entry
        self.source_entry.configure(state="normal")
        self.source_entry.delete(0, "end")
        self.source_entry.insert(0, filepath)
        self.source_entry.configure(state="readonly")
        
        # Get file type
        ext = os.path.splitext(filepath)[1].lstrip(".").lower()
        
        if ext in COMPILERS:
            self.file_type = ext
            compiler_info = COMPILERS[ext]
            file_size = os.path.getsize(filepath)
            
            # Update info panel
            self.type_label.configure(text=compiler_info["desc"])
            self.size_label.configure(text=format_size(file_size))
            self.compiler_label.configure(text=compiler_info["compiler"])
            self.est_label.configure(text=estimate_output_size(filepath, ext))
            
            # Check compiler availability
            available = DependencyChecker.check_compiler(ext)
            self.status_label.configure(
                text="Ready" if available else "Compiler not installed",
                text_color=self.theme["green"] if available else self.theme["red"]
            )
            
            # Show info panel
            self.info_frame.pack(fill="x", pady=(10, 0))
            
            # Set default output
            self.output_name_entry.delete(0, "end")
            self.output_name_entry.insert(0, os.path.splitext(os.path.basename(filepath))[0] + ".exe")
            
            self.output_dir_entry.configure(state="normal")
            self.output_dir_entry.delete(0, "end")
            self.output_dir_entry.insert(0, os.path.dirname(filepath))
            self.output_dir_entry.configure(state="readonly")
            
            # Enable compile button
            self.compile_btn.configure(state="normal" if available else "disabled")
            
            self.log(f"Loaded: {filepath}", "success")
        else:
            self.file_type = None
            self.info_frame.pack_forget()
            self.compile_btn.configure(state="disabled")
            self.log(f"Unsupported file type: {ext}", "error")
    
    # ========================================================================
    # COMPILATION
    # ========================================================================
    
    def _compile(self):
        """Compile current file."""
        if self.compiling or not self.source_file or not self.file_type:
            return
        
        self.compiling = True
        self.compile_btn.configure(state="disabled")
        
        # Get output path
        output_name = self.output_name_entry.get().strip()
        if not output_name.endswith(".exe"):
            output_name += ".exe"
        output_dir = self.output_dir_entry.get().strip()
        output_path = os.path.join(output_dir, output_name)
        
        # Get options
        icon = self.icon_entry.get().strip() or None
        admin = self.admin_var.get()
        console = self.console_var.get()
        single_file = self.single_var.get()
        metadata = {
            "product": self.product_entry.get(),
            "version": self.version_entry.get(),
            "company": self.company_entry.get(),
            "copyright": self.copyright_entry.get(),
            "description": self.desc_entry.get("1.0", "end-1c"),
        }
        
        # Start compilation in thread
        def compile_thread():
            self.log("=" * 40)
            self.log(f"Source: {self.source_file}")
            self.log(f"Output: {output_path}")
            self.status_label.configure(text="Compiling...")
            self.progress_bar.pack(fill="x", pady=(0, 8))
            self.progress_bar.set(0.3)
            
            success, output = Compiler.compile(
                self.source_file, output_path, self.file_type,
                icon, admin, console, single_file, metadata
            )
            
            self.progress_bar.set(0.9)
            
            if success and os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                self.log("=" * 40, "success")
                self.log("BUILD SUCCESSFUL", "success")
                self.log(f"Size: {format_size(file_size)}", "success")
                
                self.history.add(
                    self.source_file, output_path, self.file_type,
                    True, self.profile_combo.get(), file_size
                )
                
                # Post-build action
                postbuild = self.postbuild_combo.get()
                if postbuild == "Open Output Folder":
                    os.startfile(output_dir)
                elif postbuild == "Run Executable":
                    os.startfile(output_path)
                elif postbuild == "Copy to Folder...":
                    copy_path = self.postbuild_path_entry.get()
                    if copy_path:
                        shutil.copy(output_path, copy_path)
                        self.log(f"Copied to {copy_path}")
                
                # Notification
                if self.notify_var.get():
                    show_notification("Build Complete", f"{output_name} compiled successfully")
            else:
                self.log("BUILD FAILED", "error")
                self.log(output, "error")
                
                self.history.add(
                    self.source_file, output_path, self.file_type,
                    False, self.profile_combo.get(), 0
                )
                
                if self.notify_var.get():
                    show_notification("Build Failed", "Compilation failed")
            
            self.progress_bar.set(1.0)
            self.root.after(500, lambda: self.progress_bar.pack_forget())
            self.status_label.configure(text="Ready")
            self.compiling = False
            self.compile_btn.configure(state="normal")
        
        threading.Thread(target=compile_thread, daemon=True).start()
    
    def _compile_all(self):
        """Compile all files in queue."""
        if not self.batch_queue:
            return
        
        self.compile_all_btn.configure(state="disabled")
        total = len(self.batch_queue)
        done = 0
        
        for filepath in self.batch_queue.copy():
            self._load_file(filepath)
            if self.file_type and DependencyChecker.check_compiler(self.file_type):
                self._compile()
                # Wait for compilation to complete
                while self.compiling:
                    self.root.update()
                done += 1
        
        self.batch_queue.clear()
        self._update_queue_display()
        self.log(f"Batch complete: {done}/{total} files", "success")
        
        if self.notify_var.get():
            show_notification("Batch Complete", f"{done} of {total} files compiled")
    
    # ========================================================================
    # LOGGING
    # ========================================================================
    
    def log(self, message: str, level: str = "info"):
        """Add message to build log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "[*]", "success": "[OK]", "warning": "[!]", "error": "[X]"}.get(level, "[*]")
        
        self.log_text.insert("end", f"{timestamp} {prefix} {message}\n")
        self.log_text.see("end")
    
    # ========================================================================
    # RUN
    # ========================================================================
    
    def run(self):
        """Start the application."""
        # Initial log messages
        self.log(f"Universal Compiler v{APP_VERSION} ready", "success")
        self.log("Drag files here or click Browse")
        
        if DependencyChecker.check_ps2exe():
            self.log("PS2EXE: Ready", "success")
        else:
            self.log("PS2EXE: Not installed", "warning")
        
        self.root.mainloop()


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    app = UniversalCompiler()
    app.run()
