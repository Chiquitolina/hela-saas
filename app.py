import streamlit as st
import pandas as pd

from io import BytesIO
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import shutil
import os
import time
import uuid
import json

from models.lata import Lata
from models.camera_lata import CameraLata
from models.camera_product import (
    CameraProduct,
    CATEGORY_CODES,
    CATEGORY_SUBCATEGORIES,
    PACKAGING_PACK_BOXES_UNITS,
    PACKAGING_PACK_UNITS,
    PACKAGING_BOX_UNITS,
    PACKAGING_MODES,
)
from models.product import Product
from models.flavor import Flavor
from models.week import Week, WEEK_COLUMNS, WEEK_METADATA_VERSION
from services.flavor_service import (
    ensure_flavor_codes,
    normalize_flavor_code,
    propose_flavor_code,
)

from services.id_service import (
    next_sequential_id,
)

from services.inventory_service import (
    CAMERA_COLUMNS,
    COMBINED_STOCK_COLUMNS,
    SALON_COLUMNS,
    combine_inventory,
    load_camera_stock as load_camera_stock_file,
    load_salon_latas as load_salon_latas_file,
    migrate_legacy_inventory,
    migrate_camera_stock_to_individual,
    split_inventory,
)

from services.week_service import (
    backfill_lata_metadata_from_movements,
    current_stock_snapshot,
    refresh_weeks_dataframe,
    repair_missing_estimated_residue,
)


# ============================================================
# WEEK PRODUCT SNAPSHOTS
# ============================================================

WEEK_PRODUCT_SNAPSHOT_COLUMNS = [
    "start_products_snapshot_json",
    "current_products_snapshot_json",
    "end_products_snapshot_json",
]

for _column in WEEK_PRODUCT_SNAPSHOT_COLUMNS:
    if _column not in WEEK_COLUMNS:
        WEEK_COLUMNS.append(
            _column
        )


# ============================================================
# WEEK SALON SNAPSHOTS
# ============================================================

WEEK_SALON_SNAPSHOT_COLUMNS = [
    "start_salon_snapshot_json",
    "end_salon_snapshot_json",
]

for _column in WEEK_SALON_SNAPSHOT_COLUMNS:
    if _column not in WEEK_COLUMNS:
        WEEK_COLUMNS.append(
            _column
        )


# ============================================================
# WEEK MERMA / NOMINAL ANALYTICS
# ============================================================

WEEK_MERMA_ANALYTICS_COLUMNS = [
    # Cámara → Salón / peso real
    "camera_exit_gross_kg",
    "camera_exit_gross_avg_kg",
    "avg_final_tare_kg",
    "estimated_camera_exit_net_kg",
    "estimated_net_avg_per_lata_kg",

    # Comparación de las salidas de cámara contra 7.800 kg
    "camera_nominal_expected_kg",
    "camera_nominal_deficit_kg",
    "camera_nominal_deficit_per_lata_kg",
    "camera_nominal_deficit_pct",

    # Universo completo analizado:
    # cerradas al inicio + entradas desde cámara
    "nominal_reference_kg",
    "nominal_analyzed_latas",
    "nominal_initial_closed_latas",
    "nominal_camera_latas",
    "nominal_expected_total_kg",
    "nominal_estimated_total_kg",
    "nominal_in_range_latas",
    "nominal_deficit_latas",
    "nominal_in_range_pct",
    "nominal_deficit_total_kg",
    "nominal_excess_total_kg",
    "nominal_balance_total_kg",
    "nominal_avg_deviation_kg",
    "nominal_avg_deviation_pct",

    # Derivado de la merma final cuando exista
    "merma_latas_equivalentes",
]

for _column in WEEK_MERMA_ANALYTICS_COLUMNS:
    if _column not in WEEK_COLUMNS:
        WEEK_COLUMNS.append(
            _column
        )


# ============================================================
# CONFIG
# ============================================================

st.set_page_config(
    page_title="Control de Merma",
    page_icon="🍦",
    layout="wide",
)

APP_TZ = ZoneInfo("America/Argentina/Cordoba")

# ============================================================
# WEIGHT UNITS
# Internamente TODO se guarda en kilogramos.
# Ejemplos:
# 7 kg 580 g -> 7.580
# 380 g      -> 0.380
# ============================================================

MAX_CAN_GROSS_KG = 20.0
MAX_TARE_KG = 2.0
DEFAULT_TARE_KG = 0.380
DEFAULT_CAMERA_CAN_KG = 7.580
GRIDO_NOMINAL_NET_KG = 7.800
GRIDO_NOMINAL_TOLERANCE_KG = 0.050

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)


# ============================================================
# FILES
# ============================================================

# Inventarios separados
SALON_LATAS_FILE = DATA_DIR / "salon_latas.csv"
CAMERA_STOCK_FILE = DATA_DIR / "camera_stock.csv"
CAMERA_PRODUCTS_FILE = DATA_DIR / "camera_products.csv"
PRODUCTS_FILE = DATA_DIR / "products.csv"

# Archivo legacy: se conserva como fuente de migración/backup,
# pero la app nueva ya NO lo usa como persistencia activa.
LEGACY_CURRENT_STOCK_FILE = DATA_DIR / "current_stock.csv"

MOVEMENTS_FILE = DATA_DIR / "stock_movements.csv"
COUNTS_FILE = DATA_DIR / "inventory_counts.csv"
WEEKS_FILE = DATA_DIR / "weeks.csv"
FLAVORS_FILE = DATA_DIR / "flavors.csv"


# ============================================================
# SCHEMAS
# ============================================================

# `STOCK_COLUMNS` queda como vista combinada en memoria por compatibilidad.
# La persistencia real usa SALON_COLUMNS y CAMERA_COLUMNS.
STOCK_COLUMNS = COMBINED_STOCK_COLUMNS

MOVEMENT_COLUMNS = [
    "movement_id",
    "operation_id",
    "timestamp",
    "week_id",
    "movement_type",
    "from_location",
    "to_location",
    "source_stock_id",
    "target_stock_id",
    "sabor",
    "cantidad_latas",
    "peso_bruto_kg",
    "tara_kg",
    "peso_neto_kg",
    "tara_final_kg",
    "residuo_final_kg",
    "notes",
]

COUNT_COLUMNS = [
    "count_id",
    "week_id",
    "count_type",
    "timestamp",
    "location",
    "stock_id",
    "sabor",
    "estado",
    "peso_bruto_kg",
    "tara_kg",
    "peso_neto_kg",
    "notes",
]

FLAVOR_COLUMNS = [
    "sabor",
    "flavor_code",
    "active",
    "created_at",
]


CAMERA_PRODUCT_COLUMNS = [
    "product_stock_id","product_code","categoria","subcategoria","producto","packaging_mode",
    "cantidad_packs","cantidad_cajas","cajas_por_pack","unidades_por_pack","unidades_por_caja",
    "total_cajas","total_unidades","created_at","updated_at","active",
]

PRODUCT_COLUMNS = [
    "product_code","categoria","subcategoria","producto","packaging_mode",
    "cajas_por_pack","unidades_por_pack","unidades_por_caja","active","created_at","updated_at",
]


# ============================================================
# TIME
# ============================================================

def now_local():
    return datetime.now(APP_TZ)


def now_iso():
    return now_local().isoformat()


def format_now():
    return now_local().strftime("%d/%m/%Y %H:%M:%S")


# ============================================================
# IDS
# ============================================================

def generate_id(prefix):
    """
    IDs legibles y secuenciales.

    MOV-000001
    COUNT-000001
    WEEK-000001

    Operaciones:
    APERTURA-000001
    FINALIZA-000001
    RECAMBIO-000001
    ANULACION-CAMARA-000001
    """

    prefix = (
        str(prefix)
        .strip()
        .upper()
    )

    if prefix == "MOV":
        df = load_csv(
            MOVEMENTS_FILE
        )

        existing = (
            df[
                "movement_id"
            ]
            .dropna()
            .astype(str)
            .tolist()
            if (
                not df.empty
                and "movement_id"
                in df.columns
            )
            else []
        )

        return next_sequential_id(
            existing,
            "MOV",
        )

    if prefix == "COUNT":
        df = load_csv(
            COUNTS_FILE
        )

        existing = (
            df[
                "count_id"
            ]
            .dropna()
            .astype(str)
            .tolist()
            if (
                not df.empty
                and "count_id"
                in df.columns
            )
            else []
        )

        return next_sequential_id(
            existing,
            "COUNT",
        )

    if prefix == "WEEK":
        df = load_weeks()

        existing = (
            df[
                "week_id"
            ]
            .dropna()
            .astype(str)
            .tolist()
            if (
                not df.empty
                and "week_id"
                in df.columns
            )
            else []
        )

        return next_sequential_id(
            existing,
            "WEEK",
        )

    # Operation IDs viven en stock_movements.operation_id.
    df = load_csv(
        MOVEMENTS_FILE
    )

    existing = (
        df[
            "operation_id"
        ]
        .dropna()
        .astype(str)
        .tolist()
        if (
            not df.empty
            and "operation_id"
            in df.columns
        )
        else []
    )

    return next_sequential_id(
        existing,
        prefix,
    )


def generate_camera_id(
    sabor,
):
    flavor_code = get_flavor_code(
        sabor
    )

    camera = load_camera_stock()

    existing = (
        camera[
            "camera_stock_id"
        ]
        .dropna()
        .astype(str)
        .tolist()
        if (
            not camera.empty
            and "camera_stock_id"
            in camera.columns
        )
        else []
    )

    return next_sequential_id(
        existing,
        f"CAM-{flavor_code}",
    )


def generate_camera_ids(
    sabor,
    cantidad,
):
    flavor_code = get_flavor_code(
        sabor
    )

    camera = load_camera_stock()

    existing = (
        camera[
            "camera_stock_id"
        ]
        .dropna()
        .astype(str)
        .tolist()
        if (
            not camera.empty
            and "camera_stock_id"
            in camera.columns
        )
        else []
    )

    ids = []

    for _ in range(
        int(
            cantidad
        )
    ):
        new_id = next_sequential_id(
            existing,
            f"CAM-{flavor_code}",
        )

        ids.append(
            new_id
        )

        existing.append(
            new_id
        )

    return ids


def move_camera_flavor_to_salon(
    sabor,
    peso_bruto_kg,
    tara_kg,
    notes="",
):
    sabor = normalize_flavor_name(
        sabor
    )

    stock = load_current_stock()

    available = stock[
        (
            stock["location"].eq("CAMARA")
        )
        &
        (
            stock["active"] == True
        )
        &
        (
            stock["sabor"]
            .map(normalize_flavor_name)
            .eq(sabor)
        )
    ].copy()

    if available.empty:
        raise ValueError(
            f"No quedan latas de {sabor} en cámara."
        )

    # Usamos primero el stock más antiguo
    available["created_at_dt"] = pd.to_datetime(
        available["created_at"],
        errors="coerce",
    )

    available = available.sort_values(
        "created_at_dt",
        ascending=True,
        na_position="last",
    )

    source_stock_id = (
        available.iloc[0]["stock_id"]
    )

    return move_camera_to_salon(
        camera_stock_id=source_stock_id,
        peso_bruto_kg=peso_bruto_kg,
        tara_kg=tara_kg,
        notes=notes,
    )


# ============================================================
# CSV HELPERS / SAFE PERSISTENCE
# ============================================================

def load_csv(filepath):
    if not filepath.exists():
        return pd.DataFrame()

    try:
        return pd.read_csv(filepath)

    except pd.errors.EmptyDataError:
        # No reescribimos automáticamente un archivo que existe pero quedó
        # físicamente vacío. Es preferible detectar el problema.
        if filepath.stat().st_size == 0:
            raise RuntimeError(
                f"El archivo {filepath} existe pero está vacío. "
                "No se sobrescribirá automáticamente."
            )

        return pd.DataFrame()


def backup_file(filepath):
    """
    Copia el CSV antes de cualquier escritura que modifique un archivo
    existente. Los backups quedan en data/backups/.
    """
    if not filepath.exists():
        return None

    timestamp = now_local().strftime("%Y%m%d_%H%M%S_%f")

    backup_path = (
        BACKUP_DIR
        / f"{filepath.stem}_{timestamp}{filepath.suffix}"
    )

    shutil.copy2(
        filepath,
        backup_path,
    )

    return backup_path


def safe_write_csv(
    df,
    filepath,
    allow_empty=False,
    retries=8,
):
    """
    Escritura protegida:
    1. evita reemplazar accidentalmente un CSV con datos por uno vacío;
    2. crea backup;
    3. escribe a un temporal único;
    4. reemplaza el original de forma atómica;
    5. reintenta si Windows/OneDrive mantiene un lock transitorio.

    El feedback visual de la operación se maneja en la capa UI mediante
    run_ui_mutation(). Si todos los reintentos fallan, la excepción sube
    hasta esa capa y el usuario recibe un estado ERROR visible.
    """

    filepath = Path(
        filepath
    )

    filepath.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if (
        df.empty
        and not allow_empty
        and filepath.exists()
    ):
        try:
            existing = pd.read_csv(
                filepath
            )

        except pd.errors.EmptyDataError:
            existing = pd.DataFrame()

        if not existing.empty:
            raise RuntimeError(
                f"Protección de datos: se intentó sobrescribir "
                f"{filepath} con un DataFrame vacío."
            )

    if filepath.exists():
        backup_file(
            filepath
        )

    temp_path = filepath.with_name(
        f".{filepath.name}.{uuid.uuid4().hex}.tmp"
    )

    last_error = None

    try:
        df.to_csv(
            temp_path,
            index=False,
        )

        for attempt in range(
            retries
        ):
            try:
                os.replace(
                    temp_path,
                    filepath,
                )

                return

            except PermissionError as exc:
                last_error = exc

                # Backoff corto: 0.10, 0.20, ... 0.80 s.
                time.sleep(
                    0.10
                    * (
                        attempt + 1
                    )
                )

        if last_error is not None:
            raise last_error

    finally:
        if temp_path.exists():
            try:
                temp_path.unlink(
                    missing_ok=True
                )
            except OSError:
                pass


def append_row(
    filepath,
    row,
):
    df = load_csv(
        filepath
    )

    new_df = pd.DataFrame(
        [row]
    )

    final_df = pd.concat(
        [
            df,
            new_df,
        ],
        ignore_index=True,
    )

    safe_write_csv(
        final_df,
        filepath,
    )


def ensure_csv_schema(
    filepath,
    columns,
):
    """
    En startup:
    - crea el archivo únicamente si NO existe;
    - si existe, NO lo reescribe salvo que realmente falten columnas
      por una migración de esquema.
    """

    filepath = Path(
        filepath
    )

    if not filepath.exists():
        pd.DataFrame(
            columns=columns
        ).to_csv(
            filepath,
            index=False,
        )

        return

    if filepath.stat().st_size == 0:
        raise RuntimeError(
            f"{filepath} existe pero tiene 0 bytes. "
            "La app no lo inicializará encima para evitar pérdida de datos."
        )

    df = pd.read_csv(
        filepath
    )

    changed = False

    # Migraciones desde versiones anteriores
    if filepath == SALON_LATAS_FILE:
        if (
            "kg_lata_cerrada" in df.columns
            and "kg_referencia_lata" not in df.columns
        ):
            df[
                "kg_referencia_lata"
            ] = df[
                "kg_lata_cerrada"
            ]

            changed = True

        if (
            "peso_neto_kg" in df.columns
            and "peso_actual_neto_kg" not in df.columns
        ):
            df[
                "peso_actual_neto_kg"
            ] = df[
                "peso_neto_kg"
            ]

            changed = True

    for col in columns:
        if col not in df.columns:
            df[
                col
            ] = pd.NA

            changed = True

    if changed:
        # Conservamos solo el esquema esperado, pero únicamente durante
        # una migración real y con backup + escritura atómica.
        df = df[
            columns
        ]

        safe_write_csv(
            df,
            filepath,
            allow_empty=True,
        )


def initialize_files():
    # Primero garantizamos archivos auxiliares que la migración puede leer.
    ensure_csv_schema(
        MOVEMENTS_FILE,
        MOVEMENT_COLUMNS,
    )

    ensure_csv_schema(
        COUNTS_FILE,
        COUNT_COLUMNS,
    )

    ensure_csv_schema(
        WEEKS_FILE,
        WEEK_COLUMNS,
    )

    ensure_csv_schema(
        FLAVORS_FILE,
        FLAVOR_COLUMNS,
    )

    ensure_csv_schema(
        CAMERA_PRODUCTS_FILE,
        CAMERA_PRODUCT_COLUMNS,
    )

    ensure_csv_schema(
        PRODUCTS_FILE,
        PRODUCT_COLUMNS,
    )

    # Si venimos de current_stock.csv, lo separamos automáticamente.
    # safe_write_csv crea backups antes de modificar archivos existentes.
    migrate_legacy_inventory(
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
            lambda df, path, allow_empty=True:
                safe_write_csv(
                    df,
                    path,
                    allow_empty=allow_empty,
                ),
    )

    # Cámara ahora también se normaliza a una fila por lata física.
    migrate_camera_stock_to_individual(
        camera_file=
            CAMERA_STOCK_FILE,

        write_csv=
            lambda df, path, allow_empty=True:
                safe_write_csv(
                    df,
                    path,
                    allow_empty=allow_empty,
                ),
    )

    # Finalmente garantizamos los dos esquemas nuevos.
    ensure_csv_schema(
        SALON_LATAS_FILE,
        SALON_COLUMNS,
    )

    ensure_csv_schema(
        CAMERA_STOCK_FILE,
        CAMERA_COLUMNS,
    )


initialize_files()


# ============================================================
# LEGACY WEIGHT UNIT MIGRATION
# ============================================================

def migrate_legacy_weight_units():
    """
    Corrige datos viejos cargados en gramos dentro de columnas *_kg.

    Ejemplo incorrecto:
        7580.0  -> interpretado como 7580 kg

    Conversión:
        7580.0  -> 7.580 kg

    La migración solamente toca valores imposibles para una lata individual
    y crea backup mediante safe_write_csv() antes de guardar.
    """

    changed_stock = False
    changed_counts = False
    changed_movements = False

    # --------------------------------------------------------
    # CURRENT STOCK
    # Todos estos pesos son por lata individual.
    # --------------------------------------------------------

    stock = load_current_stock()

    legacy_camera_ids = set()

    if not stock.empty:

        if "kg_referencia_lata" in stock.columns:

            ref_values = pd.to_numeric(
                stock[
                    "kg_referencia_lata"
                ],
                errors="coerce",
            )

            bad_ref_mask = (
                ref_values
                > MAX_CAN_GROSS_KG
            )

            legacy_camera_ids = set(
                stock.loc[
                    bad_ref_mask,
                    "stock_id",
                ]
                .dropna()
                .astype(str)
                .tolist()
            )

        stock_weight_columns = [
            "kg_referencia_lata",
            "peso_inicial_bruto_kg",
            "tara_inicial_kg",
            "peso_inicial_neto_kg",
            "peso_actual_bruto_kg",
            "tara_actual_kg",
            "peso_actual_neto_kg",
        ]

        for col in stock_weight_columns:

            if col not in stock.columns:
                continue

            values = pd.to_numeric(
                stock[col],
                errors="coerce",
            )

            # Para tara usamos un límite específico.
            limit = (
                MAX_TARE_KG
                if "tara" in col
                else MAX_CAN_GROSS_KG
            )

            bad_mask = (
                values
                > limit
            )

            if bad_mask.any():

                stock.loc[
                    bad_mask,
                    col,
                ] = (
                    values.loc[
                        bad_mask
                    ]
                    / 1000.0
                ).round(3)

                changed_stock = True

        if changed_stock:

            save_current_stock(
                stock
            )

    # --------------------------------------------------------
    # INVENTORY COUNTS
    # Cada fila representa una lata individual.
    # --------------------------------------------------------

    counts = load_csv(
        COUNTS_FILE
    )

    if not counts.empty:

        for col in [
            "peso_bruto_kg",
            "tara_kg",
            "peso_neto_kg",
        ]:

            if col not in counts.columns:
                continue

            values = pd.to_numeric(
                counts[col],
                errors="coerce",
            )

            limit = (
                MAX_TARE_KG
                if col == "tara_kg"
                else MAX_CAN_GROSS_KG
            )

            bad_mask = (
                values
                > limit
            )

            if bad_mask.any():

                counts.loc[
                    bad_mask,
                    col,
                ] = (
                    values.loc[
                        bad_mask
                    ]
                    / 1000.0
                ).round(3)

                changed_counts = True

        if changed_counts:

            safe_write_csv(
                counts,
                COUNTS_FILE,
            )

    # --------------------------------------------------------
    # STOCK MOVEMENTS
    #
    # La mayoría son movimientos de una lata.
    # INGRESO_CAMARA puede representar varias latas, por eso no
    # corregimos sus totales por umbral salvo que sepamos que el
    # stock de cámara vinculado tenía kg_referencia_lata en gramos.
    # --------------------------------------------------------

    movements = load_csv(
        MOVEMENTS_FILE
    )

    if not movements.empty:

        movement_type = (
            movements[
                "movement_type"
            ]
            .fillna("")
            .astype(str)
        )

        qty = pd.to_numeric(
            movements.get(
                "cantidad_latas",
                pd.Series(
                    index=movements.index,
                    dtype=float,
                ),
            ),
            errors="coerce",
        ).fillna(1)

        target_ids = (
            movements[
                "target_stock_id"
            ]
            .fillna("")
            .astype(str)
        )

        # INGRESO_CAMARA asociado a un registro de cámara que sabemos
        # que estaba cargado en gramos.
        legacy_ingreso_mask = (
            movement_type.eq(
                "INGRESO_CAMARA"
            )
            &
            target_ids.isin(
                legacy_camera_ids
            )
        )

        for col in [
            "peso_bruto_kg",
            "tara_kg",
            "peso_neto_kg",
        ]:

            if col not in movements.columns:
                continue

            values = pd.to_numeric(
                movements[col],
                errors="coerce",
            )

            limit = (
                MAX_TARE_KG
                if col == "tara_kg"
                else MAX_CAN_GROSS_KG
            )

            # Movimientos de una sola lata: >20 kg (o tara >2 kg)
            # es un dato imposible y se interpreta como gramos.
            single_can_bad_mask = (
                (~movement_type.eq("INGRESO_CAMARA"))
                &
                (qty <= 1)
                &
                (values > limit)
            )

            # En ingreso de cámara corregimos si el stock vinculado
            # fue detectado como legacy.
            ingreso_bad_mask = (
                legacy_ingreso_mask
                &
                values.notna()
            )

            bad_mask = (
                single_can_bad_mask
                |
                ingreso_bad_mask
            )

            if bad_mask.any():

                movements.loc[
                    bad_mask,
                    col,
                ] = (
                    values.loc[
                        bad_mask
                    ]
                    / 1000.0
                ).round(3)

                changed_movements = True

        if changed_movements:

            safe_write_csv(
                movements,
                MOVEMENTS_FILE,
            )

    return {
        "stock":
            changed_stock,

        "counts":
            changed_counts,

        "movements":
            changed_movements,
    }


# ============================================================
# INVENTORY STORAGE
# ============================================================

def load_salon_latas():
    return load_salon_latas_file(
        SALON_LATAS_FILE
    )


def load_camera_stock():
    return load_camera_stock_file(
        CAMERA_STOCK_FILE
    )


def load_current_stock():
    """
    Vista combinada SOLO EN MEMORIA.

    Esto permite que cálculos globales ya existentes (Week, overview, etc.)
    sigan trabajando con `location`, pero no vuelve a mezclar la persistencia.
    """
    return combine_inventory(
        load_salon_latas(),
        load_camera_stock(),
    )


def save_current_stock(df):
    """
    Compatibilidad para operaciones existentes.

    Recibe la vista combinada y persiste cada entidad en su archivo:
        SALON  -> salon_latas.csv
        CAMARA -> camera_stock.csv
    """

    salon, camera = split_inventory(
        df
    )

    safe_write_csv(
        salon,
        SALON_LATAS_FILE,
        allow_empty=True,
    )

    safe_write_csv(
        camera,
        CAMERA_STOCK_FILE,
        allow_empty=True,
    )


# ============================================================
# STARTUP LEGACY WEIGHT MIGRATION
# ============================================================

# Esta función depende de load_current_stock() y save_current_stock(),
# por eso debe ejecutarse recién después de definir ambos helpers.
migrate_legacy_weight_units()


def generate_salon_id(
    stock_df=None,
):
    if stock_df is None:
        salon = load_salon_latas()

        existing = (
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
        )

    else:
        if "location" in stock_df.columns:
            salon = stock_df[
                stock_df[
                    "location"
                ]
                .astype(str)
                .str.upper()
                .eq(
                    "SALON"
                )
            ].copy()
        else:
            salon = (
                stock_df.copy()
            )

        existing = (
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
        )

    return next_sequential_id(
        existing,
        "SAL",
    )


# ============================================================
# FLAVORS
# ============================================================

def normalize_flavor_name(value):
    if pd.isna(value):
        return ""

    return (
        str(value)
        .strip()
        .upper()
        .replace(" ", "_")
    )


def bootstrap_flavors():
    """
    Recupera sabores existentes y garantiza un flavor_code único.
    """

    discovered = set()

    for filepath in [
        SALON_LATAS_FILE,
        CAMERA_STOCK_FILE,
        MOVEMENTS_FILE,
        COUNTS_FILE,
    ]:
        df = load_csv(
            filepath
        )

        if (
            not df.empty
            and "sabor" in df.columns
        ):
            for value in (
                df[
                    "sabor"
                ]
                .dropna()
                .unique()
            ):
                flavor = normalize_flavor_name(
                    value
                )

                if flavor:
                    discovered.add(
                        flavor
                    )

    flavor_df = load_csv(
        FLAVORS_FILE
    )

    if flavor_df.empty:
        flavor_df = pd.DataFrame(
            columns=FLAVOR_COLUMNS
        )

    for col in FLAVOR_COLUMNS:
        if col not in flavor_df.columns:
            flavor_df[
                col
            ] = pd.NA

    existing = set(
        flavor_df[
            "sabor"
        ]
        .dropna()
        .map(
            normalize_flavor_name
        )
    )

    missing = (
        discovered
        - existing
    )

    timestamp = now_iso()

    if missing:
        used_codes = (
            flavor_df[
                "flavor_code"
            ]
            .dropna()
            .astype(str)
            .tolist()
        )

        rows = []

        for flavor in sorted(
            missing
        ):
            code = propose_flavor_code(
                flavor,
                used_codes=used_codes,
            )

            used_codes.append(
                code
            )

            rows.append(
                {
                    "sabor":
                        flavor,

                    "flavor_code":
                        code,

                    "active":
                        True,

                    "created_at":
                        timestamp,
                }
            )

        flavor_df = pd.concat(
            [
                flavor_df,
                pd.DataFrame(
                    rows
                ),
            ],
            ignore_index=True,
        )

    flavor_df = ensure_flavor_codes(
        flavor_df
    )

    normalized_names = (
        flavor_df[
            "sabor"
        ]
        .map(
            normalize_flavor_name
        )
    )

    normalized_codes = (
        flavor_df[
            "flavor_code"
        ]
        .map(
            normalize_flavor_code
        )
    )

    if normalized_names.duplicated().any():
        raise ValueError(
            "Hay sabores duplicados en flavors.csv."
        )

    if normalized_codes.duplicated().any():
        duplicated = (
            normalized_codes[
                normalized_codes
                .duplicated(
                    keep=False
                )
            ]
            .unique()
            .tolist()
        )

        raise ValueError(
            "Hay flavor_code duplicados: "
            + ", ".join(
                duplicated
            )
        )

    flavor_df[
        "sabor"
    ] = normalized_names

    flavor_df[
        "flavor_code"
    ] = normalized_codes

    final_flavors = (
        flavor_df[
            FLAVOR_COLUMNS
        ]
        .copy()
    )

    existing_flavors = load_csv(
        FLAVORS_FILE
    )

    if existing_flavors.empty:
        flavors_changed = (
            not final_flavors.empty
        )

    else:
        existing_flavors = (
            existing_flavors
            .reindex(
                columns=FLAVOR_COLUMNS
            )
        )

        flavors_changed = not (
            final_flavors
            .fillna("")
            .astype(str)
            .equals(
                existing_flavors
                .fillna("")
                .astype(str)
            )
        )

    if flavors_changed:
        safe_write_csv(
            final_flavors,
            FLAVORS_FILE,
            allow_empty=True,
        )


def load_flavor_catalog(
    active_only=False,
):
    bootstrap_flavors()

    df = load_csv(
        FLAVORS_FILE
    )

    if df.empty:
        return pd.DataFrame(
            columns=FLAVOR_COLUMNS
        )

    df = ensure_flavor_codes(
        df
    )

    if active_only:
        active_mask = (
            df[
                "active"
            ]
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

        df = df[
            active_mask
        ]

    return (
        df[
            FLAVOR_COLUMNS
        ]
        .copy()
    )


def load_flavors():
    df = load_flavor_catalog(
        active_only=True
    )

    if df.empty:
        return []

    return (
        df[
            "sabor"
        ]
        .dropna()
        .map(
            normalize_flavor_name
        )
        .drop_duplicates()
        .sort_values()
        .tolist()
    )


def get_flavor_code(
    sabor,
):
    sabor = normalize_flavor_name(
        sabor
    )

    df = load_flavor_catalog(
        active_only=False
    )

    matches = df[
        df[
            "sabor"
        ]
        .map(
            normalize_flavor_name
        )
        .eq(
            sabor
        )
    ]

    if matches.empty:
        raise ValueError(
            f"El sabor {sabor} no tiene flavor_code configurado."
        )

    code = normalize_flavor_code(
        matches.iloc[0][
            "flavor_code"
        ]
    )

    if not code:
        raise ValueError(
            f"El sabor {sabor} no tiene flavor_code configurado."
        )

    return code


def add_flavor(
    flavor,
    flavor_code,
):
    new_flavor = Flavor.create(
        sabor=
            flavor,

        flavor_code=
            flavor_code,

        timestamp=
            now_iso(),
    )

    df = load_flavor_catalog(
        active_only=False
    )

    if not df.empty:
        normalized_names = set(
            df[
                "sabor"
            ]
            .dropna()
            .map(
                normalize_flavor_name
            )
        )

        normalized_codes = set(
            df[
                "flavor_code"
            ]
            .dropna()
            .map(
                normalize_flavor_code
            )
        )

        if (
            new_flavor.sabor
            in normalized_names
        ):
            raise ValueError(
                "Ese sabor ya existe."
            )

        if (
            new_flavor.flavor_code
            in normalized_codes
        ):
            raise ValueError(
                f"El código {new_flavor.flavor_code} "
                "ya está usado por otro sabor."
            )

    append_row(
        FLAVORS_FILE,
        new_flavor.to_row(),
    )

    return (
        new_flavor.sabor,
        new_flavor.flavor_code,
    )


bootstrap_flavors()


# ============================================================
# WEIGHT
# ============================================================

def calculate_net_weight(
    peso_bruto,
    tara,
):
    """
    Todos los valores entran y salen en KG.
    Redondeamos a 3 decimales: precisión de 1 gramo.
    """

    peso_bruto = round(
        float(
            peso_bruto
        ),
        3,
    )

    tara = round(
        float(
            tara
        ),
        3,
    )

    if peso_bruto <= 0:
        raise ValueError(
            "El peso bruto debe ser mayor a cero."
        )

    if peso_bruto > MAX_CAN_GROSS_KG:
        raise ValueError(
            f"El peso bruto no puede superar "
            f"{MAX_CAN_GROSS_KG:.3f} kg. "
            "Si la balanza muestra gramos, por ejemplo 7856 g, "
            "cargá 7.856 kg."
        )

    if tara < 0:
        raise ValueError(
            "La tara no puede ser negativa."
        )

    if tara > MAX_TARE_KG:
        raise ValueError(
            f"La tara no puede superar "
            f"{MAX_TARE_KG:.3f} kg. "
            "Ejemplo: 380 g se carga como 0.380 kg."
        )

    if tara >= peso_bruto:
        raise ValueError(
            "La tara debe ser menor al peso bruto."
        )

    return round(
        peso_bruto
        - tara,
        3,
    )


# ============================================================
# CAMERA PRODUCT SNAPSHOTS FOR WEEK
# ============================================================

PRODUCT_SNAPSHOT_CATEGORIES = [
    "FAMILIARES",
    "TENTACIONES",
    "POSTRES",
    "TORTAS",
    "BOMBONES",
    "PALITOS",
    "LINEAS_ESPECIALES",
    "FRIZZIO",
]


def build_camera_products_snapshot():
    """
    Snapshot vivo de productos no-granel activos en cámara.

    Persistencia detallada:
        categoría
            totals
                packs
                boxes
                units

            products
                PRODUCT_CODE
                    producto
                    packaging_mode
                    packs
                    boxes
                    units

    La UI puede seguir presentando el total por categoría, pero la Week
    conserva el detalle por producto para futuros cruces con ventas.
    """

    products = load_camera_products(
        active_only=True
    )

    snapshot = {
        category: {
            "totals": {
                "packs": 0,
                "boxes": 0,
                "units": 0,
            },
            "products": {},
        }
        for category
        in PRODUCT_SNAPSHOT_CATEGORIES
    }

    if products.empty:
        return snapshot

    for _, row in products.iterrows():

        category = str(
            row.get(
                "categoria",
                ""
            )
        ).strip().upper()

        if not category:
            continue

        if category not in snapshot:
            snapshot[
                category
            ] = {
                "totals": {
                    "packs": 0,
                    "boxes": 0,
                    "units": 0,
                },
                "products": {},
            }

        product_code = str(
            row.get(
                "product_code",
                ""
            )
        ).strip()

        product_name = str(
            row.get(
                "producto",
                ""
            )
        ).strip()

        packaging_mode = str(
            row.get(
                "packaging_mode",
                ""
            )
        ).strip()

        if not product_code:
            # Fallback para filas legacy.
            product_code = (
                product_name
                or str(
                    row.get(
                        "product_stock_id",
                        "UNKNOWN_PRODUCT"
                    )
                )
            )

        packs = pd.to_numeric(
            row.get(
                "cantidad_packs",
                0,
            ),
            errors="coerce",
        )

        boxes = pd.to_numeric(
            row.get(
                "total_cajas",
                row.get(
                    "cantidad_cajas",
                    0,
                ),
            ),
            errors="coerce",
        )

        units = pd.to_numeric(
            row.get(
                "total_unidades",
                0,
            ),
            errors="coerce",
        )

        packs = (
            int(
                packs
            )
            if pd.notna(
                packs
            )
            else 0
        )

        boxes = (
            int(
                boxes
            )
            if pd.notna(
                boxes
            )
            else 0
        )

        units = (
            int(
                units
            )
            if pd.notna(
                units
            )
            else 0
        )

        category_entry = snapshot[
            category
        ]

        category_entry[
            "totals"
        ][
            "packs"
        ] += packs

        category_entry[
            "totals"
        ][
            "boxes"
        ] += boxes

        category_entry[
            "totals"
        ][
            "units"
        ] += units

        product_entry = category_entry[
            "products"
        ].setdefault(
            product_code,
            {
                "producto":
                    product_name,

                "packaging_mode":
                    packaging_mode,

                "packs":
                    0,

                "boxes":
                    0,

                "units":
                    0,
            },
        )

        # Keep metadata populated for legacy rows that arrived empty first.
        if (
            not product_entry.get(
                "producto"
            )
            and product_name
        ):
            product_entry[
                "producto"
            ] = product_name

        if (
            not product_entry.get(
                "packaging_mode"
            )
            and packaging_mode
        ):
            product_entry[
                "packaging_mode"
            ] = packaging_mode

        product_entry[
            "packs"
        ] += packs

        product_entry[
            "boxes"
        ] += boxes

        product_entry[
            "units"
        ] += units

    return snapshot


def products_snapshot_to_json(
    snapshot,
):
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
        sort_keys=True,
    )


def products_snapshot_from_json(
    value,
):
    if value is None:
        return {}

    try:
        if pd.isna(
            value
        ):
            return {}
    except Exception:
        pass

    if isinstance(
        value,
        dict,
    ):
        return value

    raw = str(
        value
    ).strip()

    if not raw or raw.lower() in {
        "nan",
        "none",
        "<na>",
    }:
        return {}

    try:
        parsed = json.loads(
            raw
        )

        if isinstance(
            parsed,
            dict,
        ):
            return parsed

    except Exception:
        pass

    return {}


def product_snapshot_category_totals(
    category_values,
):
    """
    Devuelve packs/boxes/units tanto para el schema nuevo como para
    snapshots legacy guardados antes de este refactor.
    """

    if not isinstance(
        category_values,
        dict,
    ):
        return {
            "packs": 0,
            "boxes": 0,
            "units": 0,
        }

    totals = category_values.get(
        "totals"
    )

    if isinstance(
        totals,
        dict,
    ):
        return {
            "packs":
                int(
                    totals.get(
                        "packs",
                        0,
                    )
                    or 0
                ),

            "boxes":
                int(
                    totals.get(
                        "boxes",
                        0,
                    )
                    or 0
                ),

            "units":
                int(
                    totals.get(
                        "units",
                        0,
                    )
                    or 0
                ),
        }

    # Legacy:
    # {
    #   "FAMILIARES": {"packs": 4, "boxes": 0, "units": 24}
    # }
    return {
        "packs":
            int(
                category_values.get(
                    "packs",
                    0,
                )
                or 0
            ),

        "boxes":
            int(
                category_values.get(
                    "boxes",
                    0,
                )
                or 0
            ),

        "units":
            int(
                category_values.get(
                    "units",
                    0,
                )
                or 0
            ),
    }


def product_snapshot_category_products(
    category_values,
):
    if not isinstance(
        category_values,
        dict,
    ):
        return {}

    products = category_values.get(
        "products",
        {}
    )

    return (
        products
        if isinstance(
            products,
            dict,
        )
        else {}
    )


def product_snapshot_display_value(
    category,
    values,
):
    """
    Muestra solo niveles físicos que aplican a la categoría.

    PACK_UNIDADES:
        packs + units

    PACK_CAJAS_UNIDADES:
        packs + boxes + units

    CAJA_UNIDADES:
        boxes + units
    """

    values = product_snapshot_category_totals(
        values
    )

    packs = values[
        "packs"
    ]

    boxes = values[
        "boxes"
    ]

    units = values[
        "units"
    ]

    category = str(
        category
    ).upper()

    if category in {
        "BOMBONES",
        "POSTRES",
        "PALITOS",
    }:
        return (
            f"{packs} packs · "
            f"{boxes} cajas · "
            f"{units} unidades"
        )

    if category in {
        "FAMILIARES",
        "TENTACIONES",
        "TORTAS",
    }:
        return (
            f"{packs} packs · "
            f"{units} unidades"
        )

    if category in {
        "LINEAS_ESPECIALES",
        "FRIZZIO",
    }:
        return (
            f"{boxes} cajas · "
            f"{units} unidades"
        )

    parts = []

    if packs:
        parts.append(
            f"{packs} packs"
        )

    if boxes:
        parts.append(
            f"{boxes} cajas"
        )

    parts.append(
        f"{units} unidades"
    )

    return " · ".join(
        parts
    )


