"""Deploy only the explicit runtime allowlist, never synthetic probes or secrets."""

import importlib.metadata
import tomllib
from pathlib import Path

import yaml


def test_manifest_runtime_modules_and_exclusions():
    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "snowflake.yml").read_text())
    entity = manifest["entities"]["streamlit_app"]
    artifacts = entity["artifacts"]
    assert all(isinstance(path, str) and (root / path).is_file() for path in artifacts)
    assert len(artifacts) == len(set(artifacts))
    required = {
        str(path.relative_to(root))
        for folder in ("utils", "app_pages")
        for path in (root / folder).glob("*.py")
        if path.name != "__init__.py"
    }
    assert required <= set(artifacts)
    # A regular package resolves to one directory; a namespace package would merge
    # a stale hosted copy with the current files.
    assert "utils/__init__.py" in artifacts
    for path in artifacts:
        assert "*" not in path
        assert not any(
            token in path.lower()
            for token in ("tests/", "secret", "credential", "probe", ".snowflake/", "screenshot")
        )
    assert entity["runtime_name"] == "SYSTEM$ST_CONTAINER_RUNTIME_PY3_11"
    assert "artifact_repositories" not in entity


def test_dependencies_declare_lower_bounds_only():
    """Exact pins made the hosted runtime rewrite its venv under a running process."""
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    dependencies = project["project"]["dependencies"]
    assert dependencies == ["streamlit[snowflake]>=1.61.0"]
    installed = tuple(int(part) for part in importlib.metadata.version("streamlit").split(".")[:2])
    assert installed >= (1, 61)
    manifest = yaml.safe_load((root / "snowflake.yml").read_text())
    artifacts = manifest["entities"]["streamlit_app"]["artifacts"]
    assert "admin_access.toml" in artifacts
    # The hosted runtime resolves pyproject.toml through the attached artifact
    # repository; a local lock pins pypi.org URLs the container cannot reach.
    assert "uv.lock" not in artifacts
    # Owner-managed allowlist of authenticated viewer usernames; the app runs with
    # owner's rights so roles cannot identify viewers. Entries must be explicit names.
    users = tomllib.loads((root / "admin_access.toml").read_text())["users"]
    assert users == []
    assert all(isinstance(name, str) and name and name == name.upper() for name in users)
