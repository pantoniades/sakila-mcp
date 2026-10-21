"""MySQL server version parsing.

Handles classic (``8.0.36``, ``8.4.11-log``, ``9.7.2-commercial``) and calendar
(``26.10.0``) version strings. Versions are plain int tuples compared with ``<``/``>=``.
"""

import re

Version = tuple[int, int, int]

_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")

_LTS_LINES: dict[tuple[int, int], str] = {
    (8, 0): "8.0 LTS",
    (8, 4): "8.4 LTS",
    (9, 7): "9.7 LTS",
}


def parse_version(raw: str) -> Version:
    """Parse the leading ``X.Y.Z`` of a server version string; raise ValueError if absent."""
    match = _VERSION_RE.match(raw)
    if match is None:
        raise ValueError(f"unrecognized MySQL version string: {raw!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch)


def release_line(version: Version) -> str:
    """Map a parsed version to its release line, or ``"unknown"``."""
    major, minor, _ = version
    lts = _LTS_LINES.get((major, minor))
    if lts is not None:
        return lts
    if major == 8 and 1 <= minor <= 3:
        return "Innovation"
    if major == 9:
        return "Innovation"
    # Calendar-versioned releases (major >= 26) are reported as "unknown" until the
    # LTS/Innovation rule for that scheme is confirmed.
    return "unknown"
