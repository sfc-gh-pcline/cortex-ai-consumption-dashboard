"""Tests for :mod:`utils.snowflake`.

Offline tests: no Snowflake connection needed, safe for CI.
"""

import dataclasses
import datetime
import math

import pytest

from utils import queries, snowflake
from utils.snowflake import (
    DtBodySpec,
    OwnershipConflictError,
    _bind_params,
    _render_sql_literal,
    quote_identifier,
)


@pytest.mark.parametrize("module", [snowflake, queries])
@pytest.mark.parametrize(
    ("configured", "seconds"),
    [(None, None), ("1800", 1800), ("0.5", 0.5), ("30m", 1800), ("1h", 3600)],
)
def test_connection_ttl_preserves_seconds(monkeypatch, module, configured, seconds):
    from unittest.mock import Mock

    if configured is None:
        monkeypatch.delenv("SNOWFLAKE_CONNECTION_TTL", raising=False)
    else:
        monkeypatch.setenv("SNOWFLAKE_CONNECTION_TTL", configured)
    connection = Mock()
    monkeypatch.setattr(snowflake.st, "connection", connection)
    module._connection()
    ttl = connection.call_args.kwargs["ttl"]
    actual = ttl.total_seconds() if isinstance(ttl, datetime.timedelta) else ttl
    assert actual == seconds


@pytest.mark.parametrize("configured", ["NaT", "nan", "inf", "-1", "not-a-duration"])
def test_connection_rejects_invalid_ttl(monkeypatch, configured):
    from unittest.mock import Mock

    monkeypatch.setenv("SNOWFLAKE_CONNECTION_TTL", configured)
    connection = Mock()
    monkeypatch.setattr(snowflake.st, "connection", connection)
    with pytest.raises(ValueError):
        snowflake._connection()
    connection.assert_not_called()


def test_hosted_ttl_reuses_real_streamlit_resource_cache(monkeypatch):
    from unittest.mock import Mock

    from streamlit.connections import SnowflakeConnection
    from streamlit.testing.v1 import AppTest

    constructor = Mock(return_value=Mock())
    monkeypatch.setenv("SNOWFLAKE_CONNECTION_TTL", "1800")
    monkeypatch.setattr(SnowflakeConnection, "_connect", constructor)
    snowflake.st.cache_resource.clear()
    try:
        app = AppTest.from_string(
            """
import time
from utils import queries, snowflake
first = snowflake._connection()
time.sleep(0.01)
second = queries._connection()
time.sleep(0.01)
assert first is second is snowflake._connection()
"""
        ).run()
        assert not app.exception
        constructor.assert_called_once()
    finally:
        snowflake.st.cache_resource.clear()


class TestQuoteIdentifier:
    """Validate identifier safety and uppercase canonicalization."""

    def test_valid_simple(self):
        assert quote_identifier("my_warehouse") == '"MY_WAREHOUSE"'

    def test_valid_with_dollar(self):
        assert quote_identifier("WH_$1") == '"WH_$1"'

    def test_uppercase_conversion(self):
        assert quote_identifier("MixedCase") == '"MIXEDCASE"'

    def test_starts_with_underscore(self):
        assert quote_identifier("_private") == '"_PRIVATE"'

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="1-255 characters"):
            quote_identifier("")

    def test_rejects_overlong(self):
        with pytest.raises(ValueError, match="1-255 characters"):
            quote_identifier("A" * 256)

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError, match="invalid characters"):
            quote_identifier("name;drop")

    def test_rejects_dot(self):
        with pytest.raises(ValueError, match="invalid characters"):
            quote_identifier("db.schema")

    def test_rejects_inline_comment(self):
        with pytest.raises(ValueError, match="invalid characters"):
            quote_identifier("name--comment")

    def test_rejects_block_comment(self):
        with pytest.raises(ValueError, match="invalid characters"):
            quote_identifier("name/*x*/")

    def test_rejects_whitespace(self):
        with pytest.raises(ValueError, match="invalid characters"):
            quote_identifier("has space")

    def test_rejects_starts_with_digit(self):
        with pytest.raises(ValueError, match="invalid characters"):
            quote_identifier("1bad")

    def test_rejects_hyphen(self):
        with pytest.raises(ValueError, match="invalid characters"):
            quote_identifier("my-warehouse")


class TestOwnershipConflictError:
    """Verify the error carries the FQN."""

    def test_message_contains_fqn(self):
        err = OwnershipConflictError('"DB"."SCHEMA"."OBJ"')
        assert '"DB"."SCHEMA"."OBJ"' in str(err)
        assert err.fqn == '"DB"."SCHEMA"."OBJ"'


