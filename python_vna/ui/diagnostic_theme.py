from __future__ import annotations


# Geometry / type scale used by stylesheet builders.
# Keep UI type at 9pt and controls compact so Chinese labels fit dense panels.
RADIUS_SM = 3
RADIUS_MD = 5
RADIUS_LG = 7
RADIUS_XL = 9
CONTROL_MIN_H = 22
FONT_SIZE_UI = "9pt"
FONT_SIZE_COMPACT = "8pt"
FONT_FAMILY = '"Segoe UI", "Microsoft YaHei UI", "PingFang SC", "Noto Sans SC", sans-serif'


# Shared plot palettes follow the legacy VNA plot_vna/vi_color trace order.
# The dark colors reproduce the original scheme; light mode keeps the same hues
# at lower luminance so every channel remains visible on white plot paper.
# Order matters: channel N maps to index N-1.
DARK_TRACE_COLORS: list[str] = [
    "#20FF20",  # 1 legacy bright green
    "#A4C8F0",  # 2 legacy light blue
    "#FFFF00",  # 3 legacy yellow
    "#FF8080",  # 4 legacy light red
    "#FF3030",  # 5 red
    "#FFB300",  # 6 amber
    "#00FFFF",  # 7 cyan
    "#B3B3FF",  # 8 lavender
    "#4D6FFF",  # 9 visible blue
    "#FFFFFF",  # 10 white
    "#80FF00",  # 11 lime
    "#00A0FF",  # 12 azure
]

LIGHT_TRACE_COLORS: list[str] = [
    "#148A14",  # 1 green
    "#2878B5",  # 2 blue
    "#B88600",  # 3 dark yellow
    "#D43A3A",  # 4 red
    "#A80018",  # 5 dark red
    "#C46A00",  # 6 amber
    "#008C9E",  # 7 cyan
    "#6657B8",  # 8 violet
    "#304FC4",  # 9 blue
    "#4A4A4A",  # 10 charcoal
    "#5D8F00",  # 11 lime
    "#0077B8",  # 12 azure
]

# VC reference curves — fixed semantic colors, readable on light plot bg.
VC_REFERENCE_COLORS: dict[str, str] = {
    "VC A": "#1C1C1C",
    "VC B": "#1F5FBF",
    "VC C": "#C62828",
    "VC D": "#2E7D32",
    "VC E": "#8A5A00",
    "VC F": "#6A3FA0",
}


def is_light_plot_background(plot_bg: object) -> bool:
    """Return True when a plot background is white / near-white."""
    text = str(plot_bg or "").strip().lower()
    if text in {"#ffffff", "white", "#fff", "#fafafa", "#f8fafb", "#f7fafb"}:
        return True
    if text.startswith("#") and len(text) == 7:
        try:
            r = int(text[1:3], 16)
            g = int(text[3:5], 16)
            b = int(text[5:7], 16)
        except ValueError:
            return False
        # Relative luminance threshold for "light enough" plot paper.
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) >= 210.0
    return False


def trace_colors_for_theme(theme: dict[str, object] | None = None) -> list[str]:
    """Pick the dark or light multi-series palette from a theme dict."""
    plot_bg = (theme or {}).get("plot_bg", "#000000")
    if is_light_plot_background(plot_bg):
        return list(LIGHT_TRACE_COLORS)
    return list(DARK_TRACE_COLORS)


def default_trace_colors(mode: str = "light") -> list[str]:
    """Convenience accessor used by pages that don't carry a full theme yet."""
    key = (mode or "light").strip().lower()
    return list(LIGHT_TRACE_COLORS if key == "light" else DARK_TRACE_COLORS)


def normalize_trace_name(name: object) -> str:
    """Normalize a curve/channel label for stable color lookup."""
    text = str(name or "").strip()
    if not text:
        return ""
    # Collapse whitespace and unify common separators without changing meaning.
    return " ".join(text.replace("\\", "/").split())


