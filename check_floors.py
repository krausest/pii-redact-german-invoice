"""List pyproject.toml floors (>=) that lag behind the version in uv.lock.

A uv.lock-only push may leave GitHub's dependency graph stale, so Dependabot
alerts stay open; raising the floor in pyproject.toml refreshes it.
Exits 1 if any floor lags. With --update, raises the lagging floors in place.
"""

import re
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

root = Path(__file__).parent
pyproject = tomllib.loads((root / "pyproject.toml").read_text())
lock = tomllib.loads((root / "uv.lock").read_text())

locked: dict[str, Version] = {}
for pkg in lock["package"]:
    name, version = canonicalize_name(pkg["name"]), pkg.get("version")
    if version and (name not in locked or Version(version) > locked[name]):
        locked[name] = Version(version)

specs = list(pyproject["project"].get("dependencies", []))
for group in pyproject.get("dependency-groups", {}).values():
    specs += [s for s in group if isinstance(s, str)]

update = "--update" in sys.argv[1:]
text = (root / "pyproject.toml").read_text()

lagging = []
for spec in specs:
    req = Requirement(spec)
    current = locked.get(canonicalize_name(req.name))
    floors = [s.version for s in req.specifier if s.operator == ">="]
    floor = max(floors, key=Version, default=None)
    if current and floor and Version(floor) < current:
        lagging.append(f"  {req.name}: >={floor}  ->  >={current}")
        raised = re.sub(rf">=\s*{re.escape(floor)}(?![\w.])", f">={current}", spec, count=1)
        text = text.replace(f'"{spec}"', f'"{raised}"', 1)

if not lagging:
    print("pyproject.toml floors match uv.lock.")
elif update:
    (root / "pyproject.toml").write_text(text)
    print("Raised pyproject.toml floors to uv.lock — now run `uv lock`:")
    print("\n".join(lagging))
else:
    print("!! pyproject.toml floors below uv.lock — raise at least the security-fixed ones")
    print("   (or rerun with --update):")
    print("\n".join(lagging))
    sys.exit(1)
