#!/usr/bin/env python3
"""Enforce the workspace's 7,200-character distillation threshold."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
excluded = {".git", ".venv", "node_modules", "outputs", "work", ".cache"}
too_long = []
for path in sorted(root.rglob("*.md")):
    if excluded.intersection(path.relative_to(root).parts):
        continue
    length = len(path.read_text(encoding="utf-8"))
    print(f"{path.relative_to(root)}: {length}")
    if length >= 7200:
        too_long.append(path)
if too_long:
    raise SystemExit("核心文档达到 7,200 字符，请先蒸馏后继续提交")