def _channel_sort_key(name: str) -> tuple[object, ...]:
    """Prefer numeric channel order (AI0/CH1/…) when present; else label order.

    Returns:
        (0, zero_based_index, primary) for known channel numbers
        (1, primary_lower) for free-form labels
        (2, "") for empty
    """
    import re

    text = normalize_trace_name(name)
    if not text:
        return (2, "")
    # Transfer traces are stored as reference->response. Their color must follow
    # the response channel; otherwise ai0->ai1/2/3 all inherit ai0's color.
    if "->" in text:
        primary = text.rsplit("->", 1)[-1].strip() or text
    elif "→" in text:
        primary = text.rsplit("→", 1)[-1].strip() or text
    else:
        # Legacy response/reference labels keep the response on the left.
        primary = re.split(r"[/|←]", text, maxsplit=1)[0].strip() or text

    # AI/AO are 0-based in NI naming (ai0, ao1).
    ni_match = re.search(r"\b(ai|ao)\s*([0-9]+)\b", primary, flags=re.IGNORECASE)
    if ni_match is not None:
        return (0, int(ni_match.group(2)), primary.lower())

    # UI labels Ch/Channel/通道 are 1-based — convert to 0-based palette index.
    ch_match = re.search(r"(?:ch|channel|通道)\s*([0-9]+)", primary, flags=re.IGNORECASE)
    if ch_match is not None:
        number = int(ch_match.group(1))
        zero_based = max(0, number - 1) if number >= 1 else 0
        return (0, zero_based, primary.lower())

    # Bare integers (e.g. list items "1", "3") treated as 1-based channel numbers.
    bare = re.fullmatch(r"\s*([0-9]+)\s*", primary)
    if bare is not None:
        number = int(bare.group(1))
        zero_based = max(0, number - 1) if number >= 1 else 0
        return (0, zero_based, primary.lower())

    return (1, primary.lower())


def color_for_trace_name(
    name: object,
    colors: list[str] | None = None,
    *,
    theme: dict[str, object] | None = None,
) -> str:
    """Return a stable palette color for a curve/channel name.

    Same label always maps to the same color within a palette, so upper/lower
    plots (or different pages) stay consistent for the same channel.
    """
    palette = list(colors) if colors else (
        trace_colors_for_theme(theme) if theme is not None else list(LIGHT_TRACE_COLORS)
    )
    if not palette:
        return "#4CC9F0"
    text = normalize_trace_name(name)
    if not text:
        return palette[0]
    # Prefer channel-number based assignment when the label encodes one.
    # value is already a 0-based palette index from _channel_sort_key.
    kind, value, *_rest = _channel_sort_key(text)
    if kind == 0 and isinstance(value, int):
        return palette[value % len(palette)]
    # Stable hash for free-form labels (FRF pair names, custom series, …).
    digest = 0
    for char in text.lower():
        digest = (digest * 131 + ord(char)) & 0xFFFFFFFF
    return palette[digest % len(palette)]


def color_map_for_trace_names(
    names: list[object] | tuple[object, ...] | set[object],
    colors: list[str] | None = None,
    *,
    theme: dict[str, object] | None = None,
) -> dict[str, str]:
    """Build a name→color map, packing known channel numbers first for cohesion."""
    palette = list(colors) if colors else (
        trace_colors_for_theme(theme) if theme is not None else list(LIGHT_TRACE_COLORS)
    )
    ordered = sorted(
        {normalize_trace_name(name) for name in names if normalize_trace_name(name)},
        key=_channel_sort_key,
    )
    mapping: dict[str, str] = {}
    used_indexes: set[int] = set()
    # First pass: reserve indexes for explicit channel numbers (0-based).
    for name in ordered:
        kind, value, *_rest = _channel_sort_key(name)
        if kind == 0 and isinstance(value, int) and palette:
            index = value % len(palette)
            mapping[name] = palette[index]
            used_indexes.add(index)
    # Second pass: assign remaining labels to free palette slots, then wrap.
    next_index = 0
    for name in ordered:
        if name in mapping:
            continue
        if not palette:
            mapping[name] = "#4CC9F0"
            continue
        while next_index < len(palette) * 4 and (next_index % len(palette)) in used_indexes:
            next_index += 1
        index = next_index % len(palette)
        mapping[name] = palette[index]
        used_indexes.add(index)
        next_index += 1
    return mapping


