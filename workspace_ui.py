"""Premium multi-page workspace for the optional Universal Compiler GUI.

The compiler engine remains in ``compiler_core.py``.  This module owns only
the desktop information architecture and view refresh logic so CLI imports
stay side-effect free and the GUI can evolve without duplicating build rules.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import customtkinter as ctk  # type: ignore[import-untyped]
import tkinter as tk
from tkinter import filedialog

from compiler_core import BACKEND_NAMES, backend_status


NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("build", "Build", "▣"),
    ("queue", "Queue", "≡"),
    ("history", "History", "↺"),
    ("toolchains", "Toolchains", "⚙"),
    ("templates", "Templates", "▤"),
    ("settings", "Settings", "◉"),
)

TEMPLATE_LABELS: Mapping[str, tuple[str, str]] = {
    ".ps1": ("PowerShell utility", "PowerShell"),
    ".py": ("Python application", "Python"),
    ".bat": ("Batch task", "Batch"),
    ".js": ("Node.js script", "JavaScript"),
    ".ts": ("TypeScript script", "TypeScript"),
    ".cs": ("C# console app", "C#"),
    ".go": ("Go command", "Go"),
    ".rb": ("Ruby script", "Ruby"),
    ".vbs": ("VBScript task", "VBScript"),
    ".ahk": ("AutoHotkey automation", "AutoHotkey"),
    ".rs": ("Rust command", "Rust"),
    ".lua": ("Lua script", "Lua"),
    ".pl": ("Perl script", "Perl"),
    ".kt": ("Kotlin command", "Kotlin"),
    ".wat": ("WebAssembly module", "WebAssembly"),
}


class WorkspaceController:
    """Build and coordinate the persistent navigation workspace."""

    plan_backend_label: Any
    plan_arch_label: Any
    plan_mode_label: Any
    plan_verification_label: Any
    plan_size_label: Any

    def __init__(
        self,
        app: Any,
        *,
        version: str,
        templates_dir: Path,
        compiler_catalog: Mapping[str, Mapping[str, str]],
    ) -> None:
        self.app = app
        self.version = version
        self.templates_dir = templates_dir
        self.compiler_catalog = compiler_catalog
        self.pages: dict[str, Any] = {}
        self.nav_buttons: dict[str, Any] = {}
        self.current_page = "build"
        self.queue_selected_index: int | None = None
        self.history_selected_index: int | None = None
        self.toolchain_selected: str | None = None
        self.template_selected: Path | None = None
        self.toolchain_data: dict[str, dict[str, Any]] = {}
        self.toolchain_scanning = False
        self.settings_sections: dict[str, Any] = {}
        self.settings_nav_buttons: dict[str, Any] = {}

    @property
    def theme(self) -> Mapping[str, str]:
        return self.app.theme

    @property
    def accent(self) -> str:
        return self.theme.get("accent", self.theme["blue"])

    @property
    def accent_hover(self) -> str:
        return self.theme.get("accent_hover", self.theme["blue"])

    @property
    def accent_soft(self) -> str:
        return self.theme.get("accent_soft", self.theme["border"])

    @property
    def surface(self) -> str:
        return self.theme.get("surface", self.theme["card"])

    @property
    def divider(self) -> str:
        return self.theme.get("divider", self.theme["border"])

    def _accessible(self, widget: Any, key: str, role: str) -> Any:
        return self.app._register_accessible(widget, key, role)

    def _surface(
        self,
        parent: Any,
        *,
        corner_radius: int = 10,
        border_width: int = 1,
        fg_color: str | None = None,
    ) -> Any:
        return ctk.CTkFrame(
            parent,
            fg_color=fg_color or self.surface,
            border_color=self.divider,
            border_width=border_width,
            corner_radius=corner_radius,
        )

    def _primary_button(
        self,
        parent: Any,
        text: str,
        command: Callable[[], None],
        *,
        width: int = 120,
    ) -> Any:
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=42,
            corner_radius=7,
            fg_color=self.accent,
            hover_color=self.accent_hover,
            text_color="#ffffff",
            font=("Segoe UI", 12, "bold"),
        )

    def _secondary_button(
        self,
        parent: Any,
        text: str,
        command: Callable[[], None],
        *,
        width: int = 100,
    ) -> Any:
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=40,
            corner_radius=7,
            fg_color="transparent",
            hover_color=self.theme["card_hover"],
            border_color=self.divider,
            border_width=1,
            text_color=self.theme["text1"],
            font=("Segoe UI", 11),
        )

    def _page_header(self, parent: Any, title: str, subtitle: str) -> Any:
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", pady=(4, 22))
        copy = ctk.CTkFrame(header, fg_color="transparent")
        copy.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            copy,
            text=title,
            font=("Segoe UI", 28, "bold"),
            text_color=self.theme["text1"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            copy,
            text=subtitle,
            font=("Segoe UI", 13),
            text_color=self.theme["text2"],
        ).pack(anchor="w", pady=(4, 0))
        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.pack(side="right", padx=(18, 0))
        return actions

    def _section_title(self, parent: Any, title: str, subtitle: str = "") -> None:
        ctk.CTkLabel(
            parent,
            text=title,
            font=("Segoe UI", 13, "bold"),
            text_color=self.theme["text1"],
        ).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(
                parent,
                text=subtitle,
                font=("Segoe UI", 10),
                text_color=self.theme["text2"],
            ).pack(anchor="w", pady=(2, 0))

    def create(self) -> None:
        shell = ctk.CTkFrame(self.app.root, fg_color=self.theme["bg"], corner_radius=0)
        shell.pack(fill="both", expand=True)

        self._create_sidebar(shell)
        self.page_host = ctk.CTkFrame(shell, fg_color=self.theme["bg"], corner_radius=0)
        self.page_host.pack(side="left", fill="both", expand=True, padx=(30, 26), pady=(24, 22))

        self._create_build_page()
        self._create_queue_page()
        self._create_history_page()
        self._create_toolchains_page()
        self._create_templates_page()
        self._create_settings_page()
        self.show_page("build")

    def _create_sidebar(self, parent: Any) -> None:
        self.sidebar_color = self.theme.get("sidebar", "#0b1220")
        sidebar = ctk.CTkFrame(
            parent,
            width=220,
            fg_color=self.sidebar_color,
            corner_radius=0,
            border_width=0,
        )
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = ctk.CTkFrame(sidebar, fg_color=self.sidebar_color, height=92)
        brand.pack(fill="x", padx=18, pady=(18, 8))
        brand.pack_propagate(False)
        mark = ctk.CTkFrame(
            brand,
            width=38,
            height=38,
            fg_color=self.accent_soft,
            border_color=self.accent,
            border_width=1,
            corner_radius=8,
        )
        mark.pack(side="left", pady=(5, 0))
        mark.pack_propagate(False)
        ctk.CTkLabel(
            mark,
            text="◇",
            text_color=self.accent,
            font=("Segoe UI Symbol", 24, "bold"),
        ).pack(expand=True)
        brand_copy = ctk.CTkFrame(brand, fg_color=self.sidebar_color)
        brand_copy.pack(side="left", padx=(10, 0), pady=(4, 0))
        ctk.CTkLabel(
            brand_copy,
            text="Universal Compiler",
            font=("Segoe UI", 13, "bold"),
            text_color=self.theme["text1"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand_copy,
            text=f"v{self.version}",
            font=("Segoe UI", 10),
            text_color=self.theme["text3"],
        ).pack(anchor="w", pady=(1, 0))

        nav = ctk.CTkFrame(sidebar, fg_color=self.sidebar_color)
        nav.pack(fill="x", padx=10, pady=(8, 0))
        for key, label, icon in NAV_ITEMS:
            button = self._accessible(
                ctk.CTkButton(
                    nav,
                    text=f"{icon}    {label}",
                    anchor="w",
                    height=48,
                    corner_radius=7,
                    fg_color=self.sidebar_color,
                    hover_color=self.theme["card_hover"],
                    text_color=self.theme["text2"],
                    font=("Segoe UI", 14),
                    command=lambda page=key: self.show_page(page),
                ),
                f"navigation.{key}",
                "tab",
            )
            button.pack(fill="x", pady=2)
            self.nav_buttons[key] = button

        footer = ctk.CTkFrame(sidebar, fg_color=self.sidebar_color)
        footer.pack(side="bottom", fill="x", padx=18, pady=(0, 24))
        ctk.CTkFrame(footer, height=1, fg_color=self.divider).pack(fill="x", pady=(0, 18))
        mode = ctk.CTkFrame(footer, fg_color=self.sidebar_color)
        mode.pack(fill="x")
        ctk.CTkLabel(
            mode,
            text="●",
            text_color=self.theme["green"],
            font=("Segoe UI", 11),
        ).pack(side="left")
        ctk.CTkLabel(
            mode,
            text="Local mode",
            text_color=self.theme["text2"],
            font=("Segoe UI", 11),
        ).pack(side="left", padx=(8, 0))
        self.app_status_label = ctk.CTkLabel(
            footer,
            text="Offline by default",
            text_color=self.theme["text3"],
            font=("Segoe UI", 9),
        )
        self.app_status_label.pack(anchor="w", pady=(8, 0))

    def _new_page(self, key: str, *, scrollable: bool = True) -> Any:
        page_type = ctk.CTkScrollableFrame if scrollable else ctk.CTkFrame
        page = page_type(
            self.page_host,
            fg_color=self.theme["bg"],
            corner_radius=0,
            scrollbar_button_color=self.divider if scrollable else None,
            scrollbar_button_hover_color=self.theme["card_hover"] if scrollable else None,
        ) if scrollable else page_type(self.page_host, fg_color=self.theme["bg"], corner_radius=0)
        self.pages[key] = page
        return page

    def show_page(self, name: str) -> None:
        if name not in self.pages:
            return
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        self.current_page = name
        for key, button in self.nav_buttons.items():
            active = key == name
            button.configure(
                fg_color=self.accent_soft if active else self.sidebar_color,
                text_color=self.theme["text1"] if active else self.theme["text2"],
                border_width=1 if active else 0,
                border_color=self.accent if active else self.theme.get("sidebar", self.theme["bg"]),
            )
        if name == "queue":
            self.refresh_queue()
        elif name == "history":
            self.refresh_history()
        elif name == "toolchains":
            self.refresh_toolchains()
        elif name == "templates":
            self.refresh_templates()

    # ------------------------------------------------------------------
    # Build page
    # ------------------------------------------------------------------

    def _create_build_page(self) -> None:
        page = self._new_page("build")
        actions = self._page_header(
            page,
            "Build workspace",
            "Turn source into a verified artifact.",
        )

        self.app.profile_combo = self._accessible(
            ctk.CTkComboBox(
                actions,
                width=150,
                height=42,
                values=self.app.profiles.names(),
                fg_color=self.surface,
                border_color=self.divider,
                button_color=self.divider,
                button_hover_color=self.theme["card_hover"],
                dropdown_fg_color=self.surface,
                dropdown_hover_color=self.theme["card_hover"],
                text_color=self.theme["text1"],
                command=self.app._on_profile_change,
            ),
            "profile",
            "combobox",
        )
        self.app.profile_combo.set(self.app.settings.get("default_profile", "Default"))
        self.app.profile_combo.pack(side="left", padx=(0, 8))
        self.app.save_profile_btn = self._accessible(
            self._secondary_button(actions, "Save profile", self.app._save_profile, width=104),
            "actions.save_profile",
            "button",
        )
        self.app.save_profile_btn.pack(side="left", padx=(0, 8))
        self.app.cancel_btn = self._accessible(
            self._secondary_button(actions, "Cancel", self.app._cancel_compile, width=78),
            "accessibility.cancel",
            "button",
        )
        self.app.cancel_btn.configure(state="disabled", border_color=self.theme["red"])
        self.app.cancel_btn.pack(side="left", padx=(0, 8))
        self.app.compile_btn = self._accessible(
            self._primary_button(actions, "Build", self.app._compile, width=112),
            "actions.compile",
            "button",
        )
        self.app.compile_btn.configure(state="disabled")
        self.app.compile_btn.pack(side="left")

        source = self._surface(page)
        source.pack(fill="x", pady=(0, 14))
        source_inner = ctk.CTkFrame(source, fg_color="transparent")
        source_inner.pack(fill="x", padx=18, pady=10)
        self._section_title(source_inner, "Source", "Drop one supported script or choose it from disk.")

        drop = ctk.CTkFrame(
            source_inner,
            height=52,
            fg_color=self.theme.get("dropzone", self.theme["bg"]),
            border_color=self.theme.get("border_strong", self.divider),
            border_width=1,
            corner_radius=8,
        )
        drop.pack(fill="x", pady=(6, 5))
        drop.pack_propagate(False)
        drop_content = ctk.CTkFrame(drop, fg_color="transparent")
        drop_content.pack(expand=True)
        ctk.CTkLabel(
            drop_content,
            text="Drop source file",
            font=("Segoe UI", 13, "bold"),
            text_color=self.theme["text1"],
        ).pack(side="left", padx=(0, 10))
        self.app.browse_source_btn = self._accessible(
            self._secondary_button(drop_content, "Browse", self.app._browse_source, width=86),
            "actions.browse",
            "button",
        )
        self.app.browse_source_btn.configure(height=30)
        self.app.browse_source_btn.pack(side="left")

        source_row = ctk.CTkFrame(source_inner, fg_color="transparent")
        source_row.pack(fill="x")
        self.app.source_entry = self._accessible(
            ctk.CTkEntry(
                source_row,
                height=30,
                fg_color=self.theme["input"],
                border_color=self.divider,
                text_color=self.theme["text1"],
                state="readonly",
                placeholder_text="No source selected",
            ),
            "source.file",
            "textbox",
        )
        self.app.source_entry.pack(side="left", fill="x", expand=True)
        self.app.recent_btn = self._accessible(
            self._secondary_button(source_row, "Recent", self.app._show_recent_menu, width=82),
            "actions.recent",
            "button",
        )
        self.app.recent_btn.configure(height=30)
        self.app.recent_btn.pack(side="left", padx=(8, 0))

        self.app.info_frame = ctk.CTkFrame(
            source_inner,
            fg_color=self.accent_soft,
            border_color=self.divider,
            border_width=1,
            corner_radius=7,
        )
        info_grid = ctk.CTkFrame(self.app.info_frame, fg_color="transparent")
        info_grid.pack(fill="x", padx=14, pady=4)
        info_specs = (
            ("Type", "type_label", self.theme["text1"]),
            ("Size", "size_label", self.theme["text2"]),
            ("Backend", "compiler_label", self.accent),
            ("Estimate", "est_label", self.theme["yellow"]),
        )
        for column, (label, attr, color) in enumerate(info_specs):
            info_grid.grid_columnconfigure(column, weight=1)
            cell = ctk.CTkFrame(info_grid, fg_color="transparent")
            cell.grid(row=0, column=column, sticky="ew", padx=(0, 12))
            ctk.CTkLabel(
                cell,
                text=label.upper(),
                font=("Segoe UI", 7, "bold"),
                text_color=self.theme["text3"],
            ).pack(anchor="w")
            value = ctk.CTkLabel(
                cell,
                text="—",
                font=("Segoe UI", 9, "bold"),
                text_color=color,
            )
            value.pack(anchor="w")
            setattr(self.app, attr, value)

        configuration = ctk.CTkFrame(page, fg_color="transparent")
        configuration.pack(fill="x", pady=(0, 14))
        configuration.grid_columnconfigure(0, weight=3)
        configuration.grid_columnconfigure(1, weight=2)
        self._create_build_configuration(configuration)
        self._create_build_plan(configuration)
        self._create_build_console(page)
        self._create_advanced_build(page)

    def _form_label(self, parent: Any, text: str, row: int) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            font=("Segoe UI", 11),
            text_color=self.theme["text2"],
        ).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=3)

    def _create_build_configuration(self, parent: Any) -> None:
        panel = self._surface(parent)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=18, pady=10)
        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(
            header,
            text="Build configuration",
            font=("Segoe UI", 13, "bold"),
            text_color=self.theme["text1"],
        ).pack(side="left")
        self.advanced_toggle = ctk.CTkButton(
            header,
            text="Advanced",
            command=self.toggle_advanced,
            width=80,
            height=26,
            fg_color="transparent",
            hover_color=self.theme["card_hover"],
            border_width=0,
            text_color=self.accent,
            font=("Segoe UI", 10),
        )
        self.advanced_toggle.pack(side="right")

        form = ctk.CTkFrame(inner, fg_color="transparent")
        form.pack(fill="x")
        form.grid_columnconfigure(1, weight=1)

        self._form_label(form, "Output", 0)
        output = ctk.CTkFrame(form, fg_color="transparent")
        output.grid(row=0, column=1, sticky="ew", pady=2)
        output.grid_columnconfigure(0, weight=2)
        output.grid_columnconfigure(1, weight=3)
        self.app.output_name_entry = self._accessible(
            ctk.CTkEntry(
                output,
                height=30,
                fg_color=self.theme["input"],
                border_color=self.divider,
                text_color=self.theme["text1"],
                placeholder_text="artifact.exe",
            ),
            "output.name",
            "textbox",
        )
        self.app.output_name_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        output_dir = ctk.CTkFrame(output, fg_color="transparent")
        output_dir.grid(row=0, column=1, sticky="ew")
        output_dir.grid_columnconfigure(0, weight=1)
        self.app.output_dir_entry = self._accessible(
            ctk.CTkEntry(
                output_dir,
                height=30,
                fg_color=self.theme["input"],
                border_color=self.divider,
                text_color=self.theme["text1"],
                state="readonly",
                placeholder_text="Output directory",
            ),
            "output.directory",
            "textbox",
        )
        self.app.output_dir_entry.grid(row=0, column=0, sticky="ew")
        self.app.output_dir_btn = self._accessible(
            ctk.CTkButton(
                output_dir,
                text="…",
                command=self.app._browse_output_dir,
                width=38,
                height=30,
                fg_color=self.theme["border"],
                hover_color=self.theme["card_hover"],
                text_color=self.theme["text1"],
            ),
            "output.directory",
            "button",
        )
        self.app.output_dir_btn.grid(row=0, column=1, padx=(5, 0))

        self._form_label(form, "Backend", 1)
        self.app.backend_combo = self._accessible(
            ctk.CTkComboBox(
                form,
                height=30,
                values=["auto"],
                fg_color=self.theme["input"],
                border_color=self.divider,
                button_color=self.divider,
                button_hover_color=self.theme["card_hover"],
                dropdown_fg_color=self.surface,
                dropdown_hover_color=self.theme["card_hover"],
                text_color=self.theme["text1"],
                command=self.app._on_backend_change,
            ),
            "backend",
            "combobox",
        )
        self.app.backend_combo.set("auto")
        self.app.backend_combo.grid(row=1, column=1, sticky="ew", pady=2)

        self._form_label(form, "Architecture", 2)
        self.app.architecture_combo = self._accessible(
            ctk.CTkComboBox(
                form,
                height=30,
                values=["native", "x86", "x64", "arm64"],
                fg_color=self.theme["input"],
                border_color=self.divider,
                button_color=self.divider,
                button_hover_color=self.theme["card_hover"],
                dropdown_fg_color=self.surface,
                dropdown_hover_color=self.theme["card_hover"],
                text_color=self.theme["text1"],
                command=lambda _value: self.refresh_build_plan(),
            ),
            "architecture",
            "combobox",
        )
        self.app.architecture_combo.set("native")
        self.app.architecture_combo.grid(row=2, column=1, sticky="ew", pady=2)

        switches = ctk.CTkFrame(inner, fg_color="transparent")
        switches.pack(fill="x", pady=(2, 0))
        for column in range(3):
            switches.grid_columnconfigure(column, weight=1)
        self.app.console_var = tk.BooleanVar(value=False)
        self.app.single_var = tk.BooleanVar(value=True)
        self.app.verify_var = tk.BooleanVar(value=True)
        switch_specs = (
            ("Console", self.app.console_var, "options.console"),
            ("Single file", self.app.single_var, "options.single_file"),
            ("Verify artifact", self.app.verify_var, "options.verify"),
        )
        for column, (text, variable, key) in enumerate(switch_specs):
            switch = self._accessible(
                ctk.CTkSwitch(
                    switches,
                    text=text,
                    variable=variable,
                    progress_color=self.accent,
                    button_color="#ffffff",
                    button_hover_color="#ffffff",
                    text_color=self.theme["text1"],
                    command=self.refresh_build_plan,
                ),
                key,
                "checkbox",
            )
            switch.grid(row=0, column=column, sticky="w", padx=(0, 10))

    def _create_build_plan(self, parent: Any) -> None:
        panel = self._surface(parent)
        panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=18, pady=10)
        self._section_title(inner, "Build plan", "Resolved from the selected source and policy.")
        body = ctk.CTkFrame(inner, fg_color="transparent")
        body.pack(fill="both", expand=True, pady=(6, 0))
        body.grid_columnconfigure(1, weight=1)
        plan_rows = (
            ("Backend", "plan_backend_label"),
            ("Architecture", "plan_arch_label"),
            ("Mode", "plan_mode_label"),
            ("Verification", "plan_verification_label"),
            ("Estimated size", "plan_size_label"),
        )
        for row, (label, attribute) in enumerate(plan_rows):
            ctk.CTkLabel(
                body,
                text=label,
                font=("Segoe UI", 10),
                text_color=self.theme["text2"],
            ).grid(row=row, column=0, sticky="w", pady=3)
            value = ctk.CTkLabel(
                body,
                text="—",
                font=("Segoe UI", 10, "bold"),
                text_color=self.theme["text1"],
            )
            value.grid(row=row, column=1, sticky="e", pady=3)
            setattr(self, attribute, value)
        ctk.CTkFrame(body, height=1, fg_color=self.divider).grid(
            row=len(plan_rows), column=0, columnspan=2, sticky="ew", pady=(4, 4)
        )
        ctk.CTkLabel(
            body,
            text="Builds are offline unless access is explicitly allowed.",
            wraplength=320,
            justify="left",
            font=("Segoe UI", 9),
            text_color=self.theme["text3"],
        ).grid(row=len(plan_rows) + 1, column=0, columnspan=2, sticky="w")
        self.refresh_build_plan()

    def _create_build_console(self, parent: Any) -> None:
        panel = self._surface(parent)
        panel.pack(fill="x", pady=(0, 14))
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=10)
        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x", pady=(0, 4))
        self.log_view = ctk.CTkSegmentedButton(
            header,
            values=["Build log", "Source preview"],
            command=self.switch_log_view,
            height=30,
            selected_color=self.accent_soft,
            selected_hover_color=self.accent_soft,
            unselected_color=self.theme["input"],
            unselected_hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            border_width=1,
        )
        self.log_view.set("Build log")
        self.log_view.pack(side="left")
        self.app.export_log_btn = self._accessible(
            ctk.CTkButton(
                header,
                text="Export",
                command=self.app._export_log,
                width=62,
                height=28,
                fg_color="transparent",
                hover_color=self.theme["card_hover"],
                text_color=self.theme["text2"],
            ),
            "actions.export",
            "button",
        )
        self.app.export_log_btn.pack(side="right")
        self.app.clear_log_btn = self._accessible(
            ctk.CTkButton(
                header,
                text="Clear",
                command=self.app._clear_log,
                width=56,
                height=28,
                fg_color="transparent",
                hover_color=self.theme["card_hover"],
                text_color=self.theme["text2"],
            ),
            "actions.clear",
            "button",
        )
        self.app.clear_log_btn.pack(side="right", padx=(0, 2))
        self.app.jump_error_btn = self._accessible(
            ctk.CTkButton(
                header,
                text="Jump to error",
                command=self.app._jump_to_error,
                width=92,
                height=28,
                fg_color="transparent",
                hover_color=self.theme["card_hover"],
                text_color=self.theme["text2"],
                state="disabled",
            ),
            "actions.jump_error",
            "button",
        )
        self.app.jump_error_btn.pack(side="right", padx=(0, 2))

        console = ctk.CTkFrame(inner, fg_color="transparent", height=145)
        console.pack(fill="x")
        console.pack_propagate(False)
        self.app.log_text = self._accessible(
            ctk.CTkTextbox(
                console,
                fg_color=self.theme["log_bg"],
                border_color=self.divider,
                border_width=1,
                text_color=self.theme["text2"],
                font=("Cascadia Mono", 10),
            ),
            "accessibility.log",
            "log",
        )
        self.app.source_preview = self._accessible(
            ctk.CTkTextbox(
                console,
                fg_color=self.theme["log_bg"],
                border_color=self.divider,
                border_width=1,
                text_color=self.theme["text2"],
                font=("Cascadia Mono", 10),
            ),
            "log.source_preview",
            "textbox",
        )
        self.app.source_preview.configure(state="disabled")
        self.app.log_text.pack(fill="both", expand=True)
        self.app.error_line = None

        status = ctk.CTkFrame(inner, fg_color="transparent")
        status.pack(fill="x", pady=(8, 0))
        self.app.status_label = ctk.CTkLabel(
            status,
            text=self.app._tr("status.ready", "Ready"),
            font=("Segoe UI", 10),
            text_color=self.theme["text3"],
        )
        self.app.status_label.pack(side="left")
        self.app.progress_bar = ctk.CTkProgressBar(
            status,
            fg_color=self.divider,
            progress_color=self.accent,
            height=5,
        )
        self.app.progress_bar.set(0)

    def _create_advanced_build(self, parent: Any) -> None:
        self.advanced_panel = self._surface(parent)
        inner = ctk.CTkFrame(self.advanced_panel, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)
        self._section_title(
            inner,
            "Packaging details",
            "Optional icon, metadata, elevation, notifications, and post-build behavior.",
        )
        columns = ctk.CTkFrame(inner, fg_color="transparent")
        columns.pack(fill="x", pady=(12, 0))
        columns.grid_columnconfigure(0, weight=1)
        columns.grid_columnconfigure(1, weight=1)
        left = ctk.CTkFrame(columns, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right = ctk.CTkFrame(columns, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        ctk.CTkLabel(left, text="Custom icon", text_color=self.theme["text2"], font=("Segoe UI", 10)).pack(anchor="w")
        icon_row = ctk.CTkFrame(left, fg_color="transparent")
        icon_row.pack(fill="x", pady=(4, 10))
        self.app.icon_entry = self._accessible(
            ctk.CTkEntry(
                icon_row,
                height=36,
                fg_color=self.theme["input"],
                border_color=self.divider,
                text_color=self.theme["text1"],
                state="readonly",
            ),
            "output.custom_icon",
            "textbox",
        )
        self.app.icon_entry.pack(side="left", fill="x", expand=True)
        self.app.icon_browse_btn = self._accessible(
            self._secondary_button(icon_row, "Browse", self.app._browse_icon, width=68),
            "output.custom_icon",
            "button",
        )
        self.app.icon_browse_btn.configure(height=36)
        self.app.icon_browse_btn.pack(side="left", padx=(5, 0))
        self.app.icon_clear_btn = self._accessible(
            self._secondary_button(icon_row, "Clear", self.app._clear_icon, width=54),
            "actions.clear",
            "button",
        )
        self.app.icon_clear_btn.configure(height=36)
        self.app.icon_clear_btn.pack(side="left", padx=(5, 0))
        self.app.icon_extract_btn = self._accessible(
            self._secondary_button(icon_row, "From EXE", self.app._extract_icon, width=76),
            "actions.from_exe",
            "button",
        )
        self.app.icon_extract_btn.configure(height=36)
        self.app.icon_extract_btn.pack(side="left", padx=(5, 0))
        self.app.icon_preview_frame = ctk.CTkFrame(left, fg_color=self.accent_soft, corner_radius=6)
        self.app.icon_preview_label = ctk.CTkLabel(
            self.app.icon_preview_frame,
            text="",
            font=("Segoe UI", 10),
            text_color=self.theme["text2"],
        )
        self.app.icon_preview_label.pack(padx=10, pady=6)

        ctk.CTkLabel(left, text="Post-build action", text_color=self.theme["text2"], font=("Segoe UI", 10)).pack(anchor="w")
        postbuild_row = ctk.CTkFrame(left, fg_color="transparent")
        postbuild_row.pack(fill="x", pady=(4, 10))
        self.app.postbuild_combo = self._accessible(
            ctk.CTkComboBox(
                postbuild_row,
                width=180,
                height=36,
                values=["None", "Open Output Folder", "Copy to Folder..."],
                fg_color=self.theme["input"],
                border_color=self.divider,
                button_color=self.divider,
                button_hover_color=self.theme["card_hover"],
                dropdown_fg_color=self.surface,
                text_color=self.theme["text1"],
                command=self.app._on_postbuild_change,
            ),
            "post_build.action",
            "combobox",
        )
        self.app.postbuild_combo.set(self.app.settings.get("post_build_action", "None"))
        self.app.postbuild_combo.pack(side="left")
        self.app.postbuild_path_entry = self._accessible(
            ctk.CTkEntry(
                postbuild_row,
                height=36,
                fg_color=self.theme["input"],
                border_color=self.divider,
                text_color=self.theme["text1"],
            ),
            "output.directory",
            "textbox",
        )
        self.app.postbuild_path_btn = self._accessible(
            self._secondary_button(postbuild_row, "…", self.app._browse_postbuild_path, width=40),
            "output.directory",
            "button",
        )
        self.app.postbuild_path_btn.configure(height=36)

        self.app.admin_var = tk.BooleanVar(value=False)
        self.app.notify_var = tk.BooleanVar(value=self.app.settings.get("show_notifications", True))
        advanced_switches = ctk.CTkFrame(left, fg_color="transparent")
        advanced_switches.pack(fill="x")
        for text, variable, key in (
            ("Require administrator", self.app.admin_var, "options.admin"),
            ("Notify on completion", self.app.notify_var, "options.notify"),
        ):
            switch = self._accessible(
                ctk.CTkSwitch(
                    advanced_switches,
                    text=text,
                    variable=variable,
                    progress_color=self.accent,
                    text_color=self.theme["text1"],
                ),
                key,
                "checkbox",
            )
            switch.pack(anchor="w", pady=3)

        metadata_fields = (
            ("Product name", "product_entry", "metadata.product", ""),
            ("Version", "version_entry", "metadata.version", "1.0.0.0"),
            ("Company", "company_entry", "metadata.company", ""),
            ("Copyright", "copyright_entry", "metadata.copyright", ""),
        )
        for label, attr, key, default in metadata_fields:
            ctk.CTkLabel(right, text=label, text_color=self.theme["text2"], font=("Segoe UI", 10)).pack(anchor="w")
            entry = self._accessible(
                ctk.CTkEntry(
                    right,
                    height=34,
                    fg_color=self.theme["input"],
                    border_color=self.divider,
                    text_color=self.theme["text1"],
                ),
                key,
                "textbox",
            )
            if default:
                entry.insert(0, default)
            entry.pack(fill="x", pady=(3, 7))
            setattr(self.app, attr, entry)
        ctk.CTkLabel(right, text="Description", text_color=self.theme["text2"], font=("Segoe UI", 10)).pack(anchor="w")
        self.app.desc_entry = self._accessible(
            ctk.CTkTextbox(
                right,
                height=70,
                fg_color=self.theme["input"],
                border_color=self.divider,
                border_width=1,
                text_color=self.theme["text1"],
            ),
            "metadata.description",
            "textbox",
        )
        self.app.desc_entry.pack(fill="x", pady=(3, 0))

    def toggle_advanced(self) -> None:
        if self.advanced_panel.winfo_manager():
            self.advanced_panel.pack_forget()
            self.advanced_toggle.configure(text="Advanced")
        else:
            self.advanced_panel.pack(fill="x", pady=(0, 14))
            self.advanced_toggle.configure(text="Hide advanced")

    def switch_log_view(self, value: str) -> None:
        self.app.log_text.pack_forget()
        self.app.source_preview.pack_forget()
        if value == "Source preview":
            self.app.source_preview.pack(fill="both", expand=True)
        else:
            self.app.log_text.pack(fill="both", expand=True)

    def refresh_build_plan(self) -> None:
        backend = "auto"
        if hasattr(self.app, "backend_combo"):
            backend = self.app.backend_combo.get() or "auto"
        resolved = None
        if self.app.file_type:
            resolved = self.app.engine.choose_backend(self.app.file_type, backend)
        backend_text = BACKEND_NAMES.get(resolved or backend, (resolved or backend).title())
        architecture = "Native"
        if hasattr(self.app, "architecture_combo"):
            architecture = self.app.architecture_combo.get().replace("native", "Native")
        verify_variable = getattr(self.app, "verify_var", None)
        verify = True if verify_variable is None else bool(verify_variable.get())
        if hasattr(self, "plan_backend_label"):
            self.plan_backend_label.configure(text=backend_text)
            self.plan_arch_label.configure(text=architecture)
            self.plan_mode_label.configure(text="Offline")
            self.plan_verification_label.configure(text="Static verification" if verify else "Disabled")
            self.plan_size_label.configure(
                text=self.app.est_label.cget("text") if hasattr(self.app, "est_label") else "—"
            )

    # ------------------------------------------------------------------
    # Queue page
    # ------------------------------------------------------------------

    def _create_queue_page(self) -> None:
        page = self._new_page("queue")
        actions = self._page_header(
            page,
            "Build queue",
            "Review, reorder, and run a verified batch.",
        )
        self.app.add_queue_btn = self._accessible(
            self._secondary_button(actions, "Add files", self.app._add_to_queue, width=86),
            "actions.add",
            "button",
        )
        self.app.add_queue_btn.pack(side="left", padx=(0, 8))
        self.app.clear_queue_btn = self._accessible(
            self._secondary_button(actions, "Clear queue", self.app._clear_queue, width=96),
            "actions.clear",
            "button",
        )
        self.app.clear_queue_btn.pack(side="left", padx=(0, 8))
        self.app.compile_all_btn = self._accessible(
            self._primary_button(actions, "Build all", self.app._compile_all, width=110),
            "actions.compile_all",
            "button",
        )
        self.app.compile_all_btn.configure(state="disabled")
        self.app.compile_all_btn.pack(side="left")

        summary = self._surface(page)
        summary.pack(fill="x", pady=(0, 14))
        summary_grid = ctk.CTkFrame(summary, fg_color="transparent")
        summary_grid.pack(fill="x", padx=16, pady=12)
        self.queue_metrics: dict[str, Any] = {}
        for column, (key, label) in enumerate(
            (("queued", "queued"), ("ready", "ready"), ("blocked", "blocked"), ("completed", "completed"))
        ):
            summary_grid.grid_columnconfigure(column, weight=1)
            cell = ctk.CTkFrame(summary_grid, fg_color="transparent")
            cell.grid(row=0, column=column, sticky="ew", padx=(0, 10))
            value = ctk.CTkLabel(
                cell,
                text="0",
                font=("Segoe UI", 18, "bold"),
                text_color=self.theme["text1"],
            )
            value.pack(anchor="w")
            ctk.CTkLabel(
                cell,
                text=label,
                font=("Segoe UI", 10),
                text_color=self.theme["text2"],
            ).pack(anchor="w")
            self.queue_metrics[key] = value

        table = self._surface(page)
        table.pack(fill="x", pady=(0, 14))
        table_inner = ctk.CTkFrame(table, fg_color="transparent")
        table_inner.pack(fill="x", padx=1, pady=1)
        header = ctk.CTkFrame(table_inner, fg_color=self.theme.get("surface_alt", self.surface), height=42)
        header.pack(fill="x")
        header.pack_propagate(False)
        columns = (("Source", 4), ("Backend", 2), ("Target", 2), ("Status", 2), ("Output", 3))
        for column, (label, weight) in enumerate(columns):
            header.grid_columnconfigure(column, weight=weight)
            ctk.CTkLabel(
                header,
                text=label,
                font=("Segoe UI", 10, "bold"),
                text_color=self.theme["text2"],
            ).grid(row=0, column=column, sticky="w", padx=14, pady=11)
        self.queue_rows = ctk.CTkFrame(table_inner, fg_color="transparent")
        self.queue_rows.pack(fill="x")

        footer = ctk.CTkFrame(page, fg_color="transparent")
        footer.pack(fill="x")
        footer.grid_columnconfigure(0, weight=3)
        footer.grid_columnconfigure(1, weight=2)
        selection = self._surface(footer)
        selection.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        selection_inner = ctk.CTkFrame(selection, fg_color="transparent")
        selection_inner.pack(fill="both", expand=True, padx=16, pady=14)
        self.queue_selection_label = ctk.CTkLabel(
            selection_inner,
            text="Select a queued source to manage it.",
            font=("Segoe UI", 11, "bold"),
            text_color=self.theme["text1"],
        )
        self.queue_selection_label.pack(anchor="w")
        controls = ctk.CTkFrame(selection_inner, fg_color="transparent")
        controls.pack(fill="x", pady=(12, 10))
        self.queue_move_up = self._secondary_button(
            controls, "Move up", lambda: self.move_queue_item(-1), width=78
        )
        self.queue_move_up.pack(side="left")
        self.queue_move_down = self._secondary_button(
            controls, "Move down", lambda: self.move_queue_item(1), width=84
        )
        self.queue_move_down.pack(side="left", padx=(6, 0))
        self.queue_remove = self._secondary_button(
            controls, "Remove", self.remove_queue_item, width=72
        )
        self.queue_remove.configure(border_color=self.theme["red"])
        self.queue_remove.pack(side="left", padx=(6, 0))
        self.queue_progress = ctk.CTkProgressBar(
            selection_inner,
            height=4,
            fg_color=self.divider,
            progress_color=self.accent,
        )
        self.queue_progress.set(0)
        self.queue_progress.pack(fill="x")

        policy = self._surface(footer)
        policy.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        policy_inner = ctk.CTkFrame(policy, fg_color="transparent")
        policy_inner.pack(fill="both", expand=True, padx=16, pady=14)
        self._section_title(policy_inner, "Batch policy")
        for label, value in (
            ("Execution", "Serial"),
            ("Stop on failure", "Off"),
            ("Static verification", "On"),
            ("Network", "Offline"),
        ):
            row = ctk.CTkFrame(policy_inner, fg_color="transparent")
            row.pack(fill="x", pady=6)
            ctk.CTkLabel(row, text=label, text_color=self.theme["text2"], font=("Segoe UI", 10)).pack(side="left")
            ctk.CTkLabel(row, text=value, text_color=self.theme["text1"], font=("Segoe UI", 10, "bold")).pack(side="right")

    def refresh_queue(self) -> None:
        queue_items = list(self.app.batch_queue)
        count = len(queue_items)
        self.nav_buttons["queue"].configure(text="≡    Queue" + (f"    {count}" if count else ""))
        self.queue_metrics["queued"].configure(text=str(count))
        self.queue_metrics["ready"].configure(text=str(count))
        self.queue_metrics["blocked"].configure(text="0")
        self.queue_metrics["completed"].configure(text="0")
        self.app.compile_all_btn.configure(state="normal" if count and not self.app.compiling else "disabled")
        self.app.clear_queue_btn.configure(state="normal" if count else "disabled")

        if self.queue_selected_index is not None and self.queue_selected_index >= count:
            self.queue_selected_index = count - 1 if count else None
        for child in self.queue_rows.winfo_children():
            child.destroy()
        if not queue_items:
            ctk.CTkLabel(
                self.queue_rows,
                text="No queued sources. Add files or drop multiple scripts onto the app.",
                font=("Segoe UI", 11),
                text_color=self.theme["text3"],
            ).pack(anchor="w", padx=16, pady=28)
        else:
            for index, filepath in enumerate(queue_items):
                self._create_queue_row(index, Path(filepath))

        selected = self.queue_selected_index
        enabled = selected is not None
        for button in (self.queue_move_up, self.queue_move_down, self.queue_remove):
            button.configure(state="normal" if enabled else "disabled")
        if enabled and selected is not None:
            path = Path(queue_items[selected])
            self.queue_selection_label.configure(text=f"Selected: {path.name}")
            self.queue_progress.set((selected + 1) / max(1, count))
        else:
            self.queue_selection_label.configure(text="Select a queued source to manage it.")
            self.queue_progress.set(0)

    def _create_queue_row(self, index: int, path: Path) -> None:
        selected = index == self.queue_selected_index
        row = ctk.CTkFrame(
            self.queue_rows,
            height=64,
            corner_radius=0,
            fg_color=self.accent_soft if selected else "transparent",
            border_width=0,
        )
        row.pack(fill="x")
        row.pack_propagate(False)
        for column, weight in enumerate((4, 2, 2, 2, 3)):
            row.grid_columnconfigure(column, weight=weight)
        extension = path.suffix.lower().lstrip(".")
        backend = self.app.engine.choose_backend(extension, "auto") if extension else None
        backend_name = BACKEND_NAMES.get(backend or "auto", (backend or "Auto").title())
        source = ctk.CTkButton(
            row,
            text=path.name,
            command=lambda value=index: self.select_queue_item(value),
            anchor="w",
            height=54,
            fg_color="transparent",
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            font=("Segoe UI", 11, "bold"),
        )
        source.grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=5)
        source_ready = path.exists() or bool(getattr(self.app, "_ui_snapshot_mode", False))
        values = (
            backend_name,
            "Windows native",
            "Ready" if source_ready else "Missing",
            path.with_suffix(".exe").name,
        )
        colors = (self.theme["text2"], self.theme["text2"], self.theme["green"] if source_ready else self.theme["red"], self.theme["text2"])
        for offset, (value, color) in enumerate(zip(values, colors), start=1):
            ctk.CTkLabel(
                row,
                text=value,
                anchor="w",
                font=("Segoe UI", 10),
                text_color=color,
            ).grid(row=0, column=offset, sticky="ew", padx=12)
        ctk.CTkFrame(self.queue_rows, height=1, fg_color=self.divider).pack(fill="x")

    def select_queue_item(self, index: int) -> None:
        self.queue_selected_index = index
        self.refresh_queue()

    def move_queue_item(self, delta: int) -> None:
        index = self.queue_selected_index
        if index is None:
            return
        target = index + delta
        if target < 0 or target >= len(self.app.batch_queue):
            return
        self.app.batch_queue[index], self.app.batch_queue[target] = (
            self.app.batch_queue[target],
            self.app.batch_queue[index],
        )
        self.queue_selected_index = target
        self.refresh_queue()

    def remove_queue_item(self) -> None:
        index = self.queue_selected_index
        if index is None or index >= len(self.app.batch_queue):
            return
        self.app.batch_queue.pop(index)
        self.queue_selected_index = min(index, len(self.app.batch_queue) - 1) if self.app.batch_queue else None
        self.refresh_queue()

    # ------------------------------------------------------------------
    # History page
    # ------------------------------------------------------------------

    def _create_history_page(self) -> None:
        page = self._new_page("history")
        actions = self._page_header(
            page,
            "Build history",
            "Trace artifacts, outcomes, and verification evidence.",
        )
        self.history_search = ctk.CTkEntry(
            actions,
            width=210,
            height=40,
            fg_color=self.surface,
            border_color=self.divider,
            text_color=self.theme["text1"],
            placeholder_text="Search builds",
        )
        self.history_search.pack(side="left", padx=(0, 8))
        self.history_search.bind("<Return>", lambda _event: self.refresh_history())
        self.history_filter = ctk.CTkComboBox(
            actions,
            width=128,
            height=40,
            values=["All results", "Successful", "Failed"],
            fg_color=self.surface,
            border_color=self.divider,
            button_color=self.divider,
            dropdown_fg_color=self.surface,
            text_color=self.theme["text1"],
            command=lambda _value: self.refresh_history(),
        )
        self.history_filter.set("All results")
        self.history_filter.pack(side="left", padx=(0, 8))
        self._secondary_button(actions, "Export", self.export_history, width=76).pack(side="left")

        summary = self._surface(page)
        summary.pack(fill="x", pady=(0, 14))
        summary_grid = ctk.CTkFrame(summary, fg_color="transparent")
        summary_grid.pack(fill="x", padx=16, pady=12)
        self.history_metrics: dict[str, Any] = {}
        for column, (key, label) in enumerate(
            (("builds", "builds"), ("successful", "successful"), ("rate", "success"), ("produced", "produced"))
        ):
            summary_grid.grid_columnconfigure(column, weight=1)
            cell = ctk.CTkFrame(summary_grid, fg_color="transparent")
            cell.grid(row=0, column=column, sticky="ew")
            value = ctk.CTkLabel(cell, text="0", font=("Segoe UI", 18, "bold"), text_color=self.theme["text1"])
            value.pack(anchor="w")
            ctk.CTkLabel(cell, text=label, font=("Segoe UI", 10), text_color=self.theme["text2"]).pack(anchor="w")
            self.history_metrics[key] = value

        table = self._surface(page)
        table.pack(fill="x", pady=(0, 14))
        table_inner = ctk.CTkFrame(table, fg_color="transparent")
        table_inner.pack(fill="x", padx=1, pady=1)
        header = ctk.CTkFrame(table_inner, fg_color=self.theme.get("surface_alt", self.surface), height=40)
        header.pack(fill="x")
        header.pack_propagate(False)
        for column, (label, weight) in enumerate(
            (("Source", 4), ("Result", 2), ("Backend", 2), ("Artifact", 3), ("Size", 1), ("Built", 2))
        ):
            header.grid_columnconfigure(column, weight=weight)
            ctk.CTkLabel(header, text=label, font=("Segoe UI", 10, "bold"), text_color=self.theme["text2"]).grid(
                row=0, column=column, sticky="w", padx=12, pady=10
            )
        self.history_rows = ctk.CTkFrame(table_inner, fg_color="transparent")
        self.history_rows.pack(fill="x")

        details = self._surface(page)
        details.pack(fill="x")
        detail_inner = ctk.CTkFrame(details, fg_color="transparent")
        detail_inner.pack(fill="x", padx=18, pady=15)
        detail_header = ctk.CTkFrame(detail_inner, fg_color="transparent")
        detail_header.pack(fill="x")
        self.history_detail_title = ctk.CTkLabel(
            detail_header,
            text="Select a build",
            font=("Segoe UI", 14, "bold"),
            text_color=self.theme["text1"],
        )
        self.history_detail_title.pack(side="left")
        self.history_rebuild_button = self._secondary_button(detail_header, "Rebuild", self.rebuild_history_item, width=78)
        self.history_rebuild_button.configure(state="disabled")
        self.history_rebuild_button.pack(side="right")
        grid = ctk.CTkFrame(detail_inner, fg_color="transparent")
        grid.pack(fill="x", pady=(12, 0))
        grid.grid_columnconfigure(1, weight=1)
        grid.grid_columnconfigure(3, weight=1)
        self.history_detail_values: dict[str, Any] = {}
        for position, (key, label) in enumerate(
            (("source", "Source"), ("output", "Output"), ("backend", "Backend"), ("result", "Result"))
        ):
            row, group = divmod(position, 2)
            label_column = group * 2
            ctk.CTkLabel(grid, text=label, font=("Segoe UI", 10), text_color=self.theme["text3"]).grid(
                row=row, column=label_column, sticky="w", padx=(0, 10), pady=5
            )
            value = ctk.CTkLabel(grid, text="—", font=("Segoe UI", 10), text_color=self.theme["text2"], anchor="w")
            value.grid(row=row, column=label_column + 1, sticky="ew", padx=(0, 24), pady=5)
            self.history_detail_values[key] = value

    def refresh_history(self) -> None:
        snapshot_history = getattr(self, "snapshot_history", None)
        all_items = list(snapshot_history if snapshot_history is not None else self.app.history.get_all())
        successful = sum(1 for item in all_items if bool(item.get("success")))
        total_size = sum(int(item.get("size", 0) or 0) for item in all_items)
        self.history_metrics["builds"].configure(text=str(len(all_items)))
        self.history_metrics["successful"].configure(text=str(successful))
        self.history_metrics["rate"].configure(text=f"{(successful / len(all_items) * 100):.1f}%" if all_items else "0%")
        self.history_metrics["produced"].configure(text=self.app.catalog.format_size(total_size))

        query = self.history_search.get().strip().lower()
        result_filter = self.history_filter.get()
        filtered: list[dict[str, Any]] = []
        for item in all_items:
            if query and query not in str(item.get("source", "")).lower() and query not in str(item.get("output", "")).lower():
                continue
            if result_filter == "Successful" and not item.get("success"):
                continue
            if result_filter == "Failed" and item.get("success"):
                continue
            filtered.append(item)
        self.filtered_history = filtered
        if self.history_selected_index is not None and self.history_selected_index >= len(filtered):
            self.history_selected_index = None
        for child in self.history_rows.winfo_children():
            child.destroy()
        if not filtered:
            ctk.CTkLabel(
                self.history_rows,
                text="No builds match the current history filter.",
                font=("Segoe UI", 11),
                text_color=self.theme["text3"],
            ).pack(anchor="w", padx=16, pady=28)
        else:
            for index, item in enumerate(filtered[:12]):
                self._create_history_row(index, item)
        self._refresh_history_detail()

    def _create_history_row(self, index: int, item: Mapping[str, Any]) -> None:
        selected = index == self.history_selected_index
        row = ctk.CTkFrame(
            self.history_rows,
            height=58,
            fg_color=self.accent_soft if selected else "transparent",
            corner_radius=0,
        )
        row.pack(fill="x")
        row.pack_propagate(False)
        for column, weight in enumerate((4, 2, 2, 3, 1, 2)):
            row.grid_columnconfigure(column, weight=weight)
        source_path = Path(str(item.get("source", "Unknown")))
        ctk.CTkButton(
            row,
            text=source_path.name,
            command=lambda value=index: self.select_history_item(value),
            anchor="w",
            height=50,
            fg_color="transparent",
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=4)
        success = bool(item.get("success"))
        backend = str(item.get("backend") or item.get("type") or "Auto")
        output = Path(str(item.get("output", ""))).name or "—"
        size = self.app.catalog.format_size(int(item.get("size", 0) or 0)) if success else "—"
        timestamp = self._display_timestamp(str(item.get("timestamp", "")))
        values = (("Successful" if success else "Failed", self.theme["green"] if success else self.theme["red"]), (backend, self.theme["text2"]), (output, self.theme["text2"]), (size, self.theme["text2"]), (timestamp, self.theme["text2"]))
        for column, (text, color) in enumerate(values, start=1):
            ctk.CTkLabel(row, text=text, anchor="w", font=("Segoe UI", 10), text_color=color).grid(
                row=0, column=column, sticky="ew", padx=10
            )
        ctk.CTkFrame(self.history_rows, height=1, fg_color=self.divider).pack(fill="x")

    def _display_timestamp(self, value: str) -> str:
        if not value:
            return "—"
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.strftime("%b %d %H:%M")
        except ValueError:
            return value[:16]

    def select_history_item(self, index: int) -> None:
        self.history_selected_index = index
        self.refresh_history()

    def _refresh_history_detail(self) -> None:
        index = self.history_selected_index
        if index is None or index >= len(getattr(self, "filtered_history", [])):
            self.history_detail_title.configure(text="Select a build")
            for label in self.history_detail_values.values():
                label.configure(text="—")
            self.history_rebuild_button.configure(state="disabled")
            return
        item = self.filtered_history[index]
        source = Path(str(item.get("source", "Unknown")))
        self.history_detail_title.configure(text=source.name)
        self.history_detail_values["source"].configure(text=str(source))
        self.history_detail_values["output"].configure(text=str(item.get("output", "—")))
        self.history_detail_values["backend"].configure(text=str(item.get("backend") or item.get("type") or "Auto"))
        self.history_detail_values["result"].configure(text="Static checks passed" if item.get("success") else "Build failed")
        self.history_rebuild_button.configure(state="normal" if source.exists() else "disabled")

    def rebuild_history_item(self) -> None:
        index = self.history_selected_index
        if index is None or index >= len(getattr(self, "filtered_history", [])):
            return
        source = Path(str(self.filtered_history[index].get("source", "")))
        if source.is_file():
            self.app._load_file(str(source))
            self.show_page("build")

    def export_history(self) -> None:
        destination = filedialog.asksaveasfilename(
            title="Export build history",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not destination:
            return
        Path(destination).write_text(
            json.dumps(self.app.history.get_all(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.set_status(f"History exported to {Path(destination).name}")

    # ------------------------------------------------------------------
    # Toolchains page
    # ------------------------------------------------------------------

    def _create_toolchains_page(self) -> None:
        page = self._new_page("toolchains")
        actions = self._page_header(
            page,
            "Toolchains",
            "Inspect compiler availability without changing your system.",
        )
        self.toolchain_refresh_button = self._secondary_button(
            actions, "Refresh", lambda: self.refresh_toolchains(force=True), width=82
        )
        self.toolchain_refresh_button.pack(side="left", padx=(0, 8))
        self.app.manage_deps_btn = self._accessible(
            self._primary_button(actions, "Manage setup", self.app._show_setup, width=124),
            "actions.manage_dependencies",
            "button",
        )
        self.app.manage_deps_btn.pack(side="left")

        summary = self._surface(page)
        summary.pack(fill="x", pady=(0, 14))
        summary_grid = ctk.CTkFrame(summary, fg_color="transparent")
        summary_grid.pack(fill="x", padx=16, pady=12)
        self.toolchain_metrics: dict[str, Any] = {}
        for column, (key, label) in enumerate(
            (("backends", "backends"), ("available", "available"), ("missing", "missing"), ("experimental", "experimental"))
        ):
            summary_grid.grid_columnconfigure(column, weight=1)
            cell = ctk.CTkFrame(summary_grid, fg_color="transparent")
            cell.grid(row=0, column=column, sticky="ew")
            value = ctk.CTkLabel(cell, text="—", font=("Segoe UI", 18, "bold"), text_color=self.theme["text1"])
            value.pack(anchor="w")
            ctk.CTkLabel(cell, text=label, font=("Segoe UI", 10), text_color=self.theme["text2"]).pack(anchor="w")
            self.toolchain_metrics[key] = value

        table = self._surface(page)
        table.pack(fill="x", pady=(0, 14))
        table_inner = ctk.CTkFrame(table, fg_color="transparent")
        table_inner.pack(fill="x", padx=1, pady=1)
        header = ctk.CTkFrame(table_inner, fg_color=self.theme.get("surface_alt", self.surface), height=40)
        header.pack(fill="x")
        header.pack_propagate(False)
        for column, (label, weight) in enumerate(
            (("Backend", 4), ("Lifecycle", 2), ("Availability", 2), ("Version", 2), ("Host / targets", 3))
        ):
            header.grid_columnconfigure(column, weight=weight)
            ctk.CTkLabel(header, text=label, font=("Segoe UI", 10, "bold"), text_color=self.theme["text2"]).grid(
                row=0, column=column, sticky="w", padx=12, pady=10
            )
        self.toolchain_rows = ctk.CTkFrame(table_inner, fg_color="transparent")
        self.toolchain_rows.pack(fill="x")
        self.toolchain_loading_label = ctk.CTkLabel(
            self.toolchain_rows,
            text="Capability scan has not run yet.",
            font=("Segoe UI", 11),
            text_color=self.theme["text3"],
        )
        self.toolchain_loading_label.pack(anchor="w", padx=16, pady=28)

        details = self._surface(page)
        details.pack(fill="x")
        detail_inner = ctk.CTkFrame(details, fg_color="transparent")
        detail_inner.pack(fill="x", padx=18, pady=15)
        self.toolchain_detail_title = ctk.CTkLabel(
            detail_inner,
            text="Select a backend",
            font=("Segoe UI", 14, "bold"),
            text_color=self.theme["text1"],
        )
        self.toolchain_detail_title.pack(anchor="w")
        detail_grid = ctk.CTkFrame(detail_inner, fg_color="transparent")
        detail_grid.pack(fill="x", pady=(12, 0))
        for column in range(3):
            detail_grid.grid_columnconfigure(column, weight=1)
        self.toolchain_detail_values: dict[str, Any] = {}
        for column, (key, title) in enumerate(
            (("capability", "Capability"), ("requirements", "Requirements"), ("policy", "Policy"))
        ):
            group = ctk.CTkFrame(detail_grid, fg_color="transparent")
            group.grid(row=0, column=column, sticky="nsew", padx=(0, 22) if column < 2 else 0)
            ctk.CTkLabel(group, text=title, font=("Segoe UI", 10, "bold"), text_color=self.theme["text2"]).pack(anchor="w")
            value = ctk.CTkLabel(
                group,
                text="—",
                justify="left",
                anchor="w",
                wraplength=300,
                font=("Segoe UI", 10),
                text_color=self.theme["text1"],
            )
            value.pack(anchor="w", fill="x", pady=(6, 0))
            self.toolchain_detail_values[key] = value
        ctk.CTkFrame(detail_inner, height=1, fg_color=self.divider).pack(fill="x", pady=(14, 10))
        ctk.CTkLabel(
            detail_inner,
            text="Universal Compiler never installs tools during a build.",
            font=("Segoe UI", 9),
            text_color=self.theme["text3"],
        ).pack(anchor="w")

    def refresh_toolchains(self, *, force: bool = False) -> None:
        if self.toolchain_data and not force:
            self._populate_toolchains(self.toolchain_data)
            return
        if self.toolchain_scanning:
            return
        self.toolchain_scanning = True
        self.toolchain_refresh_button.configure(state="disabled", text="Scanning…")
        for child in self.toolchain_rows.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.toolchain_rows,
            text="Scanning installed toolchains with read-only probes…",
            font=("Segoe UI", 11),
            text_color=self.theme["text3"],
        ).pack(anchor="w", padx=16, pady=28)

        def scan() -> None:
            try:
                result = {key: dict(value) for key, value in backend_status().items()}
            except Exception as error:  # pragma: no cover - host tool probing
                result = {"error": {"name": "Capability scan failed", "error": str(error)}}
            self.app._post_ui(lambda payload=result: self._populate_toolchains(payload))

        threading.Thread(target=scan, daemon=True).start()

    def _populate_toolchains(self, data: dict[str, dict[str, Any]]) -> None:
        self.toolchain_scanning = False
        self.toolchain_refresh_button.configure(state="normal", text="Refresh")
        self.toolchain_data = data
        entries = [(key, value) for key, value in data.items() if key != "error"]
        self.toolchain_metrics["backends"].configure(text=str(len(entries)))
        self.toolchain_metrics["available"].configure(text=str(sum(1 for _, item in entries if item.get("available"))))
        self.toolchain_metrics["missing"].configure(text=str(sum(1 for _, item in entries if not item.get("available"))))
        self.toolchain_metrics["experimental"].configure(text=str(sum(1 for _, item in entries if item.get("lifecycle") == "experimental")))
        if self.toolchain_selected not in data:
            self.toolchain_selected = entries[0][0] if entries else None
        for child in self.toolchain_rows.winfo_children():
            child.destroy()
        if not entries:
            ctk.CTkLabel(
                self.toolchain_rows,
                text=str(data.get("error", {}).get("error", "No backend data is available.")),
                font=("Segoe UI", 11),
                text_color=self.theme["red"],
            ).pack(anchor="w", padx=16, pady=28)
        else:
            lifecycle_order = {"stable": 0, "experimental": 1, "deprecated": 2, "optional": 3}
            entries.sort(key=lambda pair: (lifecycle_order.get(str(pair[1].get("lifecycle")), 4), str(pair[1].get("name", pair[0]))))
            for key, item in entries[:14]:
                self._create_toolchain_row(key, item)
        self._refresh_toolchain_detail()

    def _create_toolchain_row(self, key: str, item: Mapping[str, Any]) -> None:
        selected = key == self.toolchain_selected
        row = ctk.CTkFrame(
            self.toolchain_rows,
            height=50,
            fg_color=self.accent_soft if selected else "transparent",
            corner_radius=0,
        )
        row.pack(fill="x")
        row.pack_propagate(False)
        for column, weight in enumerate((4, 2, 2, 2, 3)):
            row.grid_columnconfigure(column, weight=weight)
        ctk.CTkButton(
            row,
            text=str(item.get("name", key)),
            command=lambda backend=key: self.select_toolchain(backend),
            anchor="w",
            height=42,
            fg_color="transparent",
            hover_color=self.theme["card_hover"],
            text_color=self.theme["text1"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="ew", padx=(8, 4), pady=4)
        available = bool(item.get("available"))
        targets = ", ".join(str(value) for value in item.get("target_platforms", ())[:3]) or "—"
        version = str(item.get("verified_version") or "—").splitlines()[0][:22]
        values = (
            (str(item.get("lifecycle", "unknown")).title(), self.theme["text2"]),
            ("Available" if available else "Missing", self.theme["green"] if available else self.theme["red"]),
            (version, self.theme["text2"]),
            (targets, self.theme["text2"]),
        )
        for column, (text, color) in enumerate(values, start=1):
            ctk.CTkLabel(row, text=text, anchor="w", font=("Segoe UI", 10), text_color=color).grid(
                row=0, column=column, sticky="ew", padx=10
            )
        ctk.CTkFrame(self.toolchain_rows, height=1, fg_color=self.divider).pack(fill="x")

    def select_toolchain(self, key: str) -> None:
        self.toolchain_selected = key
        self._populate_toolchains(self.toolchain_data)

    def _refresh_toolchain_detail(self) -> None:
        item = self.toolchain_data.get(self.toolchain_selected or "")
        if not item:
            self.toolchain_detail_title.configure(text="Select a backend")
            for label in self.toolchain_detail_values.values():
                label.configure(text="—")
            return
        name = str(item.get("name", self.toolchain_selected))
        extensions = ", ".join(f".{value}" for value in item.get("extensions", ())) or "No source extension"
        architectures = ", ".join(str(value) for value in item.get("architectures", ())) or "—"
        requirements = "\n".join(f"• {value}" for value in item.get("required_sdks", ())) or "• No external SDK declared"
        lifecycle = str(item.get("lifecycle", "unknown")).title()
        policy = f"• {lifecycle}\n• {'Automatic selection' if item.get('default') else 'Explicit selection'}\n• Unsigned output"
        self.toolchain_detail_title.configure(text=name)
        self.toolchain_detail_values["capability"].configure(text=f"• {extensions}\n• {architectures}\n• Static artifact checks")
        self.toolchain_detail_values["requirements"].configure(text=requirements)
        self.toolchain_detail_values["policy"].configure(text=policy)

    # ------------------------------------------------------------------
    # Templates page
    # ------------------------------------------------------------------

    def _create_templates_page(self) -> None:
        page = self._new_page("templates", scrollable=False)
        actions = self._page_header(
            page,
            "Script templates",
            "Start from a clean, local example.",
        )
        self.template_search = ctk.CTkEntry(
            actions,
            width=210,
            height=40,
            fg_color=self.surface,
            border_color=self.divider,
            text_color=self.theme["text1"],
            placeholder_text="Search templates",
        )
        self.template_search.pack(side="left", padx=(0, 8))
        self.template_search.bind("<Return>", lambda _event: self.refresh_templates())
        self.template_filter = ctk.CTkComboBox(
            actions,
            width=138,
            height=40,
            values=["All languages", *sorted({value[1] for value in TEMPLATE_LABELS.values()})],
            fg_color=self.surface,
            border_color=self.divider,
            button_color=self.divider,
            dropdown_fg_color=self.surface,
            text_color=self.theme["text1"],
            command=lambda _value: self.refresh_templates(),
        )
        self.template_filter.set("All languages")
        self.template_filter.pack(side="left", padx=(0, 8))
        self._secondary_button(actions, "Open folder", self.open_templates_folder, width=96).pack(side="left")

        workspace = ctk.CTkFrame(page, fg_color="transparent")
        workspace.pack(fill="both", expand=True)
        workspace.grid_columnconfigure(0, weight=2)
        workspace.grid_columnconfigure(1, weight=3)
        workspace.grid_rowconfigure(0, weight=1)
        list_panel = self._surface(workspace)
        list_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        list_inner = ctk.CTkFrame(list_panel, fg_color="transparent")
        list_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.template_rows = ctk.CTkScrollableFrame(
            list_inner,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=self.divider,
            scrollbar_button_hover_color=self.theme["card_hover"],
        )
        self.template_rows.pack(fill="both", expand=True)

        preview_panel = self._surface(workspace)
        preview_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        preview_inner = ctk.CTkFrame(preview_panel, fg_color="transparent")
        preview_inner.pack(fill="both", expand=True, padx=1, pady=1)
        preview_header = ctk.CTkFrame(preview_inner, fg_color=self.theme.get("surface_alt", self.surface), height=66)
        preview_header.pack(fill="x")
        preview_header.pack_propagate(False)
        preview_copy = ctk.CTkFrame(preview_header, fg_color="transparent")
        preview_copy.pack(side="left", fill="y", expand=True, padx=16, pady=10)
        self.template_title = ctk.CTkLabel(preview_copy, text="Select a template", font=("Segoe UI", 13, "bold"), text_color=self.theme["text1"])
        self.template_title.pack(anchor="w")
        self.template_filename = ctk.CTkLabel(preview_copy, text="", font=("Segoe UI", 10), text_color=self.theme["text2"])
        self.template_filename.pack(anchor="w", pady=(2, 0))
        self.template_copy_button = self._secondary_button(preview_header, "Copy", self.copy_template, width=68)
        self.template_copy_button.configure(state="disabled")
        self.template_copy_button.pack(side="right", padx=(0, 8), pady=12)
        self.template_use_button = self._primary_button(preview_header, "Use template", self.use_template, width=112)
        self.template_use_button.configure(state="disabled")
        self.template_use_button.pack(side="right", padx=(0, 8), pady=12)
        self.template_preview = ctk.CTkTextbox(
            preview_inner,
            fg_color=self.theme["log_bg"],
            border_width=0,
            text_color=self.theme["text2"],
            font=("Cascadia Mono", 11),
        )
        self.template_preview.pack(fill="both", expand=True)
        self.template_preview.configure(state="disabled")
        self.template_meta = ctk.CTkLabel(
            preview_inner,
            text="Local templates stay unchanged.",
            anchor="w",
            font=("Segoe UI", 10),
            text_color=self.theme["text3"],
        )
        self.template_meta.pack(fill="x", padx=16, pady=12)

    def refresh_templates(self) -> None:
        query = self.template_search.get().strip().lower()
        language_filter = self.template_filter.get()
        files = sorted(
            (path for path in self.templates_dir.glob("*") if path.is_file() and path.suffix.lower() in TEMPLATE_LABELS),
            key=lambda path: (list(TEMPLATE_LABELS).index(path.suffix.lower()), path.name.lower()),
        )
        filtered: list[Path] = []
        for path in files:
            title, language = TEMPLATE_LABELS[path.suffix.lower()]
            if query and query not in title.lower() and query not in path.name.lower():
                continue
            if language_filter != "All languages" and language != language_filter:
                continue
            filtered.append(path)
        if self.template_selected not in filtered:
            self.template_selected = filtered[0] if filtered else None
        for child in self.template_rows.winfo_children():
            child.destroy()
        if not filtered:
            ctk.CTkLabel(
                self.template_rows,
                text="No templates match this filter.",
                font=("Segoe UI", 11),
                text_color=self.theme["text3"],
            ).pack(anchor="w", padx=14, pady=24)
        else:
            for path in filtered:
                title, language = TEMPLATE_LABELS[path.suffix.lower()]
                selected = path == self.template_selected
                row = ctk.CTkButton(
                    self.template_rows,
                    text=f"{title}\n{path.name}  ·  {language}",
                    command=lambda value=path: self.select_template(value),
                    anchor="w",
                    height=66,
                    corner_radius=0,
                    fg_color=self.accent_soft if selected else "transparent",
                    hover_color=self.theme["card_hover"],
                    text_color=self.theme["text1"],
                    font=("Segoe UI", 11, "bold"),
                )
                row.pack(fill="x")
                ctk.CTkFrame(self.template_rows, height=1, fg_color=self.divider).pack(fill="x")
        self._refresh_template_preview()

    def select_template(self, path: Path) -> None:
        self.template_selected = path
        self.refresh_templates()

    def _refresh_template_preview(self) -> None:
        path = self.template_selected
        if path is None or not path.is_file():
            self.template_title.configure(text="Select a template")
            self.template_filename.configure(text="")
            self.template_copy_button.configure(state="disabled")
            self.template_use_button.configure(state="disabled")
            content = ""
            meta = "Local templates stay unchanged."
        else:
            title, language = TEMPLATE_LABELS[path.suffix.lower()]
            content = path.read_text(encoding="utf-8", errors="replace")
            numbered = "\n".join(f"{index:3d}  {line}" for index, line in enumerate(content.splitlines(), start=1))
            content = numbered
            self.template_title.configure(text=title)
            self.template_filename.configure(text=path.name)
            self.template_copy_button.configure(state="normal")
            self.template_use_button.configure(state="normal")
            line_count = max(1, len(content.splitlines()))
            meta = f"{language}   ·   {line_count} lines   ·   UTF-8   ·   Local template"
        self.template_preview.configure(state="normal")
        self.template_preview.delete("1.0", "end")
        self.template_preview.insert("1.0", content)
        self.template_preview.configure(state="disabled")
        self.template_meta.configure(text=meta)

    def use_template(self) -> None:
        path = self.template_selected
        if path is None or not path.is_file():
            return
        self.app._load_file(str(path))
        self.show_page("build")
        self.set_status(f"Loaded {path.name} from local templates")

    def copy_template(self) -> None:
        path = self.template_selected
        if path is None or not path.is_file():
            return
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(path.read_text(encoding="utf-8", errors="replace"))
        self.set_status(f"Copied {path.name}")

    def open_templates_folder(self) -> None:
        if os.name == "nt":
            os.startfile(str(self.templates_dir))

    # ------------------------------------------------------------------
    # Settings page
    # ------------------------------------------------------------------

    def _create_settings_page(self) -> None:
        page = self._new_page("settings", scrollable=False)
        actions = self._page_header(
            page,
            "Settings",
            "Tune the workspace without weakening build safeguards.",
        )
        self._secondary_button(actions, "Reset", self.reset_settings, width=74).pack(side="left", padx=(0, 8))
        self._primary_button(actions, "Save changes", self.save_settings, width=122).pack(side="left")

        workspace = self._surface(page)
        workspace.pack(fill="both", expand=True)
        workspace.grid_columnconfigure(0, minsize=210)
        workspace.grid_columnconfigure(1, weight=1)
        workspace.grid_rowconfigure(0, weight=1)
        index = ctk.CTkFrame(
            workspace,
            fg_color=self.theme.get("surface_alt", self.surface),
            corner_radius=9,
            border_width=0,
        )
        index.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=1)
        index_inner = ctk.CTkFrame(index, fg_color="transparent")
        index_inner.pack(fill="x", padx=8, pady=10)
        sections = (
            ("appearance", "Appearance"),
            ("build", "Build defaults"),
            ("notifications", "Notifications"),
            ("storage", "Storage & privacy"),
        )
        for key, label in sections:
            button = ctk.CTkButton(
                index_inner,
                text=label,
                command=lambda value=key: self.scroll_settings_to(value),
                anchor="w",
                height=42,
                fg_color="transparent",
                hover_color=self.theme["card_hover"],
                text_color=self.theme["text2"],
                font=("Segoe UI", 11),
            )
            button.pack(fill="x", pady=2)
            self.settings_nav_buttons[key] = button

        detail = ctk.CTkScrollableFrame(
            workspace,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=self.divider,
            scrollbar_button_hover_color=self.theme["card_hover"],
        )
        detail.grid(row=0, column=1, sticky="nsew", padx=(18, 10), pady=(12, 12))
        self.settings_detail = detail

        self.settings_theme = tk.StringVar(value=self.app.settings.theme)
        self.settings_locale = tk.StringVar(value="Spanish" if str(self.app.settings.get("locale", "")).startswith("es") else "English")
        self.settings_profile = tk.StringVar(value=self.app.settings.get("default_profile", "Default"))
        self.settings_postbuild = tk.StringVar(value=self.app.settings.get("post_build_action", "None"))
        self.settings_notifications = tk.BooleanVar(value=self.app.settings.get("show_notifications", True))
        self.settings_recent = tk.IntVar(value=int(self.app.settings.get("max_recent_files", 10)))
        self.settings_history = tk.IntVar(value=int(self.app.settings.get("max_history_items", 50)))

        appearance = self._settings_group(detail, "appearance", "Appearance")
        self._settings_segmented_row(
            appearance,
            "Theme",
            "Choose the application theme. Changes apply after restart.",
            self.settings_theme,
            ["Dark", "Light"],
        )
        self._settings_combo_row(
            appearance,
            "Language",
            "Set the language used by the interface and formatted status text.",
            self.settings_locale,
            ["English", "Spanish"],
        )

        build = self._settings_group(detail, "build", "Build defaults")
        self._settings_combo_row(
            build,
            "Default profile",
            "Select the profile shown when a new workspace opens.",
            self.settings_profile,
            self.app.profiles.names(),
        )
        self._settings_combo_row(
            build,
            "Post-build action",
            "Choose a bounded action after a successful artifact build.",
            self.settings_postbuild,
            ["None", "Open Output Folder", "Copy to Folder..."],
        )
        self._settings_readonly_row(
            build,
            "Artifact verification",
            "Static checks run by default and never launch the produced artifact.",
            "On",
            self.theme["green"],
        )
        self._settings_readonly_row(
            build,
            "Network policy",
            "Builds remain offline unless access is explicitly allowed by a request.",
            "Offline",
            self.accent,
        )

        notifications = self._settings_group(detail, "notifications", "Notifications")
        self._settings_switch_row(
            notifications,
            "Build completion",
            "Show a local desktop notification when a build finishes.",
            self.settings_notifications,
        )

        storage = self._settings_group(detail, "storage", "Storage & privacy")
        self._settings_stepper_row(
            storage,
            "Recent files",
            "Maximum number of local recent-file entries to keep.",
            self.settings_recent,
            minimum=1,
            maximum=50,
        )
        self._settings_stepper_row(
            storage,
            "History limit",
            "Maximum number of local build history entries to keep.",
            self.settings_history,
            minimum=1,
            maximum=500,
        )
        location = ctk.CTkFrame(storage, fg_color="transparent")
        location.pack(fill="x", pady=8)
        copy = ctk.CTkFrame(location, fg_color="transparent")
        copy.pack(side="left", fill="x", expand=True)
        self._section_title(copy, "Application data", "Settings, history, and diagnostics stay in the current user profile.")
        self._secondary_button(location, "Open data folder", self.open_data_folder, width=116).pack(side="right")

        ctk.CTkFrame(detail, height=1, fg_color=self.divider).pack(fill="x", pady=(14, 10))
        self.settings_save_status = ctk.CTkLabel(
            detail,
            text="Builds remain offline unless network access is explicitly allowed.",
            anchor="w",
            font=("Segoe UI", 10),
            text_color=self.theme["text3"],
        )
        self.settings_save_status.pack(fill="x", pady=(0, 8))
        self.scroll_settings_to("appearance")

    def _settings_group(self, parent: Any, key: str, title: str) -> Any:
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.pack(fill="x", pady=(2, 8))
        ctk.CTkLabel(
            group,
            text=title,
            font=("Segoe UI", 14, "bold"),
            text_color=self.theme["text1"],
        ).pack(anchor="w", pady=(0, 8))
        ctk.CTkFrame(group, height=1, fg_color=self.divider).pack(fill="x")
        self.settings_sections[key] = group
        return group

    def _settings_row_copy(self, parent: Any, title: str, description: str) -> Any:
        copy = ctk.CTkFrame(parent, fg_color="transparent")
        copy.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            copy,
            text=title,
            font=("Segoe UI", 11, "bold"),
            text_color=self.theme["text1"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            copy,
            text=description,
            font=("Segoe UI", 9),
            text_color=self.theme["text2"],
        ).pack(anchor="w", pady=(1, 0))
        return copy

    def _settings_segmented_row(
        self,
        parent: Any,
        title: str,
        description: str,
        variable: Any,
        values: Sequence[str],
    ) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=9)
        self._settings_row_copy(row, title, description)
        control = ctk.CTkSegmentedButton(
            row,
            values=list(values),
            variable=variable,
            width=220,
            height=34,
            selected_color=self.accent_soft,
            selected_hover_color=self.accent_soft,
            unselected_color=self.theme["input"],
            unselected_hover_color=self.theme["card_hover"],
            border_width=1,
            text_color=self.theme["text1"],
        )
        control.pack(side="right")

    def _settings_combo_row(
        self,
        parent: Any,
        title: str,
        description: str,
        variable: Any,
        values: Sequence[str],
    ) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=9)
        self._settings_row_copy(row, title, description)
        combo = ctk.CTkComboBox(
            row,
            width=230,
            height=36,
            values=list(values),
            variable=variable,
            fg_color=self.theme["input"],
            border_color=self.divider,
            button_color=self.divider,
            dropdown_fg_color=self.surface,
            text_color=self.theme["text1"],
        )
        combo.pack(side="right")

    def _settings_switch_row(
        self,
        parent: Any,
        title: str,
        description: str,
        variable: Any,
    ) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=9)
        self._settings_row_copy(row, title, description)
        ctk.CTkSwitch(
            row,
            text="",
            variable=variable,
            progress_color=self.accent,
            width=48,
        ).pack(side="right")

    def _settings_readonly_row(
        self,
        parent: Any,
        title: str,
        description: str,
        value: str,
        color: str,
    ) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=9)
        self._settings_row_copy(row, title, description)
        ctk.CTkLabel(row, text=value, font=("Segoe UI", 10, "bold"), text_color=color).pack(side="right")

    def _settings_stepper_row(
        self,
        parent: Any,
        title: str,
        description: str,
        variable: Any,
        *,
        minimum: int,
        maximum: int,
    ) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=9)
        self._settings_row_copy(row, title, description)
        stepper = ctk.CTkFrame(row, fg_color="transparent")
        stepper.pack(side="right")

        def adjust(delta: int) -> None:
            variable.set(max(minimum, min(maximum, int(variable.get()) + delta)))

        ctk.CTkButton(
            stepper,
            text="−",
            command=lambda: adjust(-1),
            width=34,
            height=34,
            fg_color="transparent",
            hover_color=self.theme["card_hover"],
            border_color=self.divider,
            border_width=1,
            text_color=self.theme["text1"],
        ).pack(side="left")
        ctk.CTkLabel(
            stepper,
            textvariable=variable,
            width=54,
            height=34,
            text_color=self.theme["text1"],
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")
        ctk.CTkButton(
            stepper,
            text="+",
            command=lambda: adjust(1),
            width=34,
            height=34,
            fg_color="transparent",
            hover_color=self.theme["card_hover"],
            border_color=self.divider,
            border_width=1,
            text_color=self.theme["text1"],
        ).pack(side="left")

    def scroll_settings_to(self, key: str) -> None:
        for section, button in self.settings_nav_buttons.items():
            active = section == key
            button.configure(
                fg_color=self.accent_soft if active else "transparent",
                text_color=self.theme["text1"] if active else self.theme["text2"],
            )
        target = self.settings_sections.get(key)
        if target is None:
            return
        try:
            self.settings_detail.update_idletasks()
            canvas = self.settings_detail._parent_canvas
            content_height = max(1, self.settings_detail.winfo_reqheight())
            canvas.yview_moveto(max(0.0, min(1.0, target.winfo_y() / content_height)))
        except (AttributeError, tk.TclError):
            pass

    def save_settings(self) -> None:
        locale = "es" if self.settings_locale.get() == "Spanish" else "en"
        updates = {
            "theme": self.settings_theme.get(),
            "locale": locale,
            "default_profile": self.settings_profile.get(),
            "post_build_action": self.settings_postbuild.get(),
            "show_notifications": bool(self.settings_notifications.get()),
            "max_recent_files": int(self.settings_recent.get()),
            "max_history_items": int(self.settings_history.get()),
        }
        for key, value in updates.items():
            self.app.settings._settings[key] = value
        self.app.settings.save()
        self.app.recent_files.set_limit(updates["max_recent_files"])
        self.app.history.set_limit(updates["max_history_items"])
        self.app.notify_var.set(updates["show_notifications"])
        self.app.postbuild_combo.set(str(updates["post_build_action"]))
        self.settings_save_status.configure(
            text="Saved locally. Theme and language changes apply after restart.",
            text_color=self.theme["green"],
        )
        self.set_status("Settings saved locally")

    def reset_settings(self) -> None:
        self.settings_theme.set("Dark")
        self.settings_locale.set("English")
        self.settings_profile.set("Default")
        self.settings_postbuild.set("None")
        self.settings_notifications.set(True)
        self.settings_recent.set(10)
        self.settings_history.set(50)
        self.settings_save_status.configure(
            text="Defaults restored in the form. Save changes to persist them.",
            text_color=self.theme["yellow"],
        )

    def open_data_folder(self) -> None:
        if os.name == "nt":
            os.startfile(str(self.templates_dir.parent))

    # ------------------------------------------------------------------
    # Cross-page hooks
    # ------------------------------------------------------------------

    def on_file_loaded(self) -> None:
        self.refresh_build_plan()

    def on_backend_changed(self) -> None:
        self.refresh_build_plan()

    def on_history_changed(self) -> None:
        if self.current_page == "history":
            self.refresh_history()

    def set_status(self, message: str) -> None:
        self.app_status_label.configure(text=message)
