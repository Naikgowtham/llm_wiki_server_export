import pytest
from lib.diff_tracker import get_diff_deltas

def test_get_diff_deltas_insert():
    old = "Line 1\nLine 3"
    new = "Line 1\nLine 2\nLine 3"
    diff = get_diff_deltas(old, new)
    assert "[+ INSERTED: Line 2 +]" in diff
    assert "Line 1" in diff
    assert "Line 3" in diff

def test_get_diff_deltas_delete():
    old = "Line 1\nLine 2\nLine 3"
    new = "Line 1\nLine 3"
    diff = get_diff_deltas(old, new)
    assert "[- DELETED: Line 2 -]" in diff
    assert "Line 1" in diff
    assert "Line 3" in diff

def test_get_diff_deltas_replace():
    old = "Line 1\nOld Line\nLine 3"
    new = "Line 1\nNew Line\nLine 3"
    diff = get_diff_deltas(old, new)
    assert "[- DELETED: Old Line -]" in diff
    assert "[+ INSERTED: New Line +]" in diff

def test_get_diff_deltas_with_heading():
    old = "# Section\nLine 1\nLine 3"
    new = "# Section\nLine 1\nLine 2\nLine 3"
    diff = get_diff_deltas(old, new)
    assert "# Section" in diff
    assert "[+ INSERTED: Line 2 +]" in diff
