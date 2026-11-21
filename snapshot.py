"""Copy the supported game files into a private working directory and verify the copy."""

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "manifest", "destination"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.manifest.resolve().parent))
    from verify_game_data import verify

    files = json.loads(args.manifest.read_text(encoding="utf-8"))["files"]
    source = args.data.resolve()
    destination = args.destination.resolve()
    if destination.is_dir() and not verify(destination, files):
        return
    if destination.exists():
        raise SystemExit(f"Cached game data is invalid. Replace the build cache at {destination}.")
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "game-data"
        for name in files:
            original = (source / name).resolve(strict=True)
            if not original.is_relative_to(source):
                raise SystemExit(f"Game data path escapes the mount: {name}")
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        errors = verify(staged, files)
        if errors:
            raise SystemExit("\n".join(errors))
        staged.rename(destination)


if __name__ == "__main__":
    main()
