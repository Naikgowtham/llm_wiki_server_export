from lib.diff_tracker import get_diff_deltas

old_text = """# Header
This is line 1.
This is line 2.
This is line 3."""

new_text = """# Header
This is line 1.
This is line 3."""

print(get_diff_deltas(old_text, new_text))
