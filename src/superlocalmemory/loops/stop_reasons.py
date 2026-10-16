# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Surface a recorded gate refusal rather than hiding it behind a loop bound."""


def last_gate_reason(laps):
    for entry in reversed(laps):
        detail = entry.detail
        marker = next((value for value in (
            ' independent_receipt=False receipt_reason=',
            ' independent_receipt=True receipt_reason=') if value in detail), None)
        if marker is not None:
            reason = detail.rsplit(marker, 1)[1]
            if reason != 'authenticated PASS for current copied revision':
                return reason
            return reason if entry.passed else detail
    return laps[-1].detail if laps else ''
