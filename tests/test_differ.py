"""Unit tests for diff and review module."""

from lib.differ import FileChange, _render_unified_diff, review_changes


def test_render_unified_diff():
    old_text = "Line 1\nLine 2\nLine 3"
    new_text = "Line 1\nLine 2 modified\nLine 3"
    diff = _render_unified_diff(old_text, new_text, "concepts/test.md")
    assert "-Line 2" in diff
    assert "+Line 2 modified" in diff


def test_review_changes_auto_approve():
    changes = [
        FileChange(
            path="wiki/concepts/test.md",
            operation="create",
            new_content="---\ntitle: Test\n---\nBody",
            reason="Test create",
        )
    ]
    approved = review_changes(changes, auto_approve=True)
    assert len(approved) == 1
    assert approved[0].path == "wiki/concepts/test.md"
