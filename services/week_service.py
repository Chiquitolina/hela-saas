from __future__ import annotations

from typing import Optional

import pandas as pd

from models.week import (
    WEEK_COLUMNS,
    WEEK_METADATA_VERSION,
    Week,
)


def _to_utc(value):
    return pd.to_datetime(
        value,
        errors="coerce",
        utc=True,
    )


def _num_series(
    df,
    column,
    default=0.0,
):
    if column not in df.columns:
        return pd.Series(
            default,
            index=df.index,
            dtype=float,
        )

    return pd.to_numeric(
        df[column],
        errors="coerce",
    ).fillna(default)


def _active_mask(stock):
    if stock.empty:
        return pd.Series(
            False,
            index=stock.index,
            dtype=bool,
        )

    return (
        stock["active"]
        .astype(str)
        .str.lower()
        .isin(
            [
                "true",
                "1",
                "yes",
            ]
        )
    )


def current_stock_snapshot(
    stock_df: pd.DataFrame,
) -> dict:
    if stock_df.empty:
        return {
            "salon_latas": 0,
            "salon_kg": 0.0,
            "camera_latas": 0,
            "camera_kg": 0.0,
        }

    stock = stock_df.copy()

    stock = stock[
        _active_mask(
            stock
        )
    ].copy()

    salon = stock[
        stock["location"]
        .astype(str)
        .str.upper()
        .eq("SALON")
    ].copy()

    camera = stock[
        stock["location"]
        .astype(str)
        .str.upper()
        .eq("CAMARA")
    ].copy()

    salon_kg = _num_series(
        salon,
        "peso_actual_neto_kg",
    ).sum()

    camera_qty = _num_series(
        camera,
        "cantidad_latas",
    )

    camera_ref = _num_series(
        camera,
        "kg_referencia_lata",
    )

    return {
        "salon_latas":
            int(
                len(salon)
            ),

        "salon_kg":
            round(
                float(
                    salon_kg
                ),
                3,
            ),

        "camera_latas":
            int(
                camera_qty.sum()
            ),

        "camera_kg":
            round(
                float(
                    (
                        camera_qty
                        * camera_ref
                    )
                    .sum()
                ),
                3,
            ),
    }


def _movement_window(
    week_row,
    movements_df,
    now_iso,
):
    if movements_df.empty:
        return movements_df.copy()

    movements = (
        movements_df
        .copy()
    )

    movements[
        "_timestamp_dt"
    ] = _to_utc(
        movements[
            "timestamp"
        ]
    )

    start = _to_utc(
        week_row[
            "started_at"
        ]
    )

    end_value = (
        week_row.get(
            "closed_at"
        )
        if str(
            week_row.get(
                "status",
                ""
            )
        ).upper()
        == "CLOSED"
        else now_iso
    )

    end = _to_utc(
        end_value
    )

    if pd.isna(start):
        return movements.iloc[
            0:0
        ].copy()

    mask = (
        movements[
            "_timestamp_dt"
        ]
        >= start
    )

    if pd.notna(end):
        mask &= (
            movements[
                "_timestamp_dt"
            ]
            <= end
        )

    # El timestamp es la frontera principal. week_id se usa como
    # señal adicional, pero no excluimos históricos mal etiquetados.
    return movements[
        mask
    ].copy()


