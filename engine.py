"""Stage the engine's data preparation (its source, and the icon tool) from the same revision Cargo resolves."""

import argparse
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import tomllib


ROOT = Path(__file__).resolve().parent
TOOLS = (
    "requirements-icons.txt",
    "fetch-achievement-icons.py",
)


def read_pin():
    with (ROOT / "Cargo.toml").open("rb") as stream:
        engine = tomllib.load(stream)["dependencies"]["clr"]
    with (ROOT / "Cargo.lock").open("rb") as stream:
        lock = tomllib.load(stream)
    prefix = f"git+{engine['git']}?tag={engine['tag']}#"
    if not re.fullmatch(r"v0\.[0-9]+\.[0-9]+", engine["tag"]):
        raise ValueError("Expected a versioned engine tag.")
    revisions = [
        package["source"][len(prefix):]
        for package in lock["package"]
        if package["name"] == "clr" and package.get("source", "").startswith(prefix)
    ]
    if len(revisions) != 1 or not re.fullmatch(r"[0-9a-f]{40}", revisions[0]):
        raise ValueError("The engine tag and Cargo.lock disagree.")
    if (ROOT / "engine.tag").read_text(encoding="utf-8").strip() != engine["tag"]:
        raise ValueError("engine.tag and Cargo.toml name different engine tags.")
    return engine["git"], engine["tag"], revisions[0]


def stage_tools(destination, remote, tag, revision):
    with TemporaryDirectory(prefix="ngu-engine-") as tmp:
        def git(*arguments):
            return subprocess.check_output(["git", "-C", tmp, *arguments])

        git("init", "--quiet", "--bare")
        git("fetch", "--quiet", "--depth=1", remote, f"refs/tags/{tag}")
        resolved = git("rev-parse", "FETCH_HEAD^{commit}").decode().strip()
        if resolved != revision:
            raise ValueError(f"{tag} points to {resolved}, but Cargo.lock pins {revision}.")
        contents = {name: git("show", f"{revision}:tools/{name}") for name in TOOLS}
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in contents.items():
        (destination / name).write_bytes(data)


def stage_source(destination, remote, tag, revision):
    destination.mkdir(parents=True, exist_ok=True)

    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(destination), *arguments])

    git("init", "--quiet")
    git("fetch", "--quiet", "--depth=1", remote, f"refs/tags/{tag}")
    resolved = git("rev-parse", "FETCH_HEAD^{commit}").decode().strip()
    if resolved != revision:
        raise ValueError(f"{tag} points to {resolved}, but Cargo.lock pins {revision}.")
    git("checkout", "--quiet", "--detach", revision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--stage-source", type=Path)
    args = parser.parse_args()
    pin = read_pin()
    if args.stage:
        stage_tools(args.stage, *pin)
    if args.stage_source:
        stage_source(args.stage_source, *pin)
    print(f"{pin[1]} {pin[2]}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Engine check failed: {error}")
