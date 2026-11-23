"""Check new changes and keep local build inputs out of the Git index."""

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent
SOURCE = {".c", ".h", ".js", ".mjs", ".md", ".ps1", ".py", ".rs", ".sh", ".toml", ".yml", ".yaml"}
BUILD_ARTIFACTS = {".assets", ".dll", ".exe", ".pak", ".pyc", ".ress", ".vpk", ".wasm"}
LOCAL_FOLDERS = {"build", "out", "target", "toolchains", "venvs", "work"}
WORKSTATION_PATH = re.compile(r"(?<![\w\x5c])(?:[A-Z]:[\\/]|/(?:mnt/[a-z]/|home/|Users/))")
FRENCH = re.compile(
    r"\b(?:ne pas|vous pouvez|le jeu|la sauvegarde|dossier du|n['’]est pas|"
    r"fichier généré|a été|répertoire local|prendre en charge)\b",
    re.IGNORECASE,
)
LINK = re.compile(r"\]\(\s*<?([^\s<>)]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args]).decode("utf-8", "replace")


def names(*args):
    return set(git(*args).split("\0")) - {""}


def added_lines(base, name):
    diff = git("diff", "--no-ext-diff", "--unified=0", base, "--", name)
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++ "):
            yield line[1:]


def broken_link(path, target):
    url = urlsplit(target)
    if url.scheme or url.netloc or not url.path or url.path.startswith("/"):
        return False
    resolved = (path.parent / unquote(url.path)).resolve()
    return not resolved.is_relative_to(ROOT) or not resolved.exists()


def check_base():
    ref = os.getenv("NGU_CHECK_BASE", "")
    if ref and set(ref) != {"0"}:
        return ref
    base = (ROOT / ".github/check-baseline").read_text(encoding="utf-8").strip()
    exists = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{base}^{{tree}}"],
        stderr=subprocess.DEVNULL,
    )
    return base if exists.returncode == 0 else "HEAD^"


def main():
    base = check_base()
    tracked = names("ls-files", "-z")
    changed = names("diff", "--no-ext-diff", "--name-only", "--diff-filter=ACMR", "-z", base, "--")
    new = names("ls-files", "--others", "--exclude-standard", "-z")
    problems = []

    for name in sorted(tracked | new):
        path = ROOT / name
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(ROOT):
            problems.append(f"{name}: repository path resolves outside the checkout")
            continue
        if path.parts[len(ROOT.parts)] in LOCAL_FOLDERS or path.suffix.lower() in BUILD_ARTIFACTS:
            problems.append(f"{name}: local output or generated binary")
        elif path.stat().st_size > 1024 * 1024:
            with path.open("rb") as stream:
                if b"\0" in stream.read(8192):
                    problems.append(f"{name}: binary exceeds 1 MiB")

    for name in sorted(changed | new):
        path = ROOT / name
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            continue
        if path.suffix not in SOURCE and name != "Dockerfile":
            continue
        if path == Path(__file__):
            continue
        lines = path.read_text(encoding="utf-8").splitlines() if name in new else added_lines(base, name)
        for line in lines:
            if WORKSTATION_PATH.search(line):
                problems.append(f"{name}: local absolute path")
            if FRENCH.search(line):
                problems.append(f"{name}: possible French prose")
            if path.suffix == ".md":
                for target in LINK.findall(line):
                    if broken_link(path, target):
                        problems.append(f"{name}: broken relative link to {target}")

    if problems:
        print("\n".join(dict.fromkeys(problems)), file=sys.stderr)
        return 1
    print(f"Repository hygiene OK ({len(changed | new)} changed paths, {len(tracked)} tracked paths).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
