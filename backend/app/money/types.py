"""Currency primitives — integer cents, floats banned (the AGENTS money boundary).

All currency in ClarionPI is a non-negative integer number of cents. These helpers convert
between a human dollar string and cents *without ever constructing a float*: parsing splits on
the decimal point and does integer arithmetic, so ``"0.10"`` and ``"0.01"`` never suffer binary
float drift. The functions accept ``str``/``int`` only — a ``float`` argument is a type error by
policy, not a silent coercion.
"""

from __future__ import annotations

import re

Cents = int

# A well-formed dollar string: optional leading '-', optional '$', digits with optional thousands
# commas, and an optional exactly-two-digit cents fraction. "1.234" (three fraction digits) is
# rejected; the optional '$' makes `cents_to_display` output round-trip back through this parser.
_DOLLARS_RE = re.compile(
    r"^(?P<sign>-?)\$?(?P<dollars>\d{1,3}(?:,\d{3})*|\d+)(?:\.(?P<cents>\d{2}))?$"
)


class MoneyParseError(ValueError):
    """Raised when a dollar string is not a clean, non-lossy currency value."""


def dollars_str_to_cents(value: str, *, allow_negative: bool = False) -> Cents:
    """Parse a dollar string like ``"1,234.56"`` into integer cents (``123456``).

    Strict by design: rejects anything but ``[-]digits[,digits...][.dd]``. A value with a
    fraction that is not exactly two digits (e.g. ``"1.234"`` or ``"1.2"``) raises
    :class:`MoneyParseError` — three-decimal input would lose a fraction of a cent on coercion,
    which currency math must never do silently. Negatives raise unless ``allow_negative=True``.
    """
    if not isinstance(value, str):  # defensive: policy is str/int only, never float
        raise MoneyParseError(f"expected a string dollar value, got {type(value).__name__}")
    match = _DOLLARS_RE.match(value.strip())
    if match is None:
        raise MoneyParseError(f"not a valid dollar value: {value!r}")
    negative = match.group("sign") == "-"
    if negative and not allow_negative:
        raise MoneyParseError(f"negative amount not allowed: {value!r}")
    dollars = int(match.group("dollars").replace(",", ""))
    cents_frac = int(match.group("cents") or "0")
    total = dollars * 100 + cents_frac
    return -total if negative else total


def cents_to_display(cents: Cents) -> str:
    """Render integer cents as a ``$``-prefixed, comma-grouped dollar string.

    ``123456 -> "$1,234.56"``; ``-500 -> "-$5.00"``. Integer arithmetic throughout — no float
    ever touches the value.
    """
    if not isinstance(cents, int):  # defensive: cents are integers, never floats
        raise MoneyParseError(f"expected integer cents, got {type(cents).__name__}")
    negative = cents < 0
    magnitude = -cents if negative else cents
    dollars, remainder = divmod(magnitude, 100)
    body = f"${dollars:,}.{remainder:02d}"
    return f"-{body}" if negative else body
