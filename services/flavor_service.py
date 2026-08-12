from __future__ import annotations

import re

import pandas as pd


# Códigos operativos explícitos para los sabores conocidos.
# El objetivo es evitar colisiones semánticas:
# CHOCOLATE != CHOCOLATE_CON_ALMENDRAS, etc.
DEFAULT_FLAVOR_CODES = {
    "CAPUCCINO_GRANIZADO": "CAP",
    "ANANA": "ANA",
    "CHOCOLATE_MANI_CRUNCH": "CMC",
    "QUINOTO": "QUI",
    "CHOCOLATE_SUIZO": "CSU",
    "CHOCOLATE": "CHO",
    "CHOCOLATE_BLANCO": "CBL",
    "CHOCOLATE_CON_ALMENDRAS": "ALM",
    "GRIDO_MARROC": "MAR",
    "CREMA_RUSA": "CRU",
    "TIRAMISU": "TIR",
    "SUPER_GRIDITO": "SGI",
    "MENTA_GRANIZADO": "MEN",
    "CEREZA": "CER",
    "CHOCOLATE_BLANCO_OREO": "CBO",
    "DULCE_DE_LECHE": "DDL",
    "DULCE_DE_LECHE_GRANIZADO": "DLG",
    "DULCE_DE_LECHE_CON_NUEZ": "DLN",
    "SUPER_DULCE_DE_LECHE": "SDL",
    "MASCARPONE": "MAS",
    "DULCE_DE_LECHE_BROWNIE": "BRW",
    "CREMA_COOKIE": "CCK",
    "AMERICANA": "AME",
    "TRAMONTANA": "TRA",
    "GRANIZADO": "GRA",
    "BANANA": "BAN",
    "FRUTILLA_A_LA_CREMA": "FAC",
    "DURAZNO_A_LA_CREMA": "DAC",
}


def normalize_flavor_name(value):
    if pd.isna(value):
        return ""

    return (
        str(value)
        .strip()
        .upper()
        .replace(" ", "_")
    )


def normalize_flavor_code(value):
    if pd.isna(value):
        return ""

    return (
        str(value)
        .strip()
        .upper()
    )


def _sanitize_token(value):
    return re.sub(
        r"[^A-Z0-9]",
        "",
        value.upper(),
    )


def propose_flavor_code(
    sabor,
    used_codes=None,
):
    """
    Propone un código único para sabores históricos sin código.

    Los sabores conocidos usan DEFAULT_FLAVOR_CODES.
    Para sabores nuevos/desconocidos genera un candidato determinista
    y evita colisiones agregando dígitos.
    """

    flavor = normalize_flavor_name(
        sabor
    )

    used = {
        normalize_flavor_code(
            code
        )
        for code in (
            used_codes
            or []
        )
        if normalize_flavor_code(
            code
        )
    }

    curated = DEFAULT_FLAVOR_CODES.get(
        flavor
    )

    if curated and curated not in used:
        return curated

    words = [
        _sanitize_token(
            word
        )
        for word in flavor.split("_")
        if _sanitize_token(
            word
        )
    ]

    candidates = []

    if words:
        # Primera palabra, hasta 3 chars.
        candidates.append(
            words[0][
                :3
            ]
        )

        # Iniciales de hasta 4 palabras.
        initials = "".join(
            word[0]
            for word in words[
                :4
            ]
            if word
        )

        if initials:
            candidates.append(
                initials[
                    :4
                ]
            )

        # Primera + última palabra.
        if len(words) >= 2:
            candidates.append(
                (
                    words[0][
                        :2
                    ]
                    + words[-1][
                        :2
                    ]
                )[
                    :4
                ]
            )

    for candidate in candidates:
        candidate = (
            candidate
            .upper()
        )

        if (
            2 <= len(
                candidate
            ) <= 5
            and candidate not in used
        ):
            return candidate

    base = (
        candidates[0]
        if candidates
        else "FL"
    )

    base = (
        base[
            :3
        ]
        if len(
            base
        ) >= 2
        else "FL"
    )

    for number in range(
        1,
        100,
    ):
        suffix = str(
            number
        )

        candidate = (
            base[
                :(
                    5
                    - len(
                        suffix
                    )
                )
            ]
            + suffix
        )

        if candidate not in used:
            return candidate

    raise ValueError(
        f"No se pudo generar un código único para {flavor}."
    )


def ensure_flavor_codes(
    flavors_df,
):
    """
    Completa flavor_code sin reemplazar códigos existentes.
    Valida que el resultado sea único.
    """

    df = flavors_df.copy()

    if "flavor_code" not in df.columns:
        df[
            "flavor_code"
        ] = pd.NA

    used = set()

    # Primero reservamos códigos ya existentes.
    for value in df[
        "flavor_code"
    ].dropna():

        code = normalize_flavor_code(
            value
        )

        if not code:
            continue

        if code in used:
            raise ValueError(
                f"flavor_code duplicado: {code}"
            )

        used.add(
            code
        )

    for idx, row in df.iterrows():
        flavor = normalize_flavor_name(
            row.get(
                "sabor",
                ""
            )
        )

        existing = normalize_flavor_code(
            row.get(
                "flavor_code",
                ""
            )
        )

        if existing:
            df.loc[
                idx,
                "sabor"
            ] = flavor

            df.loc[
                idx,
                "flavor_code"
            ] = existing

            continue

        code = propose_flavor_code(
            flavor,
            used_codes=used,
        )

        used.add(
            code
        )

        df.loc[
            idx,
            "sabor"
        ] = flavor

        df.loc[
            idx,
            "flavor_code"
        ] = code

    return df
