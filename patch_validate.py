import re

with open('lib/wiki_ops.py', 'r') as f:
    content = f.read()

search = r'def _validate_and_resolve_path\(wiki_dir: Path, rel_path: str\) -> Path:.*?return full_path'
replace = r'''def _validate_and_resolve_path(wiki_dir: Path, rel_path: str) -> Path:
    """Resolve and validate that a path remains inside the wiki directory (prevents path traversal)."""
    full_path = (wiki_dir / rel_path).resolve()
    wiki_sub_dir = (wiki_dir / "wiki").resolve()
    if not full_path.is_relative_to(wiki_sub_dir):
        raise ValueError(f"Path must be within wiki/ directory: {rel_path}")
    return full_path'''

new_content = re.sub(search, replace, content, flags=re.DOTALL)

with open('lib/wiki_ops.py', 'w') as f:
    f.write(new_content)
