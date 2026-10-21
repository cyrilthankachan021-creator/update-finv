"""Exact money handling. All financial arithmetic in FinV goes through Decimal."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


class AmountError(ValueError):
    pass


def q(value: Decimal) -> Decimal:
    """Quantize to 2 decimal places (INR paise)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


_STRIP = re.compile(r"[,\s₹]|INR|Rs\.?", re.IGNORECASE)


def parse_amount(value, *, allow_negative: bool = True) -> Decimal | None:
    """Parse amounts such as '1,23,456.78', '(500.00)', '500 Dr', 1234.5.

    Returns None for blank input. Raises AmountError for values that are not amounts —
    callers must not guess.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, bool):
        raise AmountError(f"Not an amount: {value!r}")
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        # spreadsheets hand us floats; repr gives the shortest exact-looking form
        d = Decimal(repr(value))
    else:
        s = str(value).strip()
        if s == "" or s in {"-", "--"}:
            return None
        neg = False
        if s.startswith("(") and s.endswith(")"):
            neg, s = True, s[1:-1]
        suffix = re.search(r"\s*(dr|cr)\.?$", s, re.IGNORECASE)
        if suffix:
            if suffix.group(1).lower() == "cr":
                neg = not neg
            s = s[: suffix.start()]
        s = _STRIP.sub("", s)
        if s.startswith("-"):
            neg, s = (not neg), s[1:]
        if not re.fullmatch(r"\d+(\.\d+)?|\.\d+", s):
            raise AmountError(f"Not an amount: {value!r}")
        try:
            d = Decimal(s)
        except InvalidOperation as exc:  # pragma: no cover - regex guards this
            raise AmountError(f"Not an amount: {value!r}") from exc
        if neg:
            d = -d
    if not d.is_finite():
        raise AmountError(f"Not an amount: {value!r}")
    if d < 0 and not allow_negative:
        raise AmountError(f"Negative amount not allowed: {value!r}")
    return d


def has_more_than_2dp(d: Decimal) -> bool:
    return d != d.quantize(CENT)


def nz(d: Decimal | None) -> Decimal:
    return d if d is not None else ZERO


def fmt(d: Decimal | None) -> str:
    """Indian digit grouping for explanations, e.g. 1,23,456.00."""
    if d is None:
        return "—"
    d = q(d)
    sign = "-" if d < 0 else ""
    whole, frac = f"{abs(d):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{whole}.{frac}"
