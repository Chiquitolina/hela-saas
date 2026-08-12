import streamlit as st
import pandas as pd

from io import BytesIO
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import shutil
import uuid

from models.lata import Lata
from models.camera_stock import CameraStock
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
    split_inventory,
)

from services.week_service import (
    backfill_lata_metadata_from_movements,
    current_stock_snapshot,
    refresh_weeks_dataframe,
    repair_missing_estimated_residue,
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
            stock["cantidad_latas"].fillna(0) > 0
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
):
    """
    Escritura protegida:
    1. evita reemplazar accidentalmente un CSV con datos por uno vacío;
    2. crea backup;
    3. escribe a un temporal;
    4. reemplaza el original de forma atómica.
    """

    filepath = Path(filepath)

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

    try:
        df.to_csv(
            temp_path,
            index=False,
        )

        temp_path.replace(
            filepath
        )

    finally:
        if temp_path.exists():
            temp_path.unlink(
                missing_ok=True
            )


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

    safe_write_csv(
        flavor_df[
            FLAVOR_COLUMNS
        ],
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
    end_count_id,
    end_stock_kg,
    timestamp,
):
    weeks = load_weeks()

    mask = (
        weeks["week_id"]
        == week_id
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

    snapshot = current_stock_snapshot(
        load_current_stock()
    )

    weeks.loc[
        idx,
        "status"
    ] = "CLOSED"

    weeks.loc[
        idx,
        "closed_at"
    ] = timestamp

    weeks.loc[
        idx,
        "end_count_id"
    ] = end_count_id

    weeks.loc[
        idx,
        "end_stock_kg"
    ] = round(
        float(
            end_stock_kg
        ),
        3,
    )

    weeks.loc[
        idx,
        "end_salon_latas"
    ] = snapshot[
        "salon_latas"
    ]

    weeks.loc[
        idx,
        "end_salon_kg"
    ] = round(
        float(
            end_stock_kg
        ),
        3,
    )

    weeks.loc[
        idx,
        "end_camera_latas"
    ] = snapshot[
        "camera_latas"
    ]

    weeks.loc[
        idx,
        "end_camera_kg"
    ] = snapshot[
        "camera_kg"
    ]

    weeks.loc[
        idx,
        "end_salon_snapshot_source"
    ] = "LIVE_END_COUNT"

    weeks.loc[
        idx,
        "end_camera_snapshot_source"
    ] = "LIVE_END_SNAPSHOT"

    weeks.loc[
        idx,
        "metadata_refreshed_at"
    ] = timestamp

    safe_write_csv(
        weeks[
            WEEK_COLUMNS
        ],
        WEEKS_FILE,
    )

    # Recalcula actividad, consumo físico y demás métricas.
    refresh_all_metadata()


# ============================================================
# CAMERA STOCK
# ============================================================

def add_camera_stock(
    sabor,
    cantidad_latas,
    kg_referencia_lata,
    notes="",
):
    sabor = normalize_flavor_name(
        sabor
    )

    if not sabor:
        raise ValueError(
            "Seleccioná un sabor."
        )

    stock = load_current_stock()

    timestamp = now_iso()

    stock_id = generate_camera_id(
        sabor
    )

    cantidad_latas = int(
        cantidad_latas
    )

    kg_referencia_lata = round(
        float(
            kg_referencia_lata
        ),
        3,
    )

    if (
        kg_referencia_lata <= 0
        or kg_referencia_lata > MAX_CAN_GROSS_KG
    ):
        raise ValueError(
            f"El peso de referencia debe estar entre "
            f"0 y {MAX_CAN_GROSS_KG:.3f} kg. "
            "Ejemplo: 7580 g se carga como 7.580 kg."
        )

    camera_item = CameraStock.create(
        camera_stock_id=
            stock_id,

        sabor=
            sabor,

        cantidad_latas=
            cantidad_latas,

        kg_referencia_lata=
            kg_referencia_lata,

        timestamp=
            timestamp,
    )

    camera = load_camera_stock()

    camera = pd.concat(
        [
            camera,
            pd.DataFrame(
                [
                    camera_item.to_row()
                ]
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

    append_row(
        MOVEMENTS_FILE,
        {
            "movement_id":
                generate_id("MOV"),

            "timestamp":
                timestamp,

            "week_id":
                (
                    open_week["week_id"]
                    if open_week is not None
                    else pd.NA
                ),

            "movement_type":
                "INGRESO_CAMARA",

            "from_location":
                "EXTERNO",

            "to_location":
                "CAMARA",

            "source_stock_id":
                pd.NA,

            "target_stock_id":
                stock_id,

            "sabor":
                sabor,

            "cantidad_latas":
                cantidad_latas,

            "peso_bruto_kg":
                pd.NA,

            "tara_kg":
                pd.NA,

            "peso_neto_kg":
                cantidad_latas
                * kg_referencia_lata,

            "notes":
                notes,
        }
    )

    return stock_id




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
        stock["stock_id"]
        == camera_stock_id
    )

    if not mask.any():
        raise ValueError(
            "No se encontró ese stock de cámara."
        )

    idx = stock[
        mask
    ].index[0]

    source = stock.loc[
        idx
    ]

    if not bool(
        source["active"]
    ):
        raise ValueError(
            "Ese stock de cámara no está activo."
        )

    available = int(
        source[
            "cantidad_latas"
        ]
    )

    if available <= 0:
        raise ValueError(
            "No quedan latas disponibles."
        )

    timestamp = now_iso()

    salon_id = generate_salon_id(
        stock
    )

    sabor = normalize_flavor_name(
        source["sabor"]
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

    remaining = (
        available - 1
    )

    stock.loc[
        idx,
        "cantidad_latas"
    ] = remaining

    stock.loc[
        idx,
        "updated_at"
    ] = timestamp

    if remaining <= 0:
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
                    open_week["week_id"]
                    if open_week is not None
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
        if open_week is None:
            raise ValueError(
                "No existe una semana abierta "
                "para cerrar."
            )

        week_id = open_week[
            "week_id"
        ]

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

    if count_type == "CIERRE_SEMANA":
        close_week(
            week_id=
                week_id,

            end_count_id=
                count_id,

            end_stock_kg=
                total_stock_kg,

            timestamp=
                timestamp,
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
                stock["cantidad_latas"]
                .fillna(0)
                > 0
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

        available_qty = int(
            camera_row[
                "cantidad_latas"
            ]
        )

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

        remaining = (
            available_qty - 1
        )

        stock.loc[
            camera_idx,
            "cantidad_latas"
        ] = remaining

        stock.loc[
            camera_idx,
            "updated_at"
        ] = timestamp

        if remaining <= 0:
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
# UI
# ============================================================

st.title(
    "🍦 Control"
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

        st.markdown(
            "#### 🔄 Actividad de la semana"
        )

        a1, a2, a3, a4 = st.columns(
            4
        )

        a1.metric(
            "Cámara → salón",
            f"{week.camera_to_salon_latas} latas",
        )

        a2.metric(
            "Kg desde cámara",
            f"{week.camera_to_salon_kg:.3f} kg",
        )

        a3.metric(
            "Cambios de sabor",
            week.cambios_sabor,
        )

        a4.metric(
            "Latas terminadas",
            week.latas_terminadas,
        )

        a5, a6, a7, a8 = st.columns(
            4
        )

        a5.metric(
            "Latas abiertas",
            week.latas_abiertas,
        )

        a6.metric(
            "Recambios",
            week.recambios,
        )

        a7.metric(
            "Latas con tara final",
            week.latas_con_tara_final,
        )

        a8.metric(
            "Tara final acumulada",
            f"{week.tara_final_total_kg:.3f} kg",
        )

        a9, a10 = st.columns(
            2
        )

        a9.metric(
            "Residuo estimado de helado",
            f"{week.residuo_estimado_kg:.3f} kg",
        )

        a10.metric(
            "Ingresos a cámara",
            f"{week.ingreso_camera_latas} latas",
        )

        st.metric(
            "Kg ingresados a cámara",
            f"{week.ingreso_camera_kg:.3f} kg",
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
                    weeks_mix = load_weeks()

                    week_mask = (
                        weeks_mix[
                            "week_id"
                        ]
                        == open_week_for_mix[
                            "week_id"
                        ]
                    )

                    if week_mask.any():
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

                        st.success(
                            "Consumo teórico asociado a la semana."
                        )

                        st.rerun()


# ============================================================
# STOCK ACTUAL
# ============================================================

with tab_stock:
    st.header(
        "📦 Stock actual"
    )

    st.caption(
        "Persistencia separada: salon_latas.csv para latas individuales "
        "y camera_stock.csv para stock agregado de cámara."
    )

    camera_tab, salon_tab = st.tabs(
        [
            "❄️ Cámara",
            "🍦 Salón",
        ]
    )

    with camera_tab:
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
            camera[
                "kg_estimados"
            ] = (
                camera[
                    "cantidad_latas"
                ].fillna(0)
                *
                camera[
                    "kg_referencia_lata"
                ].fillna(0)
            )

            st.dataframe(
                camera[
                    [
                        "stock_id",
                        "sabor",
                        "cantidad_latas",
                        "kg_referencia_lata",
                        "kg_estimados",
                    ]
                ],
                hide_index=True,
                use_container_width=True,
            )

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
                    try:
                        add_camera_stock(
                            sabor=
                                camera_sabor,

                            cantidad_latas=
                                camera_qty,

                            kg_referencia_lata=
                                camera_ref,

                            notes=
                                camera_notes,
                        )

                        st.rerun()

                    except ValueError as e:
                        st.error(
                            str(e)
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
                    try:
                        open_salon_can(
                            stock_id=
                                open_options[
                                    selected_open_label
                                ],

                            notes=
                                open_notes,
                        )

                        st.success(
                            "Lata marcada como ABIERTA."
                        )

                        st.rerun()

                    except ValueError as e:
                        st.error(
                            str(e)
                        )

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
                    try:
                        result = mark_salon_can_empty(
                            stock_id=
                                selected_finish_id,

                            tara_final_kg=
                                finish_tara_final,

                            peso_final_bruto_kg=
                                None,

                            notes=
                                finish_notes,
                        )

                        success_msg = (
                            "Lata marcada como AGOTADA · "
                            f"tara final "
                            f"{result['tara_final_kg']:.3f} kg"
                        )

                        if (
                            result[
                                "residuo_final_kg"
                            ]
                            is not None
                        ):
                            success_msg += (
                                f" · residuo estimado "
                                f"{result['residuo_final_kg']:.3f} kg"
                            )

                        if (
                            result[
                                "residuo_final_kg"
                            ]
                            is not None
                        ):
                            success_msg += (
                                f" · residuo "
                                f"{result['residuo_final_kg']:.3f} kg"
                            )

                        st.success(
                            success_msg
                        )

                        st.rerun()

                    except ValueError as e:
                        st.error(
                            str(e)
                        )



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
                camera_same_flavor[
                    "cantidad_latas"
                ]
                .fillna(0)
                .sum()
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
                try:
                    result = perform_salon_replacement(
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
                    )

                    msg = (
                        f"Recambio registrado · "
                        f"{result['operation_id']}"
                    )

                    if (
                        result[
                            "opened_reserve_stock_id"
                        ]
                        is not None
                    ):
                        msg += (
                            f" · Abierta "
                            f"{result['opened_reserve_stock_id']}"
                        )

                    if result["replenished"]:
                        msg += (
                            f" · Nueva "
                            f"{result['new_salon_stock_id']} "
                            f"{result['new_salon_state']}"
                        )

                    if result[
                        "cambio_sabor_registered"
                    ]:
                        msg += (
                            f" · CAMBIO_SABOR → "
                            f"{result['cambio_sabor_target_stock_id']}"
                        )

                    st.success(
                        msg
                    )

                    st.rerun()

                except ValueError as e:
                    st.error(
                        str(e)
                    )


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
        &
        (
            stock[
                "cantidad_latas"
            ].fillna(0)
            > 0
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
            .agg(
                cantidad_latas=(
                    "cantidad_latas",
                    "sum",
                ),
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
            try:
                move_camera_flavor_to_salon(
                    sabor=
                        selected_flavor,

                    peso_bruto_kg=
                        transfer_bruto,

                    tara_kg=
                        transfer_tara,

                    notes=
                        transfer_notes,
                )

                st.rerun()

            except ValueError as e:
                st.error(
                    str(e)
                )


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
                "Cierre de semana",
            ],
            horizontal=True,
        )

    COUNT_TYPE_MAP = {
        "Inicio de semana":
            "INICIO_SEMANA",

        "Control":
            "CONTROL",

        "Cierre de semana":
            "CIERRE_SEMANA",
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
        try:
            result = save_salon_count(
                edited_df=
                    edited_count,

                count_type=
                    count_type,

                notes=
                    count_notes,
            )

        except ValueError as e:
            st.error(
                str(e)
            )

            st.stop()

        if result[
            "valid_rows"
        ] == 0:
            st.error(
                "No se pudo guardar "
                "ninguna fila válida."
            )

            for error in result[
                "errors"
            ]:
                st.warning(error)

        else:
            if count_type == "INICIO_SEMANA":
                st.success(
                    f"Semana iniciada · "
                    f"{result['total_stock_kg']:.3f} kg"
                )

            elif count_type == "CIERRE_SEMANA":
                st.success(
                    f"Semana cerrada · "
                    f"{result['total_stock_kg']:.3f} kg"
                )

            else:
                st.success(
                    f"Conteo guardado · "
                    f"{result['total_stock_kg']:.3f} kg"
                )

            st.rerun()


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
            report = refresh_all_metadata(
                show_result=True
            )

            st.success(
                "Metadata recalculada."
            )

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
            "start_salon_kg",
            "start_camera_latas",
            "start_camera_kg",
            "camera_to_salon_latas",
            "camera_to_salon_kg",
            "cambios_sabor",
            "latas_terminadas",
            "latas_abiertas",
            "recambios",
            "latas_con_tara_final",
            "tara_final_total_kg",
            "residuo_estimado_kg",
            "current_salon_kg",
            "current_camera_latas",
            "consumo_fisico_kg",
        ]

        visible_columns = [
            col
            for col in summary_columns
            if col in weeks.columns
        ]

        st.dataframe(
            weeks[
                visible_columns
            ],
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

                "camera_to_salon_kg":
                    st.column_config.NumberColumn(
                        "Kg cámara → salón",
                        format="%.3f kg",
                    ),

                "tara_final_total_kg":
                    st.column_config.NumberColumn(
                        "Tara final acumulada",
                        format="%.3f kg",
                    ),

                "residuo_estimado_kg":
                    st.column_config.NumberColumn(
                        "Residuo estimado",
                        format="%.3f kg",
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
        try:
            flavor, code = add_flavor(
                new_flavor,
                new_flavor_code,
            )

            st.success(
                f"Sabor agregado: {flavor} · código {code}"
            )

            st.rerun()

        except ValueError as e:
            st.error(
                str(e)
            )


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

