import re

PRAGMA_PATTERN = re.compile(
    r"pragma\s+solidity\s+([^;]+);",
    re.IGNORECASE | re.MULTILINE,
)
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")


def parse_pragma_solidity(source_code: str) -> str | None:
    """Extract the highest applicable semver from pragma solidity directives."""
    versions: list[tuple[int, int, int]] = []

    for match in PRAGMA_PATTERN.finditer(source_code):
        directive = match.group(1)
        for version_match in VERSION_PATTERN.finditer(directive):
            prefix = directive[: version_match.start()].rstrip()
            if prefix.endswith("<"):
                continue
            parts = version_match.group(0).split(".")
            versions.append((int(parts[0]), int(parts[1]), int(parts[2])))

    if not versions:
        return None

    major, minor, patch = max(versions)
    return f"{major}.{minor}.{patch}"
