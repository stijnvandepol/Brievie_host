"""Writes docker-compose.pinned.yml: the compose file with Brievie's images pinned by digest.

    python3 .github/scripts/pin-compose.py FILE OUT --backend sha256:... --proxy sha256:...

Every `image:` line of Brievie's two images (the `${BRIEVIE_VERSION:-X}` form) becomes
`image: <name>@sha256:<digest>`, so the file names exactly what the release published.
Anything else in the file stays; a line that still has a version afterwards, or an image of
Brievie that is not pinned, is an error. Standard library only.
"""

import argparse
import re
import sys
from pathlib import Path

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE_LINE = re.compile(
    r"^(?P<lead>\s*image:\s*)(?P<name>ghcr\.io/[^\s/]+/brievie-(?P<kind>backend|proxy)):"
    r"\$\{BRIEVIE_VERSION:-[^}]+\}(?P<tail>\s*(#.*)?)$"
)


class PinError(Exception):
    pass


def pin(text: str, digests: dict[str, str]) -> str:
    lines = []
    pinned = set()
    for line in text.split("\n"):
        match = IMAGE_LINE.match(line)
        if match is not None:
            kind = match["kind"]
            lines.append(f"{match['lead']}{match['name']}@{digests[kind]}")
            pinned.add(kind)
        else:
            lines.append(line)
    if pinned != {"backend", "proxy"}:
        raise PinError(f"expected both Brievie images in the file, pinned {sorted(pinned)}")
    result = "\n".join(lines)
    if re.search(r"brievie-(backend|proxy):", result):
        raise PinError("a Brievie image is still named by a tag")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--proxy", required=True)
    args = parser.parse_args(argv)
    digests = {"backend": args.backend, "proxy": args.proxy}
    try:
        for value in digests.values():
            if DIGEST.fullmatch(value) is None:
                raise PinError("a digest is not sha256:<64 hex characters>")
        args.out.write_text(pin(args.file.read_text(encoding="utf-8"), digests), encoding="utf-8")
    except (PinError, OSError) as error:
        print(f"pin-compose: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
