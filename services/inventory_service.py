from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd

from services.id_service import next_sequential_id


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
    "estado",
    "kg_referencia_lata",

    "ingresada_camera_at",
    "moved_to_salon_at",
    "target_salon_stock_id",

    "legacy_batch_id",

    "created_at",
    "updated_at",
    "active",
]


# Vista combinada SOLO EN MEMORIA.
# cantidad_latas se inyecta como 1 para cada CameraLata.
COMBINED_STOCK_COLUMNS = [
    "stock_id",
    "location",
    "sabor",
    "estado",
    "cantidad_latas",
    "kg_referencia_lata",

    # Cámara
    "ingresada_camera_at",
    "moved_to_salon_at",
    "target_salon_stock_id",
    "legacy_batch_id",

    # Salón
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

    # Si todavía tiene cantidad_latas, es formato agregado legacy.
    # La migración debe ejecutarse antes de operar con esta tabla.
    if (
        "cantidad_latas"
        in df.columns
    ):
        return df

    if (
        "stock_id"
        in df.columns
        and "camera_stock_id"
        not in df.columns
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

    # Formato nuevo: cada fila de cámara = una lata.
    if "cantidad_latas" not in camera.columns:
        camera[
            "cantidad_latas"
        ] = 1

    if not camera.empty:
        camera[
            "stock_id"
        ] = camera[
            "camera_stock_id"
        ]

    camera[
        "location"
    ] = "CAMARA"

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
    Divide current_stock.csv legacy en salón/cámara.

    La cámara puede salir todavía en formato agregado; después se ejecuta
    migrate_camera_stock_to_individual().
    """

    if not legacy_stock_file.exists():
        return {
            "migrated": False,
            "reason": "NO_LEGACY_FILE",
        }

    # Si ya hay persistencia nueva, no reimportamos current_stock.
    salon_exists = (
        salon_file.exists()
        and salon_file.stat().st_size > 0
    )

    camera_exists = (
        camera_file.exists()
        and camera_file.stat().st_size > 0
    )

    if salon_exists or camera_exists:
        return {
            "migrated": False,
            "reason": "NEW_STORAGE_ALREADY_EXISTS",
        }

    try:
        legacy = pd.read_csv(
            legacy_stock_file
        )
    except pd.errors.EmptyDataError:
        return {
            "migrated": False,
            "reason": "LEGACY_EMPTY",
        }

    if legacy.empty:
        return {
            "migrated": False,
            "reason": "LEGACY_EMPTY",
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

    # Cámara se deja en formato legacy temporalmente para que el siguiente
    # migrador pueda expandir cantidad_latas.
    if not camera.empty:
        if (
            "camera_stock_id"
            not in camera.columns
        ):
            camera[
                "camera_stock_id"
            ] = camera[
                "stock_id"
            ]

        keep = [
            "camera_stock_id",
            "sabor",
            "cantidad_latas",
            "kg_referencia_lata",
            "created_at",
            "updated_at",
            "active",
        ]

        for col in keep:
            if col not in camera.columns:
                camera[
                    col
                ] = pd.NA

        camera = camera[
            keep
        ]

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

    return {
        "migrated": True,
        "reason": "OK",
        "salon_rows": len(
            salon
        ),
        "camera_legacy_rows": len(
            camera
        ),
    }


def migrate_camera_stock_to_individual(
    *,
    camera_file: Path,
    write_csv: Callable,
):
    """
    Convierte camera_stock.csv agregado:
        CAM-FAC-000001, FRUTILLA, cantidad_latas=9

    en nueve filas individuales:
        CAM-FAC-000001
        CAM-FAC-000002
        ...
        CAM-FAC-000009

    Reglas:
    - idempotente: si ya no existe cantidad_latas, no hace nada;
    - conserva el ID legacy como primera lata del lote cuando es posible;
    - las latas extra reciben IDs secuenciales nuevos;
    - cantidad_latas desaparece del esquema final.
    """

    if not camera_file.exists():
        return {
            "migrated": False,
            "reason": "NO_CAMERA_FILE",
            "created_rows": 0,
        }

    try:
        legacy = pd.read_csv(
            camera_file
        )
    except pd.errors.EmptyDataError:
        return {
            "migrated": False,
            "reason": "EMPTY",
            "created_rows": 0,
        }

    if legacy.empty:
        # Reescribimos esquema nuevo vacío.
        write_csv(
            pd.DataFrame(
                columns=CAMERA_COLUMNS
            ),
            camera_file,
            True,
        )

        return {
            "migrated": True,
            "reason": "EMPTY_SCHEMA_UPDATED",
            "created_rows": 0,
        }

    if "cantidad_latas" not in legacy.columns:
        # Ya es individual.
        return {
            "migrated": False,
            "reason": "ALREADY_INDIVIDUAL",
            "created_rows": len(
                legacy
            ),
        }

    rows = []

    # IDs ya presentes reservados para no colisionar al expandir.
    used_ids = set(
        legacy[
            "camera_stock_id"
        ]
        .dropna()
        .astype(str)
        .tolist()
    )

    for _, row in legacy.iterrows():
        old_id = str(
            row.get(
                "camera_stock_id",
                ""
            )
        ).strip()

        sabor = _normalize_flavor(
            row.get(
                "sabor",
                ""
            )
        )

        qty = pd.to_numeric(
            row.get(
                "cantidad_latas",
                0,
            ),
            errors="coerce",
        )

        qty = (
            int(
                qty
            )
            if pd.notna(
                qty
            )
            else 0
        )

        active = (
            str(
                row.get(
                    "active",
                    True,
                )
            )
            .strip()
            .lower()
            in {
                "true",
                "1",
                "yes",
            }
        )

        # Si qty=0 conservamos una fila histórica inactiva.
        units_to_create = (
            qty
            if qty > 0
            else 1
        )

        # Prefix = todo menos el último número si el ID ya es secuencial.
        match = None

        if old_id:
            import re

            match = re.fullmatch(
                r"(.+)-(\d+)",
                old_id,
            )

        if match:
            prefix = match.group(
                1
            )
        else:
            prefix = "CAM-LEG"

        generated_for_batch = []

        for unit_index in range(
            units_to_create
        ):
            if (
                unit_index == 0
                and old_id
            ):
                new_id = old_id

            else:
                new_id = next_sequential_id(
                    used_ids,
                    prefix,
                )

            used_ids.add(
                new_id
            )

            generated_for_batch.append(
                new_id
            )

            created_at = row.get(
                "created_at",
                pd.NA,
            )

            updated_at = row.get(
                "updated_at",
                created_at,
            )

            row_active = (
                active
                and qty > 0
            )

            rows.append(
                {
                    "camera_stock_id":
                        new_id,

                    "sabor":
                        sabor,

                    "estado":
                        (
                            "DISPONIBLE"
                            if row_active
                            else "LEGACY_INACTIVA"
                        ),

                    "kg_referencia_lata":
                        row.get(
                            "kg_referencia_lata",
                            pd.NA,
                        ),

                    "ingresada_camera_at":
                        created_at,

                    "moved_to_salon_at":
                        pd.NA,

                    "target_salon_stock_id":
                        pd.NA,

                    "legacy_batch_id":
                        old_id
                        if (
                            units_to_create > 1
                            or qty <= 0
                        )
                        else pd.NA,

                    "created_at":
                        created_at,

                    "updated_at":
                        updated_at,

                    "active":
                        row_active,
                }
            )

    individual = pd.DataFrame(
        rows
    )

    for col in CAMERA_COLUMNS:
        if col not in individual.columns:
            individual[
                col
            ] = pd.NA

    individual = individual[
        CAMERA_COLUMNS
    ]

    write_csv(
        individual,
        camera_file,
        True,
    )

    return {
        "migrated": True,
        "reason": "OK",
        "legacy_rows": len(
            legacy
        ),
        "created_rows": len(
            individual
        ),
    }