def product_snapshot_units_total(
    snapshot,
):
    if not isinstance(
        snapshot,
        dict,
    ):
        return 0

    return int(
        sum(
            product_snapshot_category_totals(
                category_values
            )[
                "units"
            ]
            for category_values
            in snapshot.values()
        )
    )



# ============================================================
# WEEK SALON COUNT SNAPSHOTS
# ============================================================

def _snapshot_number(
    value,
):
    number = pd.to_numeric(
        pd.Series(
            [
                value
            ]
        ),
        errors="coerce",
    ).iloc[0]

    if pd.isna(
        number
    ):
        return None

    return round(
        float(
            number
        ),
        3,
    )


def build_salon_snapshot(
    rows,
    *,
    count_id=None,
    count_type=None,
    timestamp=None,
):
    """
    Snapshot histórico autosuficiente del salón.

    Guarda una fila por lata física con:
    stock_id, sabor, estado, bruto, tara y neto.

    Acepta tanto filas de inventory_counts.csv como filas del stock vivo.
    """

    if rows is None:
        rows = pd.DataFrame()

    rows = rows.copy()

    # Si recibimos el stock combinado, conservar solo el salón activo.
    if (
        not rows.empty
        and "location" in rows.columns
    ):
        rows = rows[
            rows[
                "location"
            ]
            .fillna("")
            .astype(str)
            .str.upper()
            .eq(
                "SALON"
            )
        ].copy()

    if (
        not rows.empty
        and "active" in rows.columns
    ):
        active_mask = (
            rows[
                "active"
            ]
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

        rows = rows[
            active_mask
        ].copy()

    def first_value(
        row,
        candidates,
    ):
        for column in candidates:
            if column not in row.index:
                continue

            value = row.get(
                column
            )

            if pd.notna(
                value
            ):
                return value

        return None

    latas = []

    for _, row in rows.iterrows():

        stock_id = first_value(
            row,
            [
                "stock_id",
                "salon_stock_id",
            ],
        )

        sabor = first_value(
            row,
            [
                "sabor",
            ],
        )

        estado = first_value(
            row,
            [
                "estado",
                "estado_lata",
            ],
        )

        bruto = _snapshot_number(
            first_value(
                row,
                [
                    "peso_bruto_kg",
                    "peso_actual_bruto_kg",
                    "peso_inicial_bruto_kg",
                ],
            )
        )

        tara = _snapshot_number(
            first_value(
                row,
                [
                    "tara_kg",
                    "tara_actual_kg",
                    "tara_inicial_kg",
                ],
            )
        )

        neto = _snapshot_number(
            first_value(
                row,
                [
                    "peso_neto_kg",
                    "peso_actual_neto_kg",
                    "peso_inicial_neto_kg",
                ],
            )
        )

        # Si neto no está persistido pero bruto/tara sí, lo derivamos.
        if (
            neto is None
            and bruto is not None
            and tara is not None
            and bruto >= tara
        ):
            neto = round(
                bruto - tara,
                3,
            )

        latas.append(
            {
                "stock_id":
                    (
                        str(
                            stock_id
                        )
                        if stock_id is not None
                        else ""
                    ),

                "sabor":
                    (
                        str(
                            sabor
                        )
                        if sabor is not None
                        else ""
                    ),

                "estado":
                    (
                        str(
                            estado
                        ).upper()
                        if estado is not None
                        else ""
                    ),

                "peso_bruto_kg":
                    bruto,

                "tara_kg":
                    tara,

                "peso_neto_kg":
                    neto,
            }
        )

    latas = sorted(
        latas,
        key=lambda item:
            (
                item.get(
                    "stock_id",
                    ""
                ),
                item.get(
                    "sabor",
                    ""
                ),
            ),
    )

    abiertas = sum(
        1
        for lata in latas
        if lata.get(
            "estado"
        ) == "ABIERTA"
    )

    cerradas = sum(
        1
        for lata in latas
        if lata.get(
            "estado"
        ) == "CERRADA"
    )

    netos = [
        lata[
            "peso_neto_kg"
        ]
        for lata in latas
        if lata.get(
            "peso_neto_kg"
        ) is not None
    ]

    total_neto = round(
        float(
            sum(
                netos
            )
        ),
        3,
    )

    snapshot = {
        "count_id":
            (
                str(
                    count_id
                )
                if count_id
                else None
            ),

        "count_type":
            (
                str(
                    count_type
                )
                if count_type
                else None
            ),

        "timestamp":
            (
                str(
                    timestamp
                )
                if timestamp
                else None
            ),

        "totals": {
            "latas":
                len(
                    latas
                ),

            "abiertas":
                abiertas,

            "cerradas":
                cerradas,

            "peso_neto_kg":
                total_neto,
        },

        "latas":
            latas,
    }

    return snapshot


def salon_snapshot_to_json(
    snapshot,
):
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
        sort_keys=True,
    )


def salon_snapshot_from_json(
    value,
):
    if value is None:
        return {}

    try:
        if pd.isna(
            value
        ):
            return {}
    except Exception:
        pass

    if isinstance(
        value,
        dict,
    ):
        return value

    raw = str(
        value
    ).strip()

    if (
        not raw
        or raw.lower()
        in {
            "nan",
            "none",
            "<na>",
        }
    ):
        return {}

    try:
        parsed = json.loads(
            raw
        )

        return (
            parsed
            if isinstance(
                parsed,
                dict,
            )
            else {}
        )

    except Exception:
        return {}


def salon_snapshot_from_count(
    counts_df,
    count_id,
):
    if (
        counts_df is None
        or counts_df.empty
        or not count_id
        or "count_id" not in counts_df.columns
    ):
        return {}

    count_rows = counts_df[
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

    if count_rows.empty:
        return {}

    if "location" in count_rows.columns:
        count_rows = count_rows[
            count_rows[
                "location"
            ]
            .fillna("")
            .astype(str)
            .str.upper()
            .eq(
                "SALON"
            )
        ].copy()

    count_type = None
    timestamp = None

    if (
        not count_rows.empty
        and "count_type" in count_rows.columns
    ):
        values = (
            count_rows[
                "count_type"
            ]
            .dropna()
            .astype(str)
        )

        if not values.empty:
            count_type = values.iloc[0]

    if (
        not count_rows.empty
        and "timestamp" in count_rows.columns
    ):
        values = (
            count_rows[
                "timestamp"
            ]
            .dropna()
            .astype(str)
        )

        if not values.empty:
            timestamp = values.iloc[0]

    return build_salon_snapshot(
        count_rows,
        count_id=count_id,
        count_type=count_type,
        timestamp=timestamp,
    )


# ============================================================
# WEEKS
# ============================================================

def load_weeks():
    df = load_csv(
        WEEKS_FILE
    )

    if df.empty:
        return pd.DataFrame(
            columns=WEEK_COLUMNS
        )

    numeric_columns = [
        "start_stock_kg",
        "start_salon_latas",
        "start_salon_kg",
        "start_camera_latas",
        "start_camera_kg",
        "current_salon_latas",
        "current_salon_kg",
        "current_camera_latas",
        "current_camera_kg",
        "end_stock_kg",
        "end_salon_latas",
        "end_salon_kg",
        "end_camera_latas",
        "end_camera_kg",
        "camera_to_salon_latas",
        "camera_to_salon_kg",
        "ingreso_camera_latas",
        "ingreso_camera_kg",
        "latas_abiertas",
        "latas_terminadas",
        "cambios_sabor",
        "recambios",
        "latas_con_tara_final",
        "tara_final_total_kg",
        "residuo_estimado_kg",
        "consumo_fisico_kg",
        "consumo_teorico_kg",
        "merma_kg",
        "merma_pct",
        "merma_no_explicada_kg",

        # Merma / peso nominal persistido
        *WEEK_MERMA_ANALYTICS_COLUMNS,

        "metadata_version",
    ]

    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce",
            )

    return df


def get_open_week():
    weeks = load_weeks()

    if weeks.empty:
        return None

    opened = weeks[
        weeks["status"]
        .astype(str)
        .str.upper()
        .eq("OPEN")
    ]

    if opened.empty:
        return None

    return opened.iloc[-1]


