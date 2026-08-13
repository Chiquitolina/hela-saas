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

from services.flavor_service import (
    ensure_flavor_codes,
    normalize_flavor_name,
)
from services.id_service import (
    remap_sequential,
)


DATA_DIR = ROOT / "data"

SALON_FILE = DATA_DIR / "salon_latas.csv"
CAMERA_FILE = DATA_DIR / "camera_stock.csv"
FLAVORS_FILE = DATA_DIR / "flavors.csv"
MOVEMENTS_FILE = DATA_DIR / "stock_movements.csv"
COUNTS_FILE = DATA_DIR / "inventory_counts.csv"
WEEKS_FILE = DATA_DIR / "weeks.csv"

BACKUP_DIR = (
    DATA_DIR
    / "backups"
    / "ids_flavors_migration"
)

TZ = ZoneInfo(
    "America/Argentina/Cordoba"
)


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

    stamp = datetime.now(
        TZ
    ).strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    target = (
        BACKUP_DIR
        / f"{path.stem}_{stamp}{path.suffix}"
    )

    shutil.copy2(
        path,
        target,
    )


def write_csv(df, path):
    backup(
        path
    )

    temp = path.with_name(
        f".{path.name}.id_migration.tmp"
    )

    df.to_csv(
        temp,
        index=False,
    )

    temp.replace(
        path
    )


def validate_unique(
    df,
    column,
    label,
):
    if (
        df.empty
        or column not in df.columns
    ):
        return

    values = (
        df[
            column
        ]
        .dropna()
        .astype(str)
    )

    duplicated = values[
        values.duplicated(
            keep=False
        )
    ].unique()

    if len(
        duplicated
    ):
        raise RuntimeError(
            f"No se puede migrar {label}: "
            f"hay IDs duplicados en {column}: "
            + ", ".join(
                duplicated[
                    :10
                ]
            )
        )


def replace_map(
    series,
    mapping,
):
    return series.map(
        lambda value:
            mapping.get(
                str(
                    value
                ),
                value,
            )
            if pd.notna(
                value
            )
            else value
    )