def movement_stats(
    week_row,
    movements_df,
    now_iso,
) -> dict:
    movements = _movement_window(
        week_row,
        movements_df,
        now_iso,
    )

    if movements.empty:
        return {
            "camera_to_salon_latas": 0,
            "camera_to_salon_kg": 0.0,
            "ingreso_camera_latas": 0,
            "ingreso_camera_kg": 0.0,
            "latas_abiertas": 0,
            "latas_terminadas": 0,
            "cambios_sabor": 0,
            "recambios": 0,
            "latas_con_tara_final": 0,
            "tara_final_total_kg": 0.0,
            "residuo_estimado_kg": 0.0,
        }

    movement_type = (
        movements[
            "movement_type"
        ]
        .fillna("")
        .astype(str)
        .str.upper()
    )

    qty = _num_series(
        movements,
        "cantidad_latas",
        default=1.0,
    )

    net = _num_series(
        movements,
        "peso_neto_kg",
    )

    final_tare = _num_series(
        movements,
        "tara_final_kg",
    )

    estimated_residue = _num_series(
        movements,
        "residuo_final_kg",
    )

    cam_to_salon = (
        movement_type
        == "CAMARA_A_SALON"
    )

    ingreso_camera = (
        movement_type
        == "INGRESO_CAMARA"
    )

    agotada = (
        movement_type
        == "LATA_AGOTADA"
    )

    abierta = (
        movement_type
        == "LATA_ABIERTA"
    )

    cambio = (
        movement_type
        == "CAMBIO_SABOR"
    )

    recambios = 0

    if "operation_id" in movements.columns:
        operation_ids = (
            movements[
                "operation_id"
            ]
            .dropna()
            .astype(str)
        )

        recambios = int(
            operation_ids[
                operation_ids
                .str.startswith(
                    "RECAMBIO_"
                )
            ]
            .nunique()
        )

    return {
        "camera_to_salon_latas":
            int(
                qty[
                    cam_to_salon
                ].sum()
            ),

        "camera_to_salon_kg":
            round(
                float(
                    net[
                        cam_to_salon
                    ].sum()
                ),
                3,
            ),

        "ingreso_camera_latas":
            int(
                qty[
                    ingreso_camera
                ].sum()
            ),

        "ingreso_camera_kg":
            round(
                float(
                    net[
                        ingreso_camera
                    ].sum()
                ),
                3,
            ),

        "latas_abiertas":
            int(
                abierta.sum()
            ),

        "latas_terminadas":
            int(
                agotada.sum()
            ),

        "cambios_sabor":
            int(
                cambio.sum()
            ),

        "recambios":
            recambios,

        "latas_con_tara_final":
            int(
                (
                    agotada
                    &
                    final_tare.gt(0)
                ).sum()
            ),

        "tara_final_total_kg":
            round(
                float(
                    final_tare[
                        agotada
                        &
                        final_tare.gt(0)
                    ].sum()
                ),
                3,
            ),

        "residuo_estimado_kg":
            round(
                float(
                    estimated_residue[
                        agotada
                    ].sum()
                ),
                3,
            ),
    }


def _camera_reference_map(
    stock_df,
):
    if stock_df.empty:
        return {}

    camera = stock_df[
        stock_df[
            "location"
        ]
        .astype(str)
        .str.upper()
        .eq("CAMARA")
    ].copy()

    if camera.empty:
        return {}

    refs = pd.to_numeric(
        camera[
            "kg_referencia_lata"
        ],
        errors="coerce",
    )

    result = {}

    for idx, row in camera.iterrows():
        stock_id = str(
            row[
                "stock_id"
            ]
        )

        ref = refs.loc[
            idx
        ]

        if pd.notna(ref):
            result[
                stock_id
            ] = float(
                ref
            )

    return result


