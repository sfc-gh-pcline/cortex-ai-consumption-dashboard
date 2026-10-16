"""Loading-dialog labels are human readable; raw keys stay in diagnostics."""

from utils import queries as q


def test_user_tags_key_has_display_label():
    assert q.dependency_label("user_tags") == "User tags"


def test_surface_titles_are_their_own_labels():
    for key in q.DEPENDENCY_KINDS:
        if key != "user_tags":
            assert q.dependency_label(key) == key


def test_every_non_title_dependency_has_a_label():
    for key in q.DEPENDENCY_KINDS:
        assert key in q.DEPENDENCY_LABELS or key[0].isupper(), key