THEMES: dict[str, dict[str, object]] = {
    "light": {
        # Surfaces — cool gray-teal stack with clear elevation
        "window_bg": "#e8eef1",
        "panel_bg": "#f7fafb",
        "panel_bg_alt": "#eef3f5",
        "control_bg": "#ffffff",
        "cell_bg": "#e3ecef",
        "plot_bg": "#ffffff",
        "plot_workspace_bg": "#dfe8eb",
        "elevated": "#ffffff",
        # Text
        "text": "#15242b",
        "muted_text": "#5a6d76",
        "label_text": "#1f3640",
        "axis": "#1f3640",
        # Brand / action — restrained teal, amber highlight
        "accent": "#0f6b78",
        "accent_hover": "#0b5560",
        "accent_soft": "#d6ecef",
        "accent_alt": "#2f7a5f",
        "highlight": "#c9851f",
        "on_accent": "#ffffff",
        # Borders / chrome
        "border": "#c5d2d7",
        "control_border": "#a8b9c0",
        "table_bg": "#ffffff",
        "header_bg": "#e7eef1",
        "menu_bg": "#f7fafb",
        "selection_bg": "#d3e8ec",
        "selection_text": "#0d3d46",
        "danger": "#c23b4a",
        "danger_hover": "#a12e3b",
        "disabled_bg": "#e6ecee",
        "disabled_text": "#8b9ba2",
        "scroll_handle": "#9aafb7",
        # Nav rail (always deep)
        "nav_bg": "#102a32",
        "nav_border": "#0a1d23",
        "nav_text": "#e4eef1",
        "nav_muted": "#8eabb4",
        "nav_hover": "#1a3d48",
        "nav_selected": "#f3f7f8",
        "nav_selected_text": "#102a32",
        # Plot overlays
        "legend_bg": (255, 255, 255, 235),
        "legend_text": "#15242b",
        "grid_alpha": 0.18,
        "marker_a": "#c9851f",
        "marker_b": "#0f6b78",
        "cursor": "#c23b4a",
        "cursor_line": "#0f6b78",
        "cursor_text": "#15242b",
        "cursor_fill": (230, 242, 245, 225),
        "cursor_border": "#0f6b78",
        "data_tip_bg": "#fff7e6",
        "data_tip_text": "#15242b",
        "data_tip_fill": (255, 255, 255, 240),
        "data_tip_border": "#0f6b78",
        "zoom_box": "#0f6b78",
    },
    "dark": {
        # Surfaces — near-black with teal undertone, soft elevation
        "window_bg": "#0d1418",
        "panel_bg": "#141e24",
        "panel_bg_alt": "#18252c",
        "control_bg": "#1a2830",
        "cell_bg": "#21323b",
        "plot_bg": "#070b0e",
        "plot_workspace_bg": "#0d1418",
        "elevated": "#1a2830",
        # Text
        "text": "#e7f0f3",
        "muted_text": "#8ea3ac",
        "label_text": "#c8dbe2",
        "axis": "#d5e4e9",
        # Brand / action — luminous teal for dark chrome
        "accent": "#3ab4c4",
        "accent_hover": "#2d96a4",
        "accent_soft": "#1a3a42",
        "accent_alt": "#45a87a",
        "highlight": "#e2a84a",
        "on_accent": "#061014",
        # Borders / chrome
        "border": "#2a3d47",
        "control_border": "#364b55",
        "table_bg": "#0f171b",
        "header_bg": "#18252c",
        "menu_bg": "#0b1216",
        "selection_bg": "#1d4a54",
        "selection_text": "#e7f0f3",
        "danger": "#e05564",
        "danger_hover": "#c24150",
        "disabled_bg": "#172228",
        "disabled_text": "#5d7179",
        "scroll_handle": "#4a616c",
        # Nav rail
        "nav_bg": "#0a1a20",
        "nav_border": "#061216",
        "nav_text": "#e4eef1",
        "nav_muted": "#87a4ad",
        "nav_hover": "#16343e",
        "nav_selected": "#f3f7f8",
        "nav_selected_text": "#0a1a20",
        # Plot overlays
        "legend_bg": (10, 18, 22, 220),
        "legend_text": "#e7f0f3",
        "grid_alpha": 0.26,
        "marker_a": "#e2a84a",
        "marker_b": "#3ab4c4",
        "cursor": "#e05564",
        "cursor_line": "#e2a84a",
        "cursor_text": "#0d1418",
        "cursor_fill": (226, 168, 74, 210),
        "cursor_border": "#c9943d",
        "data_tip_bg": "#fff7e6",
        "data_tip_text": "#0d1418",
        "data_tip_fill": (232, 244, 247, 235),
        "data_tip_border": "#3ab4c4",
        "zoom_box": "#3ab4c4",
    },
}


