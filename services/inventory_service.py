from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import pandas as pd


SALON_COLUMNS = [
    "stock_id",
    "sabor",
    "estado",

    "source_camera_stock_id",
    "ingresada_salon_at",

    "kg_referencia_lata",

    "peso_inicial_bruto_kg",
    "tara_inicial_kg",
    "peso_inicial_neto_kg",

    "opened_at",
    "opened_operation_id",

    "peso_actual_bruto_kg",
    "tara_actual_kg",
    "peso_actual_neto_kg",

    "finished_at",
    "peso_final_bruto_kg",
    "tara_final_kg",
    "residuo_final_kg",
    "finished_operation_id",

    "created_at",
    "updated_at",
    "active",
]


CAMERA_COLUMNS = [
    "camera_stock_id",
    "sabor",
    "cantidad_latas",
    "kg_referencia_lata",
    "created_at",
    "updated_at",
    "active",
]


# Vista combinada de compatibilidad para lógica que trabaja con ambos
# inventarios (por ejemplo Week). NO se persiste como archivo.
COMBINED_STOCK_COLUMNS = [
    "stock_id",
    "location",
    "sabor",
    "estado",
    "cantidad_latas",
    "kg_referencia_lata",

    "source_camera_stock_id",
    "ingresada_salon_at",

    "peso_inicial_bruto_kg",
    "tara_inicial_kg",
    "peso_inicial_neto_kg",

    "opened_at",
    "opened_operation_id",

    "peso_actual_bruto_kg",
    "tara_actual_kg",
    "peso_actual_neto_kg",

    "finished_at",
    "peso_final_bruto_kg",
    "tara_final_kg",
    "residuo_final_kg",
    "finished_operation_id",

    "created_at",
    "updated_at",
    "active",
]


def _active_series(
    series,
):
    return (
        series
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(
            [
                "true",
                "1",
                "yes",
            ]
        )
    )


