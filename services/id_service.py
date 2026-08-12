from __future__ import annotations

import re


def next_sequential_id(
    existing_ids,
    prefix,
    digits=6,
):
    """
    Ej:
        SAL-000001
        MOV-000001
        WEEK-000001
        CAM-CHO-000001
    """

    prefix = str(
        prefix
    ).strip().upper()

    pattern = re.compile(
        rf"^{re.escape(prefix)}-(\d+)$"
    )

    maximum = 0

    for value in existing_ids:
        value = str(
            value
        ).strip().upper()

        match = pattern.fullmatch(
            value
        )

        if not match:
            continue

        maximum = max(
            maximum,
            int(
                match.group(
                    1
                )
            ),
        )

    return (
        f"{prefix}-"
        f"{maximum + 1:0{digits}d}"
    )


def remap_sequential(
    values,
    prefix,
    digits=6,
):
    """
    Genera mapping old_id -> new_id conservando orden de aparición.
    Requiere IDs originales únicos.
    """

    ordered = []

    seen = set()

    for value in values:
        value = str(
            value
        ).strip()

        if not value:
            continue

        if value in seen:
            continue

        seen.add(
            value
        )

        ordered.append(
            value
        )

    return {
        old:
            f"{prefix}-{index:0{digits}d}"
        for index, old in enumerate(
            ordered,
            start=1,
        )
    }
