"""Fail if a built wheel or sdist contains anything outside the allow-list.

This is the last line of defense against shipping real mail (local-data/, stray .mbox or .eml files).

    python .github/scripts/check_dist.py dist/
"""

import re
import sys
import tarfile
import zipfile
from pathlib import Path

WHEEL_ALLOWED = [
    r"mbox_md/[_a-z]+\.py",
    r"mbox_md/py\.typed",
    r"mbox_md-[^/]+\.dist-info/(METADATA|WHEEL|RECORD|entry_points\.txt|licenses/LICENSE)",
]
SDIST_ALLOWED = [
    r"src/mbox_md/[_a-z]+\.py",
    r"src/mbox_md/py\.typed",
    r"tests/[_a-z]+\.py",
    r"tests/fixtures/(generate\.py|README\.md|[a-z]+\.mbox)",
    r"(pyproject\.toml|README\.md|TODO\.md|LICENSE|PKG-INFO|\.gitignore)",
]


def check(names: list[str], allowed: list[str], label: str) -> list[str]:
    patterns = [re.compile(p) for p in allowed]
    return [f"{label}: unexpected file {n}" for n in names if not any(p.fullmatch(n) for p in patterns)]


def main(dist: Path) -> int:
    wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        print(f"expected one wheel and one sdist in {dist}, found {wheels + sdists}")
        return 1
    problems = []
    with zipfile.ZipFile(wheels[0]) as z:
        problems += check([n for n in z.namelist() if not n.endswith("/")], WHEEL_ALLOWED, wheels[0].name)
    with tarfile.open(sdists[0]) as t:
        # Strip the leading "mbox_md-<version>/" folder.
        names = [m.name.split("/", 1)[1] for m in t.getmembers() if m.isfile()]
        problems += check(names, SDIST_ALLOWED, sdists[0].name)
    for p in problems:
        print(p)
    if not problems:
        print(f"ok: {wheels[0].name}, {sdists[0].name}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "dist")))
