"""Fail-closed authorization using the platform-authenticated viewer identity."""

import json
import os
import tomllib
from pathlib import Path

import streamlit as st


def viewer_name() -> str | None:
    """Return the platform-authenticated viewer's username, or None when unavailable."""
    try:
        viewer = st.user.get("user_name")
    except (AttributeError, TypeError):
        return None
    return viewer if isinstance(viewer, str) and viewer else None


def is_admin() -> bool:
    """Match an authenticated username against owner-managed configuration only."""
    try:
        if configured := os.environ.get("DASHBOARD_ADMIN_USERS"):
            allowed = json.loads(configured)
        else:
            path = Path(__file__).resolve().parents[1] / "admin_access.toml"
            with path.open("rb") as config_file:
                allowed = tomllib.load(config_file).get("users", [])
        viewer = st.user.get("user_name")
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return (
        isinstance(allowed, list)
        and all(isinstance(name, str) and name for name in allowed)
        and isinstance(viewer, str)
        and bool(viewer)
        and viewer in allowed
    )


def require_admin() -> None:
    """Reject mutations when a viewer has not been explicitly authorized."""
    if not is_admin():
        raise PermissionError("This action requires an authorized dashboard administrator.")
