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
    migrate_camera_stock_to_individual,
)


DATA_DIR = ROOT / "data"

CAMERA_STOCK_FILE = (
    DATA_DIR
    / "camera_stock.csv"
)

BACKUP_DIR = (
    DATA_DIR
    / "backups"
    / "camera_individual_migration"
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

    stamp = datetime.now(
        TZ
    ).strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    shutil.copy2(
        path,
        BACKUP_DIR
        / f"{path.stem}_{stamp}{path.suffix}",
    )


def safe_write(
    df,
    path,
    allow_empty=True,
):
    backup(
        path
    )

    temp = path.with_name(
        f".{path.name}.camera_individual.tmp"
    )

    df.to_csv(
        temp,
        index=False,
    )

    temp.replace(
        path
    )


def main():
    result = migrate_camera_stock_to_individual(
        camera_file=
            CAMERA_STOCK_FILE,

        write_csv=
            safe_write,
    )

    print(
        "=== Camera individual migration ==="
    )

    for key, value in result.items():
        print(
            f"{key}: {value}"
        )

    if result.get(
        "migrated"
    ):
        print()
        print(
            "Cada fila de camera_stock.csv representa ahora "
            "una lata física individual."
        )


if __name__ == "__main__":
    main()
