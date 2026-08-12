from dataclasses import dataclass, asdict
from typing import Any, Optional


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None

    try:
        if value != value:
            return None
    except Exception:
        pass

    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()

    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None

    return text


@dataclass
class Lata:
    """
    Modelo de una lata individual del salón.

    El stock de cámara continúa siendo agregado por sabor/cantidad.
    Cuando una unidad sale de cámara y entra al salón, nace una Lata
    individual con ciclo de vida y metadata propia.
    """

    stock_id: str
    location: str
    sabor: str
    estado: str
    cantidad_latas: int
    kg_referencia_lata: Optional[float]

    source_camera_stock_id: Optional[str]
    ingresada_salon_at: Optional[str]

    peso_inicial_bruto_kg: Optional[float]
    tara_inicial_kg: Optional[float]
    peso_inicial_neto_kg: Optional[float]

    opened_at: Optional[str]
    opened_operation_id: Optional[str]

    peso_actual_bruto_kg: Optional[float]
    tara_actual_kg: Optional[float]
    peso_actual_neto_kg: Optional[float]

    finished_at: Optional[str]
    peso_final_bruto_kg: Optional[float]
    tara_final_kg: Optional[float]
    residuo_final_kg: Optional[float]
    finished_operation_id: Optional[str]

    created_at: str
    updated_at: str
    active: bool

    @classmethod
    def create_salon(
        cls,
        *,
        stock_id: str,
        sabor: str,
        estado: str,
        timestamp: str,
        peso_bruto_kg: float,
        tara_kg: float,
        peso_neto_kg: float,
        kg_referencia_lata: Optional[float] = None,
        source_camera_stock_id: Optional[str] = None,
        ingresada_salon_at: Optional[str] = None,
        opened_at: Optional[str] = None,
        opened_operation_id: Optional[str] = None,
    ) -> "Lata":

        return cls(
            stock_id=stock_id,
            location="SALON",
            sabor=sabor,
            estado=estado,
            cantidad_latas=1,
            kg_referencia_lata=_optional_float(
                kg_referencia_lata
            ),
            source_camera_stock_id=_optional_str(
                source_camera_stock_id
            ),
            ingresada_salon_at=_optional_str(
                ingresada_salon_at
            ),
            peso_inicial_bruto_kg=round(
                float(peso_bruto_kg),
                3,
            ),
            tara_inicial_kg=round(
                float(tara_kg),
                3,
            ),
            peso_inicial_neto_kg=round(
                float(peso_neto_kg),
                3,
            ),
            opened_at=_optional_str(
                opened_at
            ),
            opened_operation_id=_optional_str(
                opened_operation_id
            ),
            peso_actual_bruto_kg=round(
                float(peso_bruto_kg),
                3,
            ),
            tara_actual_kg=round(
                float(tara_kg),
                3,
            ),
            peso_actual_neto_kg=round(
                float(peso_neto_kg),
                3,
            ),
            finished_at=None,
            peso_final_bruto_kg=None,
            tara_final_kg=None,
            residuo_final_kg=None,
            finished_operation_id=None,
            created_at=timestamp,
            updated_at=timestamp,
            active=True,
        )

    @classmethod
    def from_row(
        cls,
        row: Any,
    ) -> "Lata":

        getter = (
            row.get
            if hasattr(row, "get")
            else lambda key, default=None: default
        )

        return cls(
            stock_id=str(
                getter("stock_id", "")
            ),
            location=str(
                getter("location", "")
            ),
            sabor=str(
                getter("sabor", "")
            ),
            estado=str(
                getter("estado", "")
            ),
            cantidad_latas=int(
                _optional_float(
                    getter("cantidad_latas", 1)
                )
                or 1
            ),
            kg_referencia_lata=_optional_float(
                getter("kg_referencia_lata")
            ),
            source_camera_stock_id=_optional_str(
                getter("source_camera_stock_id")
            ),
            ingresada_salon_at=_optional_str(
                getter("ingresada_salon_at")
            ),
            peso_inicial_bruto_kg=_optional_float(
                getter("peso_inicial_bruto_kg")
            ),
            tara_inicial_kg=_optional_float(
                getter("tara_inicial_kg")
            ),
            peso_inicial_neto_kg=_optional_float(
                getter("peso_inicial_neto_kg")
            ),
            opened_at=_optional_str(
                getter("opened_at")
            ),
            opened_operation_id=_optional_str(
                getter("opened_operation_id")
            ),
            peso_actual_bruto_kg=_optional_float(
                getter("peso_actual_bruto_kg")
            ),
            tara_actual_kg=_optional_float(
                getter("tara_actual_kg")
            ),
            peso_actual_neto_kg=_optional_float(
                getter("peso_actual_neto_kg")
            ),
            finished_at=_optional_str(
                getter("finished_at")
            ),
            peso_final_bruto_kg=_optional_float(
                getter("peso_final_bruto_kg")
            ),
            tara_final_kg=_optional_float(
                getter("tara_final_kg")
            ),
            residuo_final_kg=_optional_float(
                getter("residuo_final_kg")
            ),
            finished_operation_id=_optional_str(
                getter("finished_operation_id")
            ),
            created_at=str(
                getter("created_at", "")
            ),
            updated_at=str(
                getter("updated_at", "")
            ),
            active=bool(
                getter("active", True)
            ),
        )

    def to_stock_row(self) -> dict:
        return asdict(self)

    def opening_updates(
        self,
        *,
        timestamp: str,
        operation_id: str,
    ) -> dict:

        if self.estado.upper() != "CERRADA":
            raise ValueError(
                "Solo se pueden abrir latas CERRADAS."
            )

        if not self.active:
            raise ValueError(
                "La lata ya no está activa."
            )

        return {
            "estado":
                "ABIERTA",

            "opened_at":
                timestamp,

            "opened_operation_id":
                operation_id,

            "updated_at":
                timestamp,
        }

    def finalization_updates(
        self,
        *,
        timestamp: str,
        operation_id: str,
        tara_final_kg: float,
        peso_final_bruto_kg: Optional[float] = None,
        max_tare_kg: float = 2.0,
        max_gross_kg: float = 20.0,
    ) -> dict:
        """
        Finaliza una lata con un único pesaje final.

        Conceptos:
        - tara_inicial_kg:
            estimación operativa del envase usada mientras la lata está activa.
        - tara_final_kg:
            peso real de la lata terminada.
        - residuo_final_kg:
            estimación del helado que quedó y ya no pudo servirse:

                max(tara_final_kg - tara_inicial_kg, 0)

        IMPORTANTE:
        la tara final NO recalcula retrospectivamente el neto inicial,
        NO modifica el stock inicial histórico y NO corrige conteos previos.
        """

        if self.estado.upper() != "ABIERTA":
            raise ValueError(
                "Solo se pueden finalizar latas ABIERTAS."
            )

        if not self.active:
            raise ValueError(
                "La lata ya no está activa."
            )

        tara_final = round(
            float(tara_final_kg),
            3,
        )

        if tara_final <= 0:
            raise ValueError(
                "La tara final debe ser mayor a cero."
            )

        if tara_final > max_gross_kg:
            raise ValueError(
                f"La tara final no puede superar "
                f"{max_gross_kg:.3f} kg."
            )

        tara_inicial = _optional_float(
            self.tara_inicial_kg
        )

        if tara_inicial is None:
            tara_inicial = _optional_float(
                self.tara_actual_kg
            )

        residuo_estimado = None

        if tara_inicial is not None:
            residuo_estimado = round(
                max(
                    tara_final
                    - tara_inicial,
                    0.0,
                ),
                3,
            )

        return {
            "estado":
                "AGOTADA",

            "finished_at":
                timestamp,

            # Compatibilidad histórica: conservamos ambos campos.
            "peso_final_bruto_kg":
                tara_final,

            "tara_final_kg":
                tara_final,

            "residuo_final_kg":
                residuo_estimado,

            "finished_operation_id":
                operation_id,

            # La lata agotada ya no aporta helado vendible.
            "peso_actual_bruto_kg":
                tara_final,

            "tara_actual_kg":
                tara_final,

            "peso_actual_neto_kg":
                0.0,

            "updated_at":
                timestamp,

            "active":
                False,
        }