def reconstruct_camera_snapshot_at(
    target_timestamp,
    stock_df,
    movements_df,
) -> dict:
    """
    Reconstruye el stock de cámara en un timestamp histórico
    partiendo del stock actual y deshaciendo movimientos posteriores.
    """

    current = current_stock_snapshot(
        stock_df
    )

    qty = float(
        current[
            "camera_latas"
        ]
    )

    kg = float(
        current[
            "camera_kg"
        ]
    )

    target = _to_utc(
        target_timestamp
    )

    if pd.isna(target):
        return {
            "camera_latas": None,
            "camera_kg": None,
            "source": "UNAVAILABLE",
        }

    if movements_df.empty:
        return {
            "camera_latas":
                int(
                    round(
                        qty
                    )
                ),

            "camera_kg":
                round(
                    kg,
                    3,
                ),

            "source":
                "CURRENT_ONLY",
        }

    refs = _camera_reference_map(
        stock_df
    )

    movements = movements_df.copy()

    movements[
        "_timestamp_dt"
    ] = _to_utc(
        movements[
            "timestamp"
        ]
    )

    after = movements[
        movements[
            "_timestamp_dt"
        ]
        > target
    ].copy()

    after = after.sort_values(
        "_timestamp_dt",
        ascending=False,
    )

    for _, row in after.iterrows():
        movement_type = str(
            row.get(
                "movement_type",
                ""
            )
        ).upper()

        movement_qty = pd.to_numeric(
            row.get(
                "cantidad_latas",
                1
            ),
            errors="coerce",
        )

        if pd.isna(
            movement_qty
        ):
            movement_qty = 1

        movement_qty = float(
            movement_qty
        )

        movement_net = pd.to_numeric(
            row.get(
                "peso_neto_kg"
            ),
            errors="coerce",
        )

        if movement_type == "INGRESO_CAMARA":
            qty -= movement_qty

            if pd.notna(
                movement_net
            ):
                kg -= float(
                    movement_net
                )

        elif movement_type == "CAMARA_A_SALON":
            qty += movement_qty

            source_id = str(
                row.get(
                    "source_stock_id",
                    ""
                )
            )

            reference = refs.get(
                source_id
            )

            if reference is not None:
                kg += (
                    movement_qty
                    * reference
                )

            elif pd.notna(
                movement_net
            ):
                # Fallback: usa el peso neto real transferido.
                kg += float(
                    movement_net
                )

    return {
        "camera_latas":
            max(
                int(
                    round(
                        qty
                    )
                ),
                0,
            ),

        "camera_kg":
            round(
                max(
                    kg,
                    0.0,
                ),
                3,
            ),

        "source":
            "RECONSTRUCTED_LEDGER",
    }


def reconstruct_salon_latas_at(
    target_timestamp,
    stock_df,
    movements_df,
) -> dict:
    """
    Reconstruye la cantidad de latas activas en salón deshaciendo
    movimientos posteriores al timestamp.
    """

    current = current_stock_snapshot(
        stock_df
    )

    latas = int(
        current[
            "salon_latas"
        ]
    )

    target = _to_utc(
        target_timestamp
    )

    if pd.isna(target):
        return {
            "salon_latas": None,
            "source": "UNAVAILABLE",
        }

    if movements_df.empty:
        return {
            "salon_latas": latas,
            "source": "CURRENT_ONLY",
        }

    movements = movements_df.copy()

    movements[
        "_timestamp_dt"
    ] = _to_utc(
        movements[
            "timestamp"
        ]
    )

    after = movements[
        movements[
            "_timestamp_dt"
        ]
        > target
    ].copy()

    after = after.sort_values(
        "_timestamp_dt",
        ascending=False,
    )

    for _, row in after.iterrows():
        movement_type = str(
            row.get(
                "movement_type",
                ""
            )
        ).upper()

        qty = pd.to_numeric(
            row.get(
                "cantidad_latas",
                1
            ),
            errors="coerce",
        )

        if pd.isna(qty):
            qty = 1

        qty = int(
            round(
                float(
                    qty
                )
            )
        )

        if movement_type == "LATA_AGOTADA":
            # Antes de agotarse estaba activa.
            latas += qty

        elif movement_type in {
            "CAMARA_A_SALON",
            "CARGA_MANUAL_SALON",
        }:
            # Antes de entrar al salón todavía no existía ahí.
            latas -= qty

    return {
        "salon_latas":
            max(
                int(
                    latas
                ),
                0,
            ),

        "source":
            "RECONSTRUCTED_LEDGER",
    }


def count_snapshot(
    count_id,
    counts_df,
) -> Optional[dict]:
    if (
        not count_id
        or counts_df.empty
        or "count_id"
        not in counts_df.columns
    ):
        return None

    rows = counts_df[
        counts_df[
            "count_id"
        ]
        .astype(str)
        .eq(
            str(
                count_id
            )
        )
    ].copy()

    if rows.empty:
        return None

    net = _num_series(
        rows,
        "peso_neto_kg",
    )

    return {
        "salon_latas":
            int(
                len(rows)
            ),

        "salon_kg":
            round(
                float(
                    net.sum()
                ),
                3,
            ),

        "source":
            "COUNT_SNAPSHOT",
    }