def main():
    salon = load_csv(
        SALON_FILE
    )

    camera = load_csv(
        CAMERA_FILE
    )

    flavors = load_csv(
        FLAVORS_FILE
    )

    movements = load_csv(
        MOVEMENTS_FILE
    )

    counts = load_csv(
        COUNTS_FILE
    )

    weeks = load_csv(
        WEEKS_FILE
    )

    # --------------------------------------------------------
    # FLAVOR CODES
    # --------------------------------------------------------

    if flavors.empty:
        discovered = set()

        for df in [
            salon,
            camera,
            movements,
            counts,
        ]:
            if (
                not df.empty
                and "sabor"
                in df.columns
            ):
                discovered.update(
                    df[
                        "sabor"
                    ]
                    .dropna()
                    .map(
                        normalize_flavor_name
                    )
                    .tolist()
                )

        flavors = pd.DataFrame(
            {
                "sabor":
                    sorted(
                        discovered
                    ),

                "flavor_code":
                    pd.NA,

                "active":
                    True,

                "created_at":
                    datetime.now(
                        TZ
                    ).isoformat(),
            }
        )

    if "flavor_code" not in flavors.columns:
        flavors[
            "flavor_code"
        ] = pd.NA

    flavors = ensure_flavor_codes(
        flavors
    )

    flavor_code_map = {
        normalize_flavor_name(
            row[
                "sabor"
            ]
        ):
            str(
                row[
                    "flavor_code"
                ]
            )
            .strip()
            .upper()
        for _, row in flavors.iterrows()
    }

    # --------------------------------------------------------
    # VALIDATE OLD IDS BEFORE REMAP
    # --------------------------------------------------------

    validate_unique(
        salon,
        "stock_id",
        "latas de salón",
    )

    validate_unique(
        camera,
        "camera_stock_id",
        "stock de cámara",
    )

    validate_unique(
        weeks,
        "week_id",
        "semanas",
    )

    # count_id NO es único por fila:
    # un mismo conteo tiene muchas filas, una por lata.
    # Por eso no validamos unicidad fila a fila de count_id.
    # Sí validamos que un mismo count_id pertenezca a una sola
    # semana, tipo de conteo y timestamp.
    if (
        not counts.empty
        and "count_id" in counts.columns
    ):
        for count_id, group in counts.groupby(
            "count_id",
            dropna=True,
        ):
            for column in [
                "week_id",
                "count_type",
                "timestamp",
            ]:
                if column not in group.columns:
                    continue

                unique_values = (
                    group[column]
                    .dropna()
                    .astype(str)
                    .unique()
                )

                if len(unique_values) > 1:
                    raise RuntimeError(
                        f"El conteo {count_id} tiene más de un "
                        f"{column}: {unique_values.tolist()}"
                    )

    validate_unique(
        movements,
        "movement_id",
        "movimientos",
    )

    # --------------------------------------------------------
    # MAP SALON
    # --------------------------------------------------------

    salon_map = remap_sequential(
        (
            salon[
                "stock_id"
            ]
            .dropna()
            .astype(str)
            .tolist()
            if (
                not salon.empty
                and "stock_id"
                in salon.columns
            )
            else []
        ),
        "SAL",
    )

    if salon_map:
        salon[
            "stock_id"
        ] = replace_map(
            salon[
                "stock_id"
            ],
            salon_map,
        )

    # --------------------------------------------------------
    # MAP CAMERA PER FLAVOR
    # --------------------------------------------------------

    camera_map = {}

    if not camera.empty:
        for flavor, group in camera.groupby(
            camera[
                "sabor"
            ].map(
                normalize_flavor_name
            )
        ):
            code = flavor_code_map.get(
                flavor
            )

            if not code:
                raise RuntimeError(
                    f"Falta flavor_code para {flavor}"
                )

            old_ids = (
                group[
                    "camera_stock_id"
                ]
                .dropna()
                .astype(str)
                .tolist()
            )

            group_map = remap_sequential(
                old_ids,
                f"CAM-{code}",
            )

            camera_map.update(
                group_map
            )

        camera[
            "camera_stock_id"
        ] = replace_map(
            camera[
                "camera_stock_id"
            ],
            camera_map,
        )

    # --------------------------------------------------------
    # MAP WEEKS / COUNTS / MOVEMENTS
    # --------------------------------------------------------

    week_map = remap_sequential(
        (
            weeks[
                "week_id"
            ]
            .dropna()
            .astype(str)
            .tolist()
            if (
                not weeks.empty
                and "week_id"
                in weeks.columns
            )
            else []
        ),
        "WEEK",
    )

    count_map = remap_sequential(
        (
            counts[
                "count_id"
            ]
            .dropna()
            .astype(str)
            .tolist()
            if (
                not counts.empty
                and "count_id"
                in counts.columns
            )
            else []
        ),
        "COUNT",
    )

    movement_map = remap_sequential(
        (
            movements[
                "movement_id"
            ]
            .dropna()
            .astype(str)
            .tolist()
            if (
                not movements.empty
                and "movement_id"
                in movements.columns
            )
            else []
        ),
        "MOV",
    )

    if week_map:
        weeks[
            "week_id"
        ] = replace_map(
            weeks[
                "week_id"
            ],
            week_map,
        )

    if count_map:
        counts[
            "count_id"
        ] = replace_map(
            counts[
                "count_id"
            ],
            count_map,
        )

    if movement_map:
        movements[
            "movement_id"
        ] = replace_map(
            movements[
                "movement_id"
            ],
            movement_map,
        )

    # --------------------------------------------------------
    # OPERATION IDS
    # --------------------------------------------------------

    operation_map = {}

    if (
        not movements.empty
        and "operation_id"
        in movements.columns
    ):
        operations = (
            movements[
                "operation_id"
            ]
            .dropna()
            .astype(str)
            .drop_duplicates()
            .tolist()
        )

        grouped = {}

        for old in operations:
            upper = old.upper()

            if upper.startswith(
                "RECAMBIO"
            ):
                prefix = "RECAMBIO"

            elif upper.startswith(
                "APERTURA"
            ):
                prefix = "APERTURA"

            elif upper.startswith(
                "FINALIZA"
            ):
                prefix = "FINALIZA"

            else:
                prefix = "OP"

            grouped.setdefault(
                prefix,
                []
            ).append(
                old
            )

        for prefix, values in grouped.items():
            operation_map.update(
                remap_sequential(
                    values,
                    prefix,
                )
            )

        movements[
            "operation_id"
        ] = replace_map(
            movements[
                "operation_id"
            ],
            operation_map,
        )

    # --------------------------------------------------------
    # REFERENCE UPDATES
    # --------------------------------------------------------

    # Salon Lata metadata
    if not salon.empty:
        if "source_camera_stock_id" in salon.columns:
            salon[
                "source_camera_stock_id"
            ] = replace_map(
                salon[
                    "source_camera_stock_id"
                ],
                camera_map,
            )

        if "opened_operation_id" in salon.columns:
            salon[
                "opened_operation_id"
            ] = replace_map(
                salon[
                    "opened_operation_id"
                ],
                operation_map,
            )

        if "finished_operation_id" in salon.columns:
            salon[
                "finished_operation_id"
            ] = replace_map(
                salon[
                    "finished_operation_id"
                ],
                operation_map,
            )

    # Weeks -> count refs
    if not weeks.empty:
        for col in [
            "start_count_id",
            "end_count_id",
        ]:
            if col in weeks.columns:
                weeks[
                    col
                ] = replace_map(
                    weeks[
                        col
                    ],
                    count_map,
                )

    # Counts -> week + salon refs
    if not counts.empty:
        if "week_id" in counts.columns:
            counts[
                "week_id"
            ] = replace_map(
                counts[
                    "week_id"
                ],
                week_map,
            )

        if "stock_id" in counts.columns:
            counts[
                "stock_id"
            ] = replace_map(
                counts[
                    "stock_id"
                ],
                salon_map,
            )

    # Movements -> all refs
    if not movements.empty:
        if "week_id" in movements.columns:
            movements[
                "week_id"
            ] = replace_map(
                movements[
                    "week_id"
                ],
                week_map,
            )

        for col in [
            "source_stock_id",
            "target_stock_id",
        ]:
            if col not in movements.columns:
                continue

            values = movements[
                col
            ]

            values = replace_map(
                values,
                salon_map,
            )

            values = replace_map(
                values,
                camera_map,
            )

            movements[
                col
            ] = values

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    write_csv(
        flavors,
        FLAVORS_FILE,
    )

    if not salon.empty:
        write_csv(
            salon,
            SALON_FILE,
        )

    if not camera.empty:
        write_csv(
            camera,
            CAMERA_FILE,
        )

    if not weeks.empty:
        write_csv(
            weeks,
            WEEKS_FILE,
        )

    if not counts.empty:
        write_csv(
            counts,
            COUNTS_FILE,
        )

    if not movements.empty:
        write_csv(
            movements,
            MOVEMENTS_FILE,
        )

    print(
        "=== IDs + Flavor codes migration ==="
    )

    print(
        f"Sabores: {len(flavors)}"
    )

    print(
        f"Latas salón remapeadas: {len(salon_map)}"
    )

    print(
        f"Stocks cámara remapeados: {len(camera_map)}"
    )

    print(
        f"Semanas remapeadas: {len(week_map)}"
    )

    print(
        f"Conteos remapeados: {len(count_map)}"
    )

    print(
        f"Movimientos remapeados: {len(movement_map)}"
    )

    print(
        f"Operaciones remapeadas: {len(operation_map)}"
    )

    print()
    print(
        "Ejemplos de códigos:"
    )

    for flavor in [
        "CHOCOLATE",
        "CHOCOLATE_CON_ALMENDRAS",
        "DULCE_DE_LECHE",
        "DULCE_DE_LECHE_BROWNIE",
    ]:
        code = flavor_code_map.get(
            flavor
        )

        if code:
            print(
                f"  {flavor}: {code}"
            )


if __name__ == "__main__":
    main()
