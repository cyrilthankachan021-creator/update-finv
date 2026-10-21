"""Tally vouchers (Day Book / "Export Vouchers" XML) -> ledger lines for the audit workspace.

Tally writes a debit as a negative AMOUNT and a credit as a positive one (ISDEEMEDPOSITIVE is Yes on the debit side).
Every ledger line of every voucher becomes one ledger line here, so a voucher keeps its voucher number, type, party and narration.
The file is read with the standard library parser after refusing DOCTYPE/ENTITY declarations, which closes the
entity-expansion attacks that parser is open to.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

LIST_TAGS = ("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST")


class TallyError(ValueError):
    pass


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    raise TallyError("The Tally file could not be read as text.")


def _text(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    return (child.text or "").strip() or None if child is not None else None


def _day(s: str | None, errs: list[dict], n: int) -> date | None:
    if not s:
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, IndexError):
        errs.append({"row": n, "message": f"Voucher date '{s}' is not a Tally date (YYYYMMDD)."})
        return None


def parse_tally_vouchers(data: bytes) -> tuple[list[dict], list[dict], list[str]]:
    """Returns (ledger_lines, row_errors, notes)."""
    text = _decode(data)
    head = text[:200_000].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in head:
        raise TallyError("The XML file declares entities, which FinV does not accept. Export the vouchers from Tally again.")
    try:
        root = ET.fromstring(text.lstrip("﻿"))
    except ET.ParseError as exc:
        raise TallyError("The file is not valid Tally XML.") from exc
    vouchers = list(root.iter("VOUCHER"))
    if not vouchers:
        raise TallyError("No vouchers were found. In Tally, export the Day Book (or Vouchers) as XML and upload that file.")
    lines: list[dict] = []
    errs: list[dict] = []
    skipped = cancelled = 0
    for n, v in enumerate(vouchers, start=1):
        if (_text(v, "ISCANCELLED") or "").lower() == "yes" or (_text(v, "ISOPTIONAL") or "").lower() == "yes":
            cancelled += 1
            continue
        d = _day(_text(v, "DATE"), errs, n)
        number = _text(v, "VOUCHERNUMBER")
        vtype = _text(v, "VOUCHERTYPENAME") or v.get("VCHTYPE")
        party, narr = _text(v, "PARTYLEDGERNAME"), _text(v, "NARRATION")
        entries = [e for tag in LIST_TAGS for e in v.findall(tag)]
        if not entries:
            skipped += 1
            continue
        for e in entries:
            name = _text(e, "LEDGERNAME")
            try:
                amt = Decimal((_text(e, "AMOUNT") or "0").replace(",", ""))
            except InvalidOperation:
                errs.append({"row": n, "message": f"Amount '{_text(e, 'AMOUNT')}' on ledger '{name}' is not a number."})
                continue
            if not name:
                errs.append({"row": n, "message": "A ledger line has no ledger name."})
                continue
            lines.append({"row_index": len(lines) + 1, "entry_date": d, "account_code": None, "account_name": name,
                          "voucher_no": number, "voucher_type": vtype, "party": party, "reference": None, "narration": narr,
                          "debit": -amt if amt < 0 else Decimal("0"), "credit": amt if amt > 0 else Decimal("0")})
    notes = [f"Read {len(vouchers)} voucher(s) from Tally into {len(lines)} ledger line(s)."]
    if cancelled:
        notes.append(f"{cancelled} cancelled or optional voucher(s) were left out.")
    if skipped:
        notes.append(f"{skipped} voucher(s) had no ledger lines and were left out.")
    return lines, errs, notes