def calculate_week_merma_analytics(
    week_row,
    movements_df,
    counts_df,
):
    """
    Calcula las métricas analíticas de una Week a partir de las fuentes
    granulares (movimientos + conteos).

    La Week persiste el resultado consolidado para que una semana histórica
    conserve exactamente sus métricas y pueda compararse Week contra Week.
    """

    result = {
        column: pd.NA
        for column in WEEK_MERMA_ANALYTICS_COLUMNS
    }

    week_id = str(
        week_row.get(
            "week_id",
            ""
        )
        or ""
    ).strip()

    if not week_id:
        return result

    # --------------------------------------------------------
    # Movimientos de esta Week
    # --------------------------------------------------------
    week_movements = (
        movements_df.copy()
        if movements_df is not None
        else pd.DataFrame()
    )

    if (
        week_movements.empty
        or "week_id" not in week_movements.columns
    ):
        week_movements = pd.DataFrame()

    else:
        week_movements = week_movements[
            week_movements[
                "week_id"
            ]
            .astype(str)
            .eq(
                week_id
            )
        ].copy()

    movement_types = (
        week_movements[
            "movement_type"
        ]
        .fillna("")
        .astype(str)
        .str.upper()
        if (
            not week_movements.empty
            and "movement_type" in week_movements.columns
        )
        else pd.Series(
            index=week_movements.index,
            dtype=str,
        )
    )

    # --------------------------------------------------------
    # Tara final promedio observada
    # --------------------------------------------------------
    avg_final_tare_kg = None

    exhausted = (
        week_movements[
            movement_types.eq(
                "LATA_AGOTADA"
            )
        ].copy()
        if not week_movements.empty
        else pd.DataFrame()
    )

    if (
        not exhausted.empty
        and "tara_final_kg" in exhausted.columns
    ):
        tare_values = pd.to_numeric(
            exhausted[
                "tara_final_kg"
            ],
            errors="coerce",
        ).dropna()

        if not tare_values.empty:
            avg_final_tare_kg = float(
                tare_values.mean()
            )

            result[
                "avg_final_tare_kg"
            ] = avg_final_tare_kg

    # --------------------------------------------------------
    # Salidas Cámara → Salón
    # --------------------------------------------------------
    camera_exits = (
        week_movements[
            movement_types.eq(
                "CAMARA_A_SALON"
            )
        ].copy()
        if not week_movements.empty
        else pd.DataFrame()
    )

    gross_values = pd.Series(
        dtype=float
    )

    if (
        not camera_exits.empty
        and "peso_bruto_kg" in camera_exits.columns
    ):
        gross_values = pd.to_numeric(
            camera_exits[
                "peso_bruto_kg"
            ],
            errors="coerce",
        ).dropna()

    camera_exit_count = int(
        len(
            gross_values
        )
    )

    if camera_exit_count > 0:
        camera_exit_gross_kg = float(
            gross_values.sum()
        )

        camera_exit_gross_avg_kg = float(
            gross_values.mean()
        )

        result[
            "camera_exit_gross_kg"
        ] = camera_exit_gross_kg

        result[
            "camera_exit_gross_avg_kg"
        ] = camera_exit_gross_avg_kg

        if avg_final_tare_kg is not None:
            estimated_camera_exit_net_kg = max(
                0.0,
                camera_exit_gross_kg
                - (
                    camera_exit_count
                    * avg_final_tare_kg
                ),
            )

            estimated_net_avg_per_lata_kg = max(
                0.0,
                camera_exit_gross_avg_kg
                - avg_final_tare_kg,
            )

            camera_nominal_expected_kg = (
                camera_exit_count
                * GRIDO_NOMINAL_NET_KG
            )

            camera_nominal_deficit_kg = (
                estimated_camera_exit_net_kg
                - camera_nominal_expected_kg
            )

            camera_nominal_deficit_per_lata_kg = (
                camera_nominal_deficit_kg
                / camera_exit_count
            )

            camera_nominal_deficit_pct = (
                camera_nominal_deficit_kg
                / camera_nominal_expected_kg
                * 100.0
                if camera_nominal_expected_kg > 0
                else None
            )

            result.update(
                {
                    "estimated_camera_exit_net_kg":
                        estimated_camera_exit_net_kg,

                    "estimated_net_avg_per_lata_kg":
                        estimated_net_avg_per_lata_kg,

                    "camera_nominal_expected_kg":
                        camera_nominal_expected_kg,

                    "camera_nominal_deficit_kg":
                        camera_nominal_deficit_kg,

                    "camera_nominal_deficit_per_lata_kg":
                        camera_nominal_deficit_per_lata_kg,

                    "camera_nominal_deficit_pct":
                        camera_nominal_deficit_pct,
                }
            )

    # Sin tara final todavía no podemos estimar el neto nominal.
    if avg_final_tare_kg is None:
        return result

    # --------------------------------------------------------
    # Universo nominal:
    # A) cerradas al inicio
    # B) salidas desde cámara durante la Week
    # --------------------------------------------------------
    nominal_sources = []

    start_count_id = str(
        week_row.get(
            "start_count_id",
            ""
        )
        or ""
    ).strip()

    counts = (
        counts_df.copy()
        if counts_df is not None
        else pd.DataFrame()
    )

    if (
        start_count_id
        and not counts.empty
        and "count_id" in counts.columns
    ):
        initial_closed = counts[
            counts[
                "count_id"
            ]
            .astype(str)
            .eq(
                start_count_id
            )
        ].copy()

        if "count_type" in initial_closed.columns:
            initial_closed = initial_closed[
                initial_closed[
                    "count_type"
                ]
                .fillna("")
                .astype(str)
                .str.upper()
                .eq(
                    "INICIO_SEMANA"
                )
            ].copy()

        if "estado" in initial_closed.columns:
            initial_closed = initial_closed[
                initial_closed[
                    "estado"
                ]
                .fillna("")
                .astype(str)
                .str.upper()
                .eq(
                    "CERRADA"
                )
            ].copy()

        if (
            not initial_closed.empty
            and "peso_bruto_kg" in initial_closed.columns
        ):
            initial_closed[
                "peso_bruto_kg"
            ] = pd.to_numeric(
                initial_closed[
                    "peso_bruto_kg"
                ],
                errors="coerce",
            )

            initial_closed = initial_closed[
                initial_closed[
                    "peso_bruto_kg"
                ].notna()
            ].copy()

            if not initial_closed.empty:
                nominal_sources.append(
                    pd.DataFrame(
                        {
                            "analysis_stock_id":
                                initial_closed.get(
                                    "stock_id",
                                    pd.Series(
                                        index=initial_closed.index,
                                        dtype=object,
                                    ),
                                ),

                            "peso_bruto_kg":
                                initial_closed[
                                    "peso_bruto_kg"
                                ],

                            "origen":
                                "CERRADA_INICIO",
                        }
                    )
                )

    if (
        not camera_exits.empty
        and "peso_bruto_kg" in camera_exits.columns
    ):
        camera_nominal = camera_exits.copy()

        camera_nominal[
            "peso_bruto_kg"
        ] = pd.to_numeric(
            camera_nominal[
                "peso_bruto_kg"
            ],
            errors="coerce",
        )

        camera_nominal = camera_nominal[
            camera_nominal[
                "peso_bruto_kg"
            ].notna()
        ].copy()

        if not camera_nominal.empty:
            if "target_stock_id" in camera_nominal.columns:
                analysis_ids = (
                    camera_nominal[
                        "target_stock_id"
                    ]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

                if "source_stock_id" in camera_nominal.columns:
                    source_ids = (
                        camera_nominal[
                            "source_stock_id"
                        ]
                        .fillna("")
                        .astype(str)
                        .str.strip()
                    )

                    analysis_ids = analysis_ids.where(
                        analysis_ids.ne(""),
                        source_ids,
                    )

            elif "source_stock_id" in camera_nominal.columns:
                analysis_ids = (
                    camera_nominal[
                        "source_stock_id"
                    ]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

            else:
                analysis_ids = pd.Series(
                    [
                        f"CAMERA_EXIT_{i}"
                        for i in range(
                            len(
                                camera_nominal
                            )
                        )
                    ],
                    index=camera_nominal.index,
                )

            nominal_sources.append(
                pd.DataFrame(
                    {
                        "analysis_stock_id":
                            analysis_ids,

                        "peso_bruto_kg":
                            camera_nominal[
                                "peso_bruto_kg"
                            ],

                        "origen":
                            "DESDE_CAMARA",
                    }
                )
            )

    if not nominal_sources:
        return result

    nominal = pd.concat(
        nominal_sources,
        ignore_index=True,
    )

    nominal[
        "analysis_stock_id"
    ] = (
        nominal[
            "analysis_stock_id"
        ]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # Deduplicación solamente cuando tenemos un ID físico confiable.
    with_id = nominal[
        nominal[
            "analysis_stock_id"
        ].ne("")
    ].drop_duplicates(
        subset=[
            "analysis_stock_id"
        ],
        keep="first",
    )

    without_id = nominal[
        nominal[
            "analysis_stock_id"
        ].eq("")
    ]

    nominal = pd.concat(
        [
            with_id,
            without_id,
        ],
        ignore_index=True,
    )

    if nominal.empty:
        return result

    nominal[
        "neto_estimado_kg"
    ] = (
        nominal[
            "peso_bruto_kg"
        ]
        - avg_final_tare_kg
    )

    nominal[
        "desvio_vs_nominal_kg"
    ] = (
        nominal[
            "neto_estimado_kg"
        ]
        - GRIDO_NOMINAL_NET_KG
    )

    nominal[
        "estado_nominal"
    ] = nominal[
        "desvio_vs_nominal_kg"
    ].apply(
        lambda value:
            "EXCEDENTE"
            if value > GRIDO_NOMINAL_TOLERANCE_KG
            else (
                "DEFICIT"
                if value < -GRIDO_NOMINAL_TOLERANCE_KG
                else "EN_RANGO"
            )
    )

    analyzed_count = int(
        len(
            nominal
        )
    )

    initial_count = int(
        (
            nominal[
                "origen"
            ]
            == "CERRADA_INICIO"
        ).sum()
    )

    from_camera_count = int(
        (
            nominal[
                "origen"
            ]
            == "DESDE_CAMARA"
        ).sum()
    )

    in_range_count = int(
        nominal[
            "estado_nominal"
        ]
        .isin(
            [
                "EN_RANGO",
                "EXCEDENTE",
            ]
        )
        .sum()
    )

    deficit_count = int(
        (
            nominal[
                "estado_nominal"
            ]
            == "DEFICIT"
        ).sum()
    )

    deviations = nominal[
        "desvio_vs_nominal_kg"
    ]

    deficits = deviations[
        deviations < 0
    ]

    excesses = deviations[
        deviations > 0
    ]

    nominal_expected_total_kg = (
        analyzed_count
        * GRIDO_NOMINAL_NET_KG
    )

    nominal_estimated_total_kg = float(
        nominal[
            "neto_estimado_kg"
        ].sum()
    )

    nominal_deficit_total_kg = (
        float(
            -deficits.sum()
        )
        if not deficits.empty
        else 0.0
    )

    nominal_excess_total_kg = (
        float(
            excesses.sum()
        )
        if not excesses.empty
        else 0.0
    )

    nominal_balance_total_kg = float(
        deviations.sum()
    )

    nominal_avg_deviation_kg = float(
        deviations.mean()
    )

    nominal_avg_deviation_pct = (
        nominal_avg_deviation_kg
        / GRIDO_NOMINAL_NET_KG
        * 100.0
    )

    nominal_in_range_pct = (
        in_range_count
        / analyzed_count
        * 100.0
        if analyzed_count > 0
        else None
    )

    result.update(
        {
            "nominal_reference_kg":
                GRIDO_NOMINAL_NET_KG,

            "nominal_analyzed_latas":
                analyzed_count,

            "nominal_initial_closed_latas":
                initial_count,

            "nominal_camera_latas":
                from_camera_count,

            "nominal_expected_total_kg":
                nominal_expected_total_kg,

            "nominal_estimated_total_kg":
                nominal_estimated_total_kg,

            "nominal_in_range_latas":
                in_range_count,

            "nominal_deficit_latas":
                deficit_count,

            "nominal_in_range_pct":
                nominal_in_range_pct,

            "nominal_deficit_total_kg":
                nominal_deficit_total_kg,

            "nominal_excess_total_kg":
                nominal_excess_total_kg,

            "nominal_balance_total_kg":
                nominal_balance_total_kg,

            "nominal_avg_deviation_kg":
                nominal_avg_deviation_kg,

            "nominal_avg_deviation_pct":
                nominal_avg_deviation_pct,
        }
    )

    # Si la Week ya tiene merma final calculada, traducimos a latas equivalentes.
    merma_value = pd.to_numeric(
        pd.Series(
            [
                week_row.get(
                    "merma_kg",
                    pd.NA,
                )
            ]
        ),
        errors="coerce",
    ).iloc[0]

    estimated_avg = result.get(
        "estimated_net_avg_per_lata_kg",
        pd.NA,
    )

    if (
        pd.notna(
            merma_value
        )
        and pd.notna(
            estimated_avg
        )
        and float(
            estimated_avg
        ) > 0
    ):
        result[
            "merma_latas_equivalentes"
        ] = (
            float(
                merma_value
            )
            / float(
                estimated_avg
            )
        )

    return result


def refresh_all_metadata(
    show_result=False,
):
    """
    Migra/refresca datos existentes SIN recrear inventario ni semanas.

    1. Recalcula Week desde weeks.csv + movements + counts + stock.
    2. Backfillea metadata de Lata desde movimientos cuando stock_id
       es único y por lo tanto la asociación es segura.
    3. Nunca modifica filas ambiguas con IDs duplicados.
    """

    stock = load_current_stock()
    movements = load_csv(
        MOVEMENTS_FILE
    )
    counts = load_csv(
        COUNTS_FILE
    )
    weeks = load_weeks()

    movements, residue_report = (
        repair_missing_estimated_residue(
            stock,
            movements,
        )
    )

    if (
        residue_report[
            "movements_repaired"
        ]
        > 0
    ):
        safe_write_csv(
            movements,
            MOVEMENTS_FILE,
        )

    migrated_stock, lata_report = (
        backfill_lata_metadata_from_movements(
            stock,
            movements,
        )
    )

    stock_changed = not (
        migrated_stock.fillna("")
        .astype(str)
        .equals(
            stock.fillna("")
            .astype(str)
        )
    )

    if stock_changed:
        save_current_stock(
            migrated_stock
        )

        stock = migrated_stock

    refreshed_weeks = (
        refresh_weeks_dataframe(
            weeks,
            stock_df=stock,
            movements_df=movements,
            counts_df=counts,
            now_iso=now_iso(),
        )
    )

    # --------------------------------------------------------
    # Persisted Week merma / nominal analytics
    # --------------------------------------------------------

    for analytics_column in WEEK_MERMA_ANALYTICS_COLUMNS:
        if analytics_column not in refreshed_weeks.columns:
            refreshed_weeks[
                analytics_column
            ] = pd.NA

    for idx, refreshed_row in refreshed_weeks.iterrows():
        analytics = calculate_week_merma_analytics(
            refreshed_row,
            movements_df=movements,
            counts_df=counts,
        )

        for column, value in analytics.items():
            refreshed_weeks.loc[
                idx,
                column,
            ] = value

    # --------------------------------------------------------
    # Salon start/end snapshots
    # --------------------------------------------------------
    # Son snapshots históricos congelados. Nunca se reconstruyen desde
    # el stock actual si ya existen. Para Weeks antiguas, si faltan,
    # se backfillean desde start_count_id / end_count_id.
    # --------------------------------------------------------

    for snapshot_column in WEEK_SALON_SNAPSHOT_COLUMNS:
        if snapshot_column not in refreshed_weeks.columns:
            refreshed_weeks[
                snapshot_column
            ] = pd.NA

    existing_salon_snapshots = {}

    if not weeks.empty:
        for _, existing_week in weeks.iterrows():
            existing_salon_snapshots[
                str(
                    existing_week.get(
                        "week_id",
                        ""
                    )
                )
            ] = {
                "start":
                    existing_week.get(
                        "start_salon_snapshot_json",
                        pd.NA,
                    ),

                "end":
                    existing_week.get(
                        "end_salon_snapshot_json",
                        pd.NA,
                    ),
            }

    for idx, refreshed_row in refreshed_weeks.iterrows():
        week_id = str(
            refreshed_row.get(
                "week_id",
                ""
            )
        )

        existing = existing_salon_snapshots.get(
            week_id,
            {},
        )

        existing_start = existing.get(
            "start",
            pd.NA,
        )

        existing_end = existing.get(
            "end",
            pd.NA,
        )

        if pd.notna(
            existing_start
        ):
            refreshed_weeks.loc[
                idx,
                "start_salon_snapshot_json",
            ] = existing_start

        else:
            start_snapshot = salon_snapshot_from_count(
                counts,
                refreshed_row.get(
                    "start_count_id"
                ),
            )

            if start_snapshot:
                refreshed_weeks.loc[
                    idx,
                    "start_salon_snapshot_json",
                ] = salon_snapshot_to_json(
                    start_snapshot
                )

        if pd.notna(
            existing_end
        ):
            refreshed_weeks.loc[
                idx,
                "end_salon_snapshot_json",
            ] = existing_end

        else:
            end_snapshot = salon_snapshot_from_count(
                counts,
                refreshed_row.get(
                    "end_count_id"
                ),
            )

            if end_snapshot:
                refreshed_weeks.loc[
                    idx,
                    "end_salon_snapshot_json",
                ] = salon_snapshot_to_json(
                    end_snapshot
                )

    # --------------------------------------------------------
    # Product snapshots are owned by the app because they come
    # from camera_products.csv, not from the granel week service.
    # Preserve historical start/end snapshots and refresh only
    # the current snapshot of OPEN weeks.
    # --------------------------------------------------------

    current_products_snapshot_json = (
        products_snapshot_to_json(
            build_camera_products_snapshot()
        )
    )

    for snapshot_column in WEEK_PRODUCT_SNAPSHOT_COLUMNS:
        if snapshot_column not in refreshed_weeks.columns:
            refreshed_weeks[
                snapshot_column
            ] = pd.NA

    if not weeks.empty:
        existing_snapshots = weeks.copy()

        for snapshot_column in WEEK_PRODUCT_SNAPSHOT_COLUMNS:
            if snapshot_column not in existing_snapshots.columns:
                existing_snapshots[
                    snapshot_column
                ] = pd.NA

        existing_snapshots = (
            existing_snapshots[
                [
                    "week_id",
                    *WEEK_PRODUCT_SNAPSHOT_COLUMNS,
                ]
            ]
            .drop_duplicates(
                subset=[
                    "week_id"
                ],
                keep="last",
            )
            .set_index(
                "week_id"
            )
        )

        for idx, refreshed_row in refreshed_weeks.iterrows():
            week_id = str(
                refreshed_row.get(
                    "week_id",
                    ""
                )
            )

            if week_id in existing_snapshots.index:
                for snapshot_column in [
                    "start_products_snapshot_json",
                    "end_products_snapshot_json",
                ]:
                    existing_value = existing_snapshots.loc[
                        week_id,
                        snapshot_column,
                    ]

                    if pd.notna(
                        existing_value
                    ):
                        refreshed_weeks.loc[
                            idx,
                            snapshot_column,
                        ] = existing_value

                existing_current = existing_snapshots.loc[
                    week_id,
                    "current_products_snapshot_json",
                ]

                if (
                    str(
                        refreshed_row.get(
                            "status",
                            ""
                        )
                    ).upper()
                    == "CLOSED"
                    and pd.notna(
                        existing_current
                    )
                ):
                    refreshed_weeks.loc[
                        idx,
                        "current_products_snapshot_json",
                    ] = existing_current

    open_mask = (
        refreshed_weeks[
            "status"
        ]
        .astype(str)
        .str.upper()
        .eq(
            "OPEN"
        )
    )

    refreshed_weeks.loc[
        open_mask,
        "current_products_snapshot_json",
    ] = current_products_snapshot_json

    weeks_changed = not (
        refreshed_weeks.fillna("")
        .astype(str)
        .equals(
            weeks.reindex(
                columns=WEEK_COLUMNS
            )
            .fillna("")
            .astype(str)
        )
    )

    if weeks_changed:
        safe_write_csv(
            refreshed_weeks,
            WEEKS_FILE,
            allow_empty=True,
        )

    report = {
        "weeks_changed":
            weeks_changed,

        "stock_changed":
            stock_changed,

        "latas_metadata_updated":
            lata_report[
                "updated_rows"
            ],

        "ambiguous_stock_ids":
            lata_report[
                "skipped_ambiguous_ids"
            ],

        "estimated_residue_movements_repaired":
            residue_report[
                "movements_repaired"
            ],
    }

    if show_result:
        return report

    return report


def create_week(
    start_count_id,
    start_stock_kg,
    timestamp,
    notes="",
):
    current = get_open_week()

    if current is not None:
        raise ValueError(
            f"Ya existe una semana abierta: "
            f"{current['week_id']}"
        )

    week_id = generate_id(
        "WEEK"
    )

    stock = load_current_stock()

    snapshot = current_stock_snapshot(
        stock
    )

    products_snapshot_json = (
        products_snapshot_to_json(
            build_camera_products_snapshot()
        )
    )

    start_salon_snapshot_json = (
        salon_snapshot_to_json(
            build_salon_snapshot(
                stock,
                count_id=start_count_id,
                count_type="INICIO_SEMANA",
                timestamp=timestamp,
            )
        )
    )

    row = {
        column:
            pd.NA
        for column in WEEK_COLUMNS
    }

    row.update(
        {
            "week_id":
                week_id,

            "status":
                "OPEN",

            "started_at":
                timestamp,

            "closed_at":
                pd.NA,

            "start_count_id":
                start_count_id,

            "end_count_id":
                pd.NA,

            "start_stock_kg":
                round(
                    float(
                        start_stock_kg
                    ),
                    3,
                ),

            "start_salon_latas":
                snapshot[
                    "salon_latas"
                ],

            "start_salon_kg":
                round(
                    float(
                        start_stock_kg
                    ),
                    3,
                ),

            "start_camera_latas":
                snapshot[
                    "camera_latas"
                ],

            "start_camera_kg":
                snapshot[
                    "camera_kg"
                ],

            "current_salon_latas":
                snapshot[
                    "salon_latas"
                ],

            "current_salon_kg":
                snapshot[
                    "salon_kg"
                ],

            "current_camera_latas":
                snapshot[
                    "camera_latas"
                ],

            "current_camera_kg":
                snapshot[
                    "camera_kg"
                ],

            "metadata_version":
                WEEK_METADATA_VERSION,

            "metadata_refreshed_at":
                timestamp,

            "start_salon_snapshot_source":
                "LIVE_START_COUNT",

            "start_camera_snapshot_source":
                "LIVE_START_SNAPSHOT",

            "start_salon_snapshot_json":
                start_salon_snapshot_json,

            "end_salon_snapshot_json":
                pd.NA,

            "start_products_snapshot_json":
                products_snapshot_json,

            "current_products_snapshot_json":
                products_snapshot_json,

            "end_products_snapshot_json":
                pd.NA,

            "notes":
                notes,
        }
    )

    append_row(
        WEEKS_FILE,
        row,
    )

    return week_id


def close_week(
    week_id,
    *,
    end_count_id,
    end_stock_kg,
    timestamp=None,
):
    """
    Congela una Week DESPUÉS de haber guardado el conteo físico final.

    El conteo final del salón es la fuente de verdad para:
        - end_count_id
        - end_stock_kg
        - end_salon_latas
        - end_salon_kg

    Cámara se congela con el snapshot vivo del mismo instante.
    """

    timestamp = (
        timestamp
        or now_iso()
    )

    weeks = load_weeks()

    mask = (
        weeks[
            "week_id"
        ]
        .astype(str)
        .eq(
            str(
                week_id
            )
        )
    )

    if not mask.any():
        raise ValueError(
            "No se encontró la semana."
        )

    idx = weeks[
        mask
    ].index[0]

    status = str(
        weeks.loc[
            idx,
            "status"
        ]
    ).upper()

    if status != "OPEN":
        raise ValueError(
            "La semana ya está cerrada."
        )

    stock = load_current_stock()

    movements = load_csv(
        MOVEMENTS_FILE
    )

    counts = load_csv(
        COUNTS_FILE
    )

    # Recalcular todavía como OPEN para congelar toda la actividad
    # ocurrida hasta el instante del cierre.
    refreshed_weeks = (
        refresh_weeks_dataframe(
            weeks,
            stock_df=
                stock,

            movements_df=
                movements,

            counts_df=
                counts,

            now_iso=
                timestamp,
        )
    )

    mask = (
        refreshed_weeks[
            "week_id"
        ]
        .astype(str)
        .eq(
            str(
                week_id
            )
        )
    )

    idx = refreshed_weeks[
        mask
    ].index[0]

    snapshot = current_stock_snapshot(
        stock
    )

    products_snapshot_json = (
        products_snapshot_to_json(
            build_camera_products_snapshot()
        )
    )

    final_count_rows = counts[
        counts[
            "count_id"
        ]
        .astype(str)
        .eq(
            str(
                end_count_id
            )
        )
    ].copy()

    if final_count_rows.empty:
        raise ValueError(
            "No se encontró el conteo final de la semana."
        )

    final_count_rows = final_count_rows[
        final_count_rows[
            "location"
        ]
        .astype(str)
        .str.upper()
        .eq(
            "SALON"
        )
    ].copy()

    end_salon_latas = int(
        len(
            final_count_rows
        )
    )

    end_salon_kg = round(
        float(
            pd.to_numeric(
                final_count_rows[
                    "peso_neto_kg"
                ],
                errors="coerce",
            )
            .fillna(0)
            .sum()
        ),
        3,
    )

    end_salon_snapshot_json = (
        salon_snapshot_to_json(
            build_salon_snapshot(
                final_count_rows,
                count_id=end_count_id,
                count_type="CIERRE_SEMANA",
                timestamp=timestamp,
            )
        )
    )

    # --------------------------------------------------------
    # Congelar Week
    # --------------------------------------------------------

    refreshed_weeks.loc[
        idx,
        "status"
    ] = "CLOSED"

    refreshed_weeks.loc[
        idx,
        "closed_at"
    ] = timestamp

    refreshed_weeks.loc[
        idx,
        "end_count_id"
    ] = end_count_id

    refreshed_weeks.loc[
        idx,
        "end_stock_kg"
    ] = round(
        float(
            end_stock_kg
        ),
        3,
    )

    refreshed_weeks.loc[
        idx,
        "end_salon_latas"
    ] = end_salon_latas

    refreshed_weeks.loc[
        idx,
        "end_salon_kg"
    ] = end_salon_kg

    refreshed_weeks.loc[
        idx,
        "end_camera_latas"
    ] = int(
        snapshot[
            "camera_latas"
        ]
    )

    refreshed_weeks.loc[
        idx,
        "end_camera_kg"
    ] = round(
        float(
            snapshot[
                "camera_kg"
            ]
        ),
        3,
    )

    # Una Week cerrada mantiene current_* como estado final congelado.
    refreshed_weeks.loc[
        idx,
        "current_salon_latas"
    ] = end_salon_latas

    refreshed_weeks.loc[
        idx,
        "current_salon_kg"
    ] = end_salon_kg

    refreshed_weeks.loc[
        idx,
        "current_camera_latas"
    ] = int(
        snapshot[
            "camera_latas"
        ]
    )

    refreshed_weeks.loc[
        idx,
        "current_camera_kg"
    ] = round(
        float(
            snapshot[
                "camera_kg"
            ]
        ),
        3,
    )

    # Conservamos estos valores para que week_service considere la Week
    # un snapshot histórico congelado.
    refreshed_weeks.loc[
        idx,
        "end_salon_snapshot_json"
    ] = end_salon_snapshot_json

    refreshed_weeks.loc[
        idx,
        "end_salon_snapshot_source"
    ] = "LIVE_CLOSE_SNAPSHOT"

    refreshed_weeks.loc[
        idx,
        "end_camera_snapshot_source"
    ] = "LIVE_CLOSE_SNAPSHOT"

    refreshed_weeks.loc[
        idx,
        "current_products_snapshot_json"
    ] = products_snapshot_json

    refreshed_weeks.loc[
        idx,
        "end_products_snapshot_json"
    ] = products_snapshot_json

    refreshed_weeks.loc[
        idx,
        "metadata_refreshed_at"
    ] = timestamp

    safe_write_csv(
        refreshed_weeks[
            WEEK_COLUMNS
        ],
        WEEKS_FILE,
    )

    return refreshed_weeks.loc[
        idx
    ].copy()


def close_week_with_final_count(
    *,
    week_id,
    edited_df,
    notes="",
):
    """
    Cierre semanal con reconciliación física del salón.

    Permite:
    - editar sabor;
    - editar estado ABIERTA/CERRADA;
    - editar peso bruto y tara;
    - marcar una lata existente para quitarla;
    - agregar nuevas filas sin stock_id.

    Luego:
    1. reconcilia el stock vivo;
    2. registra movimientos de ajuste;
    3. guarda COUNT CIERRE_SEMANA;
    4. cierra la Week;
    5. crea la Week siguiente;
    6. clona el snapshot como INICIO_SEMANA.
    """

    timestamp = now_iso()

    open_week = get_open_week()

    if open_week is None:
        raise ValueError(
            "No existe una semana abierta."
        )

    if str(open_week["week_id"]) != str(week_id):
        raise ValueError(
            "La semana seleccionada ya no es la semana abierta."
        )

    stock = load_current_stock()

    salon_active = stock[
        (
            stock["location"]
            .astype(str)
            .str.upper()
            .eq("SALON")
        )
        &
        (
            stock["active"] == True
        )
    ].copy()

    existing_ids = set(
        salon_active["stock_id"]
        .dropna()
        .astype(str)
        .tolist()
    )

    close_count_id = generate_id(
        "COUNT"
    )

    close_operation_id = generate_id(
        "AJUSTE-CIERRE-SALON"
    )

    close_rows = []

    total_stock_kg = 0.0
    abiertas = 0
    cerradas = 0
    abiertas_kg = 0.0
    cerradas_kg = 0.0

    added_ids = []
    removed_ids = []
    corrected_ids = []

    processed_existing_ids = set()

    for row_number, row in edited_df.iterrows():

        eliminar = bool(
            row.get(
                "eliminar",
                False,
            )
        )

        raw_stock_id = row.get(
            "stock_id",
            None,
        )

        stock_id = (
            str(raw_stock_id).strip()
            if pd.notna(raw_stock_id)
            else ""
        )

        sabor = normalize_flavor_name(
            row.get(
                "sabor",
                "",
            )
        )

        estado = str(
            row.get(
                "estado",
                "",
            )
        ).strip().upper()

        if eliminar and not stock_id:
            continue

        if stock_id:
            if stock_id not in existing_ids:
                raise ValueError(
                    f"Fila {row_number + 1}: "
                    f"{stock_id} no corresponde a una lata activa del salón."
                )

            if stock_id in processed_existing_ids:
                raise ValueError(
                    f"El stock_id {stock_id} aparece más de una vez."
                )

            processed_existing_ids.add(
                stock_id
            )

            mask = (
                stock["stock_id"]
                .astype(str)
                .eq(stock_id)
            )

            idx = stock[mask].index[0]

            old_sabor = normalize_flavor_name(
                stock.loc[idx, "sabor"]
            )

            old_estado = str(
                stock.loc[idx, "estado"]
            ).strip().upper()

            if eliminar:
                stock.loc[
                    idx,
                    "active",
                ] = False

                stock.loc[
                    idx,
                    "updated_at",
                ] = timestamp

                removed_ids.append(
                    stock_id
                )

                append_row(
                    MOVEMENTS_FILE,
                    {
                        "movement_id": generate_id("MOV"),
                        "operation_id": close_operation_id,
                        "timestamp": timestamp,
                        "week_id": week_id,
                        "movement_type": "AJUSTE_INVENTARIO_SALON_SALIDA",
                        "from_location": "SALON",
                        "to_location": "AJUSTE",
                        "source_stock_id": stock_id,
                        "target_stock_id": pd.NA,
                        "sabor": old_sabor,
                        "cantidad_latas": 1,
                        "peso_bruto_kg": pd.NA,
                        "tara_kg": pd.NA,
                        "peso_neto_kg": pd.NA,
                        "tara_final_kg": pd.NA,
                        "residuo_final_kg": pd.NA,
                        "notes": (
                            "Removida durante reconciliación del cierre semanal."
                            + (f" {notes}" if notes else "")
                        ),
                    },
                )

                continue

            if not sabor:
                raise ValueError(
                    f"{stock_id}: falta sabor."
                )

            if estado not in {
                "ABIERTA",
                "CERRADA",
            }:
                raise ValueError(
                    f"{stock_id}: estado inválido."
                )

            peso_bruto = pd.to_numeric(
                row.get("peso_bruto_kg"),
                errors="coerce",
            )

            tara = pd.to_numeric(
                row.get("tara_kg"),
                errors="coerce",
            )

            if pd.isna(peso_bruto):
                raise ValueError(
                    f"{stock_id}: falta peso bruto."
                )

            if pd.isna(tara):
                raise ValueError(
                    f"{stock_id}: falta tara."
                )

            peso_neto = calculate_net_weight(
                peso_bruto,
                tara,
            )

            stock.loc[idx, "sabor"] = sabor
            stock.loc[idx, "estado"] = estado
            stock.loc[idx, "peso_actual_bruto_kg"] = round(float(peso_bruto), 3)
            stock.loc[idx, "tara_actual_kg"] = round(float(tara), 3)
            stock.loc[idx, "peso_actual_neto_kg"] = peso_neto
            stock.loc[idx, "updated_at"] = timestamp

            if (
                sabor != old_sabor
                or estado != old_estado
            ):
                corrected_ids.append(
                    stock_id
                )

                append_row(
                    MOVEMENTS_FILE,
                    {
                        "movement_id": generate_id("MOV"),
                        "operation_id": close_operation_id,
                        "timestamp": timestamp,
                        "week_id": week_id,
                        "movement_type": "AJUSTE_INVENTARIO_SALON_CORRECCION",
                        "from_location": "SALON",
                        "to_location": "SALON",
                        "source_stock_id": stock_id,
                        "target_stock_id": stock_id,
                        "sabor": sabor,
                        "cantidad_latas": 1,
                        "peso_bruto_kg": round(float(peso_bruto), 3),
                        "tara_kg": round(float(tara), 3),
                        "peso_neto_kg": peso_neto,
                        "tara_final_kg": pd.NA,
                        "residuo_final_kg": pd.NA,
                        "notes": (
                            f"Corrección cierre semanal. "
                            f"old_sabor={old_sabor}; new_sabor={sabor}; "
                            f"old_estado={old_estado}; new_estado={estado}"
                            + (f"; {notes}" if notes else "")
                        ),
                    },
                )

        else:
            # Fila nueva encontrada físicamente durante el cierre.
            if eliminar:
                continue

            # Ignorar fila dinámica totalmente vacía.
            if not sabor:
                other_values = [
                    row.get("estado", None),
                    row.get("peso_bruto_kg", None),
                    row.get("tara_kg", None),
                ]

                if all(
                    pd.isna(v)
                    or str(v).strip() == ""
                    for v in other_values
                ):
                    continue

                raise ValueError(
                    f"Fila {row_number + 1}: falta sabor."
                )

            if estado not in {
                "ABIERTA",
                "CERRADA",
            }:
                raise ValueError(
                    f"Fila nueva {row_number + 1}: "
                    "seleccioná ABIERTA o CERRADA."
                )

            peso_bruto = pd.to_numeric(
                row.get("peso_bruto_kg"),
                errors="coerce",
            )

            tara = pd.to_numeric(
                row.get("tara_kg"),
                errors="coerce",
            )

            if pd.isna(peso_bruto):
                raise ValueError(
                    f"Fila nueva {row_number + 1}: falta peso bruto."
                )

            if pd.isna(tara):
                raise ValueError(
                    f"Fila nueva {row_number + 1}: falta tara."
                )

            peso_neto = calculate_net_weight(
                peso_bruto,
                tara,
            )

            new_stock_id = generate_salon_id(
                stock
            )

            lata = Lata.create_salon(
                stock_id=new_stock_id,
                sabor=sabor,
                estado=estado,
                timestamp=timestamp,
                peso_bruto_kg=peso_bruto,
                tara_kg=tara,
                peso_neto_kg=peso_neto,
                kg_referencia_lata=pd.NA,
                source_camera_stock_id=None,
                ingresada_salon_at=timestamp,
            )

            stock = pd.concat(
                [
                    stock,
                    pd.DataFrame(
                        [
                            lata.to_stock_row()
                        ]
                    ),
                ],
                ignore_index=True,
            )

            added_ids.append(
                new_stock_id
            )

            stock_id = new_stock_id

            append_row(
                MOVEMENTS_FILE,
                {
                    "movement_id": generate_id("MOV"),
                    "operation_id": close_operation_id,
                    "timestamp": timestamp,
                    "week_id": week_id,
                    "movement_type": "AJUSTE_INVENTARIO_SALON_ENTRADA",
                    "from_location": "AJUSTE",
                    "to_location": "SALON",
                    "source_stock_id": pd.NA,
                    "target_stock_id": new_stock_id,
                    "sabor": sabor,
                    "cantidad_latas": 1,
                    "peso_bruto_kg": round(float(peso_bruto), 3),
                    "tara_kg": round(float(tara), 3),
                    "peso_neto_kg": peso_neto,
                    "tara_final_kg": pd.NA,
                    "residuo_final_kg": pd.NA,
                    "notes": (
                        "Lata agregada durante reconciliación del cierre semanal."
                        + (f" {notes}" if notes else "")
                    ),
                },
            )

        close_rows.append(
            {
                "count_id": close_count_id,
                "week_id": week_id,
                "count_type": "CIERRE_SEMANA",
                "timestamp": timestamp,
                "location": "SALON",
                "stock_id": stock_id,
                "sabor": sabor,
                "estado": estado,
                "peso_bruto_kg": round(float(peso_bruto), 3),
                "tara_kg": round(float(tara), 3),
                "peso_neto_kg": peso_neto,
                "notes": notes,
            },
        )

        total_stock_kg += peso_neto

        if estado == "ABIERTA":
            abiertas += 1
            abiertas_kg += peso_neto
        else:
            cerradas += 1
            cerradas_kg += peso_neto

    unaccounted = (
        existing_ids
        - processed_existing_ids
    )

    if unaccounted:
        raise ValueError(
            "El conteo final no incluye estas latas existentes: "
            + ", ".join(
                sorted(unaccounted)[:10]
            )
        )

    if not close_rows:
        raise ValueError(
            "El conteo final no puede quedar vacío."
        )

    total_stock_kg = round(
        total_stock_kg,
        3,
    )

    abiertas_kg = round(
        abiertas_kg,
        3,
    )

    cerradas_kg = round(
        cerradas_kg,
        3,
    )

    save_current_stock(
        stock
    )

    counts = load_csv(
        COUNTS_FILE
    )

    counts = pd.concat(
        [
            counts,
            pd.DataFrame(
                close_rows
            ),
        ],
        ignore_index=True,
    )

    safe_write_csv(
        counts[
            COUNT_COLUMNS
        ],
        COUNTS_FILE,
        allow_empty=True,
    )

    closed_week = close_week(
        week_id=week_id,
        end_count_id=close_count_id,
        end_stock_kg=total_stock_kg,
        timestamp=timestamp,
    )

    next_start_count_id = generate_id(
        "COUNT"
    )

    next_week_id = create_week(
        start_count_id=next_start_count_id,
        start_stock_kg=total_stock_kg,
        timestamp=timestamp,
        notes=(
            f"Inicio automático desde cierre de {week_id}. "
            f"source_count_id={close_count_id}"
        ),
    )

    start_rows = []

    for close_row in close_rows:
        start_row = dict(
            close_row
        )

        start_row["count_id"] = next_start_count_id
        start_row["week_id"] = next_week_id
        start_row["count_type"] = "INICIO_SEMANA"
        start_row["timestamp"] = timestamp
        start_row["notes"] = (
            f"Inicio automático desde "
            f"{week_id}/{close_count_id}."
        )

        start_rows.append(
            start_row
        )

    counts = load_csv(
        COUNTS_FILE
    )

    counts = pd.concat(
        [
            counts,
            pd.DataFrame(
                start_rows
            ),
        ],
        ignore_index=True,
    )

    safe_write_csv(
        counts[
            COUNT_COLUMNS
        ],
        COUNTS_FILE,
        allow_empty=True,
    )

    refresh_all_metadata()

    return {
        "closed_week_id": week_id,
        "close_count_id": close_count_id,
        "next_week_id": next_week_id,
        "next_start_count_id": next_start_count_id,
        "timestamp": timestamp,
        "total_latas": len(close_rows),
        "abiertas": abiertas,
        "abiertas_kg": abiertas_kg,
        "cerradas": cerradas,
        "cerradas_kg": cerradas_kg,
        "total_stock_kg": total_stock_kg,
        "added_ids": added_ids,
        "removed_ids": removed_ids,
        "corrected_ids": corrected_ids,
        "end_camera_latas": int(
            closed_week["end_camera_latas"]
        ),
        "end_camera_kg": float(
            closed_week["end_camera_kg"]
        ),
    }


# ============================================================
# PRODUCT CATALOG
# ============================================================

def load_product_catalog(*,active_only=False):
    df=load_csv(PRODUCTS_FILE)
    if df.empty:return pd.DataFrame(columns=PRODUCT_COLUMNS)
    if "packaging_mode" not in df.columns: df["packaging_mode"]=PACKAGING_PACK_UNITS
    if "unidades_por_pack" not in df.columns: df["unidades_por_pack"]=df["unidades_por_bulto"] if "unidades_por_bulto" in df.columns else pd.NA
    if "cajas_por_pack" not in df.columns: df["cajas_por_pack"]=df["cajas_por_bulto"] if "cajas_por_bulto" in df.columns else pd.NA
    if "unidades_por_caja" not in df.columns: df["unidades_por_caja"]=pd.NA
    for col in PRODUCT_COLUMNS:
        if col not in df.columns: df[col]=pd.NA
    for col in ["cajas_por_pack","unidades_por_pack","unidades_por_caja"]: df[col]=pd.to_numeric(df[col],errors="coerce")
    df["active"]=df["active"].astype(str).str.strip().str.lower().isin(["true","1","yes"])
    if active_only: df=df[df["active"]==True].copy()
    return df[PRODUCT_COLUMNS].copy()


def generate_product_code(
    categoria,
):
    categoria = normalize_product_name(
        categoria
    )

    category_code = CATEGORY_CODES.get(
        categoria
    )

    if not category_code:
        raise ValueError(
            f"No hay código configurado para {categoria}."
        )

    catalog = load_product_catalog(
        active_only=False
    )

    existing = (
        catalog[
            "product_code"
        ]
        .dropna()
        .astype(str)
        .tolist()
        if (
            not catalog.empty
            and "product_code"
            in catalog.columns
        )
        else []
    )

    return next_sequential_id(
        existing,
        f"PROD-{category_code}",
    )


def add_catalog_product(*,categoria,subcategoria,producto,packaging_mode,cajas_por_pack,unidades_por_pack,unidades_por_caja):
    timestamp=now_iso(); catalog=load_product_catalog(active_only=False)
    categoria=normalize_product_name(categoria); producto=normalize_product_name(producto); subcategoria_normalizada=normalize_product_name(subcategoria) if subcategoria else None
    if not catalog.empty:
        same=catalog[(catalog["categoria"].astype(str)==categoria)&(catalog["producto"].astype(str)==producto)].copy()
        if subcategoria_normalizada is None: same=same[same["subcategoria"].isna()|same["subcategoria"].astype(str).isin(["","nan","None"])]
        else: same=same[same["subcategoria"].astype(str)==subcategoria_normalizada]
        if not same.empty: raise ValueError("Ese producto ya existe en el catálogo para esa categoría/subcategoría.")
    product=Product.create(product_code=generate_product_code(categoria),categoria=categoria,subcategoria=subcategoria_normalizada,producto=producto,
        packaging_mode=packaging_mode,cajas_por_pack=cajas_por_pack,unidades_por_pack=unidades_por_pack,unidades_por_caja=unidades_por_caja,timestamp=timestamp)
    catalog=pd.concat([catalog,pd.DataFrame([product.to_row()])],ignore_index=True)
    safe_write_csv(catalog[PRODUCT_COLUMNS],PRODUCTS_FILE,allow_empty=True); return product


def deactivate_catalog_product(
    *,
    product_code,
):
    catalog = load_product_catalog(
        active_only=False
    )

    matches = catalog[
        catalog[
            "product_code"
        ]
        .astype(str)
        .eq(
            str(
                product_code
            )
        )
    ]

    if matches.empty:
        raise ValueError(
            f"No se encontró {product_code}."
        )

    idx = matches.index[0]

    product = Product.from_row(
        catalog.loc[
            idx
        ]
    )

    updates = product.deactivate_updates(
        timestamp=
            now_iso(),
    )

    for field, value in updates.items():
        catalog.loc[
            idx,
            field
        ] = value

    safe_write_csv(
        catalog[
            PRODUCT_COLUMNS
        ],
        PRODUCTS_FILE,
        allow_empty=True,
    )

    return product


def catalog_products_for(
    *,
    categoria,
    subcategoria=None,
):
    """
    Devuelve el catálogo activo ya normalizado al esquema actual.

    Esta función es deliberadamente defensiva porque products.csv puede
    venir de versiones anteriores del proyecto. Antes de llegar a la UI,
    garantizamos siempre la existencia de packaging_mode y de todos los
    campos del esquema nuevo.
    """

    catalog = load_product_catalog(
        active_only=True
    ).copy()

    # Protección extra por si un CSV viejo o una rama anterior devuelve
    # columnas legacy.
    if "packaging_mode" not in catalog.columns:
        catalog[
            "packaging_mode"
        ] = PACKAGING_PACK_UNITS

    if "cajas_por_pack" not in catalog.columns:
        if "cajas_por_bulto" in catalog.columns:
            catalog[
                "cajas_por_pack"
            ] = pd.to_numeric(
                catalog[
                    "cajas_por_bulto"
                ],
                errors="coerce",
            )
        else:
            catalog[
                "cajas_por_pack"
            ] = pd.NA

    if "unidades_por_pack" not in catalog.columns:
        if "unidades_por_bulto" in catalog.columns:
            catalog[
                "unidades_por_pack"
            ] = pd.to_numeric(
                catalog[
                    "unidades_por_bulto"
                ],
                errors="coerce",
            )
        else:
            catalog[
                "unidades_por_pack"
            ] = pd.NA

    if "unidades_por_caja" not in catalog.columns:
        catalog[
            "unidades_por_caja"
        ] = pd.NA

    for column in PRODUCT_COLUMNS:
        if column not in catalog.columns:
            catalog[
                column
            ] = pd.NA

    categoria = normalize_product_name(
        categoria
    )

    filtered = catalog[
        catalog[
            "categoria"
        ]
        .astype(str)
        .eq(
            categoria
        )
    ].copy()

    if subcategoria:
        subcategoria = normalize_product_name(
            subcategoria
        )

        filtered = filtered[
            filtered[
                "subcategoria"
            ]
            .astype(str)
            .eq(
                subcategoria
            )
        ].copy()

    return (
        filtered[
            PRODUCT_COLUMNS
        ]
        .sort_values(
            "producto"
        )
        .reset_index(
            drop=True
        )
    )


# ============================================================
# CAMERA PRODUCTS
# ============================================================

def normalize_product_name(
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


def load_camera_products(active_only=False):
    df=load_csv(CAMERA_PRODUCTS_FILE)
    if df.empty:return pd.DataFrame(columns=CAMERA_PRODUCT_COLUMNS)
    if "packaging_mode" not in df.columns: df["packaging_mode"]=PACKAGING_PACK_UNITS
    if "cantidad_packs" not in df.columns: df["cantidad_packs"]=df["cantidad_bultos"] if "cantidad_bultos" in df.columns else pd.NA
    if "cantidad_cajas" not in df.columns: df["cantidad_cajas"]=pd.NA
    if "cajas_por_pack" not in df.columns: df["cajas_por_pack"]=df["cajas_por_bulto"] if "cajas_por_bulto" in df.columns else pd.NA
    if "unidades_por_pack" not in df.columns: df["unidades_por_pack"]=df["unidades_por_bulto"] if "unidades_por_bulto" in df.columns else pd.NA
    if "unidades_por_caja" not in df.columns: df["unidades_por_caja"]=pd.NA
    if "total_cajas" not in df.columns: df["total_cajas"]=pd.NA
    for col in CAMERA_PRODUCT_COLUMNS:
        if col not in df.columns: df[col]=pd.NA
    for col in ["cantidad_packs","cantidad_cajas","cajas_por_pack","unidades_por_pack","unidades_por_caja","total_cajas","total_unidades"]: df[col]=pd.to_numeric(df[col],errors="coerce")
    df["active"]=df["active"].astype(str).str.strip().str.lower().isin(["true","1","yes"])
    if active_only: df=df[df["active"]==True].copy()
    return df[CAMERA_PRODUCT_COLUMNS].copy()


def generate_camera_product_id(
    categoria,
):
    categoria = normalize_product_name(
        categoria
    )

    code = CATEGORY_CODES.get(
        categoria
    )

    if not code:
        raise ValueError(
            f"No hay código configurado para la categoría {categoria}."
        )

    products = load_camera_products(
        active_only=False
    )

    existing = (
        products[
            "product_stock_id"
        ]
        .dropna()
        .astype(str)
        .tolist()
        if (
            not products.empty
            and "product_stock_id"
            in products.columns
        )
        else []
    )

    return next_sequential_id(
        existing,
        f"CAM-{code}",
    )


def add_camera_product(
    *,
    product_code,
    categoria,
    subcategoria,
    producto,
    packaging_mode,
    cantidad_packs,
    cantidad_cajas,
    cajas_por_pack,
    unidades_por_pack,
    unidades_por_caja,
    notes="",
):
    """
    Agrega stock no-granel a cámara con trazabilidad física individual.

    Reglas:
    - PACK_CAJAS_UNIDADES -> 1 CAM ID por PACK
    - PACK_UNIDADES       -> 1 CAM ID por PACK
    - CAJA_UNIDADES       -> 1 CAM ID por CAJA

    Ejemplo:
        3 packs de FAM_1
        -> CAM-FAM-000001
        -> CAM-FAM-000002
        -> CAM-FAM-000003

    Cada fila guarda cantidad_packs=1 (o cantidad_cajas=1) y su
    total_unidades individual.
    """

    timestamp = now_iso()

    if packaging_mode in {
        PACKAGING_PACK_BOXES_UNITS,
        PACKAGING_PACK_UNITS,
    }:
        physical_count = int(
            cantidad_packs
            or 0
        )

        if physical_count <= 0:
            raise ValueError(
                "La cantidad de packs debe ser mayor a cero."
            )

        per_row_packs = 1
        per_row_boxes = None

        physical_label = "pack"

    elif packaging_mode == PACKAGING_BOX_UNITS:
        physical_count = int(
            cantidad_cajas
            or 0
        )

        if physical_count <= 0:
            raise ValueError(
                "La cantidad de cajas debe ser mayor a cero."
            )

        per_row_packs = None
        per_row_boxes = 1

        physical_label = "caja"

    else:
        raise ValueError(
            f"Packaging mode desconocido: {packaging_mode}"
        )

    products = load_camera_products(
        active_only=False
    )

    code = CATEGORY_CODES.get(
        normalize_product_name(
            categoria
        )
    )

    if not code:
        raise ValueError(
            f"No hay código configurado para {categoria}."
        )

    existing_ids = (
        products[
            "product_stock_id"
        ]
        .dropna()
        .astype(str)
        .tolist()
        if (
            not products.empty
            and "product_stock_id"
            in products.columns
        )
        else []
    )

    created_products = []
    created_rows = []

    open_week = get_open_week()

    operation_id = generate_id(
        "INGRESO-CAMARA-PRODUCTO"
    )

    for _ in range(
        physical_count
    ):
        product_stock_id = next_sequential_id(
            existing_ids,
            f"CAM-{code}",
        )

        existing_ids.append(
            product_stock_id
        )

        product = CameraProduct.create(
            product_stock_id=
                product_stock_id,

            product_code=
                product_code,

            categoria=
                categoria,

            subcategoria=
                subcategoria,

            producto=
                producto,

            packaging_mode=
                packaging_mode,

            cantidad_packs=
                per_row_packs,

            cantidad_cajas=
                per_row_boxes,

            cajas_por_pack=
                cajas_por_pack,

            unidades_por_pack=
                unidades_por_pack,

            unidades_por_caja=
                unidades_por_caja,

            timestamp=
                timestamp,
        )

        created_products.append(
            product
        )

        created_rows.append(
            product.to_row()
        )

        detail = (
            f"product_code={product.product_code}; "
            f"categoria={product.categoria}; "
            f"producto={product.producto}; "
            f"packaging_mode={product.packaging_mode}; "
            f"unidad_fisica={physical_label}; "
            f"total_unidades={product.total_unidades}; "
        )

        if product.subcategoria:
            detail += (
                f"subcategoria={product.subcategoria}; "
            )

        if product.cantidad_packs is not None:
            detail += (
                f"cantidad_packs={product.cantidad_packs}; "
            )

        if product.cantidad_cajas is not None:
            detail += (
                f"cantidad_cajas={product.cantidad_cajas}; "
            )

        if product.cajas_por_pack is not None:
            detail += (
                f"cajas_por_pack={product.cajas_por_pack}; "
            )

        if product.unidades_por_pack is not None:
            detail += (
                f"unidades_por_pack={product.unidades_por_pack}; "
            )

        if product.unidades_por_caja is not None:
            detail += (
                f"unidades_por_caja={product.unidades_por_caja}; "
            )

        if product.total_cajas is not None:
            detail += (
                f"total_cajas={product.total_cajas}; "
            )

        if notes:
            detail += notes

        # Un movimiento por unidad física, todos bajo la misma operation_id.
        append_row(
            MOVEMENTS_FILE,
            {
                "movement_id":
                    generate_id(
                        "MOV"
                    ),

                "operation_id":
                    operation_id,

                "timestamp":
                    timestamp,

                "week_id":
                    (
                        open_week[
                            "week_id"
                        ]
                        if open_week
                        is not None
                        else pd.NA
                    ),

                "movement_type":
                    "INGRESO_CAMARA_PRODUCTO",

                "from_location":
                    "EXTERNO",

                "to_location":
                    "CAMARA",

                "source_stock_id":
                    pd.NA,

                "target_stock_id":
                    product_stock_id,

                "sabor":
                    pd.NA,

                # Esta columna es legacy del journal.
                # Para producto no-granel siempre representa 1 unidad física.
                "cantidad_latas":
                    1,

                "peso_bruto_kg":
                    pd.NA,

                "tara_kg":
                    pd.NA,

                "peso_neto_kg":
                    pd.NA,

                "tara_final_kg":
                    pd.NA,

                "residuo_final_kg":
                    pd.NA,

                "notes":
                    detail,
            }
        )

    products = pd.concat(
        [
            products,
            pd.DataFrame(
                created_rows
            ),
        ],
        ignore_index=True,
    )

    safe_write_csv(
        products[
            CAMERA_PRODUCT_COLUMNS
        ],
        CAMERA_PRODUCTS_FILE,
        allow_empty=True,
    )

    return {
        "operation_id":
            operation_id,

        "created_products":
            created_products,

        "created_ids":
            [
                product.product_stock_id
                for product
                in created_products
            ],

        "physical_count":
            physical_count,

        "physical_label":
            physical_label,

        "total_unidades":
            int(
                sum(
                    product.total_unidades
                    for product
                    in created_products
                )
            ),
    }


def annul_camera_product(
    *,
    product_stock_id,
    notes="",
):
    products = load_camera_products(
        active_only=False
    )

    matches = products[
        products[
            "product_stock_id"
        ]
        .astype(str)
        .eq(
            str(
                product_stock_id
            )
        )
    ]

    if matches.empty:
        raise ValueError(
            f"No se encontró {product_stock_id}."
        )

    idx = matches.index[0]

    model = CameraProduct.from_row(
        products.loc[
            idx
        ]
    )

    timestamp = now_iso()

    updates = model.annul_updates(
        timestamp=
            timestamp,
    )

    for field, value in updates.items():
        products.loc[
            idx,
            field
        ] = value

    safe_write_csv(
        products[
            CAMERA_PRODUCT_COLUMNS
        ],
        CAMERA_PRODUCTS_FILE,
        allow_empty=True,
    )

    open_week = get_open_week()

    operation_id = generate_id(
        "ANULACION-CAMARA-PRODUCTO"
    )

    append_row(
        MOVEMENTS_FILE,
        {
            "movement_id":
                generate_id(
                    "MOV"
                ),

            "operation_id":
                operation_id,

            "timestamp":
                timestamp,

            "week_id":
                (
                    open_week[
                        "week_id"
                    ]
                    if open_week
                    is not None
                    else pd.NA
                ),

            "movement_type":
                "ANULACION_CAMARA_PRODUCTO",

            "from_location":
                "CAMARA",

            "to_location":
                "ANULADA",

            "source_stock_id":
                product_stock_id,

            "target_stock_id":
                pd.NA,

            "sabor":
                pd.NA,

            "cantidad_latas":
                int(
                    model.cantidad_packs
                    if model.cantidad_packs
                    is not None
                    else (
                        model.cantidad_cajas
                        if model.cantidad_cajas
                        is not None
                        else 0
                    )
                ),

            "peso_bruto_kg":
                pd.NA,

            "tara_kg":
                pd.NA,

            "peso_neto_kg":
                pd.NA,

            "tara_final_kg":
                pd.NA,

            "residuo_final_kg":
                pd.NA,

            "notes":
                (
                    f"categoria={model.categoria}; "
                    + (
                        f"subcategoria={model.subcategoria}; "
                        if model.subcategoria
                        else ""
                    )
                    + f"producto={model.producto}; "
                    f"packaging_mode={model.packaging_mode}; "
                    + (
                        f"cantidad_packs={model.cantidad_packs}; "
                        if model.cantidad_packs is not None
                        else ""
                    )
                    + (
                        f"cantidad_cajas={model.cantidad_cajas}; "
                        if model.cantidad_cajas is not None
                        else ""
                    )
                    + f"total_unidades={model.total_unidades}"
                    + (
                        f"; {notes}"
                        if notes
                        else ""
                    )
                ),
        }
    )

    return model


# ============================================================
# CAMERA STOCK
# ============================================================

def add_camera_stock(
    sabor,
    cantidad_latas,
    kg_referencia_lata,
    notes="",
):
    """
    La UI puede cargar N latas juntas por comodidad,
    pero internamente crea N CameraLata independientes.
    """

    sabor = normalize_flavor_name(
        sabor
    )

    if not sabor:
        raise ValueError(
            "Seleccioná un sabor."
        )

    cantidad_latas = int(
        cantidad_latas
    )

    if cantidad_latas <= 0:
        raise ValueError(
            "La cantidad debe ser mayor a cero."
        )

    kg_referencia_lata = round(
        float(
            kg_referencia_lata
        ),
        3,
    )

    if (
        kg_referencia_lata <= 0
        or kg_referencia_lata
        > MAX_CAN_GROSS_KG
    ):
        raise ValueError(
            f"El peso de referencia debe estar entre "
            f"0 y {MAX_CAN_GROSS_KG:.3f} kg."
        )

    timestamp = now_iso()

    camera_ids = generate_camera_ids(
        sabor,
        cantidad_latas,
    )

    camera = load_camera_stock()

    new_rows = []

    for camera_id in camera_ids:
        camera_lata = CameraLata.create(
            camera_stock_id=
                camera_id,

            sabor=
                sabor,

            kg_referencia_lata=
                kg_referencia_lata,

            timestamp=
                timestamp,
        )

        new_rows.append(
            camera_lata.to_row()
        )

    camera = pd.concat(
        [
            camera,
            pd.DataFrame(
                new_rows
            ),
        ],
        ignore_index=True,
    )

    safe_write_csv(
        camera[
            CAMERA_COLUMNS
        ],
        CAMERA_STOCK_FILE,
        allow_empty=True,
    )

    open_week = get_open_week()

    week_id = (
        open_week[
            "week_id"
        ]
        if open_week is not None
        else pd.NA
    )

    operation_id = generate_id(
        "INGRESO-CAMARA"
    )

    # Una fila de movimiento por lata física.
    for camera_id in camera_ids:
        append_row(
            MOVEMENTS_FILE,
            {
                "movement_id":
                    generate_id("MOV"),

                "operation_id":
                    operation_id,

                "timestamp":
                    timestamp,

                "week_id":
                    week_id,

                "movement_type":
                    "INGRESO_CAMARA",

                "from_location":
                    "EXTERNO",

                "to_location":
                    "CAMARA",

                "source_stock_id":
                    pd.NA,

                "target_stock_id":
                    camera_id,

                "sabor":
                    sabor,

                "cantidad_latas":
                    1,

                "peso_bruto_kg":
                    pd.NA,

                "tara_kg":
                    pd.NA,

                "peso_neto_kg":
                    kg_referencia_lata,

                "tara_final_kg":
                    pd.NA,

                "residuo_final_kg":
                    pd.NA,

                "notes":
                    notes,
            }
        )

    return camera_ids


def annul_camera_latas(
    camera_stock_ids,
    notes="",
):
    """
    Anula una o varias latas DISPONIBLES de cámara.

    No borra filas físicamente. Las deja:
        estado = ANULADA
        active = False

    Además registra un movimiento ANULACION_CAMARA por lata,
    todos bajo el mismo operation_id.
    """

    camera_stock_ids = [
        str(
            value
        ).strip()
        for value in camera_stock_ids
        if str(
            value
        ).strip()
    ]

    if not camera_stock_ids:
        raise ValueError(
            "Seleccioná al menos una lata para anular."
        )

    camera = load_camera_stock()

    if camera.empty:
        raise ValueError(
            "No hay latas en cámara."
        )

    timestamp = now_iso()

    open_week = get_open_week()

    week_id = (
        open_week[
            "week_id"
        ]
        if open_week is not None
        else pd.NA
    )

    operation_id = generate_id(
        "ANULACION-CAMARA"
    )

    annulled = []

    for camera_stock_id in camera_stock_ids:
        matches = camera[
            camera[
                "camera_stock_id"
            ]
            .astype(str)
            .eq(
                camera_stock_id
            )
        ]

        if matches.empty:
            raise ValueError(
                f"No se encontró {camera_stock_id}."
            )

        idx = matches.index[0]

        model = CameraLata.from_row(
            camera.loc[
                idx
            ]
        )

        updates = model.annul_updates(
            timestamp=
                timestamp,
        )

        for field, value in updates.items():
            camera.loc[
                idx,
                field
            ] = value

        append_row(
            MOVEMENTS_FILE,
            {
                "movement_id":
                    generate_id(
                        "MOV"
                    ),

                "operation_id":
                    operation_id,

                "timestamp":
                    timestamp,

                "week_id":
                    week_id,

                "movement_type":
                    "ANULACION_CAMARA",

                "from_location":
                    "CAMARA",

                "to_location":
                    "ANULADA",

                "source_stock_id":
                    camera_stock_id,

                "target_stock_id":
                    pd.NA,

                "sabor":
                    model.sabor,

                "cantidad_latas":
                    1,

                "peso_bruto_kg":
                    pd.NA,

                "tara_kg":
                    pd.NA,

                "peso_neto_kg":
                    model.kg_referencia_lata,

                "tara_final_kg":
                    pd.NA,

                "residuo_final_kg":
                    pd.NA,

                "notes":
                    notes,
            }
        )

        annulled.append(
            camera_stock_id
        )

    safe_write_csv(
        camera[
            CAMERA_COLUMNS
        ],
        CAMERA_STOCK_FILE,
        allow_empty=True,
    )

    return {
        "operation_id":
            operation_id,

        "cantidad":
            len(
                annulled
            ),

        "camera_stock_ids":
            annulled,
    }


# ============================================================
# CAMERA -> SALON
# ============================================================

def move_camera_to_salon(
    camera_stock_id,
    peso_bruto_kg,
    tara_kg,
    notes="",
):
    peso_neto = calculate_net_weight(
        peso_bruto_kg,
        tara_kg,
    )

    stock = load_current_stock()

    mask = (
        stock[
            "stock_id"
        ]
        == camera_stock_id
    )

    if not mask.any():
        raise ValueError(
            "No se encontró esa lata de cámara."
        )

    idx = stock[
        mask
    ].index[0]

    source = stock.loc[
        idx
    ].copy()

    if str(
        source[
            "location"
        ]
    ).upper() != "CAMARA":
        raise ValueError(
            "El ID seleccionado no pertenece a cámara."
        )

    if not bool(
        source[
            "active"
        ]
    ):
        raise ValueError(
            "Esa lata de cámara ya no está disponible."
        )

    timestamp = now_iso()

    salon_id = generate_salon_id(
        stock
    )

    sabor = normalize_flavor_name(
        source[
            "sabor"
        ]
    )

    lata = Lata.create_salon(
        stock_id=
            salon_id,

        sabor=
            sabor,

        estado=
            "CERRADA",

        timestamp=
            timestamp,

        peso_bruto_kg=
            peso_bruto_kg,

        tara_kg=
            tara_kg,

        peso_neto_kg=
            peso_neto,

        kg_referencia_lata=
            source[
                "kg_referencia_lata"
            ],

        source_camera_stock_id=
            camera_stock_id,

        ingresada_salon_at=
            timestamp,
    )

    # Creamos la lata de salón.
    stock = pd.concat(
        [
            stock,
            pd.DataFrame(
                [
                    lata.to_stock_row()
                ]
            ),
        ],
        ignore_index=True,
    )

    # Marcamos la CameraLata específica como movida.
    stock.loc[
        idx,
        "estado"
    ] = "MOVIDA_SALON"

    stock.loc[
        idx,
        "moved_to_salon_at"
    ] = timestamp

    stock.loc[
        idx,
        "target_salon_stock_id"
    ] = salon_id

    stock.loc[
        idx,
        "updated_at"
    ] = timestamp

    stock.loc[
        idx,
        "active"
    ] = False

    save_current_stock(
        stock
    )

    open_week = get_open_week()

    append_row(
        MOVEMENTS_FILE,
        {
            "movement_id":
                generate_id("MOV"),

            "operation_id":
                pd.NA,

            "timestamp":
                timestamp,

            "week_id":
                (
                    open_week[
                        "week_id"
                    ]
                    if open_week
                    is not None
                    else pd.NA
                ),

            "movement_type":
                "CAMARA_A_SALON",

            "from_location":
                "CAMARA",

            "to_location":
                "SALON",

            "source_stock_id":
                camera_stock_id,

            "target_stock_id":
                salon_id,

            "sabor":
                sabor,

            "cantidad_latas":
                1,

            "peso_bruto_kg":
                round(
                    float(
                        peso_bruto_kg
                    ),
                    3,
                ),

            "tara_kg":
                round(
                    float(
                        tara_kg
                    ),
                    3,
                ),

            "peso_neto_kg":
                peso_neto,

            "tara_final_kg":
                pd.NA,

            "residuo_final_kg":
                pd.NA,

            "notes":
                notes,
        }
    )

    return (
        salon_id,
        peso_neto,
    )


# ============================================================
# SALON COUNT
# ============================================================

def save_salon_count(
    edited_df,
    count_type,
    notes="",
):
    stock = load_current_stock()

    timestamp = now_iso()

    count_id = generate_id(
        "COUNT"
    )

    open_week = get_open_week()

    if count_type == "INICIO_SEMANA":
        if open_week is not None:
            raise ValueError(
                "Ya existe una semana abierta. "
                "Primero tenés que cerrarla."
            )

        week_id = None

    elif count_type == "CIERRE_SEMANA":
        raise ValueError(
            "El cierre de semana ya no se realiza desde Conteo físico. "
            "Usá 📅 Semanas → Cerrar semana."
        )

    else:
        week_id = (
            open_week["week_id"]
            if open_week is not None
            else None
        )

    valid_rows = 0
    created_rows = 0
    updated_rows = 0
    total_stock_kg = 0.0
    errors = []

    count_rows = []
    movement_rows = []

    for row_number, row in edited_df.iterrows():
        raw_stock_id = row.get(
            "stock_id"
        )

        no_id = (
            pd.isna(raw_stock_id)
            or not str(
                raw_stock_id
            ).strip()
            or str(
                raw_stock_id
            ).lower()
            == "nan"
        )

        sabor = normalize_flavor_name(
            row.get(
                "sabor",
                ""
            )
        )

        estado = row.get(
            "estado",
            "ABIERTA",
        )

        if pd.isna(estado):
            estado = "ABIERTA"

        peso_bruto = pd.to_numeric(
            row.get(
                "peso_bruto_kg"
            ),
            errors="coerce",
        )

        tara = pd.to_numeric(
            row.get(
                "tara_kg"
            ),
            errors="coerce",
        )

        if (
            no_id
            and not sabor
            and pd.isna(peso_bruto)
            and pd.isna(tara)
        ):
            continue

        if not sabor:
            errors.append(
                f"Fila {row_number + 1}: falta sabor."
            )
            continue

        if pd.isna(
            peso_bruto
        ):
            errors.append(
                f"Fila {row_number + 1}: "
                "falta peso bruto."
            )
            continue

        if pd.isna(
            tara
        ):
            errors.append(
                f"Fila {row_number + 1}: "
                "falta tara."
            )
            continue

        try:
            peso_neto = calculate_net_weight(
                peso_bruto,
                tara,
            )
        except ValueError as e:
            errors.append(
                f"Fila {row_number + 1}: {e}"
            )
            continue

        # Nueva lata
        if no_id:
            stock_id = generate_salon_id(
                stock
            )

            lata = Lata.create_salon(
                stock_id=
                    stock_id,

                sabor=
                    sabor,

                estado=
                    estado,

                timestamp=
                    timestamp,

                peso_bruto_kg=
                    peso_bruto,

                tara_kg=
                    tara,

                peso_neto_kg=
                    peso_neto,

                kg_referencia_lata=
                    None,

                source_camera_stock_id=
                    None,

                ingresada_salon_at=
                    None,
            )

            stock = pd.concat(
                [
                    stock,
                    pd.DataFrame(
                        [
                            lata.to_stock_row()
                        ]
                    ),
                ],
                ignore_index=True,
            )

            movement_rows.append(
                {
                    "movement_id":
                        generate_id("MOV"),

                    "timestamp":
                        timestamp,

                    "week_id":
                        week_id,

                    "movement_type":
                        "CARGA_MANUAL_SALON",

                    "from_location":
                        "INICIAL",

                    "to_location":
                        "SALON",

                    "source_stock_id":
                        pd.NA,

                    "target_stock_id":
                        stock_id,

                    "sabor":
                        sabor,

                    "cantidad_latas":
                        1,

                    "peso_bruto_kg":
                        peso_bruto,

                    "tara_kg":
                        tara,

                    "peso_neto_kg":
                        peso_neto,

                    "notes":
                        notes,
                }
            )

            created_rows += 1

        # Lata existente
        else:
            stock_id = str(
                raw_stock_id
            )

            mask = (
                stock["stock_id"]
                == stock_id
            )

            if not mask.any():
                errors.append(
                    f"Fila {row_number + 1}: "
                    f"no existe {stock_id}."
                )
                continue

            idx = stock[
                mask
            ].index[0]

            stock.loc[
                idx,
                "sabor"
            ] = sabor

            stock.loc[
                idx,
                "estado"
            ] = estado

            stock.loc[
                idx,
                "peso_actual_bruto_kg"
            ] = peso_bruto

            stock.loc[
                idx,
                "tara_actual_kg"
            ] = tara

            stock.loc[
                idx,
                "peso_actual_neto_kg"
            ] = peso_neto

            stock.loc[
                idx,
                "updated_at"
            ] = timestamp

            updated_rows += 1

        count_rows.append(
            {
                "count_id":
                    count_id,

                "week_id":
                    week_id,

                "count_type":
                    count_type,

                "timestamp":
                    timestamp,

                "location":
                    "SALON",

                "stock_id":
                    stock_id,

                "sabor":
                    sabor,

                "estado":
                    estado,

                "peso_bruto_kg":
                    peso_bruto,

                "tara_kg":
                    tara,

                "peso_neto_kg":
                    peso_neto,

                "notes":
                    notes,
            }
        )

        total_stock_kg += (
            peso_neto
        )

        valid_rows += 1

    if valid_rows == 0:
        return {
            "count_id":
                count_id,

            "week_id":
                week_id,

            "valid_rows":
                0,

            "created_rows":
                created_rows,

            "updated_rows":
                updated_rows,

            "total_stock_kg":
                0.0,

            "errors":
                errors,
        }

    if count_type == "INICIO_SEMANA":
        week_id = create_week(
            start_count_id=
                count_id,

            start_stock_kg=
                total_stock_kg,

            timestamp=
                timestamp,

            notes=
                notes,
        )

        for row in count_rows:
            row["week_id"] = week_id

        for row in movement_rows:
            row["week_id"] = week_id

    save_current_stock(
        stock
    )

    for movement_row in movement_rows:
        append_row(
            MOVEMENTS_FILE,
            movement_row,
        )

    for count_row in count_rows:
        append_row(
            COUNTS_FILE,
            count_row,
        )

    return {
        "count_id":
            count_id,

        "week_id":
            week_id,

        "valid_rows":
            valid_rows,

        "created_rows":
            created_rows,

        "updated_rows":
            updated_rows,

        "total_stock_kg":
            total_stock_kg,

        "errors":
            errors,
    }


# ============================================================
# OPEN CLOSED SALON CAN
# ============================================================

def _apply_stock_updates(
    stock,
    idx,
    updates,
):
    for key, value in updates.items():
        stock.loc[
            idx,
            key,
        ] = value


def open_salon_can(
    stock_id,
    notes="",
):
    stock = load_current_stock()

    mask = (
        stock["stock_id"]
        == stock_id
    )

    if not mask.any():
        raise ValueError(
            "No se encontró la lata."
        )

    idx = stock[
        mask
    ].index[0]

    row = stock.loc[
        idx
    ].copy()

    if str(
        row["location"]
    ).upper() != "SALON":
        raise ValueError(
            "La lata no pertenece al salón."
        )

    timestamp = now_iso()

    operation_id = generate_id(
        "APERTURA"
    )

    lata = Lata.from_row(
        row
    )

    updates = lata.opening_updates(
        timestamp=
            timestamp,

        operation_id=
            operation_id,
    )

    _apply_stock_updates(
        stock,
        idx,
        updates,
    )

    save_current_stock(
        stock
    )

    open_week = get_open_week()

    append_row(
        MOVEMENTS_FILE,
        {
            "movement_id":
                generate_id("MOV"),

            "operation_id":
                operation_id,

            "timestamp":
                timestamp,

            "week_id":
                (
                    open_week["week_id"]
                    if open_week is not None
                    else pd.NA
                ),

            "movement_type":
                "LATA_ABIERTA",

            "from_location":
                "SALON",

            "to_location":
                "SALON",

            "source_stock_id":
                stock_id,

            "target_stock_id":
                stock_id,

            "sabor":
                normalize_flavor_name(
                    row["sabor"]
                ),

            "cantidad_latas":
                1,

            "peso_bruto_kg":
                row[
                    "peso_actual_bruto_kg"
                ],

            "tara_kg":
                row[
                    "tara_actual_kg"
                ],

            "peso_neto_kg":
                row[
                    "peso_actual_neto_kg"
                ],

            "tara_final_kg":
                pd.NA,

            "residuo_final_kg":
                pd.NA,

            "notes":
                notes,
        }
    )


def mark_salon_can_empty(
    stock_id,
    tara_final_kg,
    peso_final_bruto_kg=None,
    notes="",
):
    stock = load_current_stock()

    mask = (
        stock["stock_id"]
        == stock_id
    )

    if not mask.any():
        raise ValueError(
            "No se encontró la lata."
        )

    idx = stock[
        mask
    ].index[0]

    row = stock.loc[
        idx
    ].copy()

    if str(
        row["location"]
    ).upper() != "SALON":
        raise ValueError(
            "La lata no pertenece al salón."
        )

    timestamp = now_iso()

    operation_id = generate_id(
        "FINALIZA"
    )

    lata = Lata.from_row(
        row
    )

    updates = lata.finalization_updates(
        timestamp=
            timestamp,

        operation_id=
            operation_id,

        tara_final_kg=
            tara_final_kg,

        peso_final_bruto_kg=
            peso_final_bruto_kg,

        max_tare_kg=
            MAX_TARE_KG,

        max_gross_kg=
            MAX_CAN_GROSS_KG,
    )

    _apply_stock_updates(
        stock,
        idx,
        updates,
    )

    save_current_stock(
        stock
    )

    open_week = get_open_week()

    append_row(
        MOVEMENTS_FILE,
        {
            "movement_id":
                generate_id("MOV"),

            "operation_id":
                operation_id,

            "timestamp":
                timestamp,

            "week_id":
                (
                    open_week["week_id"]
                    if open_week is not None
                    else pd.NA
                ),

            "movement_type":
                "LATA_AGOTADA",

            "from_location":
                "SALON",

            "to_location":
                "FUERA_STOCK",

            "source_stock_id":
                stock_id,

            "target_stock_id":
                pd.NA,

            "sabor":
                normalize_flavor_name(
                    row["sabor"]
                ),

            "cantidad_latas":
                1,

            "peso_bruto_kg":
                updates[
                    "tara_final_kg"
                ],

            "tara_kg":
                updates[
                    "tara_final_kg"
                ],

            "peso_neto_kg":
                0.0,

            "tara_final_kg":
                updates[
                    "tara_final_kg"
                ],

            "residuo_final_kg":
                updates[
                    "residuo_final_kg"
                ],

            "notes":
                notes,
        }
    )

    return {
        "operation_id":
            operation_id,

        "tara_final_kg":
            updates[
                "tara_final_kg"
            ],

        "peso_final_bruto_kg":
            updates[
                "peso_final_bruto_kg"
            ],

        "residuo_final_kg":
            updates[
                "residuo_final_kg"
            ],
    }



# ============================================================
# FLEXIBLE SALON REPLACEMENT
# ============================================================

def perform_salon_replacement(
    current_open_stock_id,
    tara_final_kg,
    peso_final_bruto_kg=None,
    reserve_stock_id=None,
    replenish_from_camera=False,
    peso_bruto_kg=None,
    tara_kg=None,
    notes="",
):
    """
    Recambio flexible de una lata de salón.

    Siempre:
    - finaliza la lata ABIERTA actual.

    Opcional:
    - abre una lata CERRADA de reserva del mismo sabor;
    - trae una lata del mismo sabor desde cámara.

    Regla para la lata que llega desde cámara:
    - si se abrió una reserva del salón -> queda CERRADA como nueva reserva;
    - si NO se abrió una reserva -> la nueva lata queda ABIERTA directamente.

    Todos los movimientos comparten operation_id.
    """

    stock = load_current_stock()

    current_mask = (
        stock["stock_id"]
        == current_open_stock_id
    )

    if not current_mask.any():
        raise ValueError(
            "No se encontró la lata abierta actual."
        )

    current_idx = stock[
        current_mask
    ].index[0]

    current_row = stock.loc[
        current_idx
    ].copy()

    if str(
        current_row["location"]
    ).upper() != "SALON":
        raise ValueError(
            "La lata actual no pertenece al salón."
        )

    if not bool(
        current_row["active"]
    ):
        raise ValueError(
            "La lata actual ya no está activa."
        )

    if str(
        current_row["estado"]
    ).upper() != "ABIERTA":
        raise ValueError(
            "La lata actual debe estar ABIERTA."
        )

    sabor = normalize_flavor_name(
        current_row["sabor"]
    )

    timestamp = now_iso()
    operation_id = generate_id(
        "RECAMBIO"
    )

    open_week = get_open_week()

    week_id = (
        open_week["week_id"]
        if open_week is not None
        else pd.NA
    )

    movement_rows = []

    # ========================================================
    # 1. FINALIZAR LATA ABIERTA ACTUAL
    # ========================================================

    current_lata = Lata.from_row(
        current_row
    )

    final_updates = (
        current_lata
        .finalization_updates(
            timestamp=
                timestamp,

            operation_id=
                operation_id,

            tara_final_kg=
                tara_final_kg,

            peso_final_bruto_kg=
                peso_final_bruto_kg,

            max_tare_kg=
                MAX_TARE_KG,

            max_gross_kg=
                MAX_CAN_GROSS_KG,
        )
    )

    _apply_stock_updates(
        stock,
        current_idx,
        final_updates,
    )

    movement_rows.append(
        {
            "movement_id":
                generate_id("MOV"),

            "operation_id":
                operation_id,

            "timestamp":
                timestamp,

            "week_id":
                week_id,

            "movement_type":
                "LATA_AGOTADA",

            "from_location":
                "SALON",

            "to_location":
                "FUERA_STOCK",

            "source_stock_id":
                current_open_stock_id,

            "target_stock_id":
                pd.NA,

            "sabor":
                sabor,

            "cantidad_latas":
                1,

            "peso_bruto_kg":
                final_updates[
                    "tara_final_kg"
                ],

            "tara_kg":
                final_updates[
                    "tara_final_kg"
                ],

            "peso_neto_kg":
                0.0,

            "tara_final_kg":
                final_updates[
                    "tara_final_kg"
                ],

            "residuo_final_kg":
                final_updates[
                    "residuo_final_kg"
                ],

            "notes":
                notes,
        }
    )

    # ========================================================
    # 2. ABRIR RESERVA DEL SALÓN, SI SE ELIGIÓ
    # ========================================================

    reserve_opened = False
    replacement_target_stock_id = None
    replacement_target_row = None

    if (
        reserve_stock_id is not None
        and str(reserve_stock_id).strip()
    ):
        reserve_mask = (
            stock["stock_id"]
            == reserve_stock_id
        )

        if not reserve_mask.any():
            raise ValueError(
                "No se encontró la lata de reserva."
            )

        reserve_idx = stock[
            reserve_mask
        ].index[0]

        reserve_row = stock.loc[
            reserve_idx
        ].copy()

        if str(
            reserve_row["location"]
        ).upper() != "SALON":
            raise ValueError(
                "La lata de reserva no pertenece al salón."
            )

        if not bool(
            reserve_row["active"]
        ):
            raise ValueError(
                "La lata de reserva ya no está activa."
            )

        if str(
            reserve_row["estado"]
        ).upper() != "CERRADA":
            raise ValueError(
                "La lata de reserva debe estar CERRADA."
            )

        reserve_flavor = normalize_flavor_name(
            reserve_row["sabor"]
        )

        if reserve_flavor != sabor:
            raise ValueError(
                "La lata de reserva debe ser del mismo sabor."
            )

        reserve_lata = Lata.from_row(
            reserve_row
        )

        reserve_updates = (
            reserve_lata
            .opening_updates(
                timestamp=
                    timestamp,

                operation_id=
                    operation_id,
            )
        )

        _apply_stock_updates(
            stock,
            reserve_idx,
            reserve_updates,
        )

        movement_rows.append(
            {
                "movement_id":
                    generate_id("MOV"),

                "operation_id":
                    operation_id,

                "timestamp":
                    timestamp,

                "week_id":
                    week_id,

                "movement_type":
                    "LATA_ABIERTA",

                "from_location":
                    "SALON",

                "to_location":
                    "SALON",

                "source_stock_id":
                    reserve_stock_id,

                "target_stock_id":
                    reserve_stock_id,

                "sabor":
                    sabor,

                "cantidad_latas":
                    1,

                "peso_bruto_kg":
                    reserve_row[
                        "peso_actual_bruto_kg"
                    ],

                "tara_kg":
                    reserve_row[
                        "tara_actual_kg"
                    ],

                "peso_neto_kg":
                    reserve_row[
                        "peso_actual_neto_kg"
                    ],

                "notes":
                    notes,
            }
        )

        reserve_opened = True

        replacement_target_stock_id = (
            reserve_stock_id
        )

        replacement_target_row = (
            reserve_row
        )

    # ========================================================
    # 3. REPOSICIÓN DESDE CÁMARA, OPCIONAL
    # ========================================================

    new_salon_id = None
    new_salon_state = None
    new_net_weight = None

    if replenish_from_camera:
        if (
            peso_bruto_kg is None
            or tara_kg is None
        ):
            raise ValueError(
                "Para traer una lata desde cámara "
                "tenés que cargar peso bruto y tara."
            )

        new_net_weight = calculate_net_weight(
            peso_bruto_kg,
            tara_kg,
        )

        camera_available = stock[
            (
                stock["location"]
                .astype(str)
                .str.upper()
                .eq("CAMARA")
            )
            &
            (
                stock["active"]
                == True
            )
            &
            (
                stock["sabor"]
                .map(
                    normalize_flavor_name
                )
                .eq(sabor)
            )
        ].copy()

        if camera_available.empty:
            raise ValueError(
                f"No quedan latas de {sabor} en cámara. "
                "Podés hacer el recambio sin reposición."
            )

        camera_available[
            "_created_dt"
        ] = pd.to_datetime(
            camera_available[
                "created_at"
            ],
            errors="coerce",
        )

        camera_available = (
            camera_available
            .sort_values(
                "_created_dt",
                ascending=True,
                na_position="last",
            )
        )

        camera_source_id = (
            camera_available
            .iloc[0][
                "stock_id"
            ]
        )

        camera_mask = (
            stock["stock_id"]
            == camera_source_id
        )

        camera_idx = stock[
            camera_mask
        ].index[0]

        camera_row = stock.loc[
            camera_idx
        ].copy()

        new_salon_id = generate_salon_id(
            stock
        )

        # Si ya abrimos una reserva, la que llega de cámara queda cerrada.
        # Si no había reserva abierta, la nueva entra como abierta.
        new_salon_state = (
            "CERRADA"
            if reserve_opened
            else "ABIERTA"
        )

        new_lata = Lata.create_salon(
            stock_id=
                new_salon_id,

            sabor=
                sabor,

            estado=
                new_salon_state,

            timestamp=
                timestamp,

            peso_bruto_kg=
                peso_bruto_kg,

            tara_kg=
                tara_kg,

            peso_neto_kg=
                new_net_weight,

            kg_referencia_lata=
                camera_row[
                    "kg_referencia_lata"
                ],

            source_camera_stock_id=
                camera_source_id,

            ingresada_salon_at=
                timestamp,

            opened_at=
                (
                    timestamp
                    if new_salon_state
                    == "ABIERTA"
                    else None
                ),

            opened_operation_id=
                (
                    operation_id
                    if new_salon_state
                    == "ABIERTA"
                    else None
                ),
        )

        stock = pd.concat(
            [
                stock,
                pd.DataFrame(
                    [
                        new_lata.to_stock_row()
                    ]
                ),
            ],
            ignore_index=True,
        )

        stock.loc[
            camera_idx,
            "estado"
        ] = "MOVIDA_SALON"

        stock.loc[
            camera_idx,
            "moved_to_salon_at"
        ] = timestamp

        stock.loc[
            camera_idx,
            "target_salon_stock_id"
        ] = new_salon_id

        stock.loc[
            camera_idx,
            "updated_at"
        ] = timestamp

        stock.loc[
            camera_idx,
            "active"
        ] = False

        movement_rows.append(
            {
                "movement_id":
                    generate_id("MOV"),

                "operation_id":
                    operation_id,

                "timestamp":
                    timestamp,

                "week_id":
                    week_id,

                "movement_type":
                    "CAMARA_A_SALON",

                "from_location":
                    "CAMARA",

                "to_location":
                    "SALON",

                "source_stock_id":
                    camera_source_id,

                "target_stock_id":
                    new_salon_id,

                "sabor":
                    sabor,

                "cantidad_latas":
                    1,

                "peso_bruto_kg":
                    float(
                        peso_bruto_kg
                    ),

                "tara_kg":
                    float(
                        tara_kg
                    ),

                "peso_neto_kg":
                    new_net_weight,

                "notes":
                    notes,
            }
        )

        # Si la nueva entra directamente ABIERTA, registramos también
        # explícitamente la apertura dentro del mismo recambio.
        if new_salon_state == "ABIERTA":
            movement_rows.append(
                {
                    "movement_id":
                        generate_id("MOV"),

                    "operation_id":
                        operation_id,

                    "timestamp":
                        timestamp,

                    "week_id":
                        week_id,

                    "movement_type":
                        "LATA_ABIERTA",

                    "from_location":
                        "SALON",

                    "to_location":
                        "SALON",

                    "source_stock_id":
                        new_salon_id,

                    "target_stock_id":
                        new_salon_id,

                    "sabor":
                        sabor,

                    "cantidad_latas":
                        1,

                    "peso_bruto_kg":
                        float(
                            peso_bruto_kg
                        ),

                    "tara_kg":
                        float(
                            tara_kg
                        ),

                    "peso_neto_kg":
                        new_net_weight,

                    "notes":
                        notes,
                }
            )

            replacement_target_stock_id = (
                new_salon_id
            )

            replacement_target_row = {
                "peso_actual_bruto_kg":
                    float(
                        peso_bruto_kg
                    ),

                "tara_actual_kg":
                    float(
                        tara_kg
                    ),

                "peso_actual_neto_kg":
                    new_net_weight,
            }

    # ========================================================
    # 4. CAMBIO DE SABOR
    # ========================================================

    if replacement_target_stock_id is not None:

        movement_rows.append(
            {
                "movement_id":
                    generate_id("MOV"),

                "operation_id":
                    operation_id,

                "timestamp":
                    timestamp,

                "week_id":
                    week_id,

                "movement_type":
                    "CAMBIO_SABOR",

                "from_location":
                    "SALON",

                "to_location":
                    "SALON",

                "source_stock_id":
                    current_open_stock_id,

                "target_stock_id":
                    replacement_target_stock_id,

                "sabor":
                    sabor,

                "cantidad_latas":
                    1,

                "peso_bruto_kg":
                    replacement_target_row[
                        "peso_actual_bruto_kg"
                    ],

                "tara_kg":
                    replacement_target_row[
                        "tara_actual_kg"
                    ],

                "peso_neto_kg":
                    replacement_target_row[
                        "peso_actual_neto_kg"
                    ],

                "notes":
                    notes,
            }
        )

    # ========================================================
    # 5. GUARDADO
    # ========================================================

    save_current_stock(
        stock
    )

    movements = load_csv(
        MOVEMENTS_FILE
    )

    movement_df = pd.DataFrame(
        movement_rows
    )

    movements = pd.concat(
        [
            movements,
            movement_df,
        ],
        ignore_index=True,
    )

    safe_write_csv(
        movements,
        MOVEMENTS_FILE,
    )

    return {
        "operation_id":
            operation_id,

        "sabor":
            sabor,

        "finished_stock_id":
            current_open_stock_id,

        "opened_reserve_stock_id":
            (
                reserve_stock_id
                if reserve_opened
                else None
            ),

        "replenished":
            bool(
                replenish_from_camera
            ),

        "new_salon_stock_id":
            new_salon_id,

        "new_salon_state":
            new_salon_state,

        "new_net_weight":
            new_net_weight,

        "tara_final_kg":
            final_updates[
                "tara_final_kg"
            ],

        "peso_final_bruto_kg":
            final_updates[
                "peso_final_bruto_kg"
            ],

        "residuo_final_kg":
            final_updates[
                "residuo_final_kg"
            ],

        "cambio_sabor_registered":
            (
                replacement_target_stock_id
                is not None
            ),

        "cambio_sabor_target_stock_id":
            replacement_target_stock_id,
    }


# ============================================================
# MIX VENTAS
# ============================================================

@st.cache_data
def parse_mix_ventas(
    file_bytes,
):
    df = pd.read_csv(
        BytesIO(file_bytes)
    )

    df.columns = (
        df.columns
        .str.strip()
        .str.lower()
    )

    numeric_columns = [
        "cantidad",
        "bultos",
        "preciopromedio",
        "total",
        "porctotalpesos",
        "kilos",
    ]

    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce",
            )

    for col in [
        "desde",
        "hasta",
    ]:
        if col in df.columns:
            df[col] = pd.to_datetime(
                df[col],
                errors="coerce",
                format="mixed",
            )

    detail_df = df[
        df["articulo"].notna()
        & df["artdescrip"].notna()
    ].copy()

    for col in [
        "artdescrip",
        "grudescrip",
        "sucdescrip",
    ]:
        if col in detail_df.columns:
            detail_df[col] = (
                detail_df[col]
                .astype(str)
                .str.strip()
            )

    return (
        df,
        detail_df,
    )


# ============================================================
# STARTUP METADATA REFRESH
# ============================================================

# IMPORTANTE:
# refresh_all_metadata() depende de load_weeks(), load_current_stock()
# y de otras funciones definidas arriba. Por eso se ejecuta recién acá,
# cuando todos los helpers ya existen.
#
# Si no hay cambios reales, safe_write_csv() no se ejecuta.
refresh_all_metadata()


# ============================================================
# UI OPERATION FEEDBACK
# ============================================================

def run_ui_mutation(
    *,
    running_label,
    success_label,
    operation,
    error_label=None,
):
    """
    Ejecuta una acción que escribe/edita/anula datos mostrando un estado
    visible durante toda la operación.

    Devuelve:
        (ok, result)

    - ok=True: la operación terminó sin excepciones.
    - ok=False: el error ya fue mostrado al usuario.

    Capturamos Exception deliberadamente en la capa UI para que errores de
    persistencia (PermissionError/OneDrive), validación y escritura no hagan
    caer toda la pantalla sin feedback.
    """

    with st.status(
        f"⏳ {running_label}",
        expanded=True,
    ) as status:

        status.write(
            "Procesando y guardando los cambios. "
            "No cierres ni recargues la página hasta que termine."
        )

        try:
            result = operation()

        except Exception as exc:
            final_error_label = (
                error_label
                or f"No se pudo completar: {running_label}"
            )

            status.update(
                label=
                    f"❌ {final_error_label}",

                state=
                    "error",

                expanded=
                    True,
            )

            st.error(
                f"{type(exc).__name__}: {exc}"
            )

            return (
                False,
                None,
            )

        final_success_label = (
            success_label(
                result
            )
            if callable(
                success_label
            )
            else success_label
        )

        status.update(
            label=
                f"✅ {final_success_label}",

            state=
                "complete",

            expanded=
                False,
        )

        return (
            True,
            result,
        )


def save_week_theoretical_consumption(
    *,
    week_id,
    consumo_teorico,
):
    weeks_mix = load_weeks()

    week_mask = (
        weeks_mix[
            "week_id"
        ]
        == week_id
    )

    if not week_mask.any():
        raise ValueError(
            "No se encontró la semana actual."
        )

    week_idx = weeks_mix[
        week_mask
    ].index[0]

    weeks_mix.loc[
        week_idx,
        "consumo_teorico_kg"
    ] = round(
        float(
            consumo_teorico
        ),
        3,
    )

    safe_write_csv(
        weeks_mix[
            WEEK_COLUMNS
        ],
        WEEKS_FILE,
    )

    refresh_all_metadata()

    return {
        "week_id":
            week_id,

        "consumo_teorico_kg":
            round(
                float(
                    consumo_teorico
                ),
                3,
            ),
    }


# ============================================================
# UI
# ============================================================

st.title(
    "🍦 Control de Merma"
)

st.caption(
    "Cámara · Salón · Pesajes · "
    "Semanas · Mix de Ventas"
)


(
    tab_overview,
    tab_stock,
    tab_transfer,
    tab_count,
    tab_weeks,
    tab_config,
    tab_history,
) = st.tabs(
    [
        "📊 Overview",
        "📦 Stock actual",
        "➡️ Cámara → Salón",
        "⚖️ Conteo salón",
        "📅 Semanas",
        "⚙️ Configuración",
        "🕒 Historial",
    ]
)


# ============================================================
# OVERVIEW
# ============================================================

with tab_overview:
    stock = load_current_stock()

    active_stock = stock[
        stock["active"] == True
    ].copy()

    camera = active_stock[
        active_stock[
            "location"
        ]
        == "CAMARA"
    ]

    salon = active_stock[
        active_stock[
            "location"
        ]
        == "SALON"
    ]

    camera_latas = (
        camera[
            "cantidad_latas"
        ]
        .fillna(0)
        .sum()
    )

    camera_kg_estimado = (
        (
            camera[
                "cantidad_latas"
            ].fillna(0)
            *
            camera[
                "kg_referencia_lata"
            ].fillna(0)
        )
        .sum()
    )

    salon_kg_real = (
        salon[
            "peso_actual_neto_kg"
        ]
        .fillna(0)
        .sum()
    )

    st.subheader(
        "📦 Stock actual"
    )

    c1, c2, c3, c4 = st.columns(
        4
    )

    c1.metric(
        "Latas en cámara",
        f"{camera_latas:,.0f}",
    )

    c2.metric(
        "Cámara estimada",
        f"{camera_kg_estimado:,.3f} kg",
    )

    c3.metric(
        "Latas activas salón",
        len(salon),
    )

    c4.metric(
        "Stock físico salón",
        f"{salon_kg_real:,.3f} kg",
    )

    st.divider()

    st.subheader(
        "📅 Semana de control"
    )

    # El modelo Week se refresca desde los eventos reales.
    refresh_all_metadata()

    open_week_row = get_open_week()

    if open_week_row is None:
        st.info(
            "No hay una semana abierta. "
            "Realizá un conteo de tipo "
            "'Inicio de semana' para comenzar."
        )

    else:
        week = Week.from_row(
            open_week_row
        )

        started_at = pd.to_datetime(
            week.started_at,
            errors="coerce",
        )

        elapsed_days = week.elapsed_days(
            now_iso()
        )

        w1, w2, w3 = st.columns(
            3
        )

        w1.metric(
            "Estado",
            "ABIERTA",
        )

        w2.metric(
            "Inicio",
            (
                started_at.strftime(
                    "%d/%m/%Y %H:%M"
                )
                if pd.notna(started_at)
                else "-"
            ),
        )

        w3.metric(
            "Tiempo transcurrido",
            (
                f"{elapsed_days:.2f} días"
                if elapsed_days
                is not None
                else "-"
            ),
        )

        st.markdown(
            "#### 📦 Inventario inicial"
        )

        i1, i2, i3, i4 = st.columns(
            4
        )

        i1.metric(
            "Salón inicial",
            (
                f"{week.start_salon_kg:.3f} kg"
                if week.start_salon_kg
                is not None
                else "-"
            ),
        )

        i2.metric(
            "Latas salón inicial",
            (
                week.start_salon_latas
                if week.start_salon_latas
                is not None
                else "-"
            ),
        )

        i3.metric(
            "Latas cámara inicial",
            (
                week.start_camera_latas
                if week.start_camera_latas
                is not None
                else "-"
            ),
        )

        i4.metric(
            "Cámara inicial",
            (
                f"{week.start_camera_kg:.3f} kg"
                if week.start_camera_kg
                is not None
                else "-"
            ),
        )

        open_products_snapshot = (
            products_snapshot_from_json(
                open_week_row.get(
                    "current_products_snapshot_json"
                )
            )
        )

        if (
            product_snapshot_units_total(
                open_products_snapshot
            )
            > 0
        ):
            st.markdown(
                "#### 🧊 Productos actuales en cámara"
            )

            overview_product_rows = []

            for category in PRODUCT_SNAPSHOT_CATEGORIES:
                values = open_products_snapshot.get(
                    category,
                    {},
                )

                category_totals = (
                    product_snapshot_category_totals(
                        values
                    )
                )

                if category_totals[
                    "units"
                ] <= 0:
                    continue

                overview_product_rows.append(
                    {
                        "Categoría":
                            category.replace(
                                "_",
                                " ",
                            ).title(),

                        "Stock":
                            product_snapshot_display_value(
                                category,
                                values,
                            ),

                        "Unidades":
                            category_totals[
                                "units"
                            ],
                    }
                )

            st.dataframe(
                pd.DataFrame(
                    overview_product_rows
                ),
                hide_index=True,
                use_container_width=True,
            )

        st.markdown(
            "#### 🔄 Actividad de la semana"
        )

        a1, a2, a3 = st.columns(
            3
        )

        a1.metric(
            "Cámara → salón",
            f"{week.camera_to_salon_latas} latas",
        )

        a2.metric(
            "Cambios de sabor",
            week.cambios_sabor,
        )

        a3.metric(
            "Latas terminadas",
            week.latas_terminadas,
        )

        a5, a6, a7 = st.columns(
            3
        )

        a5.metric(
            "Latas abiertas",
            week.latas_abiertas,
        )

        a6.metric(
            "Latas con tara final",
            week.latas_con_tara_final,
        )

        a7.metric(
            "Tara final acumulada",
            f"{week.tara_final_total_kg:.3f} kg",
        )

        a9, a10 = st.columns(
            2
        )

        a9.metric(
            "Ingresos a cámara",
            f"{week.ingreso_camera_latas} latas",
        )

        a10.metric(
            "Kg ingresados a cámara",
            f"{week.ingreso_camera_kg:.3f} kg",
        )

        # --------------------------------------------------------
        # Promedios operativos de la semana
        # --------------------------------------------------------
        # Se calculan desde stock_movements.csv para que reflejen
        # exactamente los eventos ocurridos dentro de esta Week:
        #
        # - Promedio tara final:
        #   tara_final_kg de movimientos LATA_AGOTADA.
        #
        # - Promedio bruto salida cámara:
        #   peso_bruto_kg de movimientos CAMARA_A_SALON.
        #
        # - Promedio neto lata semanal:
        #   promedio bruto salida cámara - promedio tara final.
        #
        # Este último es una estimación semanal del contenido neto medio
        # por lata usando dos promedios observados de la misma Week.
        # --------------------------------------------------------

        week_movements = load_csv(
            MOVEMENTS_FILE
        )

        avg_tara_final_kg = None
        avg_camera_exit_gross_kg = None
        total_camera_exit_gross_kg = None
        estimated_camera_exit_net_kg = None
        avg_weekly_net_can_kg = None

        nominal_camera_exit_total_kg = None
        nominal_camera_exit_deficit_kg = None
        nominal_camera_exit_deficit_per_can_kg = None
        nominal_camera_exit_deficit_pct = None

        nominal_analysis_df = pd.DataFrame()
        nominal_initial_closed_count = 0
        nominal_camera_count = 0
        nominal_above_count = 0
        nominal_in_range_count = 0
        nominal_below_count = 0
        nominal_deficit_total_kg = 0.0
        nominal_excess_total_kg = 0.0
        nominal_balance_total_kg = 0.0
        nominal_avg_deviation_kg = None

        if (
            not week_movements.empty
            and "week_id" in week_movements.columns
        ):
            week_movements = week_movements[
                week_movements[
                    "week_id"
                ]
                .astype(str)
                .eq(
                    str(
                        week.week_id
                    )
                )
            ].copy()

            movement_types = (
                week_movements[
                    "movement_type"
                ]
                .fillna("")
                .astype(str)
                .str.upper()
            )

            exhausted = week_movements[
                movement_types.eq(
                    "LATA_AGOTADA"
                )
            ].copy()

            if (
                not exhausted.empty
                and "tara_final_kg"
                in exhausted.columns
            ):
                tara_values = pd.to_numeric(
                    exhausted[
                        "tara_final_kg"
                    ],
                    errors="coerce",
                ).dropna()

                if not tara_values.empty:
                    avg_tara_final_kg = float(
                        tara_values.mean()
                    )

            camera_exits = week_movements[
                movement_types.eq(
                    "CAMARA_A_SALON"
                )
            ].copy()

            if (
                not camera_exits.empty
                and "peso_bruto_kg"
                in camera_exits.columns
            ):
                gross_values = pd.to_numeric(
                    camera_exits[
                        "peso_bruto_kg"
                    ],
                    errors="coerce",
                ).dropna()

                if not gross_values.empty:
                    avg_camera_exit_gross_kg = float(
                        gross_values.mean()
                    )
                    total_camera_exit_gross_kg = float(
                        gross_values.sum()
                    )

            if (
                avg_tara_final_kg is not None
                and avg_camera_exit_gross_kg is not None
            ):
                avg_weekly_net_can_kg = max(
                    0.0,
                    avg_camera_exit_gross_kg
                    - avg_tara_final_kg,
                )

            if (
                avg_tara_final_kg is not None
                and total_camera_exit_gross_kg is not None
            ):
                camera_exit_count = int(len(camera_exits))
                estimated_camera_exit_net_kg = max(
                    0.0,
                    total_camera_exit_gross_kg
                    - (camera_exit_count * avg_tara_final_kg),
                )

                nominal_camera_exit_total_kg = (
                    camera_exit_count
                    * GRIDO_NOMINAL_NET_KG
                )

                nominal_camera_exit_deficit_kg = (
                    estimated_camera_exit_net_kg
                    - nominal_camera_exit_total_kg
                )

                nominal_camera_exit_deficit_per_can_kg = (
                    nominal_camera_exit_deficit_kg
                    / camera_exit_count
                    if camera_exit_count > 0
                    else None
                )

                nominal_camera_exit_deficit_pct = (
                    nominal_camera_exit_deficit_kg
                    / nominal_camera_exit_total_kg
                    * 100.0
                    if nominal_camera_exit_total_kg > 0
                    else None
                )

                # --------------------------------------------------
                # Universo para cumplimiento nominal:
                #
                # A) latas que ya estaban CERRADAS al inicio de la Week
                #    (inventory_counts.csv / start_count_id)
                #
                # B) latas que ingresaron desde cámara durante la Week
                #    (CAMARA_A_SALON)
                #
                # Se unifican por ID físico para no contar dos veces.
                # --------------------------------------------------

                nominal_sources = []

                # A) CERRADAS AL INICIO
                week_counts = load_csv(
                    COUNTS_FILE
                )

                if (
                    not week_counts.empty
                    and "count_id" in week_counts.columns
                ):
                    start_count_id = str(
                        getattr(
                            week,
                            "start_count_id",
                            "",
                        )
                        or ""
                    ).strip()

                    initial_closed = week_counts[
                        week_counts[
                            "count_id"
                        ]
                        .astype(str)
                        .eq(
                            start_count_id
                        )
                    ].copy()

                    if "count_type" in initial_closed.columns:
                        initial_closed = initial_closed[
                            initial_closed[
                                "count_type"
                            ]
                            .fillna("")
                            .astype(str)
                            .str.upper()
                            .eq(
                                "INICIO_SEMANA"
                            )
                        ].copy()

                    if "estado" in initial_closed.columns:
                        initial_closed = initial_closed[
                            initial_closed[
                                "estado"
                            ]
                            .fillna("")
                            .astype(str)
                            .str.upper()
                            .eq(
                                "CERRADA"
                            )
                        ].copy()

                    if (
                        not initial_closed.empty
                        and "peso_bruto_kg"
                        in initial_closed.columns
                    ):
                        initial_closed[
                            "peso_bruto_kg"
                        ] = pd.to_numeric(
                            initial_closed[
                                "peso_bruto_kg"
                            ],
                            errors="coerce",
                        )

                        initial_closed = initial_closed[
                            initial_closed[
                                "peso_bruto_kg"
                            ].notna()
                        ].copy()

                        initial_nominal = pd.DataFrame(
                            {
                                "analysis_stock_id":
                                    initial_closed.get(
                                        "stock_id",
                                        pd.Series(
                                            index=initial_closed.index,
                                            dtype=object,
                                        ),
                                    ),

                                "sabor":
                                    initial_closed.get(
                                        "sabor",
                                        "",
                                    ),

                                "peso_bruto_kg":
                                    initial_closed[
                                        "peso_bruto_kg"
                                    ],

                                "origen":
                                    "CERRADA_INICIO",
                            }
                        )

                        nominal_sources.append(
                            initial_nominal
                        )

                # B) INGRESADAS DESDE CÁMARA
                camera_nominal = camera_exits[
                    [
                        col
                        for col in [
                            "target_stock_id",
                            "source_stock_id",
                            "sabor",
                            "peso_bruto_kg",
                        ]
                        if col in camera_exits.columns
                    ]
                ].copy()

                if (
                    not camera_nominal.empty
                    and "peso_bruto_kg"
                    in camera_nominal.columns
                ):
                    camera_nominal[
                        "peso_bruto_kg"
                    ] = pd.to_numeric(
                        camera_nominal[
                            "peso_bruto_kg"
                        ],
                        errors="coerce",
                    )

                    camera_nominal = camera_nominal[
                        camera_nominal[
                            "peso_bruto_kg"
                        ].notna()
                    ].copy()

                    if "target_stock_id" in camera_nominal.columns:
                        camera_nominal[
                            "analysis_stock_id"
                        ] = camera_nominal[
                            "target_stock_id"
                        ]

                        if "source_stock_id" in camera_nominal.columns:
                            missing_target = (
                                camera_nominal[
                                    "analysis_stock_id"
                                ].isna()
                                |
                                camera_nominal[
                                    "analysis_stock_id"
                                ]
                                .astype(str)
                                .str.strip()
                                .isin(
                                    [
                                        "",
                                        "nan",
                                        "None",
                                        "<NA>",
                                    ]
                                )
                            )

                            camera_nominal.loc[
                                missing_target,
                                "analysis_stock_id",
                            ] = camera_nominal.loc[
                                missing_target,
                                "source_stock_id",
                            ]

                    elif "source_stock_id" in camera_nominal.columns:
                        camera_nominal[
                            "analysis_stock_id"
                        ] = camera_nominal[
                            "source_stock_id"
                        ]

                    else:
                        camera_nominal[
                            "analysis_stock_id"
                        ] = [
                            f"CAMERA_EXIT_{i}"
                            for i in range(
                                len(
                                    camera_nominal
                                )
                            )
                        ]

                    camera_nominal[
                        "origen"
                    ] = "DESDE_CAMARA"

                    nominal_sources.append(
                        camera_nominal[
                            [
                                "analysis_stock_id",
                                "sabor",
                                "peso_bruto_kg",
                                "origen",
                            ]
                        ]
                    )

                if nominal_sources:
                    nominal_analysis_df = pd.concat(
                        nominal_sources,
                        ignore_index=True,
                    )

                    nominal_analysis_df[
                        "analysis_stock_id"
                    ] = (
                        nominal_analysis_df[
                            "analysis_stock_id"
                        ]
                        .fillna("")
                        .astype(str)
                        .str.strip()
                    )

                    # Si por algún dato legacy apareciera el mismo ID en
                    # ambos orígenes, cuenta una sola lata física.
                    with_id = nominal_analysis_df[
                        nominal_analysis_df[
                            "analysis_stock_id"
                        ].ne("")
                    ].drop_duplicates(
                        subset=[
                            "analysis_stock_id"
                        ],
                        keep="first",
                    )

                    without_id = nominal_analysis_df[
                        nominal_analysis_df[
                            "analysis_stock_id"
                        ].eq("")
                    ]

                    nominal_analysis_df = pd.concat(
                        [
                            with_id,
                            without_id,
                        ],
                        ignore_index=True,
                    )

                if not nominal_analysis_df.empty:
                    nominal_initial_closed_count = int(
                        (
                            nominal_analysis_df[
                                "origen"
                            ]
                            == "CERRADA_INICIO"
                        ).sum()
                    )

                    nominal_camera_count = int(
                        (
                            nominal_analysis_df[
                                "origen"
                            ]
                            == "DESDE_CAMARA"
                        ).sum()
                    )

                    nominal_analysis_df[
                        "tara_promedio_week_kg"
                    ] = avg_tara_final_kg

                    nominal_analysis_df[
                        "neto_estimado_kg"
                    ] = (
                        nominal_analysis_df[
                            "peso_bruto_kg"
                        ]
                        - avg_tara_final_kg
                    )

                    nominal_analysis_df[
                        "objetivo_neto_kg"
                    ] = GRIDO_NOMINAL_NET_KG

                    nominal_analysis_df[
                        "desvio_vs_nominal_kg"
                    ] = (
                        nominal_analysis_df[
                            "neto_estimado_kg"
                        ]
                        - GRIDO_NOMINAL_NET_KG
                    )

                    nominal_analysis_df[
                        "desvio_vs_nominal_pct"
                    ] = (
                        nominal_analysis_df[
                            "desvio_vs_nominal_kg"
                        ]
                        / GRIDO_NOMINAL_NET_KG
                        * 100.0
                    )

                    def classify_nominal_deviation(value):
                        if value > GRIDO_NOMINAL_TOLERANCE_KG:
                            return "EXCEDENTE"
                        if value < -GRIDO_NOMINAL_TOLERANCE_KG:
                            return "DEFICIT"
                        return "EN_RANGO"

                    nominal_analysis_df[
                        "estado_nominal"
                    ] = nominal_analysis_df[
                        "desvio_vs_nominal_kg"
                    ].apply(
                        classify_nominal_deviation
                    )

                    nominal_above_count = int(
                        (
                            nominal_analysis_df[
                                "estado_nominal"
                            ]
                            == "EXCEDENTE"
                        ).sum()
                    )

                    nominal_in_range_count = int(
                        (
                            nominal_analysis_df[
                                "estado_nominal"
                            ]
                            == "EN_RANGO"
                        ).sum()
                    )

                    nominal_below_count = int(
                        (
                            nominal_analysis_df[
                                "estado_nominal"
                            ]
                            == "DEFICIT"
                        ).sum()
                    )

                    deficits = nominal_analysis_df[
                        nominal_analysis_df[
                            "desvio_vs_nominal_kg"
                        ] < 0
                    ][
                        "desvio_vs_nominal_kg"
                    ]

                    excesses = nominal_analysis_df[
                        nominal_analysis_df[
                            "desvio_vs_nominal_kg"
                        ] > 0
                    ][
                        "desvio_vs_nominal_kg"
                    ]

                    nominal_deficit_total_kg = float(
                        -deficits.sum()
                    ) if not deficits.empty else 0.0

                    nominal_excess_total_kg = float(
                        excesses.sum()
                    ) if not excesses.empty else 0.0

                    nominal_balance_total_kg = float(
                        nominal_analysis_df[
                            "desvio_vs_nominal_kg"
                        ].sum()
                    )

                    nominal_avg_deviation_kg = float(
                        nominal_analysis_df[
                            "desvio_vs_nominal_kg"
                        ].mean()
                    )


        st.markdown(
            "#### ⚖️ Análisis de merma de la semana"
        )

        st.caption(
            "Primero analizamos cuánto pesa realmente una lata durante "
            "esta semana. Para eso cruzamos las salidas de Cámara → Salón "
            "con las taras registradas al agotar latas."
        )

        st.markdown(
            "##### 🪣 Peso de las latas ingresadas al salón"
        )

        m1, m2, m3 = st.columns(3)

        m1.metric(
            "Kg brutos desde cámara",
            f"{total_camera_exit_gross_kg:.3f} kg"
            if total_camera_exit_gross_kg is not None else "-",
            help=(
                "Suma de peso_bruto_kg de todos los movimientos "
                "CAMARA_A_SALON de esta Week. Es peso real medido, "
                "sin descontar tara."
            ),
        )

        m2.metric(
            "Prom. tara final semanal",
            f"{avg_tara_final_kg:.3f} kg"
            if avg_tara_final_kg is not None else "-",
            help=(
                "Promedio de tara_final_kg de todas las latas "
                "LATA_AGOTADA de esta Week."
            ),
        )

        m3.metric(
            "Kg netos estimados desde cámara",
            f"{estimated_camera_exit_net_kg:.3f} kg"
            if estimated_camera_exit_net_kg is not None else "-",
            help=(
                "Kg brutos desde cámara - (cantidad de latas trasladadas "
                "× promedio de tara final semanal)."
            ),
        )

        m4, m5, m6 = st.columns(3)

        m4.metric(
            "Neto nominal esperado",
            (
                f"{nominal_camera_exit_total_kg:.3f} kg"
                if nominal_camera_exit_total_kg is not None
                else "-"
            ),
            help=(
                "Cantidad de latas CAMARA_A_SALON de esta Week × "
                "7.800 kg netos nominales por lata."
            ),
        )

        m5.metric(
            "Déficit vs nominal",
            (
                f"{nominal_camera_exit_deficit_kg:+.3f} kg"
                if nominal_camera_exit_deficit_kg is not None
                else "-"
            ),
            help=(
                "Kg netos estimados desde cámara - Neto nominal esperado. "
                "Un valor negativo indica faltante respecto del nominal."
            ),
        )

        m6.metric(
            "Déficit prom. por lata",
            (
                f"{nominal_camera_exit_deficit_per_can_kg:+.3f} kg"
                if nominal_camera_exit_deficit_per_can_kg is not None
                else "-"
            ),
            help=(
                "Déficit vs nominal dividido por la cantidad de latas "
                "ingresadas desde cámara durante la Week."
            ),
        )

        m7, m8, m9 = st.columns(3)

        m7.metric(
            "Prom. bruto por lata",
            f"{avg_camera_exit_gross_kg:.3f} kg"
            if avg_camera_exit_gross_kg is not None else "-",
            help=(
                "Kg brutos totales / cantidad de movimientos "
                "CAMARA_A_SALON."
            ),
        )

        m8.metric(
            "Neto promedio estimado por lata",
            f"{avg_weekly_net_can_kg:.3f} kg"
            if avg_weekly_net_can_kg is not None else "-",
            help=(
                "Promedio bruto por lata - promedio de tara final semanal."
            ),
        )

        m9.metric(
            "Déficit %",
            (
                f"{nominal_camera_exit_deficit_pct:+.2f}%"
                if nominal_camera_exit_deficit_pct is not None
                else "-"
            ),
            help=(
                "Déficit vs nominal / Neto nominal esperado × 100. "
                "Negativo indica faltante relativo frente a 7.800 kg por lata."
            ),
        )

        st.markdown(
            "##### 🔎 Calidad de pesajes Cámara → Salón"
        )

        camera_moves_total = int(len(camera_exits))

        if (
            not camera_exits.empty
            and "peso_bruto_kg" in camera_exits.columns
        ):
            camera_weight_debug = pd.to_numeric(
                camera_exits["peso_bruto_kg"],
                errors="coerce",
            )

            camera_moves_valid_weight = int(
                camera_weight_debug.notna().sum()
            )

            camera_moves_invalid_weight = int(
                camera_weight_debug.isna().sum()
            )
        else:
            camera_moves_valid_weight = 0
            camera_moves_invalid_weight = camera_moves_total

        d1, d2, d3 = st.columns(3)

        d1.metric(
            "Movimientos totales",
            camera_moves_total,
            help=(
                "Cantidad total de movimientos CAMARA_A_SALON "
                "detectados en esta Week."
            ),
        )

        d2.metric(
            "Con peso bruto válido",
            camera_moves_valid_weight,
            help=(
                "Movimientos CAMARA_A_SALON cuyo peso_bruto_kg "
                "puede convertirse correctamente a número."
            ),
        )

        d3.metric(
            "Sin peso bruto válido",
            camera_moves_invalid_weight,
            help=(
                "Movimientos CAMARA_A_SALON sin peso_bruto_kg "
                "o con un valor que no puede interpretarse como número. "
                "Estas filas no pueden entrar al análisis nominal."
            ),
        )

        st.markdown(
            "##### 📦 Cumplimiento de peso nominal Grido"
        )

        st.caption(
            "Analizamos todas las latas con peso bruto confiable de la Week: "
            "las que ya estaban CERRADAS en el conteo inicial y las que "
            "ingresaron desde Cámara → Salón durante la semana. Para cada "
            "una estimamos el neto como peso bruto real menos la tara "
            "promedio final observada en esta misma Week y lo comparamos "
            "contra 7.800 kg."
        )

        n1, n2, n3, n4, n5 = st.columns(5)

        n1.metric(
            "Referencia neta",
            f"{GRIDO_NOMINAL_NET_KG:.3f} kg",
            help=(
                "Peso neto nominal esperado por lata según la referencia "
                "operativa usada en el sistema."
            ),
        )

        n2.metric(
            "Latas analizadas",
            len(nominal_analysis_df),
            help=(
                "Total de latas físicas únicas analizadas: cerradas al "
                "inicio de la Week + ingresadas desde cámara durante ella."
            ),
        )

        n3.metric(
            "Cerradas al inicio",
            nominal_initial_closed_count,
            help=(
                "Latas con estado CERRADA en el INICIO_SEMANA asociado "
                "al start_count_id de esta Week."
            ),
        )

        n4.metric(
            "Desde cámara",
            nominal_camera_count,
            help=(
                "Latas con movimiento CAMARA_A_SALON y peso_bruto_kg "
                "válido durante esta Week."
            ),
        )

        n5.metric(
            "Déficit claro",
            nominal_below_count,
            help=(
                "Latas cuyo neto estimado quedó más de 50 g por debajo "
                "de 7.800 kg."
            ),
        )

        n6a, n6b = st.columns(2)

        n6a.metric(
            "En rango o mejor",
            nominal_above_count + nominal_in_range_count,
            help=(
                "Latas cuyo neto estimado quedó dentro de ±50 g del nominal "
                "o por encima de ese rango."
            ),
        )

        n6b.metric(
            "% en rango o mejor",
            (
                f"{((nominal_above_count + nominal_in_range_count) / len(nominal_analysis_df) * 100):.1f}%"
                if len(nominal_analysis_df) > 0
                else "-"
            ),
            help=(
                "Porcentaje de todas las latas analizadas que quedaron "
                "dentro de la tolerancia o por encima del nominal."
            ),
        )

        n7, n8, n9, n10 = st.columns(4)

        n7.metric(
            "Desvío promedio vs 7.800",
            (
                f"{nominal_avg_deviation_kg:+.3f} kg"
                if nominal_avg_deviation_kg is not None
                else "-"
            ),
            help=(
                "Promedio de (neto estimado por lata - 7.800 kg). "
                "Negativo indica faltante promedio de origen."
            ),
        )

        n8.metric(
            "Déficit total",
            f"{nominal_deficit_total_kg:.3f} kg",
            help=(
                "Suma absoluta de todos los desvíos negativos respecto "
                "de 7.800 kg."
            ),
        )

        n9.metric(
            "Excedente total",
            f"{nominal_excess_total_kg:.3f} kg",
            help=(
                "Suma de todos los desvíos positivos respecto de 7.800 kg."
            ),
        )

        n10.metric(
            "Balance neto vs nominal",
            f"{nominal_balance_total_kg:+.3f} kg",
            help=(
                "Excedentes menos déficits. Negativo significa que, en "
                "conjunto, las latas analizadas trajeron menos helado que "
                "la referencia nominal esperada."
            ),
        )

        if not nominal_analysis_df.empty:
            with st.expander(
                "🔎 Ver lata por lata"
            ):
                nominal_detail = nominal_analysis_df.copy()

                nominal_detail[
                    "Lata"
                ] = nominal_detail[
                    "analysis_stock_id"
                ]

                nominal_detail[
                    "Origen"
                ] = nominal_detail[
                    "origen"
                ].map(
                    {
                        "CERRADA_INICIO":
                            "Cerrada al inicio",

                        "DESDE_CAMARA":
                            "Desde cámara",
                    }
                ).fillna(
                    nominal_detail[
                        "origen"
                    ]
                )

                if "sabor" not in nominal_detail.columns:
                    nominal_detail[
                        "sabor"
                    ] = ""

                nominal_detail = nominal_detail.rename(
                    columns={
                        "sabor": "Sabor",
                        "peso_bruto_kg": "Bruto",
                        "tara_promedio_week_kg": "Tara prom.",
                        "neto_estimado_kg": "Neto estimado",
                        "desvio_vs_nominal_kg": "Vs 7.800",
                        "desvio_vs_nominal_pct": "Desvío %",
                        "estado_nominal": "Estado",
                    }
                )

                st.dataframe(
                    nominal_detail[
                        [
                            "Lata",
                            "Sabor",
                            "Origen",
                            "Bruto",
                            "Tara prom.",
                            "Neto estimado",
                            "Vs 7.800",
                            "Desvío %",
                            "Estado",
                        ]
                    ],
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Bruto": st.column_config.NumberColumn(
                            format="%.3f kg"
                        ),
                        "Tara prom.": st.column_config.NumberColumn(
                            format="%.3f kg"
                        ),
                        "Neto estimado": st.column_config.NumberColumn(
                            format="%.3f kg"
                        ),
                        "Vs 7.800": st.column_config.NumberColumn(
                            format="%+.3f kg"
                        ),
                        "Desvío %": st.column_config.NumberColumn(
                            format="%+.2f%%"
                        ),
                    },
                )

        st.markdown(
            "##### 🧮 Balance de merma"
        )

        st.caption(
            "Después comparamos lo que físicamente salió del inventario "
            "con lo que, según las ventas, debería haberse consumido. "
            "La diferencia entre ambos es la merma estimada."
        )

        b1, b2, b3 = st.columns(
            3
        )

        consumo_fisico_kg = None
        consumo_teorico_kg = None
        merma_kg = None
        merma_pct = None
        merma_latas_equivalentes = None

        b1.metric(
            "Consumo físico",
            "Pendiente cierre",
            help=(
                "QUÉ ANALIZA: cuántos kg desaparecieron físicamente del "
                "stock de salón durante la Week. DATOS USADOS: kg del "
                "conteo inicial del salón + kg netos ingresados desde "
                "cámara - kg del conteo físico final. "
                "CÁLCULO: inicial + entradas - cierre."
            ),
        )

        b2.metric(
            "Consumo teórico ventas",
            "Pendiente Mix",
            help=(
                "QUÉ ANALIZA: cuántos kg de helado deberían haberse "
                "consumido según lo vendido. DATO USADO: Mix de Ventas "
                "de la misma Week, convertido a consumo teórico de "
                "helado según cada tipo de venta."
            ),
        )

        b3.metric(
            "Merma",
            "-",
            help=(
                "QUÉ ANALIZA: la diferencia entre el helado que realmente "
                "desapareció del inventario y el que las ventas justifican. "
                "DATOS USADOS: Consumo físico y Consumo teórico. "
                "CÁLCULO: consumo físico - consumo teórico."
            ),
        )

        c1, c2 = st.columns(
            2
        )

        c1.metric(
            "Merma %",
            "-",
            help=(
                "QUÉ ANALIZA: qué porcentaje del consumo teórico representa "
                "la diferencia encontrada. DATOS USADOS: Merma en kg y "
                "Consumo teórico de ventas. "
                "CÁLCULO: merma kg / consumo teórico kg × 100."
            ),
        )

        c2.metric(
            "Latas equivalentes de merma",
            "-",
            help=(
                "QUÉ ANALIZA: a cuántas latas promedio equivale la merma "
                "de la Week. DATOS USADOS: Merma en kg y Neto promedio "
                "estimado por lata. CÁLCULO: merma kg / neto promedio."
            ),
        )

        st.markdown(
            "#### 📍 Estado actual"
        )

        c1, c2, c3, c4 = st.columns(
            4
        )

        c1.metric(
            "Salón actual",
            (
                f"{week.current_salon_kg:.3f} kg"
                if week.current_salon_kg
                is not None
                else "-"
            ),
        )

        c2.metric(
            "Latas salón",
            (
                week.current_salon_latas
                if week.current_salon_latas
                is not None
                else "-"
            ),
        )

        c3.metric(
            "Latas cámara",
            (
                week.current_camera_latas
                if week.current_camera_latas
                is not None
                else "-"
            ),
        )

        c4.metric(
            "Cámara actual",
            (
                f"{week.current_camera_kg:.3f} kg"
                if week.current_camera_kg
                is not None
                else "-"
            ),
        )

        if week.consumo_fisico_kg is not None:
            st.metric(
                "🍦 Consumo físico acumulado",
                f"{week.consumo_fisico_kg:.3f} kg",
            )

    st.divider()

    st.subheader(
        "📄 Mix de Ventas"
    )

    uploaded_file = st.file_uploader(
        "Cargar Mix de Ventas",
        type=["csv"],
    )

    consumo_teorico = None

    if uploaded_file is None:
        st.info(
            "Cargá un Mix de Ventas para "
            "obtener el consumo teórico."
        )

    else:
        try:
            raw_df, sales_df = (
                parse_mix_ventas(
                    uploaded_file.getvalue()
                )
            )

        except Exception as e:
            st.error(
                "No se pudo procesar el Mix de Ventas."
            )

            st.exception(e)
            st.stop()

        if not sales_df.empty:
            desde = (
                sales_df[
                    "desde"
                ]
                .dropna()
                .min()
            )

            hasta = (
                sales_df[
                    "hasta"
                ]
                .dropna()
                .max()
            )

            sucursales = (
                sales_df[
                    "sucdescrip"
                ]
                .dropna()
            )

            sucursal = (
                sucursales.iloc[0]
                if not sucursales.empty
                else "-"
            )

            m1, m2, m3 = st.columns(
                3
            )

            m1.metric(
                "Sucursal",
                sucursal,
            )

            m2.metric(
                "Desde",
                (
                    desde.strftime(
                        "%d/%m/%Y"
                    )
                    if pd.notna(desde)
                    else "-"
                ),
            )

            m3.metric(
                "Hasta",
                (
                    hasta.strftime(
                        "%d/%m/%Y"
                    )
                    if pd.notna(hasta)
                    else "-"
                ),
            )

            group_summary = (
                sales_df
                .groupby(
                    "grudescrip",
                    as_index=False,
                )
                .agg(
                    cantidad=(
                        "cantidad",
                        "sum",
                    ),
                    kilos=(
                        "kilos",
                        "sum",
                    ),
                    total=(
                        "total",
                        "sum",
                    ),
                )
            )

            DEFAULT_GRANEL_GROUPS = {
                "Helado x Kilo",
                "Helado x Bocha",
            }

            group_summary[
                "consume_granel"
            ] = (
                group_summary[
                    "grudescrip"
                ]
                .isin(
                    DEFAULT_GRANEL_GROUPS
                )
            )

            st.markdown(
                "### 🍦 Consumo teórico de granel"
            )

            edited_granel = st.data_editor(
                group_summary[
                    [
                        "consume_granel",
                        "grudescrip",
                        "cantidad",
                        "kilos",
                        "total",
                    ]
                ],
                hide_index=True,
                use_container_width=True,
                disabled=[
                    "grudescrip",
                    "cantidad",
                    "kilos",
                    "total",
                ],
                key="granel_editor",
            )

            selected = edited_granel[
                edited_granel[
                    "consume_granel"
                ]
                == True
            ]

            consumo_teorico = (
                selected[
                    "kilos"
                ]
                .fillna(0)
                .sum()
            )

            st.metric(
                "Consumo teórico de granel",
                f"{consumo_teorico:,.3f} kg",
            )

            open_week_for_mix = get_open_week()

            if open_week_for_mix is not None:
                if st.button(
                    "💾 Asociar consumo teórico a semana actual",
                    key="save_week_theoretical_consumption",
                ):
                    ok, _ = run_ui_mutation(
                        running_label=
                            "Asociando consumo teórico a la semana...",

                        success_label=
                            "Consumo teórico asociado correctamente.",

                        error_label=
                            "Falló la asociación del consumo teórico.",

                        operation=
                            lambda:
                                save_week_theoretical_consumption(
                                    week_id=
                                        open_week_for_mix[
                                            "week_id"
                                        ],

                                    consumo_teorico=
                                        consumo_teorico,
                                ),
                    )

                    if ok:
                        st.rerun()


# ============================================================
# STOCK ACTUAL
# ============================================================

with tab_stock:
    st.header(
        "📦 Stock actual"
    )

    st.caption(
        "Persistencia separada: salon_latas.csv para salón, "
        "camera_stock.csv para latas de granel y "
        "camera_products.csv para otros productos de cámara."
    )

    camera_tab, salon_tab = st.tabs(
        [
            "❄️ Cámara",
            "🍦 Salón",
        ]
    )

    with camera_tab:
        st.subheader(
            "❄️ Cámara"
        )

        (
            granel_tab,
            familiares_tab,
            tentaciones_tab,
            postres_tab,
            tortas_tab,
            bombones_tab,
            palitos_tab,
            especiales_tab,
            frizzio_tab,
        ) = st.tabs(
            [
                "🍦 Granel",
                "🍨 Familiares",
                "🍧 Tentaciones",
                "🍰 Postres",
                "🎂 Tortas",
                "🍫 Bombones",
                "🍡 Palitos",
                "🌱 Líneas especiales",
                "🍕 Frizzio",
            ]
        )

        with granel_tab:
                stock = load_current_stock()
        
                camera = stock[
                    (
                        stock[
                            "location"
                        ]
                        == "CAMARA"
                    )
                    &
                    (
                        stock[
                            "active"
                        ]
                        == True
                    )
                ].copy()
        
                st.subheader(
                    "❄️ Cámara"
                )
        
                if camera.empty:
                    st.info(
                        "No hay stock cargado en cámara."
                    )
        
                else:
                    # --------------------------------------------------------
                    # KPIs de cámara
                    # --------------------------------------------------------
        
                    camera[
                        "kg_referencia_lata"
                    ] = pd.to_numeric(
                        camera[
                            "kg_referencia_lata"
                        ],
                        errors="coerce",
                    )
        
                    camera[
                        "sabor"
                    ] = (
                        camera[
                            "sabor"
                        ]
                        .map(
                            normalize_flavor_name
                        )
                    )
        
                    total_camera_cans = int(
                        len(
                            camera
                        )
                    )
        
                    total_camera_flavors = int(
                        camera[
                            "sabor"
                        ]
                        .nunique()
                    )
        
                    total_camera_kg = round(
                        float(
                            camera[
                                "kg_referencia_lata"
                            ]
                            .fillna(0)
                            .sum()
                        ),
                        3,
                    )
        
                    k1, k2, k3 = st.columns(
                        3
                    )
        
                    k1.metric(
                        "Sabores en cámara",
                        total_camera_flavors,
                    )
        
                    k2.metric(
                        "Latas disponibles",
                        total_camera_cans,
                    )
        
                    k3.metric(
                        "Stock estimado",
                        f"{total_camera_kg:.3f} kg",
                    )
        
                    # --------------------------------------------------------
                    # Resumen por sabor
                    # --------------------------------------------------------
        
                    st.markdown(
                        "#### 📊 Resumen por sabor"
                    )
        
                    camera_summary = (
                        camera
                        .groupby(
                            "sabor",
                            as_index=False,
                        )
                        .agg(
                            latas_disponibles=(
                                "stock_id",
                                "count",
                            ),
        
                            kg_estimados=(
                                "kg_referencia_lata",
                                "sum",
                            ),
        
                            kg_promedio_lata=(
                                "kg_referencia_lata",
                                "mean",
                            ),
                        )
                        .sort_values(
                            [
                                "latas_disponibles",
                                "sabor",
                            ],
                            ascending=[
                                False,
                                True,
                            ],
                        )
                    )
        
                    camera_summary[
                        "kg_estimados"
                    ] = (
                        camera_summary[
                            "kg_estimados"
                        ]
                        .fillna(0)
                        .round(3)
                    )
        
                    camera_summary[
                        "kg_promedio_lata"
                    ] = (
                        camera_summary[
                            "kg_promedio_lata"
                        ]
                        .fillna(0)
                        .round(3)
                    )
        
                    st.dataframe(
                        camera_summary,
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "sabor":
                                st.column_config.TextColumn(
                                    "Sabor"
                                ),
        
                            "latas_disponibles":
                                st.column_config.NumberColumn(
                                    "Latas disponibles",
                                    format="%d",
                                ),
        
                            "kg_estimados":
                                st.column_config.NumberColumn(
                                    "Kg estimados",
                                    format="%.3f kg",
                                ),
        
                            "kg_promedio_lata":
                                st.column_config.NumberColumn(
                                    "Promedio por lata",
                                    format="%.3f kg",
                                ),
                        },
                    )
        
                    st.divider()
        
                    # --------------------------------------------------------
                    # Detalle individual + filtro por sabor
                    # --------------------------------------------------------
        
                    st.markdown(
                        "#### 🔎 Detalle de latas"
                    )
        
                    camera_flavor_options = (
                        camera[
                            "sabor"
                        ]
                        .dropna()
                        .astype(str)
                        .drop_duplicates()
                        .sort_values()
                        .tolist()
                    )
        
                    selected_camera_flavor = st.selectbox(
                        "Filtrar por sabor",
                        options=[
                            "Todos",
                            *camera_flavor_options,
                        ],
                        index=0,
                        key="camera_stock_flavor_filter",
                    )
        
                    filtered_camera = (
                        camera.copy()
                        if selected_camera_flavor
                        == "Todos"
                        else camera[
                            camera[
                                "sabor"
                            ]
                            .eq(
                                selected_camera_flavor
                            )
                        ].copy()
                    )
        
                    filter_c1, filter_c2 = st.columns(
                        2
                    )
        
                    filter_c1.metric(
                        "Latas mostradas",
                        int(
                            len(
                                filtered_camera
                            )
                        ),
                    )
        
                    filter_c2.metric(
                        "Kg mostrados",
                        (
                            f"{float(filtered_camera['kg_referencia_lata'].fillna(0).sum()):.3f} kg"
                        ),
                    )
        
                    camera_editor = filtered_camera[
                        [
                            "stock_id",
                            "sabor",
                            "estado",
                            "kg_referencia_lata",
                            "ingresada_camera_at",
                        ]
                    ].copy()
        
                    camera_editor.insert(
                        0,
                        "anular",
                        False,
                    )
        
                    edited_camera = st.data_editor(
                        camera_editor,
                        hide_index=True,
                        use_container_width=True,
                        disabled=[
                            "stock_id",
                            "sabor",
                            "estado",
                            "kg_referencia_lata",
                            "ingresada_camera_at",
                        ],
                        column_config={
                            "anular":
                                st.column_config.CheckboxColumn(
                                    "🗑️ Anular",
                                    help=(
                                        "Marca una o más latas cargadas por error."
                                    ),
                                    default=False,
                                ),
        
                            "stock_id":
                                st.column_config.TextColumn(
                                    "Lata"
                                ),
        
                            "sabor":
                                st.column_config.TextColumn(
                                    "Sabor"
                                ),
        
                            "estado":
                                st.column_config.TextColumn(
                                    "Estado"
                                ),
        
                            "kg_referencia_lata":
                                st.column_config.NumberColumn(
                                    "Kg referencia",
                                    format="%.3f kg",
                                ),
        
                            "ingresada_camera_at":
                                st.column_config.TextColumn(
                                    "Ingreso cámara"
                                ),
                        },
                        key=(
                            "camera_stock_editor_"
                            + selected_camera_flavor
                            .replace(
                                " ",
                                "_",
                            )
                        ),
                    )
        
                    selected_to_annul = (
                        edited_camera.loc[
                            edited_camera[
                                "anular"
                            ]
                            == True,
                            "stock_id",
                        ]
                        .astype(str)
                        .tolist()
                    )
        
                    if selected_to_annul:
                        st.warning(
                            f"Vas a anular "
                            f"{len(selected_to_annul)} "
                            f"lata(s) de cámara. "
                            "No se borran del historial."
                        )
        
                        annul_notes = st.text_input(
                            "Motivo / observación de anulación",
                            placeholder=(
                                "Ej: carga duplicada, cantidad incorrecta..."
                            ),
                            key=(
                                "camera_annul_notes_"
                                + selected_camera_flavor
                                .replace(
                                    " ",
                                    "_",
                                )
                            ),
                        )
        
                        confirm_annul = st.checkbox(
                            "Confirmo que quiero anular las latas seleccionadas",
                            key=(
                                "confirm_camera_annul_"
                                + selected_camera_flavor
                                .replace(
                                    " ",
                                    "_",
                                )
                            ),
                        )
        
                        if st.button(
                            "🗑️ Anular seleccionadas",
                            key=(
                                "annul_camera_selected_"
                                + selected_camera_flavor
                                .replace(
                                    " ",
                                    "_",
                                )
                            ),
                            disabled=(
                                not confirm_annul
                            ),
                        ):
                            ok, result = run_ui_mutation(
                                running_label=
                                    "Anulando latas seleccionadas de cámara...",
        
                                success_label=
                                    lambda result:
                                        (
                                            f"Se anularon "
                                            f"{result['cantidad']} lata(s) · "
                                            f"{result['operation_id']}"
                                        ),
        
                                error_label=
                                    "No se pudieron anular las latas.",
        
                                operation=
                                    lambda:
                                        annul_camera_latas(
                                            selected_to_annul,
                                            notes=
                                                annul_notes,
                                        ),
                            )
        
                            if ok:
                                st.rerun()
        
                st.divider()
        
                flavors = load_flavors()
        
                if not flavors:
                    st.warning(
                        "No hay sabores configurados. "
                        "Agregalos desde Configuración."
                    )
                else:
                    a1, a2, a3 = st.columns(
                        3
                    )
        
                    with a1:
                        camera_sabor = st.selectbox(
                            "Sabor",
                            options=flavors,
                            index=None,
                            placeholder="Seleccionar sabor",
                            key="camera_sabor",
                        )
        
                    with a2:
                        camera_qty = st.number_input(
                            "Cantidad de latas",
                            min_value=1,
                            value=1,
                            step=1,
                            key="camera_qty",
                        )
        
                    with a3:
                        camera_ref = st.number_input(
                            "Peso neto de referencia por lata (kg)",
                            min_value=0.100,
                            max_value=MAX_CAN_GROSS_KG,
                            value=DEFAULT_CAMERA_CAN_KG,
                            step=0.005,
                            format="%.3f",
                            key="camera_ref",
                            help=(
                                "La app guarda todo en kilogramos. "
                                "Ejemplo: 7580 g = 7.580 kg."
                            ),
                        )
        
                    st.caption(
                        "Podés cargar varias juntas: si ponés 9, la app crea "
                        "9 IDs individuales CAM-xxx-xxxxxx."
                    )
        
                    camera_notes = st.text_area(
                        "Observaciones",
                        key="camera_notes",
                    )
        
                    if st.button(
                        "Agregar stock a cámara",
                        type="primary",
                        key="add_camera",
                    ):
                        if not camera_sabor:
                            st.error(
                                "Seleccioná un sabor."
                            )
                        else:
                            ok, camera_ids = run_ui_mutation(
                                running_label=
                                    (
                                        f"Agregando {int(camera_qty)} lata(s) "
                                        f"de {camera_sabor} a cámara..."
                                    ),
        
                                success_label=
                                    lambda camera_ids:
                                        (
                                            f"Stock agregado correctamente · "
                                            f"{len(camera_ids)} lata(s)."
                                        ),
        
                                error_label=
                                    "Falló el ingreso de stock a cámara.",
        
                                operation=
                                    lambda:
                                        add_camera_stock(
                                            sabor=
                                                camera_sabor,
        
                                            cantidad_latas=
                                                camera_qty,
        
                                            kg_referencia_lata=
                                                camera_ref,
        
                                            notes=
                                                camera_notes,
                                        ),
                            )
        
                            if ok:
                                st.rerun()
        

        def render_camera_product_category(category_name,tab_key):
            products=load_camera_products(active_only=True)
            category_products=products[products["categoria"].astype(str).eq(category_name)].copy()
            allowed_subcategories=CATEGORY_SUBCATEGORIES.get(category_name,[])
            selected_subcategory_filter="TODAS"
            if allowed_subcategories:
                selected_subcategory_filter=st.selectbox("Filtrar subcategoría",["TODAS",*allowed_subcategories],key=f"camera_product_subcategory_filter_{tab_key}")
                if selected_subcategory_filter!="TODAS": category_products=category_products[category_products["subcategoria"].astype(str).eq(selected_subcategory_filter)].copy()
            if category_products.empty: st.info(f"No hay productos cargados en {category_name.replace('_',' ').title()}.")
            else:
                k1,k2=st.columns(2); k1.metric("Productos",int(category_products["producto"].nunique())); k2.metric("Unidades totales",int(pd.to_numeric(category_products["total_unidades"],errors="coerce").fillna(0).sum()))
                display=category_products[["product_stock_id","product_code","subcategoria","producto","packaging_mode","cantidad_packs","cantidad_cajas","total_cajas","total_unidades","created_at"]].copy(); display.insert(0,"anular",False)
                edited_products=st.data_editor(display,hide_index=True,use_container_width=True,disabled=[c for c in display.columns if c!="anular"],key=f"camera_products_editor_{tab_key}")
                selected_products=edited_products.loc[edited_products["anular"]==True,"product_stock_id"].astype(str).tolist()
                if selected_products:
                    notes=st.text_input("Motivo de anulación",key=f"camera_product_annul_notes_{tab_key}"); confirm=st.checkbox("Confirmo la anulación",key=f"camera_product_annul_confirm_{tab_key}")
                    if st.button("🗑️ Anular seleccionadas",key=f"camera_product_annul_button_{tab_key}",disabled=not confirm):
                        def op(): return [annul_camera_product(product_stock_id=x,notes=notes) for x in selected_products]
                        ok,_=run_ui_mutation(running_label="Anulando productos...",success_label="Productos anulados.",error_label="No se pudieron anular.",operation=op)
                        if ok: st.rerun()
            st.divider(); st.markdown("#### ➕ Agregar stock")
            sub=None
            if allowed_subcategories: sub=st.selectbox("Subcategoría",allowed_subcategories,key=f"camera_product_subcategory_{tab_key}")
            catalog=catalog_products_for(categoria=category_name,subcategoria=sub)
            if catalog.empty:
                st.warning("No hay productos configurados. Agregalos en Configuración → Productos."); return
            def label(row):
                mode = str(
                    row.get(
                        "packaging_mode",
                        PACKAGING_PACK_UNITS,
                    )
                )

                cajas_por_pack = pd.to_numeric(
                    row.get(
                        "cajas_por_pack",
                        row.get(
                            "cajas_por_bulto",
                            pd.NA,
                        ),
                    ),
                    errors="coerce",
                )

                unidades_por_pack = pd.to_numeric(
                    row.get(
                        "unidades_por_pack",
                        row.get(
                            "unidades_por_bulto",
                            pd.NA,
                        ),
                    ),
                    errors="coerce",
                )

                unidades_por_caja = pd.to_numeric(
                    row.get(
                        "unidades_por_caja",
                        pd.NA,
                    ),
                    errors="coerce",
                )

                if mode == PACKAGING_PACK_BOXES_UNITS:
                    if (
                        pd.isna(
                            cajas_por_pack
                        )
                        or pd.isna(
                            unidades_por_caja
                        )
                    ):
                        detail = "configuración incompleta"
                    else:
                        detail = (
                            f"{int(cajas_por_pack)} cajas/pack × "
                            f"{int(unidades_por_caja)} unid/caja"
                        )

                elif mode == PACKAGING_PACK_UNITS:
                    if pd.isna(
                        unidades_por_pack
                    ):
                        detail = "configuración incompleta"
                    else:
                        detail = (
                            f"{int(unidades_por_pack)} unid/pack"
                        )

                else:
                    if pd.isna(
                        unidades_por_caja
                    ):
                        detail = "configuración incompleta"
                    else:
                        detail = (
                            f"{int(unidades_por_caja)} unid/caja"
                        )

                return (
                    f"{row.get('producto', 'SIN_NOMBRE')} · "
                    f"{detail}"
                )
            mp={label(r):r["product_code"] for _,r in catalog.iterrows()}; lbl=st.selectbox("Producto",list(mp),key=f"camera_product_catalog_select_{tab_key}"); code=mp[lbl]; row=catalog[catalog["product_code"].astype(str).eq(code)].iloc[0]
            mode = str(
                row.get(
                    "packaging_mode",
                    PACKAGING_PACK_UNITS,
                )
            )

            cpp_value = pd.to_numeric(
                row.get(
                    "cajas_por_pack",
                    row.get(
                        "cajas_por_bulto",
                        pd.NA,
                    ),
                ),
                errors="coerce",
            )

            upp_value = pd.to_numeric(
                row.get(
                    "unidades_por_pack",
                    row.get(
                        "unidades_por_bulto",
                        pd.NA,
                    ),
                ),
                errors="coerce",
            )

            upc_value = pd.to_numeric(
                row.get(
                    "unidades_por_caja",
                    pd.NA,
                ),
                errors="coerce",
            )

            cpp = (
                int(
                    cpp_value
                )
                if pd.notna(
                    cpp_value
                )
                else None
            )

            upp = (
                int(
                    upp_value
                )
                if pd.notna(
                    upp_value
                )
                else None
            )

            upc = (
                int(
                    upc_value
                )
                if pd.notna(
                    upc_value
                )
                else None
            )
            packaging_config_valid = True
            packaging_error = None

            if mode == PACKAGING_PACK_BOXES_UNITS:
                if cpp is None or upc is None:
                    packaging_config_valid = False
                    packaging_error = (
                        "Este producto necesita Cajas por pack y "
                        "Unidades por caja."
                    )

            elif mode == PACKAGING_PACK_UNITS:
                if upp is None:
                    packaging_config_valid = False
                    packaging_error = (
                        "Este producto necesita Unidades por pack."
                    )

            elif mode == PACKAGING_BOX_UNITS:
                if upc is None:
                    packaging_config_valid = False
                    packaging_error = (
                        "Este producto necesita Unidades por caja."
                    )

            else:
                packaging_config_valid = False
                packaging_error = (
                    f"Packaging mode desconocido: {mode}"
                )

            with st.form(
                f"camera_product_stock_form_{tab_key}"
            ):
                packs = None
                cajas = None
                total = None

                if not packaging_config_valid:
                    st.error(
                        packaging_error
                    )

                    st.caption(
                        "Corregí este producto en "
                        "Configuración → Productos."
                    )

                elif mode == PACKAGING_PACK_BOXES_UNITS:
                    st.caption(
                        "PACK → CAJAS → UNIDADES"
                    )

                    a, b = st.columns(
                        2
                    )

                    a.metric(
                        "Cajas por pack",
                        cpp,
                    )

                    b.metric(
                        "Unidades por caja",
                        upc,
                    )

                    packs = st.number_input(
                        "Cantidad de packs",
                        min_value=1,
                        value=1,
                        step=1,
                    )

                    total = (
                        packs
                        * cpp
                        * upc
                    )

                    st.info(
                        f"{packs} pack(s) × {cpp} cajas × {upc} unidades "
                        f"= **{total} unidades**. "
                        f"Se crearán **{packs} IDs individuales**, uno por pack."
                    )

                elif mode == PACKAGING_PACK_UNITS:
                    st.caption(
                        "PACK → UNIDADES"
                    )

                    st.metric(
                        "Unidades por pack",
                        upp,
                    )

                    packs = st.number_input(
                        "Cantidad de packs",
                        min_value=1,
                        value=1,
                        step=1,
                    )

                    total = (
                        packs
                        * upp
                    )

                    st.info(
                        f"{packs} pack(s) × {upp} unidades "
                        f"= **{total} unidades**. "
                        f"Se crearán **{packs} IDs individuales**, uno por pack."
                    )

                elif mode == PACKAGING_BOX_UNITS:
                    st.caption(
                        "CAJA → UNIDADES"
                    )

                    st.metric(
                        "Unidades por caja",
                        upc,
                    )

                    cajas = st.number_input(
                        "Cantidad de cajas",
                        min_value=1,
                        value=1,
                        step=1,
                    )

                    total = (
                        cajas
                        * upc
                    )

                    st.info(
                        f"{cajas} caja(s) × {upc} unidades "
                        f"= **{total} unidades**. "
                        f"Se crearán **{cajas} IDs individuales**, uno por caja."
                    )

                notes = st.text_area(
                    "Observaciones"
                )

                submit = st.form_submit_button(
                    "Agregar a cámara",
                    type="primary",
                    use_container_width=True,
                    disabled=(
                        not packaging_config_valid
                    ),
                )
            if submit:
                ok, result = run_ui_mutation(
                    running_label=
                        f"Agregando {row['producto']} a cámara...",

                    success_label=
                        lambda result:
                            (
                                f"Stock agregado · "
                                f"{result['physical_count']} "
                                f"{result['physical_label']}(s) · "
                                f"{result['total_unidades']} unidades · "
                                f"{len(result['created_ids'])} IDs creados."
                            ),

                    error_label=
                        "No se pudo agregar el stock.",

                    operation=
                        lambda:
                            add_camera_product(
                                product_code=
                                    row[
                                        "product_code"
                                    ],

                                categoria=
                                    row[
                                        "categoria"
                                    ],

                                subcategoria=
                                    (
                                        row[
                                            "subcategoria"
                                        ]
                                        if pd.notna(
                                            row[
                                                "subcategoria"
                                            ]
                                        )
                                        else None
                                    ),

                                producto=
                                    row[
                                        "producto"
                                    ],

                                packaging_mode=
                                    mode,

                                cantidad_packs=
                                    packs,

                                cantidad_cajas=
                                    cajas,

                                cajas_por_pack=
                                    cpp,

                                unidades_por_pack=
                                    upp,

                                unidades_por_caja=
                                    upc,

                                notes=
                                    notes,
                            ),
                )

                if ok:
                    st.rerun()


        with familiares_tab:
            render_camera_product_category(
                "FAMILIARES",
                "familiares",
            )

        with tentaciones_tab:
            render_camera_product_category(
                "TENTACIONES",
                "tentaciones",
            )

        with postres_tab:
            render_camera_product_category(
                "POSTRES",
                "postres",
            )

        with tortas_tab:
            render_camera_product_category(
                "TORTAS",
                "tortas",
            )

        with bombones_tab:
            render_camera_product_category(
                "BOMBONES",
                "bombones",
            )

        with palitos_tab:
            render_camera_product_category(
                "PALITOS",
                "palitos",
            )

        with especiales_tab:
            render_camera_product_category(
                "LINEAS_ESPECIALES",
                "lineas_especiales",
            )

        with frizzio_tab:
            render_camera_product_category(
                "FRIZZIO",
                "frizzio",
            )


    with salon_tab:
        stock = load_current_stock()

        salon = stock[
            (
                stock[
                    "location"
                ]
                == "SALON"
            )
            &
            (
                stock[
                    "active"
                ]
                == True
            )
        ].copy()

        st.subheader(
            "🍦 Salón / Freezers"
        )

        if salon.empty:
            st.info(
                "No hay latas activas."
            )

        else:
            st.dataframe(
                salon[
                    [
                        "stock_id",
                        "sabor",
                        "estado",
                        "peso_inicial_neto_kg",
                        "peso_actual_neto_kg",
                        "ingresada_salon_at",
                        "opened_at",
                        "updated_at",
                    ]
                ],
                hide_index=True,
                use_container_width=True,
            )



        st.divider()

        st.markdown(
            "### 🔧 Operación de latas"
        )

        closed_salon = salon[
            salon[
                "estado"
            ]
            .astype(str)
            .str.upper()
            .eq("CERRADA")
        ].copy()

        open_salon = salon[
            salon[
                "estado"
            ]
            .astype(str)
            .str.upper()
            .eq("ABIERTA")
        ].copy()

        op1, op2 = st.columns(
            2
        )

        with op1:
            st.markdown(
                "#### 🔓 Abrir lata cerrada"
            )

            if closed_salon.empty:
                st.info(
                    "No hay latas cerradas activas en salón."
                )

            else:
                open_options = {}

                for _, row in closed_salon.iterrows():
                    peso = pd.to_numeric(
                        row[
                            "peso_actual_neto_kg"
                        ],
                        errors="coerce",
                    )

                    peso_txt = (
                        f"{peso:.3f} kg"
                        if pd.notna(peso)
                        else "sin peso"
                    )

                    label = (
                        f"{row['stock_id']} · "
                        f"{row['sabor']} · "
                        f"{peso_txt}"
                    )

                    open_options[
                        label
                    ] = row[
                        "stock_id"
                    ]

                selected_open_label = st.selectbox(
                    "Lata cerrada",
                    options=list(
                        open_options.keys()
                    ),
                    key="open_salon_can_select",
                )

                open_notes = st.text_input(
                    "Observación de apertura",
                    key="open_salon_can_notes",
                )

                if st.button(
                    "🔓 Abrir lata",
                    type="primary",
                    key="open_salon_can_button",
                ):
                    selected_open_stock_id = (
                        open_options[
                            selected_open_label
                        ]
                    )

                    ok, _ = run_ui_mutation(
                        running_label=
                            (
                                f"Abriendo lata "
                                f"{selected_open_stock_id}..."
                            ),

                        success_label=
                            "Lata abierta y movimiento registrado.",

                        error_label=
                            "No se pudo abrir la lata.",

                        operation=
                            lambda:
                                open_salon_can(
                                    stock_id=
                                        selected_open_stock_id,

                                    notes=
                                        open_notes,
                                ),
                    )

                    if ok:
                        st.rerun()

        with op2:
            st.markdown(
                "#### ✅ Terminar lata abierta"
            )

            if open_salon.empty:
                st.info(
                    "No hay latas abiertas activas en salón."
                )

            else:
                finish_options = {}

                for _, row in open_salon.iterrows():
                    peso = pd.to_numeric(
                        row[
                            "peso_actual_neto_kg"
                        ],
                        errors="coerce",
                    )

                    peso_txt = (
                        f"{peso:.3f} kg"
                        if pd.notna(peso)
                        else "sin peso"
                    )

                    label = (
                        f"{row['stock_id']} · "
                        f"{row['sabor']} · "
                        f"{peso_txt}"
                    )

                    finish_options[
                        label
                    ] = row[
                        "stock_id"
                    ]

                selected_finish_label = st.selectbox(
                    "Lata abierta",
                    options=list(
                        finish_options.keys()
                    ),
                    key="finish_salon_can_select",
                )

                selected_finish_id = (
                    finish_options[
                        selected_finish_label
                    ]
                )

                selected_finish_row = (
                    open_salon[
                        open_salon[
                            "stock_id"
                        ]
                        == selected_finish_id
                    ]
                    .iloc[0]
                )

                previous_tare = pd.to_numeric(
                    selected_finish_row[
                        "tara_actual_kg"
                    ],
                    errors="coerce",
                )

                if pd.isna(
                    previous_tare
                ):
                    previous_tare = (
                        DEFAULT_TARE_KG
                    )

                st.markdown(
                    "##### ⚖️ Pesaje final"
                )

                estimated_initial_tare = pd.to_numeric(
                    selected_finish_row[
                        "tara_inicial_kg"
                    ],
                    errors="coerce",
                )

                if pd.isna(
                    estimated_initial_tare
                ):
                    estimated_initial_tare = (
                        previous_tare
                    )

                finish_tara_final = st.number_input(
                    "Tara final real medida (kg)",
                    min_value=0.001,
                    max_value=MAX_CAN_GROSS_KG,
                    value=float(
                        round(
                            estimated_initial_tare,
                            3,
                        )
                    ),
                    step=0.005,
                    format="%.3f",
                    key="finish_tara_final",
                    help=(
                        "Peso real de la lata cuando se terminó. "
                        "Incluye el envase más el resto inevitable "
                        "que ya no se puede servir."
                    ),
                )

                finish_residue_estimated = round(
                    max(
                        finish_tara_final
                        - float(
                            estimated_initial_tare
                        ),
                        0.0,
                    ),
                    3,
                )

                p1, p2, p3 = st.columns(
                    3
                )

                p1.metric(
                    "Tara inicial estimada",
                    f"{float(estimated_initial_tare):.3f} kg",
                )

                p2.metric(
                    "Tara final real",
                    f"{finish_tara_final:.3f} kg",
                )

                p3.metric(
                    "Residuo estimado",
                    f"{finish_residue_estimated:.3f} kg",
                )

                finish_notes = st.text_input(
                    "Observación de finalización",
                    key="finish_salon_can_notes",
                )

                st.caption(
                    "Al finalizarla queda AGOTADA e inactiva. "
                    "La tara final real y el residuo estimado quedan guardados en la ficha de la lata."
                )

                if st.button(
                    "✅ Terminar lata",
                    key="finish_salon_can_button",
                ):
                    ok, result = run_ui_mutation(
                        running_label=
                            (
                                f"Finalizando lata "
                                f"{selected_finish_id}..."
                            ),

                        success_label=
                            lambda result:
                                (
                                    "Lata finalizada · "
                                    f"tara final "
                                    f"{result['tara_final_kg']:.3f} kg"
                                    + (
                                        f" · residuo estimado "
                                        f"{result['residuo_final_kg']:.3f} kg"
                                        if (
                                            result[
                                                "residuo_final_kg"
                                            ]
                                            is not None
                                        )
                                        else ""
                                    )
                                ),

                        error_label=
                            "No se pudo finalizar la lata.",

                        operation=
                            lambda:
                                mark_salon_can_empty(
                                    stock_id=
                                        selected_finish_id,

                                    tara_final_kg=
                                        finish_tara_final,

                                    peso_final_bruto_kg=
                                        None,

                                    notes=
                                        finish_notes,
                                ),
                    )

                    if ok:
                        st.rerun()



        st.divider()

        st.markdown(
            "### 🔄 Recambio de lata"
        )

        st.caption(
            "Finaliza una lata abierta. Después podés abrir una reserva "
            "del salón y, opcionalmente, reponer desde cámara."
        )

        salon_live = load_current_stock()

        salon_live = salon_live[
            (
                salon_live[
                    "location"
                ]
                .astype(str)
                .str.upper()
                .eq("SALON")
            )
            &
            (
                salon_live[
                    "active"
                ]
                == True
            )
        ].copy()

        open_for_change = salon_live[
            salon_live[
                "estado"
            ]
            .astype(str)
            .str.upper()
            .eq("ABIERTA")
        ].copy()

        if open_for_change.empty:
            st.info(
                "No hay latas abiertas activas para realizar un recambio."
            )

        else:
            change_options = {}

            for _, row in open_for_change.iterrows():
                peso = pd.to_numeric(
                    row[
                        "peso_actual_neto_kg"
                    ],
                    errors="coerce",
                )

                peso_txt = (
                    f"{peso:.3f} kg"
                    if pd.notna(peso)
                    else "sin peso"
                )

                label = (
                    f"{row['stock_id']} · "
                    f"{row['sabor']} · "
                    f"{peso_txt}"
                )

                change_options[
                    label
                ] = row[
                    "stock_id"
                ]

            selected_change_label = st.selectbox(
                "Lata abierta que se terminó",
                options=list(
                    change_options.keys()
                ),
                key="replacement_current_open",
            )

            current_change_id = (
                change_options[
                    selected_change_label
                ]
            )

            current_change_row = (
                open_for_change[
                    open_for_change[
                        "stock_id"
                    ]
                    == current_change_id
                ]
                .iloc[0]
            )

            change_flavor = normalize_flavor_name(
                current_change_row[
                    "sabor"
                ]
            )

            same_flavor_closed = salon_live[
                (
                    salon_live[
                        "estado"
                    ]
                    .astype(str)
                    .str.upper()
                    .eq("CERRADA")
                )
                &
                (
                    salon_live[
                        "sabor"
                    ]
                    .map(
                        normalize_flavor_name
                    )
                    .eq(
                        change_flavor
                    )
                )
            ].copy()

            camera_live = load_current_stock()

            camera_same_flavor = camera_live[
                (
                    camera_live[
                        "location"
                    ]
                    .astype(str)
                    .str.upper()
                    .eq("CAMARA")
                )
                &
                (
                    camera_live[
                        "active"
                    ]
                    == True
                )
                &
                (
                    camera_live[
                        "cantidad_latas"
                    ]
                    .fillna(0)
                    > 0
                )
                &
                (
                    camera_live[
                        "sabor"
                    ]
                    .map(
                        normalize_flavor_name
                    )
                    .eq(
                        change_flavor
                    )
                )
            ].copy()

            camera_qty_for_flavor = int(
                len(
                    camera_same_flavor
                )
            )

            r1, r2, r3 = st.columns(
                3
            )

            r1.metric(
                "Sabor",
                change_flavor,
            )

            r2.metric(
                "Reservas cerradas en salón",
                len(
                    same_flavor_closed
                ),
            )

            r3.metric(
                "Latas disponibles en cámara",
                camera_qty_for_flavor,
            )

            st.markdown(
                "#### 0. Finalización de la lata que se terminó"
            )

            current_change_tare = pd.to_numeric(
                current_change_row[
                    "tara_inicial_kg"
                ],
                errors="coerce",
            )

            if pd.isna(
                current_change_tare
            ):
                current_change_tare = pd.to_numeric(
                    current_change_row[
                        "tara_actual_kg"
                    ],
                    errors="coerce",
                )

            if pd.isna(
                current_change_tare
            ):
                current_change_tare = (
                    DEFAULT_TARE_KG
                )

            replacement_final_tare = st.number_input(
                "Tara final real medida (kg)",
                min_value=0.001,
                max_value=MAX_CAN_GROSS_KG,
                value=float(
                    round(
                        current_change_tare,
                        3,
                    )
                ),
                step=0.005,
                format="%.3f",
                key="replacement_final_tare",
                help=(
                    "Peso real de la lata terminada: "
                    "envase + resto inevitable no servible."
                ),
            )

            replacement_residue_estimated = round(
                max(
                    replacement_final_tare
                    - float(
                        current_change_tare
                    ),
                    0.0,
                ),
                3,
            )

            rp1, rp2, rp3 = st.columns(
                3
            )

            rp1.metric(
                "Tara inicial estimada",
                f"{float(current_change_tare):.3f} kg",
            )

            rp2.metric(
                "Tara final real",
                f"{replacement_final_tare:.3f} kg",
            )

            rp3.metric(
                "Residuo estimado",
                f"{replacement_residue_estimated:.3f} kg",
            )

            replacement_finished_gross = None

            st.markdown(
                "#### 1. Reserva del salón"
            )

            reserve_stock_id = None

            if same_flavor_closed.empty:
                st.warning(
                    "No hay una lata cerrada de este sabor en el salón."
                )

            else:
                use_reserve = st.checkbox(
                    "Abrir una lata cerrada de reserva",
                    value=True,
                    key="replacement_use_reserve",
                )

                if use_reserve:
                    reserve_options = {}

                    for _, row in same_flavor_closed.iterrows():
                        peso = pd.to_numeric(
                            row[
                                "peso_actual_neto_kg"
                            ],
                            errors="coerce",
                        )

                        peso_txt = (
                            f"{peso:.3f} kg"
                            if pd.notna(peso)
                            else "sin peso"
                        )

                        label = (
                            f"{row['stock_id']} · "
                            f"{peso_txt}"
                        )

                        reserve_options[
                            label
                        ] = row[
                            "stock_id"
                        ]

                    selected_reserve_label = st.selectbox(
                        "Reserva a abrir",
                        options=list(
                            reserve_options.keys()
                        ),
                        key="replacement_reserve",
                    )

                    reserve_stock_id = (
                        reserve_options[
                            selected_reserve_label
                        ]
                    )

            st.markdown(
                "#### 2. Reposición desde cámara"
            )

            replenish_from_camera = False

            if camera_qty_for_flavor <= 0:
                st.info(
                    "No hay stock de este sabor en cámara. "
                    "El recambio puede hacerse igualmente."
                )

            else:
                replenish_from_camera = st.checkbox(
                    "Traer una nueva lata desde cámara",
                    value=False,
                    key="replacement_replenish",
                )

            replacement_bruto = None
            replacement_tara = None

            if replenish_from_camera:
                rc1, rc2, rc3 = st.columns(
                    3
                )

                with rc1:
                    replacement_bruto = st.number_input(
                        "Peso bruto nueva lata (kg)",
                        min_value=0.0,
                        max_value=MAX_CAN_GROSS_KG,
                        value=0.0,
                        step=0.005,
                        format="%.3f",
                        key="replacement_bruto",
                        help="Ejemplo: 7856 g = 7.856 kg.",
                    )

                with rc2:
                    replacement_tara = st.number_input(
                        "Tara nueva lata (kg)",
                        min_value=0.0,
                        max_value=MAX_TARE_KG,
                        value=DEFAULT_TARE_KG,
                        step=0.005,
                        format="%.3f",
                        key="replacement_tara",
                        help="Ejemplo: 380 g = 0.380 kg.",
                    )

                replacement_net = round(
                    max(
                        replacement_bruto
                        - replacement_tara,
                        0.0,
                    ),
                    3,
                )

                with rc3:
                    st.metric(
                        "Peso neto nueva lata",
                        f"{replacement_net:.3f} kg",
                    )

                if reserve_stock_id:
                    st.caption(
                        "La lata nueva quedará CERRADA como reserva."
                    )
                else:
                    st.caption(
                        "Como no se abrirá una reserva del salón, "
                        "la lata nueva quedará ABIERTA directamente."
                    )

            replacement_notes = st.text_area(
                "Observaciones del recambio",
                key="replacement_notes",
            )

            if st.button(
                "🔄 Realizar recambio",
                type="primary",
                key="perform_replacement_button",
            ):
                ok, result = run_ui_mutation(
                    running_label=
                        (
                            f"Registrando recambio de "
                            f"{current_change_id}..."
                        ),

                    success_label=
                        lambda result:
                            (
                                f"Recambio registrado · "
                                f"{result['operation_id']}"
                                + (
                                    f" · Abierta "
                                    f"{result['opened_reserve_stock_id']}"
                                    if (
                                        result[
                                            "opened_reserve_stock_id"
                                        ]
                                        is not None
                                    )
                                    else ""
                                )
                                + (
                                    f" · Nueva "
                                    f"{result['new_salon_stock_id']} "
                                    f"{result['new_salon_state']}"
                                    if result[
                                        "replenished"
                                    ]
                                    else ""
                                )
                                + (
                                    f" · CAMBIO_SABOR → "
                                    f"{result['cambio_sabor_target_stock_id']}"
                                    if result[
                                        "cambio_sabor_registered"
                                    ]
                                    else ""
                                )
                            ),

                    error_label=
                        "No se pudo completar el recambio.",

                    operation=
                        lambda:
                            perform_salon_replacement(
                                current_open_stock_id=
                                    current_change_id,

                                tara_final_kg=
                                    replacement_final_tare,

                                peso_final_bruto_kg=
                                    replacement_finished_gross,

                                reserve_stock_id=
                                    reserve_stock_id,

                                replenish_from_camera=
                                    replenish_from_camera,

                                peso_bruto_kg=
                                    replacement_bruto,

                                tara_kg=
                                    replacement_tara,

                                notes=
                                    replacement_notes,
                            ),
                )

                if ok:
                    st.rerun()