def default_theme(name: str = "light") -> dict[str, object]:
    """Return a copy of the named shared theme (dark|light)."""
    key = (name or "light").strip().lower()
    if key not in THEMES:
        key = "light"
    return dict(THEMES[key])


def default_diagnostic_theme() -> dict[str, object]:
    """Backward-compatible alias used by the diagnostic shell and workbench."""
    return default_theme("light")


def resolve_theme(
    theme: dict[str, object] | None = None,
    *,
    mode: str = "light",
) -> dict[str, object]:
    """Merge partial theme overrides onto the shared defaults for *mode*."""
    base = default_theme(mode)
    if not theme:
        return base
    supplied = dict(theme)
    resolved = dict(base)
    resolved.update(supplied)

    if "control_bg" not in supplied:
        resolved["control_bg"] = resolved.get("panel_bg_alt", base["control_bg"])
    if "accent_hover" not in supplied:
        resolved["accent_hover"] = resolved.get("accent_alt", base["accent_hover"])
    if "accent_soft" not in supplied:
        resolved["accent_soft"] = resolved.get("cell_bg", base["accent_soft"])
    if "highlight" not in supplied:
        resolved["highlight"] = resolved.get("accent", base["highlight"])
    if "header_bg" not in supplied:
        resolved["header_bg"] = resolved.get("panel_bg_alt", base["header_bg"])
    if "selection_bg" not in supplied:
        resolved["selection_bg"] = resolved.get("cell_bg", base["selection_bg"])
    if "selection_text" not in supplied:
        resolved["selection_text"] = resolved.get("text", base["selection_text"])
    if "on_accent" not in supplied:
        resolved["on_accent"] = base["on_accent"]
    if "scroll_handle" not in supplied:
        resolved["scroll_handle"] = resolved.get("control_border", base["scroll_handle"])
    if "disabled_bg" not in supplied:
        resolved["disabled_bg"] = resolved.get("panel_bg_alt", base["disabled_bg"])
    if "disabled_text" not in supplied:
        resolved["disabled_text"] = resolved.get("muted_text", base["disabled_text"])
    if "plot_workspace_bg" not in supplied:
        resolved["plot_workspace_bg"] = resolved.get("window_bg", base["plot_workspace_bg"])
    if "elevated" not in supplied:
        resolved["elevated"] = resolved.get("control_bg", base["elevated"])
    if "legend_text" not in supplied:
        resolved["legend_text"] = resolved.get("text", base["legend_text"])
    return resolved


def set_button_role(button, role: str) -> None:
    """Assign a QSS role property and force a style refresh when possible."""
    button.setProperty("role", role)
    style = button.style()
    if style is not None:
        style.unpolish(button)
        style.polish(button)
    button.update()


def apply_plot_legend_theme(plot, theme: dict[str, object]) -> None:
    """Apply legend_bg / legend_text tokens when a plot has a legend."""
    try:
        import pyqtgraph as pg
    except Exception:
        return
    legend = getattr(getattr(plot, "plotItem", None), "legend", None)
    if legend is None:
        return
    legend_bg = theme.get("legend_bg", (255, 255, 255, 230))
    legend_text = str(theme.get("legend_text", theme.get("text", "#1c2b33")))
    try:
        if isinstance(legend_bg, tuple):
            legend.setBrush(pg.mkBrush(*legend_bg))
        else:
            legend.setBrush(pg.mkBrush(str(legend_bg)))
        legend.setPen(pg.mkPen(legend_text, width=0.8))
        legend.opts["labelTextColor"] = legend_text
        for _sample, label in getattr(legend, "items", []):
            label.setText(label.text, color=legend_text)
    except Exception:
        return


def _scalar_theme(theme: dict[str, object]) -> dict[str, object]:
    """Drop non-scalar values that cannot be interpolated into QSS."""
    return {key: value for key, value in theme.items() if not isinstance(value, (tuple, list))}