def enrich_week_row(
    week_row,
    *,
    stock_df,
    movements_df,
    counts_df,
    now_iso,
) -> dict:
    row = week_row.copy()

    for column in WEEK_COLUMNS:
        if column not in row.index:
            row[
                column
            ] = pd.NA

    status = str(
        row.get(
            "status",
            ""
        )
    ).upper()

    is_open = (
        status
        == "OPEN"
    )

    started_at = row.get(
        "started_at"
    )

    closed_at = row.get(
        "closed_at"
    )

    current = current_stock_snapshot(
        stock_df
    )

    stats = movement_stats(
        row,
        movements_df,
        now_iso,
    )

    # ========================================================
    # SNAPSHOT INICIAL SALÓN
    # ========================================================

    start_count = count_snapshot(
        row.get(
            "start_count_id"
        ),
        counts_df,
    )

    existing_start_stock = pd.to_numeric(
        row.get(
            "start_stock_kg"
        ),
        errors="coerce",
    )

    if start_count is not None:
        start_salon_latas = (
            start_count[
                "salon_latas"
            ]
        )

        start_salon_kg = (
            float(
                existing_start_stock
            )
            if pd.notna(
                existing_start_stock
            )
            else start_count[
                "salon_kg"
            ]
        )

        start_salon_source = (
            "COUNT_SNAPSHOT"
        )

    else:
        reconstructed_salon = (
            reconstruct_salon_latas_at(
                started_at,
                stock_df,
                movements_df,
            )
        )

        start_salon_latas = (
            reconstructed_salon[
                "salon_latas"
            ]
        )

        start_salon_kg = (
            float(
                existing_start_stock
            )
            if pd.notna(
                existing_start_stock
            )
            else None
        )

        start_salon_source = (
            "LEGACY_START_STOCK+"
            + reconstructed_salon[
                "source"
            ]
        )

    # ========================================================
    # SNAPSHOT INICIAL CÁMARA
    # ========================================================

    start_camera = (
        reconstruct_camera_snapshot_at(
            started_at,
            stock_df,
            movements_df,
        )
    )

    # ========================================================
    # ESTADO ACTUAL
    # ========================================================

    current_salon_latas = (
        current[
            "salon_latas"
        ]
    )

    current_salon_kg = (
        current[
            "salon_kg"
        ]
    )

    current_camera_latas = (
        current[
            "camera_latas"
        ]
    )

    current_camera_kg = (
        current[
            "camera_kg"
        ]
    )

    # ========================================================
    # CIERRE
    # ========================================================

    end_salon_latas = pd.to_numeric(
        row.get(
            "end_salon_latas"
        ),
        errors="coerce",
    )

    end_salon_kg = pd.to_numeric(
        row.get(
            "end_salon_kg"
        ),
        errors="coerce",
    )

    end_camera_latas = pd.to_numeric(
        row.get(
            "end_camera_latas"
        ),
        errors="coerce",
    )

    end_camera_kg = pd.to_numeric(
        row.get(
            "end_camera_kg"
        ),
        errors="coerce",
    )

    end_salon_source = row.get(
        "end_salon_snapshot_source"
    )

    end_camera_source = row.get(
        "end_camera_snapshot_source"
    )

    existing_end_stock = pd.to_numeric(
        row.get(
            "end_stock_kg"
        ),
        errors="coerce",
    )

    if not is_open:
        end_count = count_snapshot(
            row.get(
                "end_count_id"
            ),
            counts_df,
        )

        if end_count is not None:
            end_salon_latas = (
                end_count[
                    "salon_latas"
                ]
            )

            end_salon_kg = (
                float(
                    existing_end_stock
                )
                if pd.notna(
                    existing_end_stock
                )
                else end_count[
                    "salon_kg"
                ]
            )

            end_salon_source = (
                "COUNT_SNAPSHOT"
            )

        else:
            reconstructed_end_salon = (
                reconstruct_salon_latas_at(
                    closed_at,
                    stock_df,
                    movements_df,
                )
            )

            end_salon_latas = (
                reconstructed_end_salon[
                    "salon_latas"
                ]
            )

            if pd.notna(
                existing_end_stock
            ):
                end_salon_kg = (
                    float(
                        existing_end_stock
                    )
                )

            end_salon_source = (
                "LEGACY_END_STOCK+"
                + reconstructed_end_salon[
                    "source"
                ]
            )

        reconstructed_end_camera = (
            reconstruct_camera_snapshot_at(
                closed_at,
                stock_df,
                movements_df,
            )
        )

        end_camera_latas = (
            reconstructed_end_camera[
                "camera_latas"
            ]
        )

        end_camera_kg = (
            reconstructed_end_camera[
                "camera_kg"
            ]
        )

        end_camera_source = (
            reconstructed_end_camera[
                "source"
            ]
        )

    # ========================================================
    # CONSUMO FÍSICO
    # ========================================================

    # El consumo físico del salón usa:
    # stock inicial + kilos que entraron desde cámara
    # - stock actual/final.
    #
    # CARGA_MANUAL_SALON dentro de una semana también se suma como
    # entrada física extraordinaria.
    movements_window = _movement_window(
        row,
        movements_df,
        now_iso,
    )

    manual_inflow_kg = 0.0

    if not movements_window.empty:
        mtype = (
            movements_window[
                "movement_type"
            ]
            .fillna("")
            .astype(str)
            .str.upper()
        )

        manual_mask = (
            mtype
            == "CARGA_MANUAL_SALON"
        )

        manual_inflow_kg = round(
            float(
                _num_series(
                    movements_window,
                    "peso_neto_kg",
                )[
                    manual_mask
                ]
                .sum()
            ),
            3,
        )

    consumo_fisico = None

    if start_salon_kg is not None:
        target_salon_kg = (
            current_salon_kg
            if is_open
            else (
                float(
                    end_salon_kg
                )
                if pd.notna(
                    end_salon_kg
                )
                else None
            )
        )

        if target_salon_kg is not None:
            consumo_fisico = round(
                float(
                    start_salon_kg
                )
                + float(
                    stats[
                        "camera_to_salon_kg"
                    ]
                )
                + float(
                    manual_inflow_kg
                )
                - float(
                    target_salon_kg
                ),
                3,
            )

    # ========================================================
    # MERMA SI YA EXISTE CONSUMO TEÓRICO GUARDADO
    # ========================================================

    consumo_teorico = pd.to_numeric(
        row.get(
            "consumo_teorico_kg"
        ),
        errors="coerce",
    )

    merma_kg = None
    merma_pct = None
    merma_no_explicada = None

    if (
        consumo_fisico is not None
        and pd.notna(
            consumo_teorico
        )
    ):
        merma_kg = round(
            consumo_fisico
            - float(
                consumo_teorico
            ),
            3,
        )

        if float(
            consumo_teorico
        ) > 0:
            merma_pct = round(
                (
                    merma_kg
                    / float(
                        consumo_teorico
                    )
                )
                * 100,
                3,
            )

        # La tara final completa no es merma de helado.
        # Solo usamos la diferencia estimada contra la tara inicial.
        merma_no_explicada = round(
            merma_kg
            - float(
                stats[
                    "residuo_estimado_kg"
                ]
            ),
            3,
        )

    result = {
        "week_id":
            row.get(
                "week_id"
            ),

        "status":
            row.get(
                "status"
            ),

        "started_at":
            row.get(
                "started_at"
            ),

        "closed_at":
            row.get(
                "closed_at"
            ),

        "start_count_id":
            row.get(
                "start_count_id"
            ),

        "end_count_id":
            row.get(
                "end_count_id"
            ),

        "start_stock_kg":
            (
                round(
                    float(
                        start_salon_kg
                    ),
                    3,
                )
                if start_salon_kg
                is not None
                else pd.NA
            ),

        "start_salon_latas":
            start_salon_latas,

        "start_salon_kg":
            (
                round(
                    float(
                        start_salon_kg
                    ),
                    3,
                )
                if start_salon_kg
                is not None
                else pd.NA
            ),

        "start_camera_latas":
            start_camera[
                "camera_latas"
            ],

        "start_camera_kg":
            start_camera[
                "camera_kg"
            ],

        "current_salon_latas":
            current_salon_latas,

        "current_salon_kg":
            current_salon_kg,

        "current_camera_latas":
            current_camera_latas,

        "current_camera_kg":
            current_camera_kg,

        "end_stock_kg":
            row.get(
                "end_stock_kg"
            ),

        "end_salon_latas":
            (
                int(
                    end_salon_latas
                )
                if pd.notna(
                    end_salon_latas
                )
                else pd.NA
            ),

        "end_salon_kg":
            (
                round(
                    float(
                        end_salon_kg
                    ),
                    3,
                )
                if pd.notna(
                    end_salon_kg
                )
                else pd.NA
            ),

        "end_camera_latas":
            (
                int(
                    end_camera_latas
                )
                if pd.notna(
                    end_camera_latas
                )
                else pd.NA
            ),

        "end_camera_kg":
            (
                round(
                    float(
                        end_camera_kg
                    ),
                    3,
                )
                if pd.notna(
                    end_camera_kg
                )
                else pd.NA
            ),

        **stats,

        "consumo_fisico_kg":
            (
                consumo_fisico
                if consumo_fisico
                is not None
                else pd.NA
            ),

        "consumo_teorico_kg":
            (
                round(
                    float(
                        consumo_teorico
                    ),
                    3,
                )
                if pd.notna(
                    consumo_teorico
                )
                else pd.NA
            ),

        "merma_kg":
            (
                merma_kg
                if merma_kg
                is not None
                else pd.NA
            ),

        "merma_pct":
            (
                merma_pct
                if merma_pct
                is not None
                else pd.NA
            ),

        "merma_no_explicada_kg":
            (
                merma_no_explicada
                if merma_no_explicada
                is not None
                else pd.NA
            ),

        "metadata_version":
            WEEK_METADATA_VERSION,

        "metadata_refreshed_at":
            now_iso,

        "start_salon_snapshot_source":
            start_salon_source,

        "start_camera_snapshot_source":
            start_camera[
                "source"
            ],

        "end_salon_snapshot_source":
            (
                end_salon_source
                if end_salon_source
                is not None
                else pd.NA
            ),

        "end_camera_snapshot_source":
            (
                end_camera_source
                if end_camera_source
                is not None
                else pd.NA
            ),

        "notes":
            row.get(
                "notes",
                ""
            ),
    }

    return result