def load_salon_latas(
    filepath: Path,
) -> pd.DataFrame:
    if not filepath.exists():
        return pd.DataFrame(
            columns=SALON_COLUMNS
        )

    try:
        df = pd.read_csv(
            filepath
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame(
            columns=SALON_COLUMNS
        )

    for col in SALON_COLUMNS:
        if col not in df.columns:
            df[
                col
            ] = pd.NA

    df = df[
        SALON_COLUMNS
    ]

    df[
        "active"
    ] = _active_series(
        df[
            "active"
        ]
    )

    numeric_columns = [
        "kg_referencia_lata",
        "peso_inicial_bruto_kg",
        "tara_inicial_kg",
        "peso_inicial_neto_kg",
        "peso_actual_bruto_kg",
        "tara_actual_kg",
        "peso_actual_neto_kg",
        "peso_final_bruto_kg",
        "tara_final_kg",
        "residuo_final_kg",
    ]

    for col in numeric_columns:
        df[
            col
        ] = pd.to_numeric(
            df[
                col
            ],
            errors="coerce",
        )

    return df


def load_camera_stock(
    filepath: Path,
) -> pd.DataFrame:
    if not filepath.exists():
        return pd.DataFrame(
            columns=CAMERA_COLUMNS
        )

    try:
        df = pd.read_csv(
            filepath
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame(
            columns=CAMERA_COLUMNS
        )

    # Compatibilidad con una migración/manual anterior que haya dejado
    # el campo como stock_id.
    if (
        "stock_id" in df.columns
        and "camera_stock_id" not in df.columns
    ):
        df[
            "camera_stock_id"
        ] = df[
            "stock_id"
        ]

    for col in CAMERA_COLUMNS:
        if col not in df.columns:
            df[
                col
            ] = pd.NA

    df = df[
        CAMERA_COLUMNS
    ]

    df[
        "active"
    ] = _active_series(
        df[
            "active"
        ]
    )

    df[
        "cantidad_latas"
    ] = pd.to_numeric(
        df[
            "cantidad_latas"
        ],
        errors="coerce",
    )

    df[
        "kg_referencia_lata"
    ] = pd.to_numeric(
        df[
            "kg_referencia_lata"
        ],
        errors="coerce",
    )

    return df


def combine_inventory(
    salon_df: pd.DataFrame,
    camera_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Construye una vista única EN MEMORIA.

    La app puede seguir realizando cálculos globales sobre location,
    pero la persistencia está realmente separada.
    """

    salon = salon_df.copy()

    if salon.empty:
        salon = pd.DataFrame(
            columns=SALON_COLUMNS
        )

    salon[
        "location"
    ] = "SALON"

    salon[
        "cantidad_latas"
    ] = 1

    camera = camera_df.copy()

    if camera.empty:
        camera = pd.DataFrame(
            columns=CAMERA_COLUMNS
        )

    if not camera.empty:
        camera[
            "stock_id"
        ] = camera[
            "camera_stock_id"
        ]

    camera[
        "location"
    ] = "CAMARA"

    camera[
        "estado"
    ] = "CERRADA"

    # Agregamos campos exclusivos de Lata como NA.
    for col in COMBINED_STOCK_COLUMNS:
        if col not in salon.columns:
            salon[
                col
            ] = pd.NA

        if col not in camera.columns:
            camera[
                col
            ] = pd.NA

    combined = pd.concat(
        [
            salon[
                COMBINED_STOCK_COLUMNS
            ],
            camera[
                COMBINED_STOCK_COLUMNS
            ],
        ],
        ignore_index=True,
    )

    combined[
        "active"
    ] = _active_series(
        combined[
            "active"
        ]
    )

    return combined


def split_inventory(
    combined_df: pd.DataFrame,
):
    """
    Convierte la vista combinada nuevamente en sus dos tablas reales.
    """

    if combined_df.empty:
        return (
            pd.DataFrame(
                columns=SALON_COLUMNS
            ),
            pd.DataFrame(
                columns=CAMERA_COLUMNS
            ),
        )

    location = (
        combined_df[
            "location"
        ]
        .astype(str)
        .str.upper()
    )

    salon = combined_df[
        location.eq(
            "SALON"
        )
    ].copy()

    camera = combined_df[
        location.eq(
            "CAMARA"
        )
    ].copy()

    for col in SALON_COLUMNS:
        if col not in salon.columns:
            salon[
                col
            ] = pd.NA

    salon = salon[
        SALON_COLUMNS
    ]

    if "camera_stock_id" not in camera.columns:
        camera[
            "camera_stock_id"
        ] = camera[
            "stock_id"
        ]

    # stock_id es el identificador histórico en la vista combinada.
    camera[
        "camera_stock_id"
    ] = (
        camera[
            "stock_id"
        ]
        .where(
            camera[
                "stock_id"
            ].notna(),
            camera[
                "camera_stock_id"
            ],
        )
    )

    for col in CAMERA_COLUMNS:
        if col not in camera.columns:
            camera[
                col
            ] = pd.NA

    camera = camera[
        CAMERA_COLUMNS
    ]

    return (
        salon,
        camera,
    )


def _normalize_flavor(
    value,
):
    if pd.isna(
        value
    ):
        return ""

    return (
        str(
            value
        )
        .strip()
        .upper()
        .replace(
            " ",
            "_",
        )
    )


def _latest_start_count_flavor_map(
    counts_df: pd.DataFrame,
):
    if (
        counts_df.empty
        or "count_type" not in counts_df.columns
    ):
        return {}

    counts = counts_df.copy()

    starts = counts[
        counts[
            "count_type"
        ]
        .astype(str)
        .str.upper()
        .eq(
            "INICIO_SEMANA"
        )
    ].copy()

    if starts.empty:
        return {}

    starts[
        "_timestamp_dt"
    ] = pd.to_datetime(
        starts[
            "timestamp"
        ],
        errors="coerce",
        utc=True,
    )

    latest_count_id = (
        starts
        .sort_values(
            "_timestamp_dt"
        )
        .iloc[-1][
            "count_id"
        ]
    )

    latest = starts[
        starts[
            "count_id"
        ]
        .astype(str)
        .eq(
            str(
                latest_count_id
            )
        )
    ].copy()

    latest[
        "_flavor"
    ] = latest[
        "sabor"
    ].map(
        _normalize_flavor
    )

    result = {}

    for flavor, group in latest.groupby(
        "_flavor"
    ):
        ids = (
            group[
                "stock_id"
            ]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )

        if (
            flavor
            and len(
                ids
            )
            == 1
        ):
            result[
                flavor
            ] = ids[0]

    return result


def repair_duplicate_salon_ids(
    salon_df: pd.DataFrame,
    counts_df: pd.DataFrame,
):
    """
    Repara IDs duplicados durante la migración.

    Prioridad:
    1. usa el ID del último conteo INICIO_SEMANA si el sabor identifica
       inequívocamente la lata;
    2. si no, genera SAL-xxxx nuevo.

    Devuelve también flavor -> stock_id para poder reparar referencias
    antiguas de movimientos cuando el sabor las hace inequívocas.
    """

    if salon_df.empty:
        return (
            salon_df.copy(),
            {},
            [],
        )

    salon = salon_df.copy()

    count_map = (
        _latest_start_count_flavor_map(
            counts_df
        )
    )

    original_ids = (
        salon[
            "stock_id"
        ]
        .astype(str)
    )

    duplicated_original = set(
        original_ids[
            original_ids.duplicated(
                keep=False
            )
        ].tolist()
    )

    used = set()
    assigned = []
    flavor_to_id = {}
    changed_rows = []

    # Primero identificamos máximo SAL histórico válido.
    max_number = 0

    for stock_id in list(
        original_ids
    ) + list(
        count_map.values()
    ):
        if str(
            stock_id
        ).startswith(
            "SAL-"
        ):
            try:
                max_number = max(
                    max_number,
                    int(
                        str(
                            stock_id
                        ).replace(
                            "SAL-",
                            "",
                        )
                    ),
                )
            except ValueError:
                pass

    def next_id():
        nonlocal max_number

        while True:
            max_number += 1

            candidate = (
                f"SAL-{max_number:04d}"
            )

            if candidate not in used:
                return candidate

    for idx, row in salon.iterrows():
        flavor = _normalize_flavor(
            row[
                "sabor"
            ]
        )

        original = str(
            row[
                "stock_id"
            ]
        )

        candidate = None

        mapped = count_map.get(
            flavor
        )

        if (
            mapped
            and mapped not in used
        ):
            candidate = mapped

        elif (
            original
            and original not in used
            and original not in duplicated_original
        ):
            candidate = original

        elif (
            original
            and original not in used
            and original.startswith(
                "SAL-"
            )
            and len(
                assigned
            )
            == 0
        ):
            # Conserva el primer ID de un bloque duplicado cuando corresponde.
            candidate = original

        else:
            candidate = next_id()

        used.add(
            candidate
        )

        assigned.append(
            candidate
        )

        flavor_to_id[
            flavor
        ] = candidate

        if candidate != original:
            changed_rows.append(
                {
                    "row_index":
                        int(
                            idx
                        ),

                    "sabor":
                        flavor,

                    "old_stock_id":
                        original,

                    "new_stock_id":
                        candidate,
                }
            )

    salon[
        "stock_id"
    ] = assigned

    return (
        salon,
        flavor_to_id,
        changed_rows,
    )


def repair_movement_salon_references(
    movements_df: pd.DataFrame,
    flavor_to_id: dict,
):
    """
    Si el sabor tiene una única Lata migrada, actualiza referencias SAL
    antiguas de movimientos. No toca referencias de cámara.
    """

    if movements_df.empty:
        return (
            movements_df.copy(),
            0,
        )

    movements = movements_df.copy()

    changed = 0

    for idx, row in movements.iterrows():
        flavor = _normalize_flavor(
            row.get(
                "sabor",
                "",
            )
        )

        target_id = flavor_to_id.get(
            flavor
        )

        if not target_id:
            continue

        for col in [
            "source_stock_id",
            "target_stock_id",
        ]:
            value = str(
                row.get(
                    col,
                    "",
                )
            )

            if (
                value.startswith(
                    "SAL-"
                )
                and value != target_id
            ):
                movements.loc[
                    idx,
                    col,
                ] = target_id

                changed += 1

    return (
        movements,
        changed,
    )


def migrate_legacy_inventory(
    *,
    legacy_stock_file: Path,
    salon_file: Path,
    camera_file: Path,
    counts_file: Path,
    movements_file: Path,
    write_csv: Callable,
):
    """
    Migra `current_stock.csv` a:
        - salon_latas.csv
        - camera_stock.csv

    Es idempotente: si alguno de los nuevos inventarios ya tiene datos,
    NO vuelve a importar el legacy.
    """

    if not legacy_stock_file.exists():
        return {
            "migrated": False,
            "reason": "NO_LEGACY_FILE",
            "salon_rows": 0,
            "camera_rows": 0,
            "ids_repaired": [],
            "movement_references_repaired": 0,
        }

    existing_salon = load_salon_latas(
        salon_file
    )

    existing_camera = load_camera_stock(
        camera_file
    )

    if (
        not existing_salon.empty
        or not existing_camera.empty
    ):
        return {
            "migrated": False,
            "reason": "NEW_STORAGE_ALREADY_HAS_DATA",
            "salon_rows": len(
                existing_salon
            ),
            "camera_rows": len(
                existing_camera
            ),
            "ids_repaired": [],
            "movement_references_repaired": 0,
        }

    try:
        legacy = pd.read_csv(
            legacy_stock_file
        )
    except pd.errors.EmptyDataError:
        return {
            "migrated": False,
            "reason": "LEGACY_EMPTY",
            "salon_rows": 0,
            "camera_rows": 0,
            "ids_repaired": [],
            "movement_references_repaired": 0,
        }

    if legacy.empty:
        return {
            "migrated": False,
            "reason": "LEGACY_EMPTY",
            "salon_rows": 0,
            "camera_rows": 0,
            "ids_repaired": [],
            "movement_references_repaired": 0,
        }

    location = (
        legacy[
            "location"
        ]
        .astype(str)
        .str.upper()
    )

    salon = legacy[
        location.eq(
            "SALON"
        )
    ].copy()

    camera = legacy[
        location.eq(
            "CAMARA"
        )
    ].copy()

    for col in SALON_COLUMNS:
        if col not in salon.columns:
            salon[
                col
            ] = pd.NA

    salon = salon[
        SALON_COLUMNS
    ]

    if not camera.empty:
        camera[
            "camera_stock_id"
        ] = camera[
            "stock_id"
        ]

    for col in CAMERA_COLUMNS:
        if col not in camera.columns:
            camera[
                col
            ] = pd.NA

    camera = camera[
        CAMERA_COLUMNS
    ]

    try:
        counts = pd.read_csv(
            counts_file
        )
    except (
        FileNotFoundError,
        pd.errors.EmptyDataError,
    ):
        counts = pd.DataFrame()

    (
        salon,
        flavor_to_id,
        repaired_ids,
    ) = repair_duplicate_salon_ids(
        salon,
        counts,
    )

    try:
        movements = pd.read_csv(
            movements_file
        )
    except (
        FileNotFoundError,
        pd.errors.EmptyDataError,
    ):
        movements = pd.DataFrame()

    movements_repaired = 0

    if not movements.empty:
        (
            movements,
            movements_repaired,
        ) = repair_movement_salon_references(
            movements,
            flavor_to_id,
        )

    write_csv(
        salon,
        salon_file,
        True,
    )

    write_csv(
        camera,
        camera_file,
        True,
    )

    if movements_repaired > 0:
        write_csv(
            movements,
            movements_file,
            True,
        )

    return {
        "migrated": True,
        "reason": "OK",
        "salon_rows": len(
            salon
        ),
        "camera_rows": len(
            camera
        ),
        "ids_repaired": repaired_ids,
        "movement_references_repaired": movements_repaired,
    }