# ============================================================
# CAMERA -> SALON
# ============================================================

with tab_transfer:
    st.header(
        "➡️ Cámara → Salón"
    )

    stock = load_current_stock()

    camera = stock[
        (
            stock[
                "location"
            ]
            == "CAMARA"
        )
        &
        (
            stock[
                "active"
            ]
            == True
        )
    ].copy()

    if camera.empty:
        st.warning(
            "No hay latas disponibles."
        )

    else:
        camera_by_flavor = (
            camera
            .groupby(
                "sabor",
                as_index=False,
            )
            .size()
            .rename(
                columns={
                    "size":
                        "cantidad_latas"
                }
            )
            .sort_values(
                "sabor"
            )
        )

        flavor_options = {}

        for _, row in camera_by_flavor.iterrows():

            sabor = normalize_flavor_name(
                row["sabor"]
            )

            cantidad = int(
                row["cantidad_latas"]
            )

            label = (
                f"{sabor} · "
                f"{cantidad} disponibles"
            )

            flavor_options[
                label
            ] = sabor


        selected_label = st.selectbox(
            "Stock de cámara",
            list(
                flavor_options.keys()
            ),
        )

        selected_flavor = (
            flavor_options[
                selected_label
            ]
        )

        selected_camera_rows = camera[
            camera[
                "sabor"
            ]
            .map(
                normalize_flavor_name
            )
            .eq(
                selected_flavor
            )
        ].copy()

        selected_camera_rows[
            "_created_dt"
        ] = pd.to_datetime(
            selected_camera_rows[
                "created_at"
            ],
            errors="coerce",
        )

        selected_camera_rows = (
            selected_camera_rows
            .sort_values(
                "_created_dt",
                ascending=True,
                na_position="last",
            )
        )

        fifo_camera_id = (
            selected_camera_rows
            .iloc[0][
                "stock_id"
            ]
        )

        st.caption(
            f"Se moverá por FIFO la lata {fifo_camera_id}."
        )

        p1, p2, p3 = st.columns(
            3
        )

        with p1:
            transfer_bruto = st.number_input(
                "Peso bruto (kg)",
                min_value=0.0,
                max_value=MAX_CAN_GROSS_KG,
                value=0.0,
                step=0.005,
                format="%.3f",
                help="Ejemplo: 7856 g = 7.856 kg.",
            )

        with p2:
            transfer_tara = st.number_input(
                "Tara (kg)",
                min_value=0.0,
                max_value=MAX_TARE_KG,
                value=DEFAULT_TARE_KG,
                step=0.005,
                format="%.3f",
                help="Ejemplo: 380 g = 0.380 kg.",
            )

        transfer_net = round(
            max(
                transfer_bruto
                - transfer_tara,
                0,
            ),
            3,
        )

        with p3:
            st.metric(
                "Peso neto",
                f"{transfer_net:.3f} kg",
            )

        transfer_notes = st.text_area(
            "Observaciones",
        )

        if st.button(
            "Registrar y pasar al salón",
            type="primary",
        ):
            ok, result = run_ui_mutation(
                running_label=
                    (
                        f"Moviendo {fifo_camera_id} "
                        f"de cámara al salón..."
                    ),

                success_label=
                    lambda result:
                        (
                            f"Lata trasladada al salón · "
                            f"{result[0]} · "
                            f"{result[1]:.3f} kg netos."
                        ),

                error_label=
                    "No se pudo pasar la lata al salón.",

                operation=
                    lambda:
                        move_camera_flavor_to_salon(
                            sabor=
                                selected_flavor,

                            peso_bruto_kg=
                                transfer_bruto,

                            tara_kg=
                                transfer_tara,

                            notes=
                                transfer_notes,
                        ),
            )

            if ok:
                st.rerun()