def build_shared_stylesheet(theme: dict[str, object]) -> str:
    """Shared desktop chrome used by both acquisition and diagnostic apps."""
    theme = _scalar_theme(resolve_theme(theme))
    on_accent = theme.get("on_accent", "#ffffff")
    return f"""
        * {{
            font-family: {FONT_FAMILY};
        }}
        QMainWindow, QWidget {{
            background: {theme.get('window_bg')};
            color: {theme.get('text')};
            font-size: {FONT_SIZE_UI};
        }}
        QLabel, QCheckBox, QRadioButton {{
            color: {theme.get('text')};
            font-weight: 400;
        }}
        QGroupBox {{
            background: {theme.get('panel_bg')};
            color: {theme.get('label_text')};
            border: 1px solid {theme.get('border')};
            border-radius: {RADIUS_LG}px;
            margin-top: 14px;
            padding-top: 4px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 1px 6px;
            background: {theme.get('window_bg')};
            color: {theme.get('label_text')};
        }}
        QPushButton, QToolButton {{
            background: {theme.get('control_bg')};
            color: {theme.get('text')};
            border: 1px solid {theme.get('control_border')};
            border-radius: {RADIUS_MD}px;
            padding: 3px 8px;
            font-weight: 600;
            min-height: {CONTROL_MIN_H}px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background: {theme.get('accent_soft')};
            border-color: {theme.get('accent')};
            color: {theme.get('accent')};
        }}
        QPushButton:pressed, QToolButton:pressed {{
            background: {theme.get('cell_bg')};
        }}
        QPushButton:checked, QToolButton:checked {{
            background: {theme.get('accent')};
            border-color: {theme.get('accent')};
            color: {on_accent};
        }}
        QPushButton[role="primary"], QToolButton[role="primary"] {{
            background: {theme.get('accent')};
            color: {on_accent};
            border-color: {theme.get('accent')};
            padding: 3px 10px;
        }}
        QPushButton[role="primary"]:hover, QToolButton[role="primary"]:hover {{
            background: {theme.get('accent_hover')};
            border-color: {theme.get('accent_hover')};
            color: {on_accent};
        }}
        QPushButton[role="primary"]:pressed, QToolButton[role="primary"]:pressed {{
            background: {theme.get('accent_hover')};
        }}
        QPushButton[role="secondary"], QToolButton[role="secondary"] {{
            background: {theme.get('control_bg')};
            color: {theme.get('text')};
            border-color: {theme.get('control_border')};
        }}
        QPushButton[role="secondary"]:hover, QToolButton[role="secondary"]:hover {{
            background: {theme.get('accent_soft')};
            border-color: {theme.get('accent')};
            color: {theme.get('accent')};
        }}
        QPushButton[role="secondary"]:checked, QToolButton[role="secondary"]:checked {{
            background: {theme.get('accent_alt')};
            color: {on_accent};
            border-color: {theme.get('accent_alt')};
        }}
        QPushButton[role="danger"], QToolButton[role="danger"],
        QPushButton#dangerButton, QToolButton#dangerButton {{
            background: {theme.get('danger')};
            color: #ffffff;
            border-color: {theme.get('danger')};
            padding: 3px 10px;
        }}
        QPushButton[role="danger"]:hover, QToolButton[role="danger"]:hover,
        QPushButton#dangerButton:hover, QToolButton#dangerButton:hover {{
            background: {theme.get('danger_hover')};
            border-color: {theme.get('danger_hover')};
            color: #ffffff;
        }}
        QPushButton:disabled, QToolButton:disabled {{
            background: {theme.get('disabled_bg')};
            color: {theme.get('disabled_text')};
            border-color: {theme.get('border')};
        }}
        QComboBox, QLineEdit, QPlainTextEdit, QTextEdit,
        QDoubleSpinBox, QSpinBox {{
            background: {theme.get('control_bg')};
            color: {theme.get('text')};
            border: 1px solid {theme.get('control_border')};
            border-radius: {RADIUS_SM}px;
            padding: 2px 5px;
            min-height: 20px;
            selection-background-color: {theme.get('accent')};
            selection-color: {on_accent};
        }}
        QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover,
        QDoubleSpinBox:hover, QSpinBox:hover {{
            border-color: {theme.get('accent')};
        }}
        QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
        QDoubleSpinBox:focus, QSpinBox:focus {{
            border: 1px solid {theme.get('accent')};
            background: {theme.get('elevated')};
        }}
        QComboBox::drop-down {{
            width: 20px;
            border: 0;
            border-left: 1px solid {theme.get('border')};
        }}
        QDoubleSpinBox::up-button, QSpinBox::up-button {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 16px;
            min-height: 10px;
            border-left: 1px solid {theme.get('border')};
            border-bottom: 1px solid {theme.get('border')};
        }}
        QDoubleSpinBox::down-button, QSpinBox::down-button {{
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 16px;
            min-height: 10px;
            border-left: 1px solid {theme.get('border')};
        }}
        QListWidget, QTableWidget, QTreeWidget {{
            background: {theme.get('table_bg')};
            alternate-background-color: {theme.get('panel_bg_alt')};
            color: {theme.get('text')};
            border: 1px solid {theme.get('border')};
            border-radius: {RADIUS_MD}px;
            selection-background-color: {theme.get('selection_bg')};
            selection-color: {theme.get('selection_text')};
            outline: 0;
            padding: 2px;
        }}
        QListWidget::item, QTableWidget::item, QTreeWidget::item {{
            padding: 3px 5px;
            min-height: 18px;
            border-radius: {RADIUS_SM}px;
        }}
        QListWidget::item:alternate, QTableWidget::item:alternate {{
            background: {theme.get('panel_bg_alt')};
            color: {theme.get('text')};
        }}
        QListWidget::item:hover, QTableWidget::item:hover {{
            background: {theme.get('accent_soft')};
            color: {theme.get('text')};
        }}
        QListWidget::item:selected, QTableWidget::item:selected {{
            background: {theme.get('selection_bg')};
            color: {theme.get('selection_text')};
        }}
        QHeaderView::section {{
            background: {theme.get('header_bg')};
            color: {theme.get('label_text')};
            border: 0;
            border-right: 1px solid {theme.get('border')};
            border-bottom: 1px solid {theme.get('border')};
            padding: 6px 8px;
            font-weight: 600;
        }}
        QTabWidget::pane {{
            border: 1px solid {theme.get('border')};
            border-top: 0;
            background: {theme.get('panel_bg')};
            border-bottom-left-radius: {RADIUS_MD}px;
            border-bottom-right-radius: {RADIUS_MD}px;
            top: -1px;
        }}
        QTabBar {{
            background: transparent;
            qproperty-drawBase: 0;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {theme.get('muted_text')};
            border: 0;
            border-bottom: 2px solid transparent;
            padding: 6px 10px 5px 10px;
            min-width: 56px;
            max-width: 160px;
            margin-right: 1px;
        }}
        QTabBar::tab:hover {{
            color: {theme.get('accent')};
            background: {theme.get('panel_bg_alt')};
            border-top-left-radius: {RADIUS_MD}px;
            border-top-right-radius: {RADIUS_MD}px;
        }}
        QTabBar::tab:selected {{
            background: {theme.get('panel_bg')};
            color: {theme.get('accent')};
            border-bottom: 2px solid {theme.get('accent')};
            font-weight: 700;
        }}
        QMenuBar {{
            background: {theme.get('panel_bg')};
            color: {theme.get('text')};
            border-bottom: 1px solid {theme.get('border')};
            padding: 2px 4px;
            spacing: 2px;
        }}
        QMenuBar::item {{
            padding: 5px 10px;
            border-radius: {RADIUS_SM}px;
        }}
        QMenuBar::item:selected {{
            background: {theme.get('accent_soft')};
            color: {theme.get('accent')};
        }}
        QMenu {{
            background: {theme.get('menu_bg')};
            color: {theme.get('text')};
            border: 1px solid {theme.get('border')};
            border-radius: {RADIUS_MD}px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 6px 24px 6px 14px;
            border-radius: {RADIUS_SM}px;
        }}
        QMenu::item:selected {{
            background: {theme.get('selection_bg')};
            color: {theme.get('selection_text')};
        }}
        QMenu::separator {{
            height: 1px;
            background: {theme.get('border')};
            margin: 4px 8px;
        }}
        QStatusBar {{
            background: {theme.get('panel_bg')};
            color: {theme.get('muted_text')};
            border-top: 1px solid {theme.get('border')};
            min-height: 24px;
            padding: 1px 8px;
        }}
        QStatusBar::item {{
            border: 0;
        }}
        QScrollArea {{
            border: 0;
            background: transparent;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 3px 1px;
        }}
        QScrollBar::handle:vertical {{
            background: {theme.get('scroll_handle')};
            min-height: 32px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {theme.get('accent')};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
            margin: 1px 3px;
        }}
        QScrollBar::handle:horizontal {{
            background: {theme.get('scroll_handle')};
            min-width: 32px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {theme.get('accent')};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
        }}
        QSplitter::handle {{
            background: {theme.get('window_bg')};
        }}
        QSplitter::handle:horizontal {{
            width: 3px;
        }}
        QSplitter::handle:vertical {{
            height: 3px;
        }}
        QSplitter::handle:hover {{
            background: {theme.get('accent')};
        }}
        QDialog {{
            background: {theme.get('window_bg')};
            color: {theme.get('text')};
        }}
        QDialogButtonBox QPushButton {{
            min-width: 78px;
        }}
        QToolTip {{
            background: {theme.get('elevated')};
            color: {theme.get('text')};
            border: 1px solid {theme.get('border')};
            border-radius: {RADIUS_SM}px;
            padding: 5px 8px;
        }}
        QFrame#readmePanel {{
            background: {theme.get('panel_bg')};
            border: 1px solid {theme.get('border')};
            border-radius: {RADIUS_LG}px;
        }}
        QLabel#sectionTitle {{
            color: {theme.get('label_text')};
            font-weight: 700;
            font-size: {FONT_SIZE_UI};
            border-left: 3px solid {theme.get('accent')};
            padding: 2px 0 2px 8px;
        }}
        QFrame#toolbarDivider {{
            background: {theme.get('border')};
            max-width: 1px;
            min-width: 1px;
            margin: 4px 6px;
        }}
        QCheckBox#vcCheck {{
            spacing: 8px;
            font-weight: 600;
        }}
        QCheckBox#vcCheck::indicator {{
            width: 15px;
            height: 15px;
            border: 1px solid {theme.get('control_border')};
            border-radius: {RADIUS_SM}px;
            background: {theme.get('control_bg')};
        }}
        QCheckBox#vcCheck::indicator:checked {{
            background: {theme.get('accent')};
            border-color: {theme.get('accent')};
        }}
    """


