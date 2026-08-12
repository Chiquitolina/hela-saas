from dataclasses import dataclass, asdict
import re


FLAVOR_CODE_PATTERN = re.compile(
    r"^[A-Z0-9]{2,5}$"
)


@dataclass
class Flavor:
    sabor: str
    flavor_code: str
    active: bool
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        sabor: str,
        flavor_code: str,
        timestamp: str,
    ) -> "Flavor":

        sabor = (
            str(sabor)
            .strip()
            .upper()
            .replace(" ", "_")
        )

        flavor_code = (
            str(flavor_code)
            .strip()
            .upper()
        )

        if not sabor:
            raise ValueError(
                "El nombre del sabor no puede estar vacío."
            )

        if not FLAVOR_CODE_PATTERN.fullmatch(
            flavor_code
        ):
            raise ValueError(
                "El código debe tener entre 2 y 5 caracteres "
                "alfanuméricos en mayúscula. Ej: CHO, ALM, DDL, BRW."
            )

        return cls(
            sabor=sabor,
            flavor_code=flavor_code,
            active=True,
            created_at=timestamp,
        )

    def to_row(self):
        return asdict(self)