# ============================================================
# CONTEO SALÓN
# ============================================================

with tab_count:
    st.header(
        "⚖️ Conteo físico del salón"
    )

    open_week = get_open_week()

    if open_week is None:
        count_type_label = st.radio(
            "Tipo de conteo",
            [
                "Inicio de semana",
                "Control",
            ],
            horizontal=True,
        )

    else:
        count_type_label = st.radio(
            "Tipo de conteo",
            [
                "Control",
            ],
            horizontal=True,
        )

        st.info(
            "El conteo de CIERRE se realiza dentro de "
            "📅 Semanas → Ficha de semana → Cierre de semana. "
            "Acá podés seguir haciendo controles físicos intermedios."
        )

    COUNT_TYPE_MAP = {
        "Inicio de semana":
            "INICIO_SEMANA",

        "Control":
            "CONTROL",
    }

    count_type = (
        COUNT_TYPE_MAP[
            count_type_label
        ]
    )

    stock = load_current_stock()

    salon = stock[
        (
            stock[
                "location"
            ]
            == "SALON"
        )
        &
        (
            stock[
                "active"
            ]
            == True
        )
    ].copy()

    if salon.empty:
        count_table = pd.DataFrame(
            columns=[
                "stock_id",
                "sabor",
                "estado",
                "peso_bruto_kg",
                "tara_kg",
            ]
        )

    else:
        count_table = salon[
            [
                "stock_id",
                "sabor",
                "estado",
                "peso_actual_bruto_kg",
                "tara_actual_kg",
            ]
        ].copy()

        count_table = (
            count_table.rename(
                columns={
                    "peso_actual_bruto_kg":
                        "peso_bruto_kg",

                    "tara_actual_kg":
                        "tara_kg",
                }
            )
        )

    flavors = load_flavors()

    if not flavors:
        st.warning(
            "No hay sabores configurados. "
            "Agregalos desde Configuración."
        )

    edited_count = st.data_editor(
        count_table,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,

        column_config={
            "stock_id":
                st.column_config.TextColumn(
                    "Lata",
                    disabled=True,
                ),

            "sabor":
                st.column_config.SelectboxColumn(
                    "Sabor",
                    options=flavors,
                    required=True,
                ),

            "estado":
                st.column_config.SelectboxColumn(
                    "Estado",
                    options=[
                        "CERRADA",
                        "ABIERTA",
                    ],
                    default="ABIERTA",
                ),

            "peso_bruto_kg":
                st.column_config.NumberColumn(
                    "Peso bruto (kg)",
                    min_value=0.0,
                    max_value=MAX_CAN_GROSS_KG,
                    step=0.005,
                    format="%.3f",
                    help="Ejemplo: 7856 g = 7.856 kg.",
                ),

            "tara_kg":
                st.column_config.NumberColumn(
                    "Tara (kg)",
                    min_value=0.0,
                    max_value=MAX_TARE_KG,
                    step=0.005,
                    format="%.3f",
                    help="Ejemplo: 380 g = 0.380 kg.",
                ),
        },
        key="salon_count_editor",
    )

    preview = edited_count.copy()

    preview[
        "peso_bruto_kg"
    ] = pd.to_numeric(
        preview[
            "peso_bruto_kg"
        ],
        errors="coerce",
    )

    preview[
        "tara_kg"
    ] = pd.to_numeric(
        preview[
            "tara_kg"
        ],
        errors="coerce",
    )

    valid_weight_mask = (
        preview[
            "peso_bruto_kg"
        ].notna()
        &
        preview[
            "tara_kg"
        ].notna()
        &
        (
            preview[
                "peso_bruto_kg"
            ]
            >
            preview[
                "tara_kg"
            ]
        )
    )

    preview[
        "peso_neto_kg"
    ] = pd.NA

    preview.loc[
        valid_weight_mask,
        "peso_neto_kg"
    ] = (
        preview.loc[
            valid_weight_mask,
            "peso_bruto_kg"
        ]
        -
        preview.loc[
            valid_weight_mask,
            "tara_kg"
        ]
    ).round(3)

    preview_display = preview[
        preview[
            "sabor"
        ]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
    ].copy()

    st.markdown(
        "### Resultado del conteo"
    )

    if not preview_display.empty:
        st.dataframe(
            preview_display[
                [
                    "stock_id",
                    "sabor",
                    "estado",
                    "peso_neto_kg",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

        total_neto = pd.to_numeric(
            preview_display[
                "peso_neto_kg"
            ],
            errors="coerce",
        ).fillna(0).sum()

        nuevas = (
            preview_display[
                "stock_id"
            ]
            .isna()
            .sum()
        )

        existentes = (
            len(preview_display)
            - nuevas
        )

        c1, c2, c3 = st.columns(
            3
        )

        c1.metric(
            "Stock físico medido",
            f"{total_neto:.3f} kg",
        )

        c2.metric(
            "Latas existentes",
            existentes,
        )

        c3.metric(
            "Latas nuevas",
            nuevas,
        )

    count_notes = st.text_area(
        "Observaciones del conteo",
    )

    if st.button(
        "💾 Registrar conteo",
        type="primary",
    ):
        count_action_label = (
            "Iniciando semana y guardando conteo..."
            if count_type == "INICIO_SEMANA"
            else "Guardando conteo físico del salón..."
        )

        with st.status(
            f"⏳ {count_action_label}",
            expanded=True,
        ) as count_status:

            count_status.write(
                "Guardando inventario, movimientos y datos de la semana."
            )

            try:
                result = save_salon_count(
                    edited_df=
                        edited_count,

                    count_type=
                        count_type,

                    notes=
                        count_notes,
                )

                if result[
                    "valid_rows"
                ] == 0:
                    count_status.update(
                        label=
                            "❌ No se pudo guardar ninguna fila válida.",

                        state=
                            "error",

                        expanded=
                            True,
                    )

                    for error in result[
                        "errors"
                    ]:
                        st.warning(
                            error
                        )

                else:
                    if count_type == "INICIO_SEMANA":
                        final_label = (
                            f"Semana iniciada · "
                            f"{result['total_stock_kg']:.3f} kg"
                        )

                    else:
                        final_label = (
                            f"Conteo guardado · "
                            f"{result['total_stock_kg']:.3f} kg"
                        )

                    count_status.update(
                        label=
                            f"✅ {final_label}",

                        state=
                            "complete",

                        expanded=
                            False,
                    )

                    st.rerun()

            except Exception as exc:
                count_status.update(
                    label=
                        "❌ Falló el guardado del conteo.",

                    state=
                        "error",

                    expanded=
                        True,
                )

                st.error(
                    f"{type(exc).__name__}: {exc}"
                )


# ============================================================
# SEMANAS
# ============================================================

with tab_weeks:
    st.header(
        "📅 Semanas"
    )

    refresh_col1, refresh_col2 = st.columns(
        [
            1,
            3,
        ]
    )

    with refresh_col1:
        if st.button(
            "🔄 Recalcular metadata",
            key="refresh_week_metadata_button",
        ):
            ok, report = run_ui_mutation(
                running_label=
                    "Recalculando y sincronizando metadata...",

                success_label=
                    "Metadata recalculada y sincronizada.",

                error_label=
                    "Falló el recálculo de metadata.",

                operation=
                    lambda:
                        refresh_all_metadata(
                            show_result=True
                        ),
            )

            if ok:
                if report[
                    "ambiguous_stock_ids"
                ]:
                    st.warning(
                        "Hay IDs de lata duplicados. "
                        "La migración de metadata de esas latas fue omitida "
                        "para no asociar eventos a la fila incorrecta."
                    )

                st.rerun()

    with refresh_col2:
        st.caption(
            "La semana se recalcula desde stock_movements.csv, "
            "inventory_counts.csv y current_stock.csv. "
            "No necesitás volver a cargar el inventario."
        )

    refresh_all_metadata()

    weeks = load_weeks()

    if weeks.empty:
        st.info(
            "Todavía no hay semanas."
        )

    else:
        summary_columns = [
            "week_id",
            "status",
            "started_at",
            "closed_at",

            # Inventario / actividad
            "start_salon_kg",
            "start_camera_latas",
            "start_camera_kg",
            "camera_to_salon_latas",
            "cambios_sabor",
            "latas_terminadas",
            "latas_abiertas",
            "latas_con_tara_final",
            "tara_final_total_kg",

            # Peso real / nominal de las latas
            "camera_exit_gross_kg",
            "avg_final_tare_kg",
            "estimated_camera_exit_net_kg",
            "camera_nominal_expected_kg",
            "camera_nominal_deficit_kg",
            "camera_nominal_deficit_pct",

            # Universo nominal completo de la Week
            "nominal_analyzed_latas",
            "nominal_initial_closed_latas",
            "nominal_camera_latas",
            "nominal_in_range_latas",
            "nominal_deficit_latas",
            "nominal_in_range_pct",
            "nominal_deficit_total_kg",
            "nominal_excess_total_kg",
            "nominal_balance_total_kg",
            "nominal_avg_deviation_kg",
            "nominal_avg_deviation_pct",

            # Estado / merma
            "current_salon_kg",
            "current_camera_latas",
            "consumo_fisico_kg",
            "consumo_teorico_kg",
            "merma_kg",
            "merma_pct",
            "merma_latas_equivalentes",
        ]

        visible_columns = [
            col
            for col in summary_columns
            if col in weeks.columns
        ]

        weeks_summary_view = weeks[
            visible_columns
        ].copy()

        # Compact totals by category for the main weeks table.
        for category in PRODUCT_SNAPSHOT_CATEGORIES:
            column_name = (
                f"{category.lower()}_actual"
            )

            weeks_summary_view[
                column_name
            ] = weeks.apply(
                lambda row:
                    product_snapshot_display_value(
                        category,
                        products_snapshot_from_json(
                            (
                                row.get(
                                    "end_products_snapshot_json"
                                )
                                if str(
                                    row.get(
                                        "status",
                                        ""
                                    )
                                ).upper()
                                == "CLOSED"
                                else row.get(
                                    "current_products_snapshot_json"
                                )
                            )
                        ).get(
                            category,
                            {},
                        ),
                    ),
                axis=1,
            )

        st.dataframe(
            weeks_summary_view,
            hide_index=True,
            use_container_width=True,
            column_config={
                "start_salon_kg":
                    st.column_config.NumberColumn(
                        "Salón inicial",
                        format="%.3f kg",
                    ),

                "start_camera_kg":
                    st.column_config.NumberColumn(
                        "Cámara inicial",
                        format="%.3f kg",
                    ),

                "camera_to_salon_latas":
                    st.column_config.NumberColumn(
                        "Cámara → salón",
                        format="%d latas",
                    ),

                "tara_final_total_kg":
                    st.column_config.NumberColumn(
                        "Tara final acumulada",
                        format="%.3f kg",
                    ),

                "camera_exit_gross_kg":
                    st.column_config.NumberColumn(
                        "Bruto desde cámara",
                        format="%.3f kg",
                    ),

                "avg_final_tare_kg":
                    st.column_config.NumberColumn(
                        "Tara prom.",
                        format="%.3f kg",
                    ),

                "estimated_camera_exit_net_kg":
                    st.column_config.NumberColumn(
                        "Neto est. cámara",
                        format="%.3f kg",
                    ),

                "camera_nominal_expected_kg":
                    st.column_config.NumberColumn(
                        "Nominal esperado cámara",
                        format="%.3f kg",
                    ),

                "camera_nominal_deficit_kg":
                    st.column_config.NumberColumn(
                        "Déficit cámara",
                        format="%+.3f kg",
                    ),

                "camera_nominal_deficit_pct":
                    st.column_config.NumberColumn(
                        "Déficit cámara %",
                        format="%+.2f%%",
                    ),

                "nominal_analyzed_latas":
                    st.column_config.NumberColumn(
                        "Latas analizadas",
                        format="%d",
                    ),

                "nominal_initial_closed_latas":
                    st.column_config.NumberColumn(
                        "Cerradas inicio",
                        format="%d",
                    ),

                "nominal_camera_latas":
                    st.column_config.NumberColumn(
                        "Desde cámara",
                        format="%d",
                    ),

                "nominal_in_range_latas":
                    st.column_config.NumberColumn(
                        "En rango o mejor",
                        format="%d",
                    ),

                "nominal_deficit_latas":
                    st.column_config.NumberColumn(
                        "Déficit claro",
                        format="%d",
                    ),

                "nominal_in_range_pct":
                    st.column_config.NumberColumn(
                        "% en rango o mejor",
                        format="%.1f%%",
                    ),

                "nominal_deficit_total_kg":
                    st.column_config.NumberColumn(
                        "Déficit total",
                        format="%.3f kg",
                    ),

                "nominal_excess_total_kg":
                    st.column_config.NumberColumn(
                        "Excedente total",
                        format="%.3f kg",
                    ),

                "nominal_balance_total_kg":
                    st.column_config.NumberColumn(
                        "Balance vs nominal",
                        format="%+.3f kg",
                    ),

                "nominal_avg_deviation_kg":
                    st.column_config.NumberColumn(
                        "Desvío prom. vs 7.800",
                        format="%+.3f kg",
                    ),

                "nominal_avg_deviation_pct":
                    st.column_config.NumberColumn(
                        "Desvío prom. %",
                        format="%+.2f%%",
                    ),

                "current_salon_kg":
                    st.column_config.NumberColumn(
                        "Salón actual",
                        format="%.3f kg",
                    ),

                "consumo_fisico_kg":
                    st.column_config.NumberColumn(
                        "Consumo físico",
                        format="%.3f kg",
                    ),

                "consumo_teorico_kg":
                    st.column_config.NumberColumn(
                        "Consumo teórico",
                        format="%.3f kg",
                    ),

                "merma_kg":
                    st.column_config.NumberColumn(
                        "Merma",
                        format="%+.3f kg",
                    ),

                "merma_pct":
                    st.column_config.NumberColumn(
                        "Merma %",
                        format="%+.2f%%",
                    ),

                "merma_latas_equivalentes":
                    st.column_config.NumberColumn(
                        "Latas eq. merma",
                        format="%.2f",
                    ),
            },
        )

        st.divider()

        st.markdown(
            "### 📋 Ficha de semana"
        )

        week_options = {}

        for idx, row in weeks.iterrows():
            started = pd.to_datetime(
                row[
                    "started_at"
                ],
                errors="coerce",
            )

            started_text = (
                started.strftime(
                    "%d/%m/%Y %H:%M"
                )
                if pd.notna(
                    started
                )
                else str(
                    row[
                        "started_at"
                    ]
                )
            )

            label = (
                f"{row['week_id']} · "
                f"{row['status']} · "
                f"{started_text}"
            )

            week_options[
                label
            ] = idx

        selected_week_label = st.selectbox(
            "Semana",
            options=list(
                week_options.keys()
            ),
            key="week_detail_select",
        )

        selected_week_row = weeks.loc[
            week_options[
                selected_week_label
            ]
        ]

        week = Week.from_row(
            selected_week_row
        )

        d1, d2, d3 = st.columns(
            3
        )

        d1.metric(
            "Estado",
            week.status,
        )

        d2.metric(
            "Duración",
            (
                f"{week.elapsed_days(now_iso()):.2f} días"
                if week.elapsed_days(
                    now_iso()
                )
                is not None
                else "-"
            ),
        )

        d3.metric(
            "Consumo físico",
            (
                f"{week.consumo_fisico_kg:.3f} kg"
                if week.consumo_fisico_kg
                is not None
                else "-"
            ),
        )

        st.markdown(
            "#### ⚖️ Análisis de peso y cumplimiento nominal"
        )

        def _week_num(
            column,
        ):
            value = pd.to_numeric(
                pd.Series(
                    [
                        selected_week_row.get(
                            column,
                            pd.NA,
                        )
                    ]
                ),
                errors="coerce",
            ).iloc[0]

            return (
                float(value)
                if pd.notna(value)
                else None
            )

        wa1, wa2, wa3, wa4 = st.columns(4)

        _gross = _week_num(
            "camera_exit_gross_kg"
        )
        _tare = _week_num(
            "avg_final_tare_kg"
        )
        _net = _week_num(
            "estimated_camera_exit_net_kg"
        )
        _camera_deficit = _week_num(
            "camera_nominal_deficit_kg"
        )

        wa1.metric(
            "Kg brutos desde cámara",
            (
                f"{_gross:.3f} kg"
                if _gross is not None
                else "-"
            ),
        )

        wa2.metric(
            "Prom. tara final",
            (
                f"{_tare:.3f} kg"
                if _tare is not None
                else "-"
            ),
        )

        wa3.metric(
            "Kg netos estimados",
            (
                f"{_net:.3f} kg"
                if _net is not None
                else "-"
            ),
        )

        wa4.metric(
            "Déficit cámara vs nominal",
            (
                f"{_camera_deficit:+.3f} kg"
                if _camera_deficit is not None
                else "-"
            ),
        )

        # ----------------------------------------------------
        # Fuente usada para los kg Cámara → Salón del consumo físico
        # ----------------------------------------------------
        # Reproducimos la misma condición de week_service:
        # si hay bruto válido en CAMARA_A_SALON y tara final válida
        # en la Week, el consumo usa bruto - tara promedio.
        # En caso contrario usa el peso_neto_kg histórico del movimiento.
        selected_week_id = str(
            selected_week_row.get(
                "week_id",
                ""
            )
            or ""
        )

        source_label = "MOVEMENT_NET_FALLBACK"

        try:
            _week_movs = movements.copy()

            if (
                not _week_movs.empty
                and "week_id" in _week_movs.columns
                and "movement_type" in _week_movs.columns
            ):
                _week_movs = _week_movs[
                    _week_movs["week_id"]
                    .astype(str)
                    .eq(selected_week_id)
                ].copy()

                _types = (
                    _week_movs["movement_type"]
                    .fillna("")
                    .astype(str)
                    .str.upper()
                )

                _camera_rows = _week_movs[
                    _types.eq("CAMARA_A_SALON")
                ].copy()

                _exhausted_rows = _week_movs[
                    _types.eq("LATA_AGOTADA")
                ].copy()

                _gross_ok = (
                    "peso_bruto_kg" in _camera_rows.columns
                    and not pd.to_numeric(
                        _camera_rows["peso_bruto_kg"],
                        errors="coerce",
                    ).dropna().empty
                )

                _tare_ok = (
                    "tara_final_kg" in _exhausted_rows.columns
                    and not pd.to_numeric(
                        _exhausted_rows["tara_final_kg"],
                        errors="coerce",
                    ).dropna().empty
                )

                if _gross_ok and _tare_ok:
                    source_label = "GROSS_MINUS_WEEK_AVG_FINAL_TARE"

        except Exception:
            source_label = "MOVEMENT_NET_FALLBACK"

        source_friendly = (
            "Bruto − tara prom. semanal"
            if source_label == "GROSS_MINUS_WEEK_AVG_FINAL_TARE"
            else "Neto guardado en movimientos (fallback)"
        )

        st.metric(
            "Fuente kg Cámara → Salón usada en consumo físico",
            source_friendly,
            help=(
                f"Fuente técnica: {source_label}. "
                "Si existen pesos brutos válidos de las salidas de cámara "
                "y una tara final promedio válida de la Week, se usa "
                "Σ bruto − (cantidad de latas × tara promedio semanal). "
                "Si faltan esos datos, se usa el peso_neto_kg histórico "
                "de los movimientos CAMARA_A_SALON."
            ),
        )

        # ====================================================
        # AUDITORÍA DEL CONSUMO FÍSICO
        # ====================================================
        if str(
            selected_week_row.get(
                "status",
                ""
            )
        ).upper() == "CLOSED":

            with st.expander(
                "🧾 Ver detalle exacto del cálculo de consumo físico",
                expanded=False,
            ):
                st.caption(
                    "Esta tabla muestra cada componente que entra en la "
                    "ecuación de consumo físico. Las filas de INICIO y "
                    "ENTRADA suman; las filas de CIERRE restan."
                )

                audit_rows = []

                # --------------------------------------------
                # 1) Snapshot inicial: suma
                # --------------------------------------------
                start_snapshot_audit = salon_snapshot_from_json(
                    selected_week_row.get(
                        "start_salon_snapshot_json"
                    )
                )

                for lata in start_snapshot_audit.get(
                    "latas",
                    []
                ):
                    neto = pd.to_numeric(
                        pd.Series(
                            [
                                lata.get(
                                    "peso_neto_kg"
                                )
                            ]
                        ),
                        errors="coerce",
                    ).iloc[0]

                    if pd.isna(
                        neto
                    ):
                        continue

                    audit_rows.append(
                        {
                            "Tipo": "INICIO",
                            "ID salón": lata.get(
                                "stock_id",
                                "",
                            ),
                            "ID cámara": "",
                            "Sabor": lata.get(
                                "sabor",
                                "",
                            ),
                            "Estado": lata.get(
                                "estado",
                                "",
                            ),
                            "Bruto kg": lata.get(
                                "peso_bruto_kg"
                            ),
                            "Tara aplicada kg": lata.get(
                                "tara_kg"
                            ),
                            "Neto usado kg": float(
                                neto
                            ),
                            "Impacto consumo kg": float(
                                neto
                            ),
                            "Criterio": "Conteo inicial",
                        }
                    )

                # --------------------------------------------
                # 2) Movimientos de la ventana real de la Week
                # --------------------------------------------
                audit_movements = load_csv(
                    MOVEMENTS_FILE
                )

                if not audit_movements.empty:
                    audit_movements[
                        "_timestamp_dt"
                    ] = pd.to_datetime(
                        audit_movements[
                            "timestamp"
                        ],
                        errors="coerce",
                        utc=True,
                    )

                    audit_start = pd.to_datetime(
                        selected_week_row.get(
                            "started_at"
                        ),
                        errors="coerce",
                        utc=True,
                    )

                    audit_end = pd.to_datetime(
                        selected_week_row.get(
                            "closed_at"
                        ),
                        errors="coerce",
                        utc=True,
                    )

                    if pd.notna(
                        audit_start
                    ):
                        audit_movements = audit_movements[
                            audit_movements[
                                "_timestamp_dt"
                            ]
                            >= audit_start
                        ].copy()

                    if pd.notna(
                        audit_end
                    ):
                        audit_movements = audit_movements[
                            audit_movements[
                                "_timestamp_dt"
                            ]
                            <= audit_end
                        ].copy()

                    audit_types = (
                        audit_movements[
                            "movement_type"
                        ]
                        .fillna("")
                        .astype(str)
                        .str.upper()
                    )

                    audit_camera = audit_movements[
                        audit_types.eq(
                            "CAMARA_A_SALON"
                        )
                    ].copy()

                    audit_exhausted = audit_movements[
                        audit_types.eq(
                            "LATA_AGOTADA"
                        )
                    ].copy()

                    audit_manual = audit_movements[
                        audit_types.eq(
                            "CARGA_MANUAL_SALON"
                        )
                    ].copy()

                    audit_tare_values = pd.to_numeric(
                        audit_exhausted.get(
                            "tara_final_kg",
                            pd.Series(
                                index=audit_exhausted.index,
                                dtype=float,
                            ),
                        ),
                        errors="coerce",
                    ).dropna()

                    audit_avg_tare = (
                        float(
                            audit_tare_values.mean()
                        )
                        if not audit_tare_values.empty
                        else None
                    )

                    # ----------------------------------------
                    # Cámara → Salón: suma
                    # ----------------------------------------
                    for _, movement in audit_camera.iterrows():
                        gross = pd.to_numeric(
                            movement.get(
                                "peso_bruto_kg"
                            ),
                            errors="coerce",
                        )

                        stored_net = pd.to_numeric(
                            movement.get(
                                "peso_neto_kg"
                            ),
                            errors="coerce",
                        )

                        if (
                            source_label
                            == "GROSS_MINUS_WEEK_AVG_FINAL_TARE"
                        ):
                            if (
                                pd.isna(
                                    gross
                                )
                                or audit_avg_tare is None
                            ):
                                # Igual que week_service: una fila sin bruto
                                # no forma parte del gross_values usado.
                                continue

                            net_used = max(
                                0.0,
                                float(
                                    gross
                                )
                                - audit_avg_tare,
                            )

                            tare_used = audit_avg_tare
                            criterio = "Bruto − tara prom. Week"

                        else:
                            if pd.isna(
                                stored_net
                            ):
                                continue

                            net_used = float(
                                stored_net
                            )

                            tare_used = pd.to_numeric(
                                movement.get(
                                    "tara_kg"
                                ),
                                errors="coerce",
                            )

                            criterio = "Neto guardado (fallback)"

                        salon_id = str(
                            movement.get(
                                "target_stock_id",
                                ""
                            )
                            or ""
                        ).strip()

                        camera_id = str(
                            movement.get(
                                "source_stock_id",
                                ""
                            )
                            or ""
                        ).strip()

                        audit_rows.append(
                            {
                                "Tipo": "ENTRADA CÁMARA",
                                "ID salón": salon_id,
                                "ID cámara": camera_id,
                                "Sabor": movement.get(
                                    "sabor",
                                    "",
                                ),
                                "Estado": "",
                                "Bruto kg": (
                                    float(
                                        gross
                                    )
                                    if pd.notna(
                                        gross
                                    )
                                    else None
                                ),
                                "Tara aplicada kg": (
                                    float(
                                        tare_used
                                    )
                                    if pd.notna(
                                        tare_used
                                    )
                                    else None
                                ),
                                "Neto usado kg": round(
                                    net_used,
                                    3,
                                ),
                                "Impacto consumo kg": round(
                                    net_used,
                                    3,
                                ),
                                "Criterio": criterio,
                            }
                        )

                    # ----------------------------------------
                    # Cargas manuales: suma
                    # ----------------------------------------
                    for _, movement in audit_manual.iterrows():
                        net_used = pd.to_numeric(
                            movement.get(
                                "peso_neto_kg"
                            ),
                            errors="coerce",
                        )

                        if pd.isna(
                            net_used
                        ):
                            continue

                        audit_rows.append(
                            {
                                "Tipo": "CARGA MANUAL",
                                "ID salón": str(
                                    movement.get(
                                        "target_stock_id",
                                        ""
                                    )
                                    or ""
                                ),
                                "ID cámara": "",
                                "Sabor": movement.get(
                                    "sabor",
                                    "",
                                ),
                                "Estado": "",
                                "Bruto kg": movement.get(
                                    "peso_bruto_kg"
                                ),
                                "Tara aplicada kg": movement.get(
                                    "tara_kg"
                                ),
                                "Neto usado kg": float(
                                    net_used
                                ),
                                "Impacto consumo kg": float(
                                    net_used
                                ),
                                "Criterio": "Carga manual",
                            }
                        )

                # --------------------------------------------
                # 3) Snapshot final: resta
                # --------------------------------------------
                end_snapshot_audit = salon_snapshot_from_json(
                    selected_week_row.get(
                        "end_salon_snapshot_json"
                    )
                )

                for lata in end_snapshot_audit.get(
                    "latas",
                    []
                ):
                    neto = pd.to_numeric(
                        pd.Series(
                            [
                                lata.get(
                                    "peso_neto_kg"
                                )
                            ]
                        ),
                        errors="coerce",
                    ).iloc[0]

                    if pd.isna(
                        neto
                    ):
                        continue

                    audit_rows.append(
                        {
                            "Tipo": "CIERRE",
                            "ID salón": lata.get(
                                "stock_id",
                                "",
                            ),
                            "ID cámara": "",
                            "Sabor": lata.get(
                                "sabor",
                                "",
                            ),
                            "Estado": lata.get(
                                "estado",
                                "",
                            ),
                            "Bruto kg": lata.get(
                                "peso_bruto_kg"
                            ),
                            "Tara aplicada kg": lata.get(
                                "tara_kg"
                            ),
                            "Neto usado kg": float(
                                neto
                            ),
                            "Impacto consumo kg": -float(
                                neto
                            ),
                            "Criterio": "Conteo final",
                        }
                    )

                # --------------------------------------------
                # Resumen de la ecuación
                # --------------------------------------------
                audit_df = pd.DataFrame(
                    audit_rows
                )

                if audit_df.empty:
                    st.info(
                        "No hay datos suficientes para reconstruir "
                        "el detalle del consumo físico."
                    )

                else:
                    start_component = float(
                        audit_df.loc[
                            audit_df[
                                "Tipo"
                            ].eq(
                                "INICIO"
                            ),
                            "Impacto consumo kg",
                        ].sum()
                    )

                    camera_component = float(
                        audit_df.loc[
                            audit_df[
                                "Tipo"
                            ].eq(
                                "ENTRADA CÁMARA"
                            ),
                            "Impacto consumo kg",
                        ].sum()
                    )

                    manual_component = float(
                        audit_df.loc[
                            audit_df[
                                "Tipo"
                            ].eq(
                                "CARGA MANUAL"
                            ),
                            "Impacto consumo kg",
                        ].sum()
                    )

                    end_component = abs(
                        float(
                            audit_df.loc[
                                audit_df[
                                    "Tipo"
                                ].eq(
                                    "CIERRE"
                                ),
                                "Impacto consumo kg",
                            ].sum()
                        )
                    )

                    reconstructed_consumption = round(
                        float(
                            audit_df[
                                "Impacto consumo kg"
                            ].sum()
                        ),
                        3,
                    )

                    stored_consumption = pd.to_numeric(
                        selected_week_row.get(
                            "consumo_fisico_kg"
                        ),
                        errors="coerce",
                    )

                    equation_difference = (
                        round(
                            reconstructed_consumption
                            - float(
                                stored_consumption
                            ),
                            3,
                        )
                        if pd.notna(
                            stored_consumption
                        )
                        else None
                    )

                    eq1, eq2, eq3, eq4, eq5 = st.columns(
                        5
                    )

                    eq1.metric(
                        "Salón inicial",
                        f"{start_component:.3f} kg",
                    )

                    eq2.metric(
                        "+ Neto desde cámara",
                        f"{camera_component:.3f} kg",
                    )

                    eq3.metric(
                        "+ Cargas manuales",
                        f"{manual_component:.3f} kg",
                    )

                    eq4.metric(
                        "− Salón al cierre",
                        f"{end_component:.3f} kg",
                    )

                    eq5.metric(
                        "= Consumo físico",
                        f"{reconstructed_consumption:.3f} kg",
                    )

                    if (
                        equation_difference is not None
                        and abs(
                            equation_difference
                        ) > 0.002
                    ):
                        st.warning(
                            "El detalle reconstruido difiere del "
                            f"`consumo_fisico_kg` guardado en "
                            f"{equation_difference:+.3f} kg. "
                            "Recalculá metadata antes de usar el dato."
                        )

                    else:
                        st.success(
                            "El detalle fila por fila reconcilia con el "
                            "consumo físico guardado de la Week."
                        )


                    # Comparación específica de las latas CERRADAS al cierre
                    closed_at_end = audit_df[
                        audit_df["Tipo"].eq("CIERRE")
                        & audit_df["Estado"].fillna("").astype(str).str.upper().eq("CERRADA")
                    ].copy()

                    if not closed_at_end.empty:
                        closed_at_end["Nominal 7.800 kg"] = GRIDO_NOMINAL_NET_KG
                        closed_at_end["Vs 7.800 kg"] = (
                            pd.to_numeric(closed_at_end["Neto usado kg"], errors="coerce")
                            - GRIDO_NOMINAL_NET_KG
                        )
                        closed_at_end["Desvío %"] = (
                            closed_at_end["Vs 7.800 kg"]
                            / GRIDO_NOMINAL_NET_KG
                            * 100.0
                        )

                        closed_count = int(len(closed_at_end))
                        closed_net_total = float(
                            pd.to_numeric(
                                closed_at_end["Neto usado kg"],
                                errors="coerce",
                            ).fillna(0).sum()
                        )
                        closed_nominal_total = (
                            closed_count * GRIDO_NOMINAL_NET_KG
                        )
                        closed_delta_total = (
                            closed_net_total - closed_nominal_total
                        )
                        closed_delta_pct = (
                            closed_delta_total
                            / closed_nominal_total
                            * 100.0
                            if closed_nominal_total > 0
                            else None
                        )

                        st.markdown(
                            "##### 🧊 Latas CERRADAS al cierre vs 7.800 kg"
                        )
                        st.caption(
                            "Compara solo las latas que quedaron CERRADAS al terminar "
                            "la Week. En ellas sí tiene sentido contrastar el neto "
                            "contabilizado contra 7.800 kg nominales por lata. "
                            "Las ABIERTAS no participan de esta comparación."
                        )

                        ccl1, ccl2, ccl3, ccl4 = st.columns(4)
                        ccl1.metric("Cerradas al cierre", closed_count)
                        ccl2.metric(
                            "Neto contabilizado",
                            f"{closed_net_total:.3f} kg",
                        )
                        ccl3.metric(
                            "Nominal esperado",
                            f"{closed_nominal_total:.3f} kg",
                            help=(
                                f"{closed_count} × "
                                f"{GRIDO_NOMINAL_NET_KG:.3f} kg"
                            ),
                        )
                        ccl4.metric(
                            "Diferencia vs nominal",
                            (
                                f"{closed_delta_total:+.3f} kg"
                                + (
                                    f" ({closed_delta_pct:+.2f}%)"
                                    if closed_delta_pct is not None
                                    else ""
                                )
                            ),
                        )

                        closed_detail = closed_at_end[
                            [
                                "ID salón",
                                "Sabor",
                                "Estado",
                                "Bruto kg",
                                "Tara aplicada kg",
                                "Neto usado kg",
                                "Nominal 7.800 kg",
                                "Vs 7.800 kg",
                                "Desvío %",
                            ]
                        ].copy()

                        st.dataframe(
                            closed_detail,
                            hide_index=True,
                            use_container_width=True,
                            column_config={
                                "Bruto kg": st.column_config.NumberColumn(
                                    "Bruto", format="%.3f kg"
                                ),
                                "Tara aplicada kg": st.column_config.NumberColumn(
                                    "Tara", format="%.3f kg"
                                ),
                                "Neto usado kg": st.column_config.NumberColumn(
                                    "Neto contabilizado", format="%.3f kg"
                                ),
                                "Nominal 7.800 kg": st.column_config.NumberColumn(
                                    "Nominal", format="%.3f kg"
                                ),
                                "Vs 7.800 kg": st.column_config.NumberColumn(
                                    "Vs 7.800", format="%+.3f kg"
                                ),
                                "Desvío %": st.column_config.NumberColumn(
                                    "Desvío %", format="%+.2f%%"
                                ),
                            },
                        )

                    st.markdown(
                        "##### 📋 Todas las filas usadas en el balance"
                    )

                    st.dataframe(
                        audit_df,
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "Bruto kg":
                                st.column_config.NumberColumn(
                                    "Bruto",
                                    format="%.3f kg",
                                ),

                            "Tara aplicada kg":
                                st.column_config.NumberColumn(
                                    "Tara aplicada",
                                    format="%.3f kg",
                                ),

                            "Neto usado kg":
                                st.column_config.NumberColumn(
                                    "Neto usado",
                                    format="%.3f kg",
                                ),

                            "Impacto consumo kg":
                                st.column_config.NumberColumn(
                                    "Impacto en consumo",
                                    format="%+.3f kg",
                                ),
                        },
                    )

                    st.caption(
                        "Lectura: INICIO suma el stock que existía al "
                        "arrancar; ENTRADA CÁMARA suma el neto estimado "
                        "que ingresó; CARGA MANUAL suma ajustes físicos; "
                        "CIERRE resta lo que todavía quedó en el salón."
                    )

                    # ================================================
                    # SEGUNDA LECTURA: QUÉ PASÓ CON LOS IDs
                    # ================================================
                    st.markdown(
                        "##### 🔎 El mismo consumo explicado por continuidad de latas"
                    )

                    st.caption(
                        "Esta segunda vista no cambia el cálculo. Reordena "
                        "los mismos kilos según qué latas ya estaban al inicio, "
                        "cuáles siguen al cierre y cuáles entraron nuevas desde cámara."
                    )

                    start_latas_df = pd.DataFrame(
                        start_snapshot_audit.get(
                            "latas",
                            [],
                        )
                    )

                    end_latas_df = pd.DataFrame(
                        end_snapshot_audit.get(
                            "latas",
                            [],
                        )
                    )

                    if (
                        not start_latas_df.empty
                        and not end_latas_df.empty
                        and "stock_id" in start_latas_df.columns
                        and "stock_id" in end_latas_df.columns
                    ):
                        for _df in [
                            start_latas_df,
                            end_latas_df,
                        ]:
                            _df["stock_id"] = (
                                _df["stock_id"]
                                .fillna("")
                                .astype(str)
                                .str.strip()
                            )

                            _df["peso_neto_kg"] = pd.to_numeric(
                                _df.get(
                                    "peso_neto_kg"
                                ),
                                errors="coerce",
                            )

                        start_ids = set(
                            start_latas_df.loc[
                                start_latas_df["stock_id"].ne(""),
                                "stock_id",
                            ]
                        )

                        end_ids = set(
                            end_latas_df.loc[
                                end_latas_df["stock_id"].ne(""),
                                "stock_id",
                            ]
                        )

                        surviving_ids = (
                            start_ids
                            & end_ids
                        )

                        disappeared_ids = (
                            start_ids
                            - end_ids
                        )

                        new_end_ids = (
                            end_ids
                            - start_ids
                        )

                        surviving_start_kg = float(
                            start_latas_df.loc[
                                start_latas_df[
                                    "stock_id"
                                ].isin(
                                    surviving_ids
                                ),
                                "peso_neto_kg",
                            ]
                            .fillna(0)
                            .sum()
                        )

                        surviving_end_kg = float(
                            end_latas_df.loc[
                                end_latas_df[
                                    "stock_id"
                                ].isin(
                                    surviving_ids
                                ),
                                "peso_neto_kg",
                            ]
                            .fillna(0)
                            .sum()
                        )

                        surviving_consumed_kg = (
                            surviving_start_kg
                            - surviving_end_kg
                        )

                        disappeared_initial_kg = float(
                            start_latas_df.loc[
                                start_latas_df[
                                    "stock_id"
                                ].isin(
                                    disappeared_ids
                                ),
                                "peso_neto_kg",
                            ]
                            .fillna(0)
                            .sum()
                        )

                        new_end_remaining_kg = float(
                            end_latas_df.loc[
                                end_latas_df[
                                    "stock_id"
                                ].isin(
                                    new_end_ids
                                ),
                                "peso_neto_kg",
                            ]
                            .fillna(0)
                            .sum()
                        )

                        continuity_consumption = round(
                            surviving_consumed_kg
                            + disappeared_initial_kg
                            + camera_component
                            + manual_component
                            - new_end_remaining_kg,
                            3,
                        )

                        id1, id2, id3, id4 = st.columns(
                            4
                        )

                        id1.metric(
                            "IDs inicio → cierre",
                            len(
                                surviving_ids
                            ),
                            help=(
                                "Latas con el mismo stock_id presentes "
                                "tanto en el conteo inicial como en el final."
                            ),
                        )

                        id2.metric(
                            "IDs iniciales que salieron",
                            len(
                                disappeared_ids
                            ),
                            help=(
                                "Latas que estaban al inicio y ya no "
                                "aparecen en el conteo final."
                            ),
                        )

                        id3.metric(
                            "IDs nuevos al cierre",
                            len(
                                new_end_ids
                            ),
                            help=(
                                "Latas presentes al cierre que no estaban "
                                "en el snapshot inicial."
                            ),
                        )

                        id4.metric(
                            "Consumo reconciliado",
                            f"{continuity_consumption:.3f} kg",
                        )

                        continuity_rows = [
                            {
                                "Componente":
                                    "Consumo de latas que siguen del inicio al cierre",
                                "Cómo se obtiene":
                                    (
                                        f"{surviving_start_kg:.3f} kg inicial "
                                        f"− {surviving_end_kg:.3f} kg final"
                                    ),
                                "Impacto kg":
                                    surviving_consumed_kg,
                            },
                            {
                                "Componente":
                                    "Contenido inicial de latas que desaparecieron",
                                "Cómo se obtiene":
                                    (
                                        f"{len(disappeared_ids)} IDs estaban "
                                        "al inicio y ya no están al cierre"
                                    ),
                                "Impacto kg":
                                    disappeared_initial_kg,
                            },
                            {
                                "Componente":
                                    "Entradas nuevas desde cámara",
                                "Cómo se obtiene":
                                    "Neto usado por el balance semanal",
                                "Impacto kg":
                                    camera_component,
                            },
                        ]

                        if abs(
                            manual_component
                        ) > 0.0005:
                            continuity_rows.append(
                                {
                                    "Componente":
                                        "Cargas manuales",
                                    "Cómo se obtiene":
                                        "Entradas físicas extraordinarias",
                                    "Impacto kg":
                                        manual_component,
                                }
                            )

                        continuity_rows.append(
                            {
                                "Componente":
                                    "Contenido que todavía queda en IDs nuevos",
                                "Cómo se obtiene":
                                    (
                                        f"{len(new_end_ids)} IDs nuevos "
                                        "presentes al cierre"
                                    ),
                                "Impacto kg":
                                    -new_end_remaining_kg,
                            }
                        )

                        continuity_rows.append(
                            {
                                "Componente":
                                    "CONSUMO FÍSICO",
                                "Cómo se obtiene":
                                    "Suma de los componentes anteriores",
                                "Impacto kg":
                                    continuity_consumption,
                            }
                        )

                        continuity_df = pd.DataFrame(
                            continuity_rows
                        )

                        st.dataframe(
                            continuity_df,
                            hide_index=True,
                            use_container_width=True,
                            column_config={
                                "Impacto kg":
                                    st.column_config.NumberColumn(
                                        "Impacto",
                                        format="%+.3f kg",
                                    ),
                            },
                        )

                        st.code(
                            (
                                f"{surviving_consumed_kg:.3f} kg  "
                                "consumidos de latas que siguieron\n"
                                f"+ {disappeared_initial_kg:.3f} kg  "
                                "de latas iniciales que desaparecieron\n"
                                f"+ {camera_component:.3f} kg  "
                                "de entradas desde cámara\n"
                                + (
                                    f"+ {manual_component:.3f} kg  "
                                    "de cargas manuales\n"
                                    if abs(
                                        manual_component
                                    ) > 0.0005
                                    else ""
                                )
                                + f"- {new_end_remaining_kg:.3f} kg  "
                                "que todavía quedan en IDs nuevos\n"
                                "────────────────────────────────────\n"
                                f"= {continuity_consumption:.3f} kg  "
                                "de consumo físico"
                            ),
                            language=None,
                        )

                        continuity_difference = round(
                            continuity_consumption
                            - reconstructed_consumption,
                            3,
                        )

                        if abs(
                            continuity_difference
                        ) <= 0.002:
                            st.success(
                                "La lectura por continuidad de IDs coincide "
                                "con el balance físico general."
                            )
                        else:
                            st.warning(
                                "La lectura por IDs difiere del balance "
                                f"general en {continuity_difference:+.3f} kg. "
                                "Revisá IDs faltantes, duplicados o movimientos "
                                "sin correspondencia."
                            )

                    else:
                        st.info(
                            "No hay snapshots inicial/final con stock_id "
                            "suficientes para mostrar la reconciliación por IDs."
                        )

        wb1, wb2, wb3, wb4 = st.columns(4)

        _analyzed = _week_num(
            "nominal_analyzed_latas"
        )
        _in_range = _week_num(
            "nominal_in_range_latas"
        )
        _deficit_latas = _week_num(
            "nominal_deficit_latas"
        )
        _in_range_pct = _week_num(
            "nominal_in_range_pct"
        )

        wb1.metric(
            "Latas analizadas",
            (
                int(_analyzed)
                if _analyzed is not None
                else "-"
            ),
        )

        wb2.metric(
            "En rango o mejor",
            (
                int(_in_range)
                if _in_range is not None
                else "-"
            ),
        )

        wb3.metric(
            "Déficit claro",
            (
                int(_deficit_latas)
                if _deficit_latas is not None
                else "-"
            ),
        )

        wb4.metric(
            "% en rango o mejor",
            (
                f"{_in_range_pct:.1f}%"
                if _in_range_pct is not None
                else "-"
            ),
        )

        wc1, wc2, wc3, wc4 = st.columns(4)

        _deficit_total = _week_num(
            "nominal_deficit_total_kg"
        )
        _excess_total = _week_num(
            "nominal_excess_total_kg"
        )
        _balance = _week_num(
            "nominal_balance_total_kg"
        )
        _avg_dev_pct = _week_num(
            "nominal_avg_deviation_pct"
        )

        wc1.metric(
            "Déficit total",
            (
                f"{_deficit_total:.3f} kg"
                if _deficit_total is not None
                else "-"
            ),
        )

        wc2.metric(
            "Excedente total",
            (
                f"{_excess_total:.3f} kg"
                if _excess_total is not None
                else "-"
            ),
        )

        wc3.metric(
            "Balance vs nominal",
            (
                f"{_balance:+.3f} kg"
                if _balance is not None
                else "-"
            ),
        )

        wc4.metric(
            "Desvío promedio %",
            (
                f"{_avg_dev_pct:+.2f}%"
                if _avg_dev_pct is not None
                else "-"
            ),
        )

        st.markdown(
            "#### 📦 Inventario"
        )

        inventory_df = pd.DataFrame(
            [
                {
                    "Momento":
                        "Inicio",

                    "Salón latas":
                        week.start_salon_latas,

                    "Salón kg":
                        week.start_salon_kg,

                    "Cámara latas":
                        week.start_camera_latas,

                    "Cámara kg":
                        week.start_camera_kg,
                },
                {
                    "Momento":
                        "Actual",

                    "Salón latas":
                        week.current_salon_latas,

                    "Salón kg":
                        week.current_salon_kg,

                    "Cámara latas":
                        week.current_camera_latas,

                    "Cámara kg":
                        week.current_camera_kg,
                },
                {
                    "Momento":
                        "Cierre",

                    "Salón latas":
                        week.end_salon_latas,

                    "Salón kg":
                        week.end_salon_kg,

                    "Cámara latas":
                        week.end_camera_latas,

                    "Cámara kg":
                        week.end_camera_kg,
                },
            ]
        )

        st.dataframe(
            inventory_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Salón kg":
                    st.column_config.NumberColumn(
                        "Salón kg",
                        format="%.3f kg",
                    ),

                "Cámara kg":
                    st.column_config.NumberColumn(
                        "Cámara kg",
                        format="%.3f kg",
                    ),
            },
        )

        st.markdown(
            "#### 🍦 Latas del salón · snapshots"
        )

        start_salon_snapshot = (
            salon_snapshot_from_json(
                selected_week_row.get(
                    "start_salon_snapshot_json"
                )
            )
        )

        end_salon_snapshot = (
            salon_snapshot_from_json(
                selected_week_row.get(
                    "end_salon_snapshot_json"
                )
            )
        )

        salon_snapshot_tabs = st.tabs(
            [
                "🟢 Inicio",
                "🔴 Cierre",
            ]
        )

        for snapshot_tab, snapshot_name, snapshot_data in [
            (
                salon_snapshot_tabs[0],
                "Inicio",
                start_salon_snapshot,
            ),
            (
                salon_snapshot_tabs[1],
                "Cierre",
                end_salon_snapshot,
            ),
        ]:
            with snapshot_tab:
                if not snapshot_data:
                    st.info(
                        f"No hay snapshot de {snapshot_name.lower()} "
                        "guardado para esta Week."
                    )

                else:
                    totals = snapshot_data.get(
                        "totals",
                        {},
                    )

                    ss1, ss2, ss3, ss4 = st.columns(
                        4
                    )

                    ss1.metric(
                        "Latas",
                        int(
                            totals.get(
                                "latas",
                                0,
                            )
                            or 0
                        ),
                    )

                    ss2.metric(
                        "Abiertas",
                        int(
                            totals.get(
                                "abiertas",
                                0,
                            )
                            or 0
                        ),
                    )

                    ss3.metric(
                        "Cerradas",
                        int(
                            totals.get(
                                "cerradas",
                                0,
                            )
                            or 0
                        ),
                    )

                    snapshot_total_kg = pd.to_numeric(
                        pd.Series(
                            [
                                totals.get(
                                    "peso_neto_kg",
                                    pd.NA,
                                )
                            ]
                        ),
                        errors="coerce",
                    ).iloc[0]

                    ss4.metric(
                        "Kg netos",
                        (
                            f"{float(snapshot_total_kg):.3f} kg"
                            if pd.notna(
                                snapshot_total_kg
                            )
                            else "-"
                        ),
                    )

                    snapshot_latas = snapshot_data.get(
                        "latas",
                        [],
                    )

                    if snapshot_latas:
                        snapshot_df = pd.DataFrame(
                            snapshot_latas
                        )

                        display_columns = [
                            column
                            for column in [
                                "stock_id",
                                "sabor",
                                "estado",
                                "peso_bruto_kg",
                                "tara_kg",
                                "peso_neto_kg",
                            ]
                            if column in snapshot_df.columns
                        ]

                        st.dataframe(
                            snapshot_df[
                                display_columns
                            ],
                            hide_index=True,
                            use_container_width=True,
                            column_config={
                                "stock_id":
                                    st.column_config.TextColumn(
                                        "ID"
                                    ),

                                "sabor":
                                    st.column_config.TextColumn(
                                        "Sabor"
                                    ),

                                "estado":
                                    st.column_config.TextColumn(
                                        "Estado"
                                    ),

                                "peso_bruto_kg":
                                    st.column_config.NumberColumn(
                                        "Bruto",
                                        format="%.3f kg",
                                    ),

                                "tara_kg":
                                    st.column_config.NumberColumn(
                                        "Tara",
                                        format="%.3f kg",
                                    ),

                                "peso_neto_kg":
                                    st.column_config.NumberColumn(
                                        "Neto",
                                        format="%.3f kg",
                                    ),
                            },
                        )

                    st.caption(
                        (
                            f"count_id={snapshot_data.get('count_id') or '-'} · "
                            f"tipo={snapshot_data.get('count_type') or '-'} · "
                            f"timestamp={snapshot_data.get('timestamp') or '-'}"
                        )
                    )

        st.markdown(
            "#### 🧊 Productos en cámara"
        )

        start_products_snapshot = (
            products_snapshot_from_json(
                selected_week_row.get(
                    "start_products_snapshot_json"
                )
            )
        )

        current_products_snapshot = (
            products_snapshot_from_json(
                selected_week_row.get(
                    "current_products_snapshot_json"
                )
            )
        )

        end_products_snapshot = (
            products_snapshot_from_json(
                selected_week_row.get(
                    "end_products_snapshot_json"
                )
            )
        )

        product_inventory_rows = []

        for category in PRODUCT_SNAPSHOT_CATEGORIES:

            start_values = (
                start_products_snapshot.get(
                    category,
                    {},
                )
            )

            current_values = (
                current_products_snapshot.get(
                    category,
                    {},
                )
            )

            end_values = (
                end_products_snapshot.get(
                    category,
                    {},
                )
            )

            # Hide completely empty categories only if the category
            # never had inventory during this Week.
            if (
                product_snapshot_category_totals(
                    start_values
                )[
                    "units"
                ]
                == 0
                and int(
                    current_values.get(
                        "units",
                        0,
                    )
                    or 0
                )
                == 0
                and int(
                    end_values.get(
                        "units",
                        0,
                    )
                    or 0
                )
                == 0
            ):
                continue

            product_inventory_rows.append(
                {
                    "Categoría":
                        category.replace(
                            "_",
                            " ",
                        ).title(),

                    "Inicio":
                        product_snapshot_display_value(
                            category,
                            start_values,
                        ),

                    "Actual":
                        product_snapshot_display_value(
                            category,
                            current_values,
                        ),

                    "Cierre":
                        (
                            product_snapshot_display_value(
                                category,
                                end_values,
                            )
                            if end_values
                            else "-"
                        ),

                    "Unidades inicio":
                        int(
                            start_values.get(
                                "units",
                                0,
                            )
                            or 0
                        ),

                    "Unidades actual":
                        int(
                            current_values.get(
                                "units",
                                0,
                            )
                            or 0
                        ),

                    "Unidades cierre":
                        (
                            int(
                                end_values.get(
                                    "units",
                                    0,
                                )
                                or 0
                            )
                            if end_values
                            else None
                        ),
                }
            )

        if product_inventory_rows:
            product_inventory_df = pd.DataFrame(
                product_inventory_rows
            )

            ps1, ps2, ps3 = st.columns(
                3
            )

            ps1.metric(
                "Unidades iniciales",
                product_snapshot_units_total(
                    start_products_snapshot
                ),
            )

            ps2.metric(
                "Unidades actuales",
                product_snapshot_units_total(
                    current_products_snapshot
                ),
            )

            ps3.metric(
                "Unidades al cierre",
                (
                    product_snapshot_units_total(
                        end_products_snapshot
                    )
                    if end_products_snapshot
                    else "-"
                ),
            )

            st.dataframe(
                product_inventory_df[
                    [
                        "Categoría",
                        "Inicio",
                        "Actual",
                        "Cierre",
                    ]
                ],
                hide_index=True,
                use_container_width=True,
            )

            with st.expander(
                "🔎 Ver detalle por producto"
            ):
                detail_rows = []

                for category in PRODUCT_SNAPSHOT_CATEGORIES:
                    start_category = (
                        start_products_snapshot.get(
                            category,
                            {},
                        )
                    )

                    current_category = (
                        current_products_snapshot.get(
                            category,
                            {},
                        )
                    )

                    end_category = (
                        end_products_snapshot.get(
                            category,
                            {},
                        )
                    )

                    start_products = (
                        product_snapshot_category_products(
                            start_category
                        )
                    )

                    current_products = (
                        product_snapshot_category_products(
                            current_category
                        )
                    )

                    end_products = (
                        product_snapshot_category_products(
                            end_category
                        )
                    )

                    product_codes = sorted(
                        set(
                            start_products.keys()
                        )
                        |
                        set(
                            current_products.keys()
                        )
                        |
                        set(
                            end_products.keys()
                        )
                    )

                    for product_code in product_codes:
                        start_product = (
                            start_products.get(
                                product_code,
                                {},
                            )
                        )

                        current_product = (
                            current_products.get(
                                product_code,
                                {},
                            )
                        )

                        end_product = (
                            end_products.get(
                                product_code,
                                {},
                            )
                        )

                        product_name = (
                            current_product.get(
                                "producto"
                            )
                            or end_product.get(
                                "producto"
                            )
                            or start_product.get(
                                "producto"
                            )
                            or product_code
                        )

                        packaging_mode = (
                            current_product.get(
                                "packaging_mode"
                            )
                            or end_product.get(
                                "packaging_mode"
                            )
                            or start_product.get(
                                "packaging_mode"
                            )
                            or ""
                        )

                        detail_rows.append(
                            {
                                "Categoría":
                                    category.replace(
                                        "_",
                                        " ",
                                    ).title(),

                                "Producto":
                                    product_name,

                                "Código":
                                    product_code,

                                "Estructura":
                                    packaging_mode,

                                "Inicio":
                                    int(
                                        start_product.get(
                                            "units",
                                            0,
                                        )
                                        or 0
                                    ),

                                "Actual":
                                    int(
                                        current_product.get(
                                            "units",
                                            0,
                                        )
                                        or 0
                                    ),

                                "Cierre":
                                    (
                                        int(
                                            end_product.get(
                                                "units",
                                                0,
                                            )
                                            or 0
                                        )
                                        if end_product
                                        else None
                                    ),
                            }
                        )

                if detail_rows:
                    st.dataframe(
                        pd.DataFrame(
                            detail_rows
                        ),
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "Inicio":
                                st.column_config.NumberColumn(
                                    "Inicio (u.)",
                                    format="%d",
                                ),

                            "Actual":
                                st.column_config.NumberColumn(
                                    "Actual (u.)",
                                    format="%d",
                                ),

                            "Cierre":
                                st.column_config.NumberColumn(
                                    "Cierre (u.)",
                                    format="%d",
                                ),
                        },
                    )

                else:
                    st.caption(
                        "No hay detalle por producto disponible "
                        "para esta semana."
                    )

        else:
            st.caption(
                "Esta semana todavía no tiene productos no-granel "
                "registrados en cámara."
            )

        st.markdown(
            "#### 🔄 Actividad"
        )

        activity_df = pd.DataFrame(
            [
                {
                    "Métrica":
                        "Latas cámara → salón",

                    "Valor":
                        week.camera_to_salon_latas,
                },
                {
                    "Métrica":
                        "Kg cámara → salón",

                    "Valor":
                        week.camera_to_salon_kg,
                },
                {
                    "Métrica":
                        "Ingresos a cámara",

                    "Valor":
                        week.ingreso_camera_latas,
                },
                {
                    "Métrica":
                        "Kg ingresados a cámara",

                    "Valor":
                        week.ingreso_camera_kg,
                },
                {
                    "Métrica":
                        "Latas abiertas",

                    "Valor":
                        week.latas_abiertas,
                },
                {
                    "Métrica":
                        "Latas terminadas",

                    "Valor":
                        week.latas_terminadas,
                },
                {
                    "Métrica":
                        "Cambios de sabor",

                    "Valor":
                        week.cambios_sabor,
                },
                {
                    "Métrica":
                        "Recambios",

                    "Valor":
                        week.recambios,
                },
                {
                    "Métrica":
                        "Latas con tara final",

                    "Valor":
                        week.latas_con_tara_final,
                },
                {
                    "Métrica":
                        "Tara final acumulada kg",

                    "Valor":
                        week.tara_final_total_kg,
                },
                {
                    "Métrica":
                        "Residuo estimado de helado kg",

                    "Valor":
                        week.residuo_estimado_kg,
                },
            ]
        )

        st.dataframe(
            activity_df,
            hide_index=True,
            use_container_width=True,
        )

        if str(
            week.status
        ).upper() == "OPEN":

            st.divider()

            st.markdown(
                "### 🔒 Cierre de semana"
            )

            st.caption(
                "Para cerrar la semana primero se realiza el conteo físico "
                "final de TODAS las latas activas del salón. "
                "Ese mismo snapshot se usa automáticamente como apertura "
                "de la semana siguiente."
            )

            close_stock = load_current_stock()

            close_salon = close_stock[
                (
                    close_stock[
                        "location"
                    ]
                    .astype(str)
                    .str.upper()
                    .eq(
                        "SALON"
                    )
                )
                &
                (
                    close_stock[
                        "active"
                    ]
                    == True
                )
            ].copy()

            close_salon = close_salon[
                close_salon[
                    "estado"
                ]
                .astype(str)
                .str.upper()
                .isin(
                    [
                        "ABIERTA",
                        "CERRADA",
                    ]
                )
            ].copy()

            if close_salon.empty:
                st.error(
                    "No hay latas activas ABIERTAS/CERRADAS en el salón. "
                    "No se puede realizar el conteo final."
                )

            else:
                close_salon[
                    "peso_bruto_kg"
                ] = pd.to_numeric(
                    close_salon[
                        "peso_actual_bruto_kg"
                    ],
                    errors="coerce",
                )

                close_salon[
                    "tara_kg"
                ] = pd.to_numeric(
                    close_salon[
                        "tara_actual_kg"
                    ],
                    errors="coerce",
                )

                # Fallback a tara inicial estimada.
                close_salon[
                    "tara_kg"
                ] = (
                    close_salon[
                        "tara_kg"
                    ]
                    .fillna(
                        pd.to_numeric(
                            close_salon[
                                "tara_inicial_kg"
                            ],
                            errors="coerce",
                        )
                    )
                    .fillna(
                        DEFAULT_TARE_KG
                    )
                )

                close_editor_source = close_salon[
                    [
                        "stock_id",
                        "sabor",
                        "estado",
                        "peso_bruto_kg",
                        "tara_kg",
                    ]
                ].copy()

                close_editor_source.insert(
                    0,
                    "eliminar",
                    False,
                )

                close_editor_source = (
                    close_editor_source
                    .sort_values(
                        [
                            "estado",
                            "sabor",
                        ]
                    )
                )

                st.markdown(
                    "#### ⚖️ Reconciliación física final del salón"
                )

                st.info(
                    "Podés corregir SABOR/ESTADO/PESO/TARA, marcar una "
                    "lata como quitar si no está físicamente, o agregar "
                    "una fila nueva al final si encontrás una lata que "
                    "no figura en el sistema."
                )

                close_flavors = load_flavors()

                edited_close_count = st.data_editor(
                    close_editor_source,
                    hide_index=True,
                    use_container_width=True,
                    num_rows="dynamic",
                    disabled=[
                        "stock_id",
                    ],
                    column_config={
                        "eliminar":
                            st.column_config.CheckboxColumn(
                                "🗑️ Quitar",
                                help=(
                                    "Marca esta lata si figura en el sistema "
                                    "pero no existe físicamente en el salón."
                                ),
                                default=False,
                            ),

                        "stock_id":
                            st.column_config.TextColumn(
                                "Lata",
                                help=(
                                    "Vacío = nueva lata encontrada durante "
                                    "el conteo. El ID se crea al confirmar."
                                ),
                            ),

                        "sabor":
                            st.column_config.SelectboxColumn(
                                "Sabor",
                                options=
                                    close_flavors,
                                required=False,
                            ),

                        "estado":
                            st.column_config.SelectboxColumn(
                                "Estado",
                                options=[
                                    "ABIERTA",
                                    "CERRADA",
                                ],
                                required=False,
                            ),

                        "peso_bruto_kg":
                            st.column_config.NumberColumn(
                                "Peso bruto",
                                min_value=0.001,
                                max_value=MAX_CAN_GROSS_KG,
                                step=0.005,
                                format="%.3f kg",
                            ),

                        "tara_kg":
                            st.column_config.NumberColumn(
                                "Tara",
                                min_value=0.0,
                                max_value=MAX_TARE_KG,
                                step=0.005,
                                format="%.3f kg",
                            ),
                    },
                    key=
                        f"week_close_count_editor_{week.week_id}",
                )

                # ----------------------------------------------------
                # Preview dinámico del conteo
                # ----------------------------------------------------

                preview_close = (
                    edited_close_count.copy()
                )

                preview_keep = preview_close[
                    preview_close[
                        "eliminar"
                    ]
                    != True
                ].copy()

                has_any_content = (
                    preview_keep[
                        "stock_id"
                    ].notna()
                    |
                    preview_keep[
                        "sabor"
                    ].notna()
                    |
                    preview_keep[
                        "estado"
                    ].notna()
                    |
                    preview_keep[
                        "peso_bruto_kg"
                    ].notna()
                    |
                    preview_keep[
                        "tara_kg"
                    ].notna()
                )

                preview_keep = preview_keep[
                    has_any_content
                ].copy()

                preview_keep[
                    "peso_bruto_kg"
                ] = pd.to_numeric(
                    preview_keep[
                        "peso_bruto_kg"
                    ],
                    errors="coerce",
                )

                preview_keep[
                    "tara_kg"
                ] = pd.to_numeric(
                    preview_keep[
                        "tara_kg"
                    ],
                    errors="coerce",
                )

                preview_keep[
                    "peso_neto_kg"
                ] = (
                    preview_keep[
                        "peso_bruto_kg"
                    ]
                    -
                    preview_keep[
                        "tara_kg"
                    ]
                )

                valid_preview = (
                    preview_keep[
                        "sabor"
                    ].notna()
                    &
                    preview_keep[
                        "estado"
                    ]
                    .astype(str)
                    .str.upper()
                    .isin(
                        [
                            "ABIERTA",
                            "CERRADA",
                        ]
                    )
                    &
                    preview_keep[
                        "peso_bruto_kg"
                    ].notna()
                    &
                    preview_keep[
                        "tara_kg"
                    ].notna()
                    &
                    (
                        preview_keep[
                            "peso_bruto_kg"
                        ]
                        >
                        preview_keep[
                            "tara_kg"
                        ]
                    )
                )

                invalid_close_rows = int(
                    (
                        ~valid_preview
                    ).sum()
                )

                abiertas_preview = preview_keep[
                    preview_keep[
                        "estado"
                    ]
                    .astype(str)
                    .str.upper()
                    .eq(
                        "ABIERTA"
                    )
                    &
                    valid_preview
                ].copy()

                cerradas_preview = preview_keep[
                    preview_keep[
                        "estado"
                    ]
                    .astype(str)
                    .str.upper()
                    .eq(
                        "CERRADA"
                    )
                    &
                    valid_preview
                ].copy()

                abiertas_count = int(
                    len(
                        abiertas_preview
                    )
                )

                cerradas_count = int(
                    len(
                        cerradas_preview
                    )
                )

                abiertas_kg = round(
                    float(
                        abiertas_preview[
                            "peso_neto_kg"
                        ]
                        .fillna(0)
                        .sum()
                    ),
                    3,
                )

                cerradas_kg = round(
                    float(
                        cerradas_preview[
                            "peso_neto_kg"
                        ]
                        .fillna(0)
                        .sum()
                    ),
                    3,
                )

                final_total_kg = round(
                    abiertas_kg
                    + cerradas_kg,
                    3,
                )

                st.markdown(
                    "#### 📊 Resumen del cierre"
                )

                rc1, rc2, rc3, rc4 = st.columns(
                    4
                )

                rc1.metric(
                    "Latas abiertas",
                    abiertas_count,
                )

                rc2.metric(
                    "Kg abiertas",
                    f"{abiertas_kg:.3f} kg",
                )

                rc3.metric(
                    "Latas cerradas",
                    cerradas_count,
                )

                rc4.metric(
                    "Kg cerradas",
                    f"{cerradas_kg:.3f} kg",
                )

                rct1, rct2 = st.columns(
                    2
                )

                rct1.metric(
                    "Total latas",
                    (
                        abiertas_count
                        + cerradas_count
                    ),
                )

                rct2.metric(
                    "Stock final salón",
                    f"{final_total_kg:.3f} kg",
                )

                if invalid_close_rows > 0:
                    st.error(
                        f"Hay {invalid_close_rows} fila(s) con peso/tara "
                        "incompletos o inválidos. "
                        "No se puede cerrar hasta corregirlas."
                    )

                st.markdown(
                    "#### 🔁 Qué ocurrirá al confirmar"
                )

                st.write(
                    f"1. Se guardará un conteo `CIERRE_SEMANA` "
                    f"para **{week.week_id}**."
                )

                st.write(
                    "2. Se congelarán las métricas de la semana actual."
                )

                st.write(
                    "3. Se abrirá automáticamente la siguiente Week "
                    "en el mismo timestamp."
                )

                st.write(
                    "4. Las altas, bajas y correcciones detectadas "
                    "reconciliarán el stock del salón y quedarán en Historial."
                )

                st.write(
                    "5. Este mismo conteo reconciliado se copiará como "
                    "`INICIO_SEMANA` de la nueva Week."
                )

                close_notes = st.text_input(
                    "Observaciones del cierre",
                    key=
                        f"week_close_notes_{week.week_id}",
                )

                confirm_close_week = st.checkbox(
                    (
                        f"Confirmo el conteo y quiero cerrar "
                        f"{week.week_id}."
                    ),
                    key=
                        f"confirm_close_week_{week.week_id}",
                )

                if st.button(
                    f"🔒 Cerrar {week.week_id} y abrir siguiente",
                    type="primary",
                    key=
                        f"close_week_button_{week.week_id}",
                    disabled=(
                        not confirm_close_week
                        or invalid_close_rows > 0
                    ),
                ):
                    ok, result = run_ui_mutation(
                        running_label=
                            (
                                f"Guardando conteo final de {week.week_id}, "
                                "cerrando semana y creando la siguiente..."
                            ),

                        success_label=
                            lambda result:
                                (
                                    f"{result['closed_week_id']} cerrada · "
                                    f"{result['total_latas']} latas · "
                                    f"{result['total_stock_kg']:.3f} kg · "
                                    f"+{len(result['added_ids'])} altas · "
                                    f"-{len(result['removed_ids'])} bajas · "
                                    f"{len(result['corrected_ids'])} correcciones · "
                                    f"{result['next_week_id']} abierta automáticamente."
                                ),

                        error_label=
                            "No se pudo completar el cierre semanal.",

                        operation=
                            lambda:
                                close_week_with_final_count(
                                    week_id=
                                        week.week_id,

                                    edited_df=
                                        edited_close_count,

                                    notes=
                                        close_notes,
                                ),
                    )

                    if ok:
                        st.success(
                            (
                                f"Conteo cierre: "
                                f"{result['close_count_id']} · "
                                f"Conteo inicio siguiente: "
                                f"{result['next_start_count_id']}."
                            )
                        )

                        st.rerun()

        else:
            st.success(
                (
                    f"🔒 {week.week_id} está cerrada desde "
                    f"{week.closed_at}."
                )
            )

        st.divider()

        st.markdown(
            "#### 🧾 Auditoría de reconstrucción"
        )

        audit_df = pd.DataFrame(
            [
                {
                    "Dato":
                        "Snapshot salón inicial",

                    "Fuente":
                        week.start_salon_snapshot_source,
                },
                {
                    "Dato":
                        "Snapshot cámara inicial",

                    "Fuente":
                        week.start_camera_snapshot_source,
                },
                {
                    "Dato":
                        "Snapshot salón final",

                    "Fuente":
                        week.end_salon_snapshot_source,
                },
                {
                    "Dato":
                        "Snapshot cámara final",

                    "Fuente":
                        week.end_camera_snapshot_source,
                },
                {
                    "Dato":
                        "Metadata version",

                    "Fuente":
                        week.metadata_version,
                },
                {
                    "Dato":
                        "Último refresh",

                    "Fuente":
                        week.metadata_refreshed_at,
                },
            ]
        )

        st.dataframe(
            audit_df,
            hide_index=True,
            use_container_width=True,
        )


