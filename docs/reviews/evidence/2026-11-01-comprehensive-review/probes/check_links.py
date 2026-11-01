"""Check that relative Markdown links in repo docs resolve to real paths."""

import re
import sys
from pathlib import Path

ROOT = Path(r"E:\Workspace\imeeting")
DOCS = [ROOT / "README.md", ROOT / "DESIGN.md", ROOT / "OPTIMIZATION.md"]
DOCS += sorted((ROOT / "docs").rglob("*.md"))
DOCS += [ROOT / "deploy" / "README.md", ROOT / "backend" / "README.md", ROOT / "frontend" / "README.md"]

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
broken = []
checked = 0
for doc in DOCS:
    if not doc.is_file():
        continue
    text = doc.read_text(encoding="utf-8", errors="replace")
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path_part = target.split("#", 1)[0].strip()
        if not path_part:
            continue
        checked += 1
        resolved = (doc.parent / path_part).resolve()
        inside = str(resolved).startswith(str(ROOT))
        if not inside or not resolved.exists():
            broken.append((doc.relative_to(ROOT).as_posix(), target, resolved.exists()))

print(f"documents scanned: {len(DOCS)}  relative links checked: {checked}")
print(f"broken/escaping links: {len(broken)}")
for doc, target, exists in broken:
    print(f"  {doc} -> {target} (exists={exists})")
