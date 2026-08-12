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


@dataclass
class CameraStock:
    """
    Stock agregado de cámara.

    Una fila NO representa una lata física individual:
    representa un lote/saldo de un sabor almacenado en cámara.
    Las latas adquieren identidad individual recién cuando pasan al salón.
    """

    camera_stock_id: str
    sabor: str
    cantidad_latas: int
    kg_referencia_lata: Optional[float]
    created_at: str
    updated_at: str
    active: bool

    @classmethod
    def create(
        cls,
        *,
        camera_stock_id: str,
        sabor: str,
        cantidad_latas: int,
        kg_referencia_lata: float,
        timestamp: str,
    ) -> "CameraStock":
        cantidad = int(
            cantidad_latas
        )

        if cantidad <= 0:
            raise ValueError(
                "La cantidad de latas debe ser mayor a cero."
            )

        return cls(
            camera_stock_id=camera_stock_id,
            sabor=str(sabor),
            cantidad_latas=cantidad,
            kg_referencia_lata=round(
                float(
                    kg_referencia_lata
                ),
                3,
            ),
            created_at=timestamp,
            updated_at=timestamp,
            active=True,
        )

    @classmethod
    def from_row(
        cls,
        row: Any,
    ) -> "CameraStock":
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
            cantidad_latas=int(
                float(
                    g(
                        "cantidad_latas",
                        0,
                    )
                    or 0
                )
            ),
            kg_referencia_lata=_optional_float(
                g(
                    "kg_referencia_lata",
                    None,
                )
            ),
            created_at=str(
                g(
                    "created_at",
                    "",
                )
            ),
            updated_at=str(
                g(
                    "updated_at",
                    "",
                )
            ),
            active=str(
                g(
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
            },
        )

    def to_row(self) -> dict:
        return asdict(self)