# ============================================================
# CONFIGURACIÓN
# ============================================================

with tab_config:
    st.header(
        "⚙️ Configuración"
    )

    st.subheader(
        "🍦 Sabores"
    )

    flavors_df = load_flavor_catalog(
        active_only=False
    )

    if flavors_df.empty:
        st.info(
            "Todavía no hay sabores configurados."
        )
    else:
        st.dataframe(
            flavors_df.sort_values(
                "sabor"
            ),
            hide_index=True,
            use_container_width=True,
        )

    st.divider()

    st.markdown(
        "### ➕ Agregar sabor"
    )

    c_flavor, c_code = st.columns(
        2
    )

    with c_flavor:
        new_flavor = st.text_input(
            "Nombre del sabor",
            placeholder="Ej: CHOCOLATE_CON_ALMENDRAS",
            key="new_flavor_name",
        )

    with c_code:
        proposed_code = ""

        if new_flavor.strip():
            try:
                proposed_code = propose_flavor_code(
                    new_flavor,
                    used_codes=(
                        flavors_df[
                            "flavor_code"
                        ]
                        .dropna()
                        .astype(str)
                        .tolist()
                        if (
                            not flavors_df.empty
                            and "flavor_code"
                            in flavors_df.columns
                        )
                        else []
                    ),
                )
            except ValueError:
                proposed_code = ""

        new_flavor_code = st.text_input(
            "Código único",
            value=proposed_code,
            placeholder="Ej: ALM",
            key="new_flavor_code",
            help=(
                "Se usa en IDs de cámara. "
                "Ej: CHOCOLATE=CHO, CHOCOLATE_CON_ALMENDRAS=ALM, "
                "DULCE_DE_LECHE=DDL, DULCE_DE_LECHE_BROWNIE=BRW."
            ),
        )

    if st.button(
        "Agregar sabor",
        type="primary",
        key="add_flavor_button",
    ):
        ok, result = run_ui_mutation(
            running_label=
                "Agregando nuevo sabor al catálogo...",

            success_label=
                lambda result:
                    (
                        f"Sabor agregado: "
                        f"{result[0]} · código {result[1]}"
                    ),

            error_label=
                "No se pudo agregar el sabor.",

            operation=
                lambda:
                    add_flavor(
                        new_flavor,
                        new_flavor_code,
                    ),
        )

        if ok:
            st.rerun()




    st.divider()

    st.subheader(
        "📦 Productos"
    )

    st.caption(
        "Este catálogo se configura una sola vez. "
        "Después, en Cámara, los productos aparecen en desplegables."
    )

    products_catalog_df = load_product_catalog(
        active_only=False
    )

    if products_catalog_df.empty:
        st.info(
            "Todavía no hay productos configurados."
        )

    else:
        catalog_display = products_catalog_df.copy()

        st.dataframe(
            catalog_display.sort_values(
                [
                    "categoria",
                    "subcategoria",
                    "producto",
                ],
                na_position="last",
            ),
            hide_index=True,
            use_container_width=True,
            column_config={
                "product_code":
                    st.column_config.TextColumn(
                        "Código"
                    ),

                "categoria":
                    st.column_config.TextColumn(
                        "Categoría"
                    ),

                "subcategoria":
                    st.column_config.TextColumn(
                        "Subcategoría"
                    ),

                "producto":
                    st.column_config.TextColumn(
                        "Producto"
                    ),

                "tipo_empaque":
                    st.column_config.TextColumn(
                        "Empaque"
                    ),

                "unidades_por_bulto":
                    st.column_config.NumberColumn(
                        "Unid. x bulto",
                        format="%d",
                    ),

                "active":
                    st.column_config.CheckboxColumn(
                        "Activo"
                    ),
            },
        )

        active_catalog = products_catalog_df[
            products_catalog_df[
                "active"
            ]
            == True
        ].copy()

        if not active_catalog.empty:
            deactivate_map = {
                (
                    f"{row['product_code']} · "
                    f"{row['producto']} · "
                    f"{row['categoria']}"
                ):
                    row[
                        "product_code"
                    ]

                for _, row in active_catalog.iterrows()
            }

            selected_deactivate_label = st.selectbox(
                "Producto activo para desactivar",
                options=
                    [
                        "—",
                        *deactivate_map.keys(),
                    ],
                key="deactivate_catalog_product_select",
            )

            if (
                selected_deactivate_label
                != "—"
            ):
                if st.button(
                    "Desactivar producto",
                    key="deactivate_catalog_product_button",
                ):
                    ok, product = run_ui_mutation(
                        running_label=
                            "Desactivando producto del catálogo...",

                        success_label=
                            lambda product:
                                (
                                    f"Producto desactivado · "
                                    f"{product.product_code}"
                                ),

                        error_label=
                            "No se pudo desactivar el producto.",

                        operation=
                            lambda:
                                deactivate_catalog_product(
                                    product_code=
                                        deactivate_map[
                                            selected_deactivate_label
                                        ]
                                ),
                    )

                    if ok:
                        st.rerun()

    st.markdown("### ➕ Agregar producto al catálogo")
    st.caption("Definí una vez la estructura física. Todo termina normalizado en unidades.")
    product_category=st.selectbox("Categoría",list(CATEGORY_CODES.keys()),key="catalog_product_category")
    subs=CATEGORY_SUBCATEGORIES.get(product_category,[]); catalog_subcategory=st.selectbox("Subcategoría",subs,key="catalog_product_subcategory") if subs else None
    default=PACKAGING_PACK_BOXES_UNITS if product_category in {"BOMBONES","POSTRES","PALITOS"} else (PACKAGING_PACK_UNITS if product_category in {"TENTACIONES","TORTAS","FAMILIARES"} else PACKAGING_BOX_UNITS)
    labels={PACKAGING_PACK_BOXES_UNITS:"Pack → Cajas → Unidades",PACKAGING_PACK_UNITS:"Pack → Unidades",PACKAGING_BOX_UNITS:"Caja → Unidades"}
    with st.form("catalog_product_form"):
        name=st.text_input("Nombre del producto"); opts=list(labels); mode=st.selectbox("Estructura",opts,index=opts.index(default),format_func=lambda x:labels[x]); cpp=upp=upc=None
        if mode==PACKAGING_PACK_BOXES_UNITS:
            a,b=st.columns(2); cpp=a.number_input("Cajas por pack",1,step=1); upc=b.number_input("Unidades por caja",1,step=1)
        elif mode==PACKAGING_PACK_UNITS:
            unit_label="Tortas por pack" if product_category=="TORTAS" else ("Familiares por pack" if product_category=="FAMILIARES" else "Unidades por pack"); upp=st.number_input(unit_label,1,step=1)
        else:
            upc=st.number_input("Unidades por caja",1,step=1)
        st.caption(f"Próximo código: {generate_product_code(product_category)}"); submit=st.form_submit_button("Agregar producto al catálogo",type="primary",use_container_width=True)
    if submit:
        if not name.strip(): st.error("Ingresá el nombre del producto.")
        else:
            ok,p=run_ui_mutation(running_label="Guardando producto...",success_label=lambda p:f"Producto guardado · {p.product_code}",error_label="No se pudo guardar.",operation=lambda:add_catalog_product(categoria=product_category,subcategoria=catalog_subcategory,producto=name,packaging_mode=mode,cajas_por_pack=cpp,unidades_por_pack=upp,unidades_por_caja=upc))
            if ok: st.rerun()