def build_diagnostic_stylesheet(theme: dict[str, object]) -> str:
    """Shared chrome + diagnostic nav-rail selectors."""
    theme = resolve_theme(theme, mode="light")
    scalar = _scalar_theme(theme)
    shared = build_shared_stylesheet(theme)
    return shared + f"""
        QWidget#diagnosticRoot {{
            background: {scalar.get('window_bg')};
            border: 0;
        }}
        QWidget#diagnosticControlPanel {{
            background: transparent;
            border: 0;
        }}
        QWidget#diagnosticContent {{
            background: {scalar.get('window_bg')};
        }}
        QFrame#diagnosticRail {{
            background: {scalar.get('nav_bg')};
            border: 0;
            border-right: 1px solid {scalar.get('nav_border')};
        }}
        QWidget#diagnosticBrandWrap {{
            background: transparent;
        }}
        QLabel#diagnosticBrandMark {{
            background: rgba(255, 255, 255, 0.10);
            color: {scalar.get('nav_text')};
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 8px;
            font-size: 10pt;
            font-weight: 800;
            padding: 0;
            min-width: 34px;
            max-width: 34px;
            min-height: 34px;
            max-height: 34px;
            qproperty-alignment: AlignCenter;
        }}
        QLabel#diagnosticBrand {{
            background: transparent;
            color: {scalar.get('nav_text')};
            font-size: 12pt;
            font-weight: 700;
            letter-spacing: 0.2px;
        }}
        QLabel#diagnosticBrandEnglish {{
            background: transparent;
            color: {scalar.get('nav_muted')};
            font-size: 7.5pt;
            font-weight: 700;
            letter-spacing: 1.0px;
        }}
        QLabel#diagnosticVersion {{
            background: transparent;
            color: {scalar.get('nav_muted')};
            font-size: 7.5pt;
            padding-top: 1px;
        }}
        QFrame#diagnosticNavDivider {{
            background: rgba(255, 255, 255, 0.10);
            max-height: 1px;
            min-height: 1px;
            margin: 2px 14px 8px 14px;
        }}
        QListWidget#diagnosticNav {{
            background: transparent;
            color: {scalar.get('nav_text')};
            border: 0;
            padding: 2px 8px;
            outline: 0;
            font-size: 9pt;
        }}
        QListWidget#diagnosticNav::item {{
            min-height: 36px;
            padding: 6px 8px;
            border: 0;
            border-left: 3px solid transparent;
            border-radius: {RADIUS_MD}px;
            margin: 2px 0;
        }}
        QListWidget#diagnosticNav::item:hover {{
            background: {scalar.get('nav_hover')};
            color: #ffffff;
        }}
        QListWidget#diagnosticNav::item:selected {{
            background: {scalar.get('nav_selected')};
            color: {scalar.get('nav_selected_text')};
            border-left: 3px solid {scalar.get('highlight')};
            font-weight: 700;
        }}
        QStatusBar {{
            background: {scalar.get('panel_bg')};
            color: {scalar.get('muted_text')};
            border-top: 1px solid {scalar.get('border')};
            padding-left: 12px;
        }}
    """


