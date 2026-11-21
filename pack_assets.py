"""Pack prepared assets for the WASI reactor using the shared NGUPAK1 format."""

import argparse
import gzip
from pathlib import Path
import shutil
import struct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", type=Path)
    parser.add_argument("dll", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    assets = args.assets
    files = {"Assembly-CSharp.dll": args.dll, "scene.json": assets / "scene.json", "tags.json": assets / "tags.json"}
    for folder in ("fonts", "tex", "text"):
        files.update((path.relative_to(assets).as_posix(), path) for path in (assets / folder).rglob("*") if path.is_file())
    index = [(name.encode(), path, path.stat().st_size) for name, path in sorted(files.items())]
    offset = 12 + sum(18 + len(name) for name, _, _ in index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    with temporary.open("wb") as output:
        output.write(struct.pack("<8sI", b"NGUPAK1\0", len(index)))
        for name, _, size in index:
            output.write(struct.pack("<H", len(name)) + name + struct.pack("<QQ", offset, size))
            offset += size
        for _, path, _ in index:
            with path.open("rb") as source:
                shutil.copyfileobj(source, output)
    temporary.replace(args.output)
    compressed = args.output.with_suffix(args.output.suffix + ".gz")
    temporary = compressed.with_suffix(".tmp")
    with args.output.open("rb") as source, gzip.open(temporary, "wb", compresslevel=6) as output:
        shutil.copyfileobj(source, output)
    temporary.replace(compressed)
    print(f"Packed {len(index)} files ({offset:,} bytes, {compressed.stat().st_size:,} over HTTP).")


if __name__ == "__main__":
    main()
