from pathlib import Path
import sys
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )

from models.week import WEEK_COLUMNS
from services.week_service import (
    backfill_lata_metadata_from_movements,
    refresh_weeks_dataframe,
    repair_missing_estimated_residue,
)


DATA_DIR = ROOT / "data"

CURRENT_STOCK_FILE = DATA_DIR / "current_stock.csv"
MOVEMENTS_FILE = DATA_DIR / "stock_movements.csv"
COUNTS_FILE = DATA_DIR / "inventory_counts.csv"
WEEKS_FILE = DATA_DIR / "weeks.csv"

BACKUP_DIR = DATA_DIR / "backups" / "metadata_refresh"

TZ = ZoneInfo(
    "America/Argentina/Cordoba"
)


def now_iso():
    return datetime.now(
        TZ
    ).isoformat()


def load_csv(path):
    if not path.exists():
        return pd.DataFrame()

    try:
        return pd.read_csv(
            path
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def backup(path):
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
        / f"{path.stem}_{timestamp}.csv"
    )

    shutil.copy2(
        path,
        target,
    )


def write_csv(
    df,
    path,
):
    backup(
        path
    )

    temp = path.with_name(
        f".{path.name}.metadata.tmp"
    )

    df.to_csv(
        temp,
        index=False,
    )

    temp.replace(
        path
    )


def ensure_week_columns(
    weeks,
):
    for column in WEEK_COLUMNS:
        if column not in weeks.columns:
            weeks[
                column
            ] = pd.NA

    return weeks[
        WEEK_COLUMNS
    ]


def main():
    stock = load_csv(
        CURRENT_STOCK_FILE
    )

    movements = load_csv(
        MOVEMENTS_FILE
    )

    counts = load_csv(
        COUNTS_FILE
    )

    weeks = ensure_week_columns(
        load_csv(
            WEEKS_FILE
        )
    )

    movements, residue_report = (
        repair_missing_estimated_residue(
            stock,
            movements,
        )
    )

    migrated_stock, lata_report = (
        backfill_lata_metadata_from_movements(
            stock,
            movements,
        )
    )

    refreshed_weeks = (
        refresh_weeks_dataframe(
            weeks,
            stock_df=migrated_stock,
            movements_df=movements,
            counts_df=counts,
            now_iso=now_iso(),
        )
    )

    movements_changed = (
        residue_report[
            "movements_repaired"
        ]
        > 0
    )

    stock_changed = not (
        migrated_stock.fillna("")
        .astype(str)
        .equals(
            stock.fillna("")
            .astype(str)
        )
    )

    weeks_changed = not (
        refreshed_weeks.fillna("")
        .astype(str)
        .equals(
            weeks.fillna("")
            .astype(str)
        )
    )

    if stock_changed:
        write_csv(
            migrated_stock,
            CURRENT_STOCK_FILE,
        )

    if movements_changed:
        write_csv(
            movements,
            MOVEMENTS_FILE,
        )

    if weeks_changed:
        write_csv(
            refreshed_weeks,
            WEEKS_FILE,
        )

    print(
        "=== Metadata refresh ==="
    )

    print(
        f"current_stock actualizado: "
        f"{stock_changed}"
    )

    print(
        f"weeks actualizado: "
        f"{weeks_changed}"
    )

    print(
        f"residuos estimados reconstruidos: "
        f"{residue_report['movements_repaired']}"
    )

    print(
        f"movimientos de residuo reparados: "
        f"{residue_report['movements_repaired']}"
    )

    print(
        f"latas enriquecidas: "
        f"{lata_report['updated_rows']}"
    )

    if lata_report[
        "skipped_ambiguous_ids"
    ]:
        print(
            "IDs ambiguos omitidos:"
        )

        for stock_id in lata_report[
            "skipped_ambiguous_ids"
        ]:
            print(
                f"  - {stock_id}"
            )

        print(
            "No se modificaron esas filas para "
            "evitar asociar movimientos a la lata incorrecta."
        )


if __name__ == "__main__":
    main()
