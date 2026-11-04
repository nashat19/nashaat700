"""توسيع صيغة spintax: {أ|ب|ج}."""
from __future__ import annotations

import random
import re

_PATTERN = re.compile(r"\{([^{}]*)\}")


def expand(template: str, rng: random.Random | None = None) -> str:
    """يستبدل كل مجموعة spintax بخيار عشوائي."""
    rng = rng or random.Random()

    def repl(match: re.Match[str]) -> str:
        options = [opt.strip() for opt in match.group(1).split("|")]
        options = [opt for opt in options if opt]
        return rng.choice(options) if options else ""

    result = template
    for _ in range(10):
        new = _PATTERN.sub(repl, result)
        if new == result:
            break
        result = new
    return result.strip()
