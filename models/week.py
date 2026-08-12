from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd


WEEK_METADATA_VERSION = 3

WEEK_COLUMNS = [
    "week_id",
    "status",
    "started_at",
    "closed_at",

    "start_count_id",
    "end_count_id",

    # Snapshot inicial del salón
    "start_stock_kg",
    "start_salon_latas",
    "start_salon_kg",

    # Snapshot inicial de cámara
    "start_camera_latas",
    "start_camera_kg",

    # Estado vivo / último refresh
    "current_salon_latas",
    "current_salon_kg",
    "current_camera_latas",
    "current_camera_kg",

    # Snapshot final
    "end_stock_kg",
    "end_salon_latas",
    "end_salon_kg",
    "end_camera_latas",
    "end_camera_kg",

    # Actividad
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

    # Resultado físico
    "consumo_fisico_kg",

    # Resultado comercial (se completa cuando se asocia Mix Ventas)
    "consumo_teorico_kg",
    "merma_kg",
    "merma_pct",
    "merma_no_explicada_kg",

    # Auditoría de la reconstrucción
    "metadata_version",
    "metadata_refreshed_at",
    "start_salon_snapshot_source",
    "start_camera_snapshot_source",
    "end_salon_snapshot_source",
    "end_camera_snapshot_source",

    "notes",
]


def _float(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    try:
        if value is None or pd.isna(value):
            return None
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> Optional[str]:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    value = str(value).strip()

    if not value or value.lower() in {"nan", "none", "<na>"}:
        return None

    return value


@dataclass
class Week:
    week_id: str
    status: str
    started_at: str
    closed_at: Optional[str]

    start_count_id: Optional[str]
    end_count_id: Optional[str]

    start_stock_kg: Optional[float]
    start_salon_latas: Optional[int]
    start_salon_kg: Optional[float]

    start_camera_latas: Optional[int]
    start_camera_kg: Optional[float]

    current_salon_latas: Optional[int]
    current_salon_kg: Optional[float]
    current_camera_latas: Optional[int]
    current_camera_kg: Optional[float]

    end_stock_kg: Optional[float]
    end_salon_latas: Optional[int]
    end_salon_kg: Optional[float]
    end_camera_latas: Optional[int]
    end_camera_kg: Optional[float]

    camera_to_salon_latas: int
    camera_to_salon_kg: float
    ingreso_camera_latas: int
    ingreso_camera_kg: float
    latas_abiertas: int
    latas_terminadas: int
    cambios_sabor: int
    recambios: int
    latas_con_tara_final: int
    tara_final_total_kg: float
    residuo_estimado_kg: float

    consumo_fisico_kg: Optional[float]

    consumo_teorico_kg: Optional[float]
    merma_kg: Optional[float]
    merma_pct: Optional[float]
    merma_no_explicada_kg: Optional[float]

    metadata_version: int
    metadata_refreshed_at: Optional[str]
    start_salon_snapshot_source: Optional[str]
    start_camera_snapshot_source: Optional[str]
    end_salon_snapshot_source: Optional[str]
    end_camera_snapshot_source: Optional[str]

    notes: str

    @classmethod
    def from_row(
        cls,
        row: Any,
    ) -> "Week":
        g = row.get

        notes = g("notes", "")
        try:
            if pd.isna(notes):
                notes = ""
        except Exception:
            pass

        return cls(
            week_id=str(g("week_id", "")),
            status=str(g("status", "")),
            started_at=str(g("started_at", "")),
            closed_at=_str(g("closed_at")),

            start_count_id=_str(g("start_count_id")),
            end_count_id=_str(g("end_count_id")),

            start_stock_kg=_float(g("start_stock_kg")),
            start_salon_latas=_int(g("start_salon_latas")),
            start_salon_kg=_float(g("start_salon_kg")),

            start_camera_latas=_int(g("start_camera_latas")),
            start_camera_kg=_float(g("start_camera_kg")),

            current_salon_latas=_int(g("current_salon_latas")),
            current_salon_kg=_float(g("current_salon_kg")),
            current_camera_latas=_int(g("current_camera_latas")),
            current_camera_kg=_float(g("current_camera_kg")),

            end_stock_kg=_float(g("end_stock_kg")),
            end_salon_latas=_int(g("end_salon_latas")),
            end_salon_kg=_float(g("end_salon_kg")),
            end_camera_latas=_int(g("end_camera_latas")),
            end_camera_kg=_float(g("end_camera_kg")),

            camera_to_salon_latas=_int(g("camera_to_salon_latas")) or 0,
            camera_to_salon_kg=_float(g("camera_to_salon_kg")) or 0.0,
            ingreso_camera_latas=_int(g("ingreso_camera_latas")) or 0,
            ingreso_camera_kg=_float(g("ingreso_camera_kg")) or 0.0,
            latas_abiertas=_int(g("latas_abiertas")) or 0,
            latas_terminadas=_int(g("latas_terminadas")) or 0,
            cambios_sabor=_int(g("cambios_sabor")) or 0,
            recambios=_int(g("recambios")) or 0,
            latas_con_tara_final=_int(
                g("latas_con_tara_final")
            ) or 0,

            tara_final_total_kg=_float(
                g("tara_final_total_kg")
            ) or 0.0,

            residuo_estimado_kg=_float(
                g("residuo_estimado_kg")
            ) or 0.0,

            consumo_fisico_kg=_float(g("consumo_fisico_kg")),

            consumo_teorico_kg=_float(g("consumo_teorico_kg")),
            merma_kg=_float(g("merma_kg")),
            merma_pct=_float(g("merma_pct")),
            merma_no_explicada_kg=_float(
                g("merma_no_explicada_kg")
            ),

            metadata_version=_int(g("metadata_version")) or WEEK_METADATA_VERSION,
            metadata_refreshed_at=_str(g("metadata_refreshed_at")),
            start_salon_snapshot_source=_str(
                g("start_salon_snapshot_source")
            ),
            start_camera_snapshot_source=_str(
                g("start_camera_snapshot_source")
            ),
            end_salon_snapshot_source=_str(
                g("end_salon_snapshot_source")
            ),
            end_camera_snapshot_source=_str(
                g("end_camera_snapshot_source")
            ),

            notes=str(notes),
        )

    def to_dict(self) -> dict:
        return {
            column:
                getattr(
                    self,
                    column,
                    None,
                )
            for column in WEEK_COLUMNS
        }

    @property
    def is_open(self) -> bool:
        return self.status.upper() == "OPEN"

    def elapsed_days(
        self,
        now_value=None,
    ) -> Optional[float]:
        start = pd.to_datetime(
            self.started_at,
            errors="coerce",
            utc=True,
        )

        if pd.isna(start):
            return None

        end_raw = (
            self.closed_at
            if self.closed_at
            else now_value
        )

        if end_raw is None:
            end = pd.Timestamp.now(
                tz="UTC"
            )
        else:
            end = pd.to_datetime(
                end_raw,
                errors="coerce",
                utc=True,
            )

        if pd.isna(end):
            return None

        return max(
            round(
                (
                    end - start
                ).total_seconds()
                / 86400,
                2,
            ),
            0.0,
        )
