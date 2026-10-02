"""Display labels only; signed catalog metadata is never rewritten."""
import re

_PREFIX = re.compile(r"^(?:TypixDeck|Typix)(?:\s*[-–—]\s*|\s+)(?=[^\s–—-])", re.IGNORECASE)


def display_name(name: str) -> str:
    candidate = name.strip()
    cleaned, count = _PREFIX.subn("", candidate, count=1)
    return cleaned if count else name
