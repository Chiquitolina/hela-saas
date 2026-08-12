from pathlib import Path
import sys
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(
    __file__
).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )

from services.inventory_service import (
    CAMERA_COLUMNS,
    SALON_COLUMNS,
    migrate_legacy_inventory,
)


DATA_DIR = ROOT / "data"

LEGACY_CURRENT_STOCK_FILE = (
    DATA_DIR
    / "current_stock.csv"
)

SALON_LATAS_FILE = (
    DATA_DIR
    / "salon_latas.csv"
)

CAMERA_STOCK_FILE = (
    DATA_DIR
    / "camera_stock.csv"
)

COUNTS_FILE = (
    DATA_DIR
    / "inventory_counts.csv"
)

MOVEMENTS_FILE = (
    DATA_DIR
    / "stock_movements.csv"
)

BACKUP_DIR = (
    DATA_DIR
    / "backups"
    / "inventory_split"
)

TZ = ZoneInfo(
    "America/Argentina/Cordoba"
)


def backup(
    path,
):
    if not path.exists():
        return

    BACKUP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        TZ
    ).strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    target = (
        BACKUP_DIR
        / f"{path.stem}_{timestamp}{path.suffix}"
    )

    shutil.copy2(
        path,
        target,
    )


def safe_write(
    df,
    path,
    allow_empty=True,
):
    path = Path(
        path
    )

    if path.exists():
        backup(
            path
        )

    temp = path.with_name(
        f".{path.name}.split.tmp"
    )

    df.to_csv(
        temp,
        index=False,
    )

    temp.replace(
        path
    )


def main():
    # Backup explícito del archivo legacy; nunca se elimina.
    backup(
        LEGACY_CURRENT_STOCK_FILE
    )

    result = migrate_legacy_inventory(
        legacy_stock_file=
            LEGACY_CURRENT_STOCK_FILE,

        salon_file=
            SALON_LATAS_FILE,

        camera_file=
            CAMERA_STOCK_FILE,

        counts_file=
            COUNTS_FILE,

        movements_file=
            MOVEMENTS_FILE,

        write_csv=
            safe_write,
    )

    print(
        "=== Inventory split migration ==="
    )

    for key, value in result.items():
        if key == "ids_repaired":
            continue

        print(
            f"{key}: {value}"
        )

    repairs = result[
        "ids_repaired"
    ]

    if repairs:
        print()
        print(
            "IDs de salón reparados:"
        )

        for item in repairs:
            print(
                f"  {item['sabor']}: "
                f"{item['old_stock_id']} "
                f"-> {item['new_stock_id']}"
            )

    print()
    print(
        "current_stock.csv se conserva como archivo legacy. "
        "La app nueva usa salon_latas.csv y camera_stock.csv."
    )


if __name__ == "__main__":
    main()
