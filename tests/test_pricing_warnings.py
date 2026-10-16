"""Pricing warnings explain missing prices without implying missing usage."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from utils import queries as q
from utils import ui


@pytest.mark.parametrize(
    ("count", "label", "summary"),
    [
        (1, "1 token not priced", "1 Cortex Inference API token could not be priced"),
        (
            12_345,
            "12,345 tokens not priced",
            "12,345 Cortex Inference API tokens could not be priced",
        ),
        (
            None,
            "Token pricing coverage unknown",
            "Cortex Inference API pricing coverage is unknown",
        ),
        (
            float("nan"),
            "Token pricing coverage unknown",
            "Cortex Inference API pricing coverage is unknown",
        ),
    ],
)
def test_pricing_copy_handles_exact_and_unknown_counts(count, label, summary):
    assert ui.inference_pricing_label(count) == label
    assert ui.inference_pricing_warning(count).startswith(summary)
    assert ui.inference_pricing_warning(count, overview=True).startswith(summary)


def test_complete_pricing_label_does_not_claim_billing_accuracy():
    assert ui.inference_pricing_label(0) == "All reported tokens have observed rates"


@pytest.mark.parametrize("page", ["total_consumption", "cortex_inference_api"])
@pytest.mark.parametrize("unrated", [349, None, 0])
def test_pricing_warning_uses_current_filtered_facts(monkeypatch, page, unrated):
    frame = pd.DataFrame(
        {
            "USAGE_DATE": [date(2026, 9, 19), date(2026, 9, 19)],
            "USER_NAME": ["Alice", "Bob"],
            "MODEL_NAME": ["model-a", "model-b"],
            "SURFACE": [q.SURFACE_REST_API] * 2,
            "CREDITS_ESTIMATED": [True, True],
            "CREDITS": [1.0, 2.0],
            "TOKENS": [1_000_000, 500],
            "CALLS": [3, 1],
            "TOK_INPUT": [999_651, 0],
            "TOK_OUTPUT": [349, 500],
            "INFERENCE_REGION": ["region-a", "region-b"],
            "QUERY_TAG": ["tag-a", "tag-b"],
            "UNRATED_TOKENS": [unrated, 500],
            "RATE_INCOMPLETE": [unrated is None or unrated > 0, True],
        }
    )
    for loader in ("load_unified_facts", "load_rest_facts"):
        monkeypatch.setattr(q, loader, lambda *_args: frame.copy())
    root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(str(root / "app_pages" / f"{page}.py"))
    for key, value in {
        "start_date": date(2026, 9, 18),
        "end_date": date(2026, 9, 19),
        "users": ["Alice"],
        "models": [],
        "tags": [],
        "tag_users": None,
    }.items():
        app.session_state[key] = value
    app.run()
    assert not app.exception
    warnings = " ".join(item.value for item in app.warning)
    if unrated == 0:
        assert "could not be priced" not in warnings
        assert "pricing coverage is unknown" not in warnings
        return
    if unrated is None:
        assert "pricing coverage is unknown" in warnings
        assert "unpriced token count is unavailable" in warnings
        assert "0 Cortex Inference API tokens" not in warnings
    else:
        assert "349 Cortex Inference API tokens could not be priced" in warnings
        assert "849 Cortex" not in warnings
        assert "model/token-type rate or token-type breakdown is missing" in warnings
    assert "Tokens and requests are still counted" in warnings
    assert "unpriced does not mean free" in warnings
    if page == "total_consumption":
        assert "Estimated credits and AI usage credits" in warnings
        assert "Reported credits are unaffected" in warnings
    else:
        assert "Credits (partial estimate)" in warnings
        assert all("100% of tokens rated" not in item.value for item in app.info)
