"""Numeric contrast and stable chart-identity checks."""

import tomllib
from pathlib import Path

from utils import ui


def contrast(first, second):
    """Calculate the WCAG relative-luminance contrast ratio."""

    def luminance(color):
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return sum(
            value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
        )

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_theme_text_and_action_contrast():
    config = tomllib.loads(
        (Path(__file__).resolve().parents[1] / ".streamlit/config.toml").read_text()
    )
    theme = config["theme"]
    assert contrast(theme["primaryColor"], "#ffffff") >= 4.5
    for mode in ("dark", "light"):
        palette = theme[mode]
        for background in ("backgroundColor", "secondaryBackgroundColor"):
            assert contrast(palette["textColor"], palette[background]) >= 4.5
            assert contrast(palette["linkColor"], palette[background]) >= 4.5
    assert contrast("#29B5E8", "#ffffff") < 4.5


def test_chart_identity_and_null_gap_policy():
    light = ui.trend_spec("CREDITS", "SURFACE", "Credits")
    dark = ui.trend_spec("CREDITS", "SURFACE", "Credits", dark=True)
    assert (
        light["encoding"]["color"]["scale"]["domain"]
        == dark["encoding"]["color"]["scale"]["domain"]
    )
    assert (
        light["encoding"]["color"]["scale"]["range"] != dark["encoding"]["color"]["scale"]["range"]
    )
    assert light["mark"]["invalid"] == "break-paths-show-domains"
    assert "strokeDash" in light["encoding"]
    assert light["encoding"]["y"]["stack"] is None


def test_dash_domain_matches_color_domain():
    light = ui.trend_spec("CREDITS", "SURFACE", "Credits")
    assert (
        light["encoding"]["strokeDash"]["scale"]["domain"]
        == light["encoding"]["color"]["scale"]["domain"]
    )
    assert len(light["encoding"]["strokeDash"]["scale"]["range"]) == len(
        light["encoding"]["color"]["scale"]["domain"]
    )


def test_status_colors_meet_text_contrast_in_each_theme():
    for dark, background in ((False, "#ffffff"), (True, "#161b22")):
        for status in (
            "Ready",
            "Unknown",
            "Stale snapshot",
            "Awaiting first refresh",
            "Refresh failed",
            "Task missing",
            "Suspended",
            "Not created",
        ):
            assert contrast(ui.resource_status_color(status, dark=dark), background) >= 4.5


def test_selected_controls_use_readable_labels_without_changing_primary_buttons():
    for color, tinted_background in (
        ("#e2e8f0", "#0d1c25"),
        ("#1e293b", "#e0eef3"),
    ):
        style = ui.selected_control_style()
        assert 'button[role="radio"][aria-checked="true"]' in style
        assert "color: inherit" in style
        assert "background" not in style
        assert contrast(color, tinted_background) >= 4.5
    assert contrast("#087ea4", "#0d1c25") < 4.5
