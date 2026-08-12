from pathlib import Path
import pandas as pd
import shutil
from datetime import datetime


DATA_DIR = Path("data")

CURRENT_STOCK_FILE = DATA_DIR / "current_stock.csv"
MOVEMENTS_FILE = DATA_DIR / "stock_movements.csv"
COUNTS_FILE = DATA_DIR / "inventory_counts.csv"


def backup_file(path: Path):
    if not path.exists():
        return

    backup_dir = DATA_DIR / "backup_fix_ids"
    backup_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    target = backup_dir / f"{path.stem}_{timestamp}{path.suffix}"

    shutil.copy2(
        path,
        target,
    )

    print(
        f"[BACKUP] {path} -> {target}"
    )


def main():

    # ========================================================
    # BACKUPS
    # ========================================================

    for file in [
        CURRENT_STOCK_FILE,
        MOVEMENTS_FILE,
        COUNTS_FILE,
    ]:
        backup_file(file)

    # ========================================================
    # LOAD
    # ========================================================

    stock = pd.read_csv(
        CURRENT_STOCK_FILE
    )

    movements = pd.read_csv(
        MOVEMENTS_FILE
    )

    counts = pd.read_csv(
        COUNTS_FILE
    )

    # ========================================================
    # SOLO STOCK SALON
    # ========================================================

    salon_mask = (
        stock["location"]
        .astype(str)
        .str.upper()
        .eq("SALON")
    )

    salon_indices = stock[
        salon_mask
    ].index.tolist()

    print(
        f"[INFO] Latas de salón encontradas: "
        f"{len(salon_indices)}"
    )

    # ========================================================
    # NECESITAMOS ORDEN ESTABLE
    #
    # Usamos created_at si existe.
    # ========================================================

    salon = stock.loc[
        salon_indices
    ].copy()

    if "created_at" in salon.columns:

        salon["_created_dt"] = pd.to_datetime(
            salon["created_at"],
            errors="coerce",
        )

        salon = salon.sort_values(
            [
                "_created_dt",
            ],
            kind="stable",
        )

    # ========================================================
    # GENERAR IDS NUEVOS
    # ========================================================

    new_ids = {}

    for number, idx in enumerate(
        salon.index,
        start=1,
    ):

        old_id = str(
            stock.loc[
                idx,
                "stock_id"
            ]
        )

        new_id = f"SAL-{number:04d}"

        new_ids[idx] = {
            "old_id": old_id,
            "new_id": new_id,
        }

        stock.loc[
            idx,
            "stock_id"
        ] = new_id

    # ========================================================
    # ACTUALIZAR COUNTS
    #
    # Como todos tenían SAL-0001,
    # no podemos hacer un simple replace.
    #
    # La mejor asociación disponible es por:
    # - sabor
    # - timestamp/count
    # - orden de aparición
    # ========================================================

    salon_new = stock[
        salon_mask
    ].copy()

    # Normalizar sabor
    salon_new["_sabor_key"] = (
        salon_new["sabor"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    counts["_sabor_key"] = (
        counts["sabor"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # --------------------------------------------------------
    # Para cada fila de stock, buscar su conteo inicial
    # usando sabor.
    #
    # Si hay sabores repetidos, usamos orden de aparición.
    # --------------------------------------------------------

    assigned_count_indices = set()

    for _, stock_row in salon_new.iterrows():

        sabor_key = stock_row[
            "_sabor_key"
        ]

        new_id = stock_row[
            "stock_id"
        ]

        candidates = counts[
            (
                counts["_sabor_key"]
                == sabor_key
            )
            &
            (
                ~counts.index.isin(
                    assigned_count_indices
                )
            )
        ]

        if candidates.empty:

            print(
                f"[WARN] No encontré count para "
                f"{new_id} / {stock_row['sabor']}"
            )

            continue

        count_idx = candidates.index[0]

        counts.loc[
            count_idx,
            "stock_id"
        ] = new_id

        assigned_count_indices.add(
            count_idx
        )

    # ========================================================
    # ACTUALIZAR MOVIMIENTOS CARGA MANUAL
    # ========================================================

    movements["_sabor_key"] = (
        movements["sabor"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    assigned_movement_indices = set()

    for _, stock_row in salon_new.iterrows():

        sabor_key = stock_row[
            "_sabor_key"
        ]

        new_id = stock_row[
            "stock_id"
        ]

        candidates = movements[
            (
                movements[
                    "movement_type"
                ]
                .astype(str)
                .eq(
                    "CARGA_MANUAL_SALON"
                )
            )
            &
            (
                movements["_sabor_key"]
                == sabor_key
            )
            &
            (
                ~movements.index.isin(
                    assigned_movement_indices
                )
            )
        ]

        if candidates.empty:
            continue

        mov_idx = candidates.index[0]

        movements.loc[
            mov_idx,
            "target_stock_id"
        ] = new_id

        assigned_movement_indices.add(
            mov_idx
        )

    # ========================================================
    # LIMPIAR COLUMNAS TEMP
    # ========================================================

    stock = stock.drop(
        columns=[
            col
            for col in [
                "_created_dt",
                "_sabor_key",
            ]
            if col in stock.columns
        ],
        errors="ignore",
    )

    counts = counts.drop(
        columns=[
            "_sabor_key",
        ],
        errors="ignore",
    )

    movements = movements.drop(
        columns=[
            "_sabor_key",
        ],
        errors="ignore",
    )

    # ========================================================
    # SAVE
    # ========================================================

    stock.to_csv(
        CURRENT_STOCK_FILE,
        index=False,
    )

    counts.to_csv(
        COUNTS_FILE,
        index=False,
    )

    movements.to_csv(
        MOVEMENTS_FILE,
        index=False,
    )

    # ========================================================
    # RESULT
    # ========================================================

    print()
    print(
        "[OK] IDs corregidos."
    )

    print(
        f"[OK] Stock salón: "
        f"{len(salon_indices)} latas."
    )

    print()

    print(
        stock[
            stock["location"]
            .astype(str)
            .str.upper()
            .eq("SALON")
        ][
            [
                "stock_id",
                "sabor",
                "estado",
            ]
        ].to_string(
            index=False
        )
    )


if __name__ == "__main__":
    main()