# ============================================================
# HISTORIAL
# ============================================================

with tab_history:
    st.header(
        "🕒 Historial"
    )

    h1, h2, h3, h4, h5 = st.tabs(
        [
            "Movimientos",
            "Conteos",
            "Stock completo",
            "Sabores",
            "Ficha lata",
        ]
    )

    with h1:
        movements = load_csv(
            MOVEMENTS_FILE
        )

        if movements.empty:
            st.info(
                "No hay movimientos registrados."
            )

        else:
            # ====================================================
            # NORMALIZAR
            # ====================================================

            movements[
                "timestamp_dt"
            ] = pd.to_datetime(
                movements[
                    "timestamp"
                ],
                errors="coerce",
            )

            movements[
                "peso_neto_kg"
            ] = pd.to_numeric(
                movements[
                    "peso_neto_kg"
                ],
                errors="coerce",
            )

            st.markdown(
                "### 🔎 Filtros"
            )

            st.caption(
                "Todos los pesos se muestran en kg. "
                "Ejemplo: 7.580 kg = 7580 g."
            )

            f1, f2, f3, f4 = st.columns(
                4
            )

            movement_types = sorted(
                movements[
                    "movement_type"
                ]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )

            with f1:
                selected_types = st.multiselect(
                    "Tipo de movimiento",
                    options=movement_types,
                    default=[],
                    placeholder="Todos",
                    key="history_movement_types",
                )

            sabores = sorted(
                movements[
                    "sabor"
                ]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )

            with f2:
                selected_flavors = st.multiselect(
                    "Sabor",
                    options=sabores,
                    default=[],
                    placeholder="Todos",
                    key="history_flavors",
                )

            valid_dates = (
                movements[
                    "timestamp_dt"
                ]
                .dropna()
            )

            min_date = (
                valid_dates.min().date()
                if not valid_dates.empty
                else now_local().date()
            )

            max_date = (
                valid_dates.max().date()
                if not valid_dates.empty
                else now_local().date()
            )

            with f3:
                date_from = st.date_input(
                    "Desde",
                    value=min_date,
                    min_value=min_date,
                    max_value=max_date,
                    key="history_date_from",
                )

            with f4:
                date_to = st.date_input(
                    "Hasta",
                    value=max_date,
                    min_value=min_date,
                    max_value=max_date,
                    key="history_date_to",
                )

            # ====================================================
            # FILTRO PESO NETO
            # ====================================================

            valid_weights = (
                movements[
                    "peso_neto_kg"
                ]
                .dropna()
            )

            weight_range = None

            if not valid_weights.empty:
                min_weight = float(
                    valid_weights.min()
                )

                max_weight = float(
                    valid_weights.max()
                )

                if min_weight == max_weight:
                    weight_range = (
                        min_weight,
                        max_weight,
                    )

                    st.caption(
                        f"Peso neto disponible: "
                        f"{min_weight:.3f} kg"
                    )

                else:
                    weight_range = st.slider(
                        "Peso neto kg",
                        min_value=min_weight,
                        max_value=max_weight,
                        value=(
                            min_weight,
                            max_weight,
                        ),
                        step=0.05,
                        format="%.3f kg",
                        key="history_weight_range",
                    )

            # ====================================================
            # APLICAR FILTROS
            # ====================================================

            filtered_movements = (
                movements.copy()
            )

            if selected_types:
                filtered_movements = (
                    filtered_movements[
                        filtered_movements[
                            "movement_type"
                        ]
                        .astype(str)
                        .isin(
                            selected_types
                        )
                    ]
                )

            if selected_flavors:
                filtered_movements = (
                    filtered_movements[
                        filtered_movements[
                            "sabor"
                        ]
                        .astype(str)
                        .isin(
                            selected_flavors
                        )
                    ]
                )

            filtered_movements = (
                filtered_movements[
                    filtered_movements[
                        "timestamp_dt"
                    ]
                    .dt.date
                    .between(
                        date_from,
                        date_to,
                    )
                ]
            )

            if weight_range is not None:
                min_selected_weight = (
                    weight_range[0]
                )

                max_selected_weight = (
                    weight_range[1]
                )

                weight_mask = (
                    filtered_movements[
                        "peso_neto_kg"
                    ]
                    .isna()
                    |
                    filtered_movements[
                        "peso_neto_kg"
                    ]
                    .between(
                        min_selected_weight,
                        max_selected_weight,
                    )
                )

                filtered_movements = (
                    filtered_movements[
                        weight_mask
                    ]
                )

            filtered_movements = (
                filtered_movements
                .sort_values(
                    "timestamp_dt",
                    ascending=False,
                )
            )

            # ====================================================
            # KPIS
            # ====================================================

            st.markdown(
                "### Resultado"
            )

            m1, m2, m3, m4 = st.columns(
                4
            )

            m1.metric(
                "Movimientos",
                len(
                    filtered_movements
                ),
            )

            m2.metric(
                "Sabores",
                filtered_movements[
                    "sabor"
                ]
                .dropna()
                .nunique(),
            )

            m3.metric(
                "Kg netos",
                f"{filtered_movements['peso_neto_kg'].fillna(0).sum():.3f} kg",
            )

            if "operation_id" in filtered_movements.columns:
                operations_count = (
                    filtered_movements[
                        "operation_id"
                    ]
                    .dropna()
                    .nunique()
                )
            else:
                operations_count = 0

            m4.metric(
                "Operaciones",
                operations_count,
            )

            display_movements = (
                filtered_movements.drop(
                    columns=[
                        "timestamp_dt",
                    ],
                    errors="ignore",
                )
            )

            st.dataframe(
                display_movements,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "peso_bruto_kg":
                        st.column_config.NumberColumn(
                            "Peso bruto (kg)",
                            format="%.3f kg",
                        ),

                    "tara_kg":
                        st.column_config.NumberColumn(
                            "Tara (kg)",
                            format="%.3f kg",
                        ),

                    "peso_neto_kg":
                        st.column_config.NumberColumn(
                            "Peso neto (kg)",
                            format="%.3f kg",
                        ),
                },
            )

    with h2:
        st.dataframe(
            load_csv(
                COUNTS_FILE
            ),
            hide_index=True,
            use_container_width=True,
        )

    with h3:
        stock_h1, stock_h2 = st.tabs(
            [
                "Latas salón",
                "Stock cámara",
            ]
        )

        with stock_h1:
            st.dataframe(
                load_salon_latas(),
                hide_index=True,
                use_container_width=True,
            )

        with stock_h2:
            st.dataframe(
                load_camera_stock(),
                hide_index=True,
                use_container_width=True,
            )

    with h4:
        st.dataframe(
            load_csv(
                FLAVORS_FILE
            ),
            hide_index=True,
            use_container_width=True,
        )


    with h5:
        stock_history = load_current_stock()

        latas_history = stock_history[
            stock_history[
                "location"
            ]
            .astype(str)
            .str.upper()
            .eq("SALON")
        ].copy()

        if latas_history.empty:
            st.info(
                "Todavía no hay latas individuales registradas."
            )

        else:
            ficha_options = {}

            for idx, row in latas_history.iterrows():
                label = (
                    f"{row['stock_id']} · "
                    f"{row['sabor']} · "
                    f"{row['estado']} · "
                    f"fila {idx}"
                )

                ficha_options[
                    label
                ] = idx

            selected_ficha = st.selectbox(
                "Seleccionar lata",
                options=list(
                    ficha_options.keys()
                ),
                key="history_lata_ficha",
            )

            ficha_idx = ficha_options[
                selected_ficha
            ]

            ficha_row = latas_history.loc[
                ficha_idx
            ]

            ficha_lata = Lata.from_row(
                ficha_row
            )

            st.markdown(
                f"### 🍦 {ficha_lata.stock_id} · "
                f"{ficha_lata.sabor}"
            )

            c1, c2, c3, c4 = st.columns(
                4
            )

            c1.metric(
                "Estado",
                ficha_lata.estado,
            )

            c2.metric(
                "Activo",
                (
                    "Sí"
                    if ficha_lata.active
                    else "No"
                ),
            )

            c3.metric(
                "Origen cámara",
                ficha_lata.source_camera_stock_id
                or "-",
            )

            c4.metric(
                "Tara final real",
                (
                    f"{ficha_lata.tara_final_kg:.3f} kg"
                    if ficha_lata.tara_final_kg
                    is not None
                    else "-"
                ),
            )

            if ficha_lata.residuo_final_kg is not None:
                st.metric(
                    "Residuo estimado de helado",
                    f"{ficha_lata.residuo_final_kg:.3f} kg",
                )

            def _fmt_ts(value):
                if not value:
                    return "-"

                dt = pd.to_datetime(
                    value,
                    errors="coerce",
                )

                if pd.isna(dt):
                    return str(
                        value
                    )

                return dt.strftime(
                    "%d/%m/%Y %H:%M:%S"
                )

            def _duration(
                start_value,
                end_value,
            ):
                if (
                    not start_value
                    or not end_value
                ):
                    return "-"

                start_dt = pd.to_datetime(
                    start_value,
                    errors="coerce",
                    utc=True,
                )

                end_dt = pd.to_datetime(
                    end_value,
                    errors="coerce",
                    utc=True,
                )

                if (
                    pd.isna(start_dt)
                    or pd.isna(end_dt)
                ):
                    return "-"

                seconds = max(
                    int(
                        (
                            end_dt
                            - start_dt
                        )
                        .total_seconds()
                    ),
                    0,
                )

                days, rem = divmod(
                    seconds,
                    86400,
                )

                hours, rem = divmod(
                    rem,
                    3600,
                )

                minutes = (
                    rem
                    // 60
                )

                if days:
                    return (
                        f"{days} d "
                        f"{hours} h "
                        f"{minutes} min"
                    )

                return (
                    f"{hours} h "
                    f"{minutes} min"
                )

            st.markdown(
                "#### 🕒 Ciclo de vida"
            )

            lifecycle_df = pd.DataFrame(
                [
                    {
                        "Evento":
                            "Ingreso al salón",

                        "Timestamp":
                            _fmt_ts(
                                ficha_lata
                                .ingresada_salon_at
                            ),

                        "Operation ID":
                            "-",
                    },
                    {
                        "Evento":
                            "Apertura",

                        "Timestamp":
                            _fmt_ts(
                                ficha_lata
                                .opened_at
                            ),

                        "Operation ID":
                            ficha_lata
                            .opened_operation_id
                            or "-",
                    },
                    {
                        "Evento":
                            "Finalización",

                        "Timestamp":
                            _fmt_ts(
                                ficha_lata
                                .finished_at
                            ),

                        "Operation ID":
                            ficha_lata
                            .finished_operation_id
                            or "-",
                    },
                ]
            )

            st.dataframe(
                lifecycle_df,
                hide_index=True,
                use_container_width=True,
            )

            d1, d2 = st.columns(
                2
            )

            d1.metric(
                "Tiempo cerrada conocido",
                _duration(
                    ficha_lata
                    .ingresada_salon_at,

                    ficha_lata
                    .opened_at,
                ),
            )

            d2.metric(
                "Tiempo abierta conocido",
                _duration(
                    ficha_lata
                    .opened_at,

                    ficha_lata
                    .finished_at,
                ),
            )

            st.markdown(
                "#### ⚖️ Pesos"
            )

            weights_df = pd.DataFrame(
                [
                    {
                        "Momento":
                            "Ingreso",

                        "Bruto kg":
                            ficha_lata
                            .peso_inicial_bruto_kg,

                        "Tara kg":
                            ficha_lata
                            .tara_inicial_kg,

                        "Neto kg":
                            ficha_lata
                            .peso_inicial_neto_kg,
                    },
                    {
                        "Momento":
                            "Actual",

                        "Bruto kg":
                            ficha_lata
                            .peso_actual_bruto_kg,

                        "Tara kg":
                            ficha_lata
                            .tara_actual_kg,

                        "Neto kg":
                            ficha_lata
                            .peso_actual_neto_kg,
                    },
                    {
                        "Momento":
                            "Final",

                        "Bruto kg":
                            ficha_lata
                            .peso_final_bruto_kg,

                        "Tara kg":
                            ficha_lata
                            .tara_final_kg,

                        "Neto kg":
                            (
                                0.0
                                if ficha_lata
                                .finished_at
                                else None
                            ),
                    },
                ]
            )

            st.dataframe(
                weights_df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Bruto kg":
                        st.column_config.NumberColumn(
                            "Bruto",
                            format="%.3f kg",
                        ),

                    "Tara kg":
                        st.column_config.NumberColumn(
                            "Tara",
                            format="%.3f kg",
                        ),

                    "Neto kg":
                        st.column_config.NumberColumn(
                            "Neto",
                            format="%.3f kg",
                        ),
                },
            )

