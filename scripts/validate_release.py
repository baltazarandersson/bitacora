"""Reject a release tag that does not match the package version."""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path


def package_version(root: Path) -> str:
    tree = ast.parse((root / "bitacora" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", None) == "__version__"
                                               for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise ValueError("package version is missing")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_REF_NAME", "")
    expected = "v" + package_version(root)
    if tag != expected:
        raise SystemExit(f"Release tag {tag!r} does not match {expected!r}")
    print(f"Release version validated: {tag}")
