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

    if not text or text.lower() in {
        "nan",
        "none",
        "<na>",
    }:
        return None

    return text


@dataclass
class CameraLata:
    """
    Una fila = una lata física individual en cámara.

    La lata mantiene identidad propia desde que ingresa a cámara.
    Al pasar al salón NO se elimina: queda histórica/inactiva y se
    vincula con la nueva SAL-xxxxxx mediante target_salon_stock_id.
    """

    camera_stock_id: str
    sabor: str
    estado: str
    kg_referencia_lata: Optional[float]

    ingresada_camera_at: str
    moved_to_salon_at: Optional[str]
    target_salon_stock_id: Optional[str]

    # Si la fila nació al expandir un viejo lote agregado,
    # conservamos el ID de ese lote solo como auditoría.
    legacy_batch_id: Optional[str]

    created_at: str
    updated_at: str
    active: bool

    @classmethod
    def create(
        cls,
        *,
        camera_stock_id: str,
        sabor: str,
        kg_referencia_lata: float,
        timestamp: str,
        legacy_batch_id: Optional[str] = None,
    ) -> "CameraLata":

        reference = round(
            float(
                kg_referencia_lata
            ),
            3,
        )

        if reference <= 0:
            raise ValueError(
                "El peso de referencia debe ser mayor a cero."
            )

        return cls(
            camera_stock_id=str(
                camera_stock_id
            ),
            sabor=str(
                sabor
            ),
            estado="DISPONIBLE",
            kg_referencia_lata=reference,
            ingresada_camera_at=timestamp,
            moved_to_salon_at=None,
            target_salon_stock_id=None,
            legacy_batch_id=_optional_str(
                legacy_batch_id
            ),
            created_at=timestamp,
            updated_at=timestamp,
            active=True,
        )

    @classmethod
    def from_row(
        cls,
        row: Any,
    ) -> "CameraLata":

        g = row.get

        stock_id = (
            g(
                "camera_stock_id",
                None,
            )
            or g(
                "stock_id",
                "",
            )
        )

        created_at = _optional_str(
            g(
                "created_at",
                None,
            )
        ) or ""

        ingresada_camera_at = _optional_str(
            g(
                "ingresada_camera_at",
                None,
            )
        ) or created_at

        active_raw = g(
            "active",
            True,
        )

        active = (
            str(
                active_raw
            )
            .strip()
            .lower()
            in {
                "true",
                "1",
                "yes",
            }
        )

        estado = _optional_str(
            g(
                "estado",
                None,
            )
        )

        if not estado:
            estado = (
                "DISPONIBLE"
                if active
                else "MOVIDA_SALON"
            )

        return cls(
            camera_stock_id=str(
                stock_id
            ),
            sabor=str(
                g(
                    "sabor",
                    "",
                )
            ),
            estado=estado,
            kg_referencia_lata=_optional_float(
                g(
                    "kg_referencia_lata",
                    None,
                )
            ),
            ingresada_camera_at=ingresada_camera_at,
            moved_to_salon_at=_optional_str(
                g(
                    "moved_to_salon_at",
                    None,
                )
            ),
            target_salon_stock_id=_optional_str(
                g(
                    "target_salon_stock_id",
                    None,
                )
            ),
            legacy_batch_id=_optional_str(
                g(
                    "legacy_batch_id",
                    None,
                )
            ),
            created_at=created_at,
            updated_at=_optional_str(
                g(
                    "updated_at",
                    None,
                )
            ) or created_at,
            active=active,
        )

    def to_row(self) -> dict:
        return asdict(self)

    def moved_updates(
        self,
        *,
        timestamp: str,
        target_salon_stock_id: str,
    ) -> dict:

        if not self.active:
            raise ValueError(
                "La lata de cámara ya no está disponible."
            )

        return {
            "estado":
                "MOVIDA_SALON",

            "moved_to_salon_at":
                timestamp,

            "target_salon_stock_id":
                target_salon_stock_id,

            "updated_at":
                timestamp,

            "active":
                False,
        }

    def annul_updates(
        self,
        *,
        timestamp: str,
    ) -> dict:
        """
        Baja lógica de una lata cargada por error en cámara.

        No se borra físicamente:
        - estado = ANULADA
        - active = False

        La trazabilidad queda preservada en camera_stock.csv
        y mediante un movimiento ANULACION_CAMARA.
        """

        if not self.active:
            raise ValueError(
                "La lata de cámara ya no está activa."
            )

        if self.estado.upper() != "DISPONIBLE":
            raise ValueError(
                "Solo se pueden anular latas DISPONIBLES."
            )

        return {
            "estado":
                "ANULADA",

            "updated_at":
                timestamp,

            "active":
                False,
        }

