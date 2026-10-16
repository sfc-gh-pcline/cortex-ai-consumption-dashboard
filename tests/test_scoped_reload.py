"""Scoped eviction must cause selected queries to rerun without clearing neighbors."""

import datetime as dt
from unittest.mock import Mock

import pandas as pd
import streamlit as st

from utils import queries as q


def test_reload_evicts_selected_dependency_only(monkeypatch):
    calls = []

    def query(sql, **_kwargs):
        calls.append(sql)
        return pd.DataFrame({"CREDITS": [1.0]})

    def load(dependency, *_args):
        return q.run_query("SELECT " + dependency)

    monkeypatch.setattr(q, "_connection", lambda: Mock(query=query))
    monkeypatch.setattr(q, "load_dependency", load)
    st.session_state.dependency_query_keys = {}
    st.session_state.source_query_count = 0
    q.run_query.clear()
    window = (dt.date(2026, 9, 1), dt.date(2026, 9, 7))
    try:
        for _ in range(2):
            q.load_dependency_result(q.SURFACE_COCO, *window)
            q.load_dependency_result(q.SURFACE_AGENTS, *window)
        assert calls == ["SELECT CoCo", "SELECT Cortex Agents"]
        assert st.session_state.source_query_count == 2
        q.reload_dependencies((q.SURFACE_COCO,))
        q.load_dependency_result(q.SURFACE_COCO, *window)
        q.load_dependency_result(q.SURFACE_AGENTS, *window)
        assert calls == ["SELECT CoCo", "SELECT Cortex Agents", "SELECT CoCo"]
        assert st.session_state.source_query_count == 3
    finally:
        q.run_query.clear()
        st.session_state.pop("dependency_query_keys", None)
        st.session_state.pop("source_query_count", None)