class TestDtBodySpec:
    """Verify DtBodySpec instantiation and body_fn invocation without Snowflake."""

    def test_basic_instantiation(self):
        spec = DtBodySpec(
            surface_key="CoCo",
            body_fn=lambda: "SELECT 1",
        )
        assert spec.surface_key == "CoCo"
        assert spec.body_fn() == "SELECT 1"
        assert spec.create_probe is None
        assert spec.degraded_flag_key is None

    def test_with_probe_and_flag(self):
        spec = DtBodySpec(
            surface_key="Warehouse Attribution",
            body_fn=lambda: "SELECT 2",
            create_probe=lambda: "full",
            degraded_flag_key="wh_degraded",
        )
        assert spec.create_probe is not None
        assert spec.create_probe() == "full"
        assert spec.degraded_flag_key == "wh_degraded"

    def test_frozen(self):
        spec = DtBodySpec(surface_key="X", body_fn=lambda: "")
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.surface_key = "Y"  # ty: ignore[invalid-assignment]


class TestImportSafety:
    """Verify that importing utils modules does not open a Snowflake connection."""

    def test_import_snowflake_module(self):
        import importlib

        import utils.snowflake

        importlib.reload(utils.snowflake)
        # If we get here without error, no connection was attempted at import time

    def test_import_queries_module(self):
        import importlib

        import utils.queries

        importlib.reload(utils.queries)
        # queries.py imports at module level should not trigger connections


class TestRenderSqlLiteral:
    """Exhaustive offline tests for _render_sql_literal."""

    # -- None --
    def test_none(self):
        assert _render_sql_literal(None) == "NULL"

    # -- bool (must be checked before int) --
    def test_true(self):
        assert _render_sql_literal(True) == "TRUE"

    def test_false(self):
        assert _render_sql_literal(False) == "FALSE"

    # -- int --
    def test_int_zero(self):
        assert _render_sql_literal(0) == "0"

    def test_int_positive(self):
        assert _render_sql_literal(42) == "42"

    def test_int_negative(self):
        assert _render_sql_literal(-7) == "-7"

    def test_int_large(self):
        assert _render_sql_literal(10**18) == str(10**18)

    def test_bool_is_not_rendered_as_int(self):
        # bool is a subclass of int; ensure we produce TRUE/FALSE, not 1/0
        assert _render_sql_literal(True) != "1"
        assert _render_sql_literal(False) != "0"

    # -- float --
    def test_float_positive(self):
        assert _render_sql_literal(3.14) == repr(3.14)

    def test_float_negative(self):
        assert _render_sql_literal(-0.5) == repr(-0.5)

    def test_float_zero(self):
        result = _render_sql_literal(0.0)
        assert float(result) == 0.0

    def test_float_inf_raises(self):
        with pytest.raises(ValueError, match="non-finite"):
            _render_sql_literal(math.inf)

    def test_float_neg_inf_raises(self):
        with pytest.raises(ValueError, match="non-finite"):
            _render_sql_literal(-math.inf)

    def test_float_nan_raises(self):
        with pytest.raises(ValueError, match="non-finite"):
            _render_sql_literal(math.nan)

    # -- str --
    def test_str_simple(self):
        assert _render_sql_literal("hello") == "'hello'"

    def test_str_empty(self):
        assert _render_sql_literal("") == "''"

    def test_str_single_quote(self):
        # Single quote must be doubled
        assert _render_sql_literal("it's") == "'it''s'"

    def test_str_multiple_quotes(self):
        assert _render_sql_literal("a'b'c") == "'a''b''c'"

    def test_str_only_quotes(self):
        assert _render_sql_literal("'''") == "''''''''"

    def test_str_with_question_mark(self):
        # A ? inside a string value must NOT become a new placeholder
        result = _render_sql_literal("has?mark")
        assert result == "'has?mark'"

    def test_str_backslash(self):
        # Backslash must be doubled so Snowflake sees a literal backslash
        assert _render_sql_literal("back\\slash") == "'back\\\\slash'"

    def test_str_backslash_and_quote(self):
        # input: \' (backslash then apostrophe) → SQL: '\\'' (outer quotes + \\ + '')
        assert _render_sql_literal("\\'") == "'\\\\'''"

    def test_str_newline_preserved(self):
        # Newlines are passed through as-is (no special escaping needed for \n in SQL)
        result = _render_sql_literal("line1\nline2")
        assert result == "'line1\nline2'"

    def test_str_unicode(self):
        result = _render_sql_literal("caf\u00e9")
        assert result == "'caf\u00e9'"

    # -- datetime.date --
    def test_date(self):
        d = datetime.date(2024, 1, 15)
        assert _render_sql_literal(d) == "DATE '2024-01-15'"

    def test_date_padding(self):
        # Month and day must be zero-padded
        d = datetime.date(2024, 3, 5)
        assert _render_sql_literal(d) == "DATE '2024-03-05'"

    # -- datetime.datetime --
    def test_datetime(self):
        dt = datetime.datetime(2024, 6, 1, 10, 30, 0)
        assert _render_sql_literal(dt) == "TIMESTAMP '2024-06-01 10:30:00'"

    def test_datetime_midnight(self):
        dt = datetime.datetime(2024, 1, 1, 0, 0, 0)
        assert _render_sql_literal(dt) == "TIMESTAMP '2024-01-01 00:00:00'"

    def test_datetime_is_not_rendered_as_date(self):
        # datetime is a subclass of date; ensure we get TIMESTAMP, not DATE
        dt = datetime.datetime(2024, 6, 1, 10, 30, 0)
        result = _render_sql_literal(dt)
        assert result.startswith("TIMESTAMP")

    # -- unsupported types --
    def test_list_raises(self):
        with pytest.raises(TypeError, match="Unsupported type"):
            _render_sql_literal([1, 2])

    def test_dict_raises(self):
        with pytest.raises(TypeError, match="Unsupported type"):
            _render_sql_literal({"a": 1})

    def test_tuple_raises(self):
        with pytest.raises(TypeError, match="Unsupported type"):
            _render_sql_literal((1,))

    def test_bytes_raises(self):
        with pytest.raises(TypeError, match="Unsupported type"):
            _render_sql_literal(b"raw")

    def test_object_raises(self):
        with pytest.raises(TypeError, match="Unsupported type"):
            _render_sql_literal(object())