def refresh_weeks_dataframe(
    weeks_df,
    *,
    stock_df,
    movements_df,
    counts_df,
    now_iso,
):
    if weeks_df.empty:
        return pd.DataFrame(
            columns=WEEK_COLUMNS
        )

    rows = []

    for _, week_row in weeks_df.iterrows():

        status = str(
            week_row.get(
                "status",
                ""
            )
        ).upper()

        end_salon_source = str(
            week_row.get(
                "end_salon_snapshot_source",
                ""
            )
        )

        end_camera_source = str(
            week_row.get(
                "end_camera_snapshot_source",
                ""
            )
        )

        # Las semanas cerradas mediante el nuevo cierre explícito
        # son snapshots históricos definitivos.
        #
        # No deben volver a absorber cambios del stock actual,
        # movimientos futuros ni nuevos refresh de metadata.
        frozen_close = (
            status
            == "CLOSED"
            and end_salon_source
            == "LIVE_CLOSE_SNAPSHOT"
            and end_camera_source
            == "LIVE_CLOSE_SNAPSHOT"
        )

        if frozen_close:
            frozen = week_row.copy()

            for column in WEEK_COLUMNS:
                if column not in frozen.index:
                    frozen[
                        column
                    ] = pd.NA

            rows.append(
                {
                    column:
                        frozen[
                            column
                        ]
                    for column in WEEK_COLUMNS
                }
            )

            continue

        rows.append(
            enrich_week_row(
                week_row,
                stock_df=
                    stock_df,

                movements_df=
                    movements_df,

                counts_df=
                    counts_df,

                now_iso=
                    now_iso,
            )
        )

    result = pd.DataFrame(
        rows
    )

    for column in WEEK_COLUMNS:
        if column not in result.columns:
            result[
                column
            ] = pd.NA

    return result[
        WEEK_COLUMNS
    ]




