"""The running-build fingerprint is local-only and flags imports older than the files on disk."""

import datetime as dt
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from utils import runtime

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    (tmp_path / "utils").mkdir()
    (tmp_path / "app_pages").mkdir()
    (tmp_path / "streamlit_app.py").write_text("print('entry')\n")
    (tmp_path / "utils" / "queries.py").write_text("X = 1\n")
    (tmp_path / "app_pages" / "coco.py").write_text("Y = 2\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.3.0"\n')
    return tmp_path


def _touch(path: Path, when: dt.datetime) -> None:
    os.utime(path, (when.timestamp(), when.timestamp()))


def test_files_newer_than_import_are_flagged_stale(project):
    imported = dt.datetime(2026, 9, 21, 12, 46, tzinfo=dt.UTC)
    for path in runtime.runtime_files(project):
        _touch(path, imported - dt.timedelta(hours=1))
    fresh = runtime.fingerprint(project, imported)
    assert not fresh.stale_imports
    assert fresh.runtime_files == 3
    assert fresh.project_version == "0.3.0"

    _touch(project / "utils" / "queries.py", imported + dt.timedelta(hours=4))
    stale = runtime.fingerprint(project, imported)
    assert stale.stale_imports
    assert stale.newest_file_at == imported + dt.timedelta(hours=4)
    assert stale.artifact_digest == fresh.artifact_digest


def test_digest_tracks_content_not_timestamps(project):
    before = runtime.fingerprint(project, runtime.IMPORTED_AT).artifact_digest
    (project / "utils" / "queries.py").write_text("X = 2\n")
    after = runtime.fingerprint(project, runtime.IMPORTED_AT).artifact_digest
    assert before != after
    assert len(after) == 12


def test_missing_pyproject_is_unknown_not_an_error(project):
    (project / "pyproject.toml").unlink()
    assert runtime.fingerprint(project, runtime.IMPORTED_AT).project_version == "unknown"


def test_module_origins_detect_mixed_directories(monkeypatch):
    import types

    stale = types.ModuleType("utils.queries")
    stale.__file__ = "/opt/old/utils/queries.py"
    fresh = types.ModuleType("utils.runtime")
    fresh.__file__ = "/opt/new/utils/runtime.py"
    monkeypatch.setitem(runtime.sys.modules, "utils.queries", stale)
    monkeypatch.setitem(runtime.sys.modules, "utils.runtime", fresh)
    origins = dict(runtime.loaded_module_origins())
    assert origins["utils.queries"] == "/opt/old/utils/queries.py"
    info = runtime.fingerprint(ROOT, runtime.IMPORTED_AT)
    assert info.mixed_origins
    assert "/opt/old/utils" in info.origin_directories

    monkeypatch.setitem(runtime.sys.modules, "utils.queries", fresh)
    single = runtime.fingerprint(ROOT, runtime.IMPORTED_AT)
    assert not any(directory == "/opt/old/utils" for directory in single.origin_directories)


def _fake_module(name: str, path: Path):
    import types

    module = types.ModuleType(name)
    module.__file__ = str(path)
    return module


@pytest.fixture
def restore_utils_modules():
    """Put the real utils module objects back so other tests keep their identities."""
    saved = {
        name: module for name, module in runtime.sys.modules.items() if name.startswith("utils")
    }
    yield
    for name in [name for name in runtime.sys.modules if name.startswith("utils")]:
        del runtime.sys.modules[name]
    runtime.sys.modules.update(saved)


@pytest.mark.usefixtures("restore_utils_modules")
def test_stamping_imports_the_whole_package():
    runtime.stamp_loaded_modules()
    expected = {
        f"utils.{path.stem}" for path in (ROOT / "utils").glob("*.py") if path.stem != "__init__"
    }
    assert expected <= set(runtime.sys.modules)
    assert all(hasattr(runtime.sys.modules[name], runtime.STAMP) for name in expected)
    assert runtime.refresh_stale_modules() == ()


@pytest.mark.usefixtures("restore_utils_modules")
def test_refresh_drops_whole_family_when_one_file_changed(tmp_path, monkeypatch):
    fresh = tmp_path / "fresh.py"
    changed = tmp_path / "changed.py"
    fresh.write_text("A = 1\n")
    changed.write_text("B = 1\n")
    for name, path in (("utils.fresh_mod", fresh), ("utils.changed_mod", changed)):
        monkeypatch.setitem(runtime.sys.modules, name, _fake_module(name, path))
    real_utils = {name for name in runtime.sys.modules if name.startswith("utils")}
    runtime.stamp_loaded_modules()
    assert runtime.refresh_stale_modules() == ()
    assert real_utils <= set(runtime.sys.modules)

    _touch(changed, dt.datetime.now(dt.UTC) + dt.timedelta(hours=1))  # stamped, then file changed
    dropped = runtime.refresh_stale_modules()
    assert "utils.changed_mod" in dropped
    assert "utils.fresh_mod" in dropped
    assert "utils.runtime" in dropped
    assert not any(name.startswith("utils") for name in runtime.sys.modules)
    # The real package re-imports cleanly afterwards.
    import utils.runtime as reloaded

    assert callable(reloaded.refresh_stale_modules)


@pytest.mark.usefixtures("restore_utils_modules")
def test_unstamped_module_is_stale_only_if_its_file_postdates_process_start(monkeypatch, tmp_path):
    started = dt.datetime.fromtimestamp(runtime.process_started_at(), dt.UTC)
    old = tmp_path / "imported_at_boot.py"
    old.write_text("C = 1\n")
    _touch(old, started - dt.timedelta(minutes=5))
    monkeypatch.setitem(runtime.sys.modules, "utils.at_boot", _fake_module("utils.at_boot", old))
    assert not runtime.is_stale(runtime.sys.modules["utils.at_boot"])
    assert runtime.refresh_stale_modules() == ()

    synced = tmp_path / "synced_later.py"
    synced.write_text("D = 1\n")
    _touch(synced, started + dt.timedelta(minutes=5))
    monkeypatch.setitem(runtime.sys.modules, "utils.synced", _fake_module("utils.synced", synced))
    assert runtime.is_stale(runtime.sys.modules["utils.synced"])
    assert "utils.synced" in runtime.refresh_stale_modules()


@pytest.mark.usefixtures("restore_utils_modules")
def test_entrypoint_recovers_from_stale_module(monkeypatch):
    """A module object older than its file is replaced before the entrypoint uses it."""
    import types

    from utils import queries as real_queries

    stale = types.ModuleType("utils.queries")
    stale.__file__ = real_queries.__file__
    setattr(stale, runtime.STAMP, 0.0)  # stamped when the file was much older
    monkeypatch.setitem(runtime.sys.modules, "utils.queries", stale)
    assert not hasattr(stale, "prepare_acquisition")

    dropped = runtime.refresh_stale_modules()
    assert "utils.queries" in dropped
    from utils import queries as refreshed

    assert hasattr(refreshed, "prepare_acquisition")


def test_real_process_imports_from_one_directory():
    info = runtime.fingerprint()
    assert not info.mixed_origins
    assert info.origin_directories == (str(ROOT / "utils"),)


def test_config_page_shows_build_before_admin_gate(monkeypatch):
    monkeypatch.delenv("DASHBOARD_ADMIN_USERS", raising=False)
    app = AppTest.from_file(str(ROOT / "app_pages/config.py"))
    app.run()
    assert not app.exception
    captions = [item.value for item in app.caption]
    assert any(value.startswith("Build 0.3.0 / ") and "connector" in value for value in captions)
    assert any("authorized dashboard administrator" in item.value for item in app.info)
    assert not app.warning