def build_acquisition_stylesheet(theme: dict[str, object]) -> str:
    """Shared chrome + acquisition-specific group title / workspace polish."""
    mode = "dark"
    plot_bg = str((theme or {}).get("plot_bg", "")).lower()
    if plot_bg in {"#ffffff", "white"}:
        mode = "light"
    theme = resolve_theme(theme, mode=mode)
    scalar = _scalar_theme(theme)
    on_accent = scalar.get("on_accent", "#ffffff")
    shared = build_shared_stylesheet(theme)
    return shared + f"""
        QGroupBox {{
            margin-top: 16px;
            padding: 8px 8px 8px 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 2px 8px;
            background: {scalar.get('window_bg')};
            color: {scalar.get('accent')};
            font-weight: 700;
            border-left: 3px solid {scalar.get('accent')};
            border-radius: 0;
        }}
        QTabWidget::pane {{
            border: 1px solid {scalar.get('border')};
            background: {scalar.get('panel_bg')};
            border-radius: {RADIUS_LG}px;
        }}
        QTabBar::tab {{
            background: {scalar.get('panel_bg_alt')};
            color: {scalar.get('muted_text')};
            border: 1px solid {scalar.get('border')};
            border-bottom: 0;
            border-top-left-radius: {RADIUS_MD}px;
            border-top-right-radius: {RADIUS_MD}px;
            padding: 5px 10px;
            min-width: 56px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background: {scalar.get('accent')};
            color: {on_accent};
            border-color: {scalar.get('accent')};
            border-bottom: 0;
            font-weight: 700;
        }}
        QTabBar::tab:hover:!selected {{
            color: {scalar.get('accent')};
            background: {scalar.get('accent_soft')};
        }}
        QMenuBar {{
            background: {scalar.get('menu_bg')};
            color: {scalar.get('text')};
            border-bottom: 1px solid {scalar.get('border')};
            padding: 3px 6px;
        }}
        QMenuBar::item {{
            padding: 5px 10px;
            border-radius: {RADIUS_SM}px;
        }}
        QMenuBar::item:selected {{
            background: {scalar.get('accent_soft')};
            color: {scalar.get('accent')};
        }}
        QMenu {{
            background: {scalar.get('menu_bg')};
            border-radius: {RADIUS_LG}px;
        }}
        QMenu::item:selected {{
            background: {scalar.get('selection_bg')};
            color: {scalar.get('selection_text')};
        }}
        QStatusBar {{
            background: {scalar.get('menu_bg')};
            color: {scalar.get('muted_text')};
            border-top: 1px solid {scalar.get('border')};
            padding-left: 10px;
        }}
        QWidget#plotWorkspace {{
            background: {scalar.get('plot_workspace_bg')};
        }}
        QWidget#plotWorkspace QLabel {{
            color: {scalar.get('label_text')};
        }}
        QWidget#topToolbar {{
            background: {scalar.get('panel_bg')};
            border: 1px solid {scalar.get('border')};
            border-radius: {RADIUS_LG}px;
        }}
        QWidget#topToolbar QLabel {{
            color: {scalar.get('label_text')};
            font-weight: 600;
        }}
        QWidget#topToolbar QPushButton, QWidget#topToolbar QToolButton {{
            border-radius: {RADIUS_MD}px;
            min-height: 22px;
            font-size: {FONT_SIZE_COMPACT};
            padding: 2px 8px;
        }}
        QFrame#toolbarDivider {{
            background: {scalar.get('border')};
            max-width: 1px;
            min-width: 1px;
            margin: 6px 4px;
        }}
    """