def repair_missing_estimated_residue(
    stock_df,
    movements_df,
):
    """
    Completa residuo_final_kg en finalizaciones viejas cuando sea posible.

    Fórmula:
        max(tara_final_kg - tara_inicial_kg, 0)

    Se intenta identificar la lata por stock_id + sabor. Si la asociación
    no es única, no se modifica nada.
    """

    stock = stock_df.copy()
    movements = movements_df.copy()

    if stock.empty or movements.empty:
        return (
            movements,
            {
                "movements_repaired": 0,
            },
        )

    movement_type = (
        movements[
            "movement_type"
        ]
        .fillna("")
        .astype(str)
        .str.upper()
    )

    repaired = 0

    for mov_idx, mov in movements[
        movement_type.eq(
            "LATA_AGOTADA"
        )
    ].iterrows():

        existing_residue = pd.to_numeric(
            mov.get(
                "residuo_final_kg"
            ),
            errors="coerce",
        )

        if pd.notna(
            existing_residue
        ):
            continue

        stock_id = str(
            mov.get(
                "source_stock_id",
                ""
            )
        ).strip()

        sabor = str(
            mov.get(
                "sabor",
                ""
            )
        ).strip()

        if not stock_id:
            continue

        candidates = stock[
            stock[
                "stock_id"
            ]
            .astype(str)
            .eq(
                stock_id
            )
        ].copy()

        if sabor:
            flavor_candidates = candidates[
                candidates[
                    "sabor"
                ]
                .astype(str)
                .eq(
                    sabor
                )
            ].copy()

            if not flavor_candidates.empty:
                candidates = flavor_candidates

        if len(candidates) != 1:
            continue

        stock_idx = candidates.index[0]

        tara_inicial = pd.to_numeric(
            stock.loc[
                stock_idx,
                "tara_inicial_kg"
            ],
            errors="coerce",
        )

        if pd.isna(
            tara_inicial
        ):
            continue

        tara_final = pd.to_numeric(
            mov.get(
                "tara_final_kg"
            ),
            errors="coerce",
        )

        if pd.isna(
            tara_final
        ):
            tara_final = pd.to_numeric(
                mov.get(
                    "tara_kg"
                ),
                errors="coerce",
            )

        if pd.isna(
            tara_final
        ):
            continue

        residuo = round(
            max(
                float(
                    tara_final
                )
                - float(
                    tara_inicial
                ),
                0.0,
            ),
            3,
        )

        movements.loc[
            mov_idx,
            "residuo_final_kg"
        ] = residuo

        repaired += 1

    return (
        movements,
        {
            "movements_repaired":
                repaired,
        },
    )


