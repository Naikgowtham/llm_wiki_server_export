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


def test_review_changes_non_interactive(monkeypatch):
    """Test review_changes in non-interactive environment (not a tty)."""
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    changes = [FileChange(path="wiki/concepts/test.md", operation="create", new_content="Content")]
    assert review_changes(changes, auto_approve=False) == []


def test_review_changes_reject_all(monkeypatch):
    """Test user pressing 's' to reject all changes."""
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "s")
    changes = [FileChange(path="wiki/concepts/test.md", operation="create", new_content="Content")]
    assert review_changes(changes, auto_approve=False) == []


def test_review_changes_approve_all(monkeypatch):
    """Test user pressing 'a' to approve all changes."""
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "a")
    changes = [FileChange(path="wiki/concepts/test.md", operation="create", new_content="Content")]
    approved = review_changes(changes, auto_approve=False)
    assert len(approved) == 1


def test_review_changes_step_by_step(monkeypatch):
    """Test user reviewing step-by-step with 'r', approving first, skipping second."""
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    inputs = iter(["r", "y", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    changes = [
        FileChange(path="wiki/concepts/c1.md", operation="create", new_content="New 1"),
        FileChange(path="wiki/concepts/c2.md", operation="update", old_content="Old 2", new_content="New 2"),
    ]
    approved = review_changes(changes, auto_approve=False)
    assert len(approved) == 1
    assert approved[0].path == "wiki/concepts/c1.md"


def test_review_changes_abort_eof(monkeypatch):
    """Test handling of EOFError during review."""
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    def raise_eof(prompt=""):
        raise EOFError()
    monkeypatch.setattr("builtins.input", raise_eof)

    changes = [FileChange(path="wiki/concepts/test.md", operation="create", new_content="Content")]
    assert review_changes(changes, auto_approve=False) == []

