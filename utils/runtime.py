"""Read-only identity of the code this process is actually running.

A hosted container re-executes ``streamlit_app.py`` from disk on every run but
keeps already-imported ``utils`` modules cached for the life of the process, so a
newly committed version can leave the entrypoint new and its imports old. This
module records when the process first imported it and compares that with the
newest runtime file on disk; a newer file means the process needs a restart.
No Snowflake access is performed here.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import os
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Captured once per process, at first import. Streamlit imports this module from the
# entrypoint, so this is effectively the process's import generation timestamp.
IMPORTED_AT = dt.datetime.now(dt.UTC)


@dataclass(frozen=True)
class RuntimeFingerprint:
    """What is running: declared version, on-disk code digest, and import freshness."""

    project_version: str
    artifact_digest: str
    runtime_files: int
    newest_file_at: dt.datetime | None
    imported_at: dt.datetime
    streamlit_version: str
    connector_version: str
    connection_ttl: str
    module_origins: tuple[tuple[str, str], ...] = ()

    @property
    def stale_imports(self) -> bool:
        """True when a runtime file changed on disk after this process imported its modules."""
        return self.newest_file_at is not None and self.newest_file_at > self.imported_at

    @property
    def origin_directories(self) -> tuple[str, ...]:
        """Distinct directories the loaded ``utils`` modules were imported from."""
        return tuple(sorted({str(Path(path).parent) for _name, path in self.module_origins}))

    @property
    def mixed_origins(self) -> bool:
        """True when ``utils`` modules were imported from more than one directory."""
        return len(self.origin_directories) > 1


STAMP = "__source_mtime__"
# Process start marker kept in the environment so it survives re-imports of this module.
_STARTED_KEY = "DASHBOARD_PROCESS_STARTED_AT"
os.environ.setdefault(_STARTED_KEY, str(time.time()))


def process_started_at() -> float:
    """Return when this process first imported the dashboard, as a POSIX timestamp."""
    return float(os.environ[_STARTED_KEY])


def is_stale(module: ModuleType) -> bool:
    """Decide whether a loaded module's file changed after the module was imported.

    A stamped module is stale when its recorded mtime differs from the file. An
    unstamped module (imported before any stamping, for example at boot from an old
    snapshot) is stale only when its file was written after the process started.
    """
    mtime = _source_mtime(module)
    stamp = getattr(module, STAMP, None)
    if stamp is not None:
        return stamp != mtime
    return mtime is not None and mtime > process_started_at()


def _utils_modules() -> list[tuple[str, ModuleType]]:
    return [
        (name, module)
        for name, module in list(sys.modules.items())
        if (name == "utils" or name.startswith("utils.")) and getattr(module, "__file__", None)
    ]


def _source_mtime(module: ModuleType) -> float | None:
    try:
        return Path(str(module.__file__)).stat().st_mtime
    except OSError:
        return None


def stamp_loaded_modules() -> None:
    """Import every ``utils`` submodule, then record each one's current source mtime.

    Importing the whole package here means a page cannot later add an unstamped
    module, which the next refresh would otherwise treat as stale on every run.
    """
    import importlib
    import pkgutil

    import utils

    for info in pkgutil.iter_modules(utils.__path__):
        importlib.import_module(f"utils.{info.name}")
    for _name, module in _utils_modules():
        setattr(module, STAMP, _source_mtime(module))


def refresh_stale_modules() -> tuple[str, ...]:
    """Drop every ``utils`` module from the import cache if any file changed since import.

    Hosted containers can boot from an old file snapshot, import those modules, and
    only then receive the committed files. The entrypoint is re-read on every run,
    but imported modules are not, so it calls this first; the next import statement
    then loads the current files. Modules are dropped as a family so they never mix.

    Returns:
        The names that were dropped, in sorted order; empty when nothing changed.
    """
    modules = _utils_modules()
    if not any(is_stale(module) for _name, module in modules):
        return ()
    dropped = sorted(name for name, _module in modules)
    for name in dropped:
        sys.modules.pop(name, None)
    return tuple(dropped)


def loaded_module_origins() -> tuple[tuple[str, str], ...]:
    """Return (module name, file path) for every imported ``utils`` module.

    A namespace package can merge a stale directory with the current one, so the
    origin of each module is the fact that distinguishes stale disk from stale memory.
    """
    origins = []
    for name, module in sorted(sys.modules.items()):
        if name.startswith("utils.") and getattr(module, "__file__", None):
            origins.append((name, str(module.__file__)))
    return tuple(origins)


def runtime_files(root: Path) -> list[Path]:
    """Return the Python runtime files whose in-memory copies can go stale."""
    files = [root / "streamlit_app.py"]
    for folder in ("utils", "app_pages"):
        files.extend(sorted((root / folder).glob("*.py")))
    return [path for path in files if path.is_file()]


def fingerprint(
    root: Path = PROJECT_ROOT, imported_at: dt.datetime = IMPORTED_AT
) -> RuntimeFingerprint:
    """Compute the fingerprint from local files and installed package metadata only."""
    files = runtime_files(root)
    digest = hashlib.sha256()
    newest: float | None = None
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
        modified = path.stat().st_mtime
        newest = modified if newest is None or modified > newest else newest
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text())
        version = str(project["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        version = "unknown"
    try:
        connector = importlib.metadata.version("snowflake-connector-python")
    except importlib.metadata.PackageNotFoundError:
        connector = "unknown"
    return RuntimeFingerprint(
        project_version=version,
        artifact_digest=digest.hexdigest()[:12],
        runtime_files=len(files),
        newest_file_at=dt.datetime.fromtimestamp(newest, dt.UTC) if newest is not None else None,
        imported_at=imported_at,
        streamlit_version=st.__version__,
        connector_version=connector,
        connection_ttl=os.getenv("SNOWFLAKE_CONNECTION_TTL") or "default",
        module_origins=loaded_module_origins(),
    )


def render_sidebar_build(info: RuntimeFingerprint) -> None:
    """Show the build digest in the sidebar and warn when loaded modules disagree.

    Rendered before any data work so it stays visible when a page fails later.
    """
    st.sidebar.caption(f"Streamlit {info.streamlit_version} | build {info.artifact_digest}")
    if info.mixed_origins:
        st.sidebar.warning(
            "Modules were imported from more than one directory: "
            + "; ".join(info.origin_directories)
            + ". The running code mixes versions.",
            icon=":material/warning:",
        )


def render_fingerprint(info: RuntimeFingerprint) -> None:
    """Show the running build compactly; warn when imports predate the files on disk."""
    st.caption(
        f"Build {info.project_version} / {info.artifact_digest} "
        f"({info.runtime_files} runtime files) | Streamlit {info.streamlit_version} | "
        f"connector {info.connector_version} | connection TTL {info.connection_ttl} | "
        f"process imports since {info.imported_at:%Y-%m-%d %H:%M} UTC"
    )
    if info.stale_imports:
        st.warning(
            "Runtime files on disk changed at "
            f"{info.newest_file_at:%Y-%m-%d %H:%M} UTC, after this process imported its "
            "modules. The running code is a mix of versions; restart the app to load the "
            "committed version.",
            icon=":material/warning:",
        )
    if info.mixed_origins:
        st.warning(
            "Modules were imported from more than one directory: "
            + "; ".join(info.origin_directories),
            icon=":material/warning:",
        )
    with st.expander("Loaded module origins"):
        for name, path in info.module_origins:
            st.code(f"{name}  {path}")