def backfill_lata_metadata_from_movements(
    stock_df,
    movements_df,
):
    """
    Recupera metadata de Lata desde stock_movements.csv cuando es seguro.

    IMPORTANTE:
    Si un stock_id está duplicado en current_stock.csv, NO lo modifica,
    porque no existe forma confiable de saber a qué fila corresponde
    el movimiento histórico.
    """

    if (
        stock_df.empty
        or movements_df.empty
    ):
        return (
            stock_df.copy(),
            {
                "updated_rows": 0,
                "skipped_ambiguous_ids": [],
            },
        )

    stock = stock_df.copy()
    movements = movements_df.copy()

    salon_mask = (
        stock[
            "location"
        ]
        .astype(str)
        .str.upper()
        .eq("SALON")
    )

    salon_ids = (
        stock.loc[
            salon_mask,
            "stock_id"
        ]
        .astype(str)
    )

    counts = (
        salon_ids
        .value_counts()
    )

    unique_ids = set(
        counts[
            counts
            == 1
        ].index
    )

    ambiguous_ids = sorted(
        counts[
            counts
            > 1
        ].index.tolist()
    )

    movements[
        "_timestamp_dt"
    ] = _to_utc(
        movements[
            "timestamp"
        ]
    )

    updated_rows = 0

    for idx, row in stock[
        salon_mask
    ].iterrows():

        stock_id = str(
            row[
                "stock_id"
            ]
        )

        if stock_id not in unique_ids:
            continue

        changed = False

        # Ingreso al salón
        incoming = movements[
            (
                movements[
                    "target_stock_id"
                ]
                .astype(str)
                .eq(
                    stock_id
                )
            )
            &
            (
                movements[
                    "movement_type"
                ]
                .astype(str)
                .str.upper()
                .eq(
                    "CAMARA_A_SALON"
                )
            )
        ].sort_values(
            "_timestamp_dt"
        )

        if not incoming.empty:
            inc = incoming.iloc[0]

            if (
                pd.isna(
                    row.get(
                        "source_camera_stock_id"
                    )
                )
                or not str(
                    row.get(
                        "source_camera_stock_id",
                        ""
                    )
                ).strip()
            ):
                stock.loc[
                    idx,
                    "source_camera_stock_id"
                ] = inc.get(
                    "source_stock_id"
                )

                changed = True

            if (
                pd.isna(
                    row.get(
                        "ingresada_salon_at"
                    )
                )
                or not str(
                    row.get(
                        "ingresada_salon_at",
                        ""
                    )
                ).strip()
            ):
                stock.loc[
                    idx,
                    "ingresada_salon_at"
                ] = inc.get(
                    "timestamp"
                )

                changed = True

        # Apertura
        openings = movements[
            (
                movements[
                    "target_stock_id"
                ]
                .astype(str)
                .eq(
                    stock_id
                )
            )
            &
            (
                movements[
                    "movement_type"
                ]
                .astype(str)
                .str.upper()
                .eq(
                    "LATA_ABIERTA"
                )
            )
        ].sort_values(
            "_timestamp_dt"
        )

        if not openings.empty:
            op = openings.iloc[0]

            if (
                pd.isna(
                    row.get(
                        "opened_at"
                    )
                )
                or not str(
                    row.get(
                        "opened_at",
                        ""
                    )
                ).strip()
            ):
                stock.loc[
                    idx,
                    "opened_at"
                ] = op.get(
                    "timestamp"
                )

                stock.loc[
                    idx,
                    "opened_operation_id"
                ] = op.get(
                    "operation_id"
                )

                changed = True

        # Finalización
        finished = movements[
            (
                movements[
                    "source_stock_id"
                ]
                .astype(str)
                .eq(
                    stock_id
                )
            )
            &
            (
                movements[
                    "movement_type"
                ]
                .astype(str)
                .str.upper()
                .eq(
                    "LATA_AGOTADA"
                )
            )
        ].sort_values(
            "_timestamp_dt"
        )

        if not finished.empty:
            fin = finished.iloc[-1]

            if (
                pd.isna(
                    row.get(
                        "finished_at"
                    )
                )
                or not str(
                    row.get(
                        "finished_at",
                        ""
                    )
                ).strip()
            ):
                stock.loc[
                    idx,
                    "finished_at"
                ] = fin.get(
                    "timestamp"
                )

                stock.loc[
                    idx,
                    "finished_operation_id"
                ] = fin.get(
                    "operation_id"
                )

                if "tara_final_kg" in fin.index:
                    stock.loc[
                        idx,
                        "tara_final_kg"
                    ] = fin.get(
                        "tara_final_kg"
                    )

                if "residuo_final_kg" in fin.index:
                    stock.loc[
                        idx,
                        "residuo_final_kg"
                    ] = fin.get(
                        "residuo_final_kg"
                    )

                changed = True

        if changed:
            updated_rows += 1

    return (
        stock,
        {
            "updated_rows":
                updated_rows,

            "skipped_ambiguous_ids":
                ambiguous_ids,
        },
    )