class TestBindParams:
    """Exhaustive offline tests for _bind_params."""

    # -- zero params --
    def test_no_params_no_placeholders(self):
        assert _bind_params("SELECT 1", ()) == "SELECT 1"

    def test_no_params_passthrough(self):
        sql = "SELECT * FROM t"
        assert _bind_params(sql, ()) == sql

    # -- correct substitution --
    def test_single_string(self):
        result = _bind_params("WHERE x = ?", ("hello",))
        assert result == "WHERE x = 'hello'"

    def test_single_none(self):
        assert _bind_params("WHERE x IS ?", (None,)) == "WHERE x IS NULL"

    def test_single_int(self):
        assert _bind_params("LIMIT ?", (100,)) == "LIMIT 100"

    def test_single_bool_true(self):
        assert _bind_params("SET flag = ?", (True,)) == "SET flag = TRUE"

    def test_single_bool_false(self):
        assert _bind_params("SET flag = ?", (False,)) == "SET flag = FALSE"

    def test_multiple_params_ordered(self):
        result = _bind_params("? AND ?", (1, 2))
        assert result == "1 AND 2"

    def test_string_with_question_mark_does_not_become_placeholder(self):
        # The '?' inside the rendered string value must NOT consume the next placeholder
        result = _bind_params("? AND ?", ("has?", 99))
        assert result == "'has?' AND 99"

    def test_string_with_single_quote(self):
        result = _bind_params("WHERE name = ?", ("O'Brien",))
        assert result == "WHERE name = 'O''Brien'"

    def test_date_param(self):
        d = datetime.date(2024, 3, 5)
        result = _bind_params("WHERE dt >= ?", (d,))
        assert result == "WHERE dt >= DATE '2024-03-05'"

    def test_datetime_param(self):
        dt = datetime.datetime(2024, 6, 1, 12, 0, 0)
        result = _bind_params("WHERE ts = ?", (dt,))
        assert result == "WHERE ts = TIMESTAMP '2024-06-01 12:00:00'"

    def test_mixed_types(self):
        dt = datetime.date(2025, 1, 1)
        result = _bind_params("? ? ? ?", (None, True, 7, dt))
        assert result == "NULL TRUE 7 DATE '2025-01-01'"

    # -- mismatch errors --
    def test_too_few_params_raises(self):
        with pytest.raises(ValueError, match="placeholder"):
            _bind_params("? AND ?", (1,))

    def test_too_many_params_raises(self):
        with pytest.raises(ValueError, match="placeholder"):
            _bind_params("WHERE x = ?", (1, 2))

    def test_zero_placeholders_with_params_raises(self):
        with pytest.raises(ValueError, match="placeholder"):
            _bind_params("SELECT 1", (42,))

    def test_placeholders_with_zero_params_raises(self):
        with pytest.raises(ValueError, match="placeholder"):
            _bind_params("WHERE x = ?", ())

    # -- unsupported type propagates --
    def test_unsupported_type_in_params_raises(self):
        with pytest.raises(TypeError, match="Unsupported type"):
            _bind_params("WHERE x = ?", ([1, 2],))
