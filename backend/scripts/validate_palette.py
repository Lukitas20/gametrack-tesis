#!/usr/bin/env python
"""Valida la paleta de los gráficos del dashboard.

Comprueba dos cosas que no deben quedar a criterio visual:

1. **Contraste WCAG** de cada relleno contra la superficie sobre la que se
   dibuja, en modo claro y oscuro.
2. **Separación perceptual** entre pares de rellenos, medida como distancia
   euclídea en OKLab (×100), tanto con visión normal como simulando las tres
   dicromacias (protanopia, deuteranopia, tritanopia) con el modelo de
   Viénot, Brettel & Mollon (1999).

Uso:
    python scripts/validate_palette.py
"""

from __future__ import annotations

import sys

# Umbrales del método de visualización que seguimos.
CVD_TARGET = 8.0  # ΔE OKLab ×100 entre pares adyacentes
NORMAL_FLOOR = 15.0  # piso para visión normal
FILL_CONTRAST_FLOOR = 2.0  # un relleno debe despegarse de la superficie

Rgb = tuple[float, float, float]

# --- Paleta usada por los gráficos ----------------------------------------
# Escala divergente para el sentimiento: azul ↔ rojo con gris neutro en el
# medio. Se descarta verde↔rojo, que es la peor combinación posible para las
# dicromacias más frecuentes.
#
# El gris del modo oscuro es más oscuro que el del modo claro, y no es una
# elección estética: bajo protanopia el rojo se desatura hacia el gris, así que
# lo único que separa los dos rellenos es la luminosidad. Con el mismo gris en
# ambos modos, el par neutro/negativo caía a ΔE 4.2 en oscuro. Este valor lo
# lleva a 11.7 conservando 2,6:1 de contraste contra la superficie.
PALETTES: dict[str, dict[str, str]] = {
    "claro": {
        "_surface": "#fcfcfb",
        "positivo": "#2a78d6",
        "neutro": "#898781",
        "negativo": "#e34948",
    },
    "oscuro": {
        "_surface": "#1a1a19",
        "positivo": "#3987e5",
        "neutro": "#5c5c54",
        "negativo": "#e66767",
    },
}


def hex_to_rgb(value: str) -> Rgb:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def to_srgb(channel: float) -> float:
    channel = max(0.0, min(1.0, channel))
    return channel * 12.92 if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055


def linearize(rgb: Rgb) -> Rgb:
    return tuple(to_linear(c) for c in rgb)  # type: ignore[return-value]


def relative_luminance(rgb: Rgb) -> float:
    r, g, b = linearize(rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first: str, second: str) -> float:
    a = relative_luminance(hex_to_rgb(first))
    b = relative_luminance(hex_to_rgb(second))
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def to_oklab(rgb: Rgb) -> tuple[float, float, float]:
    r, g, b = linearize(rgb)
    long_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    medium = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    short = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b

    l_, m_, s_ = (value ** (1 / 3) if value > 0 else -((-value) ** (1 / 3)) for value in (long_, medium, short))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def delta_e(first: Rgb, second: Rgb) -> float:
    a = to_oklab(first)
    b = to_oklab(second)
    return 100 * sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


# --- Simulación de dicromacias (Viénot, Brettel & Mollon 1999) -------------

_RGB_TO_LMS = (
    (17.8824, 43.5161, 4.11935),
    (3.45565, 27.1554, 3.86714),
    (0.0299566, 0.184309, 1.46709),
)
_LMS_TO_RGB = (
    (0.0809444479, -0.130504409, 0.116721066),
    (-0.0102485335, 0.0540193266, -0.113614708),
    (-0.000365296938, -0.00412161469, 0.693511405),
)

_DICHROMAT = {
    "protanopia": ((0.0, 2.02344, -2.52581), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "deuteranopia": ((1.0, 0.0, 0.0), (0.494207, 0.0, 1.24827), (0.0, 0.0, 1.0)),
    "tritanopia": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-0.395913, 0.801109, 0.0)),
}


def _apply(matrix: tuple, vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(sum(m * v for m, v in zip(row, vector)) for row in matrix)  # type: ignore[return-value]


def simulate(rgb: Rgb, kind: str) -> Rgb:
    """Simula cómo percibe un color una persona con la dicromacia indicada."""
    linear = linearize(rgb)
    lms = _apply(_RGB_TO_LMS, linear)
    projected = _apply(_DICHROMAT[kind], lms)
    linear_out = _apply(_LMS_TO_RGB, projected)
    return tuple(to_srgb(c) for c in linear_out)  # type: ignore[return-value]


# --- Informe ---------------------------------------------------------------


def main() -> int:
    failures: list[str] = []

    for mode, palette in PALETTES.items():
        surface = palette["_surface"]
        fills = {name: value for name, value in palette.items() if not name.startswith("_")}

        print("=" * 68)
        print(f"MODO {mode.upper()}  ·  superficie {surface}")
        print("=" * 68)

        print("\nContraste de cada relleno contra la superficie:")
        for name, value in fills.items():
            ratio = contrast_ratio(value, surface)
            ok = ratio >= FILL_CONTRAST_FLOOR
            print(f"  {name:<10} {value}  {ratio:5.2f}:1  {'OK' if ok else 'BAJO'}")
            if not ok:
                failures.append(f"[{mode}] {name} contrasta {ratio:.2f}:1 con la superficie")

        names = list(fills)
        pairs = [(names[i], names[j]) for i in range(len(names)) for j in range(i + 1, len(names))]

        print("\nSeparación entre pares (ΔE OKLab ×100):")
        header = f"  {'par':<22}{'normal':>9}" + "".join(f"{k[:6]:>9}" for k in _DICHROMAT)
        print(header)
        for first, second in pairs:
            a, b = hex_to_rgb(fills[first]), hex_to_rgb(fills[second])
            normal = delta_e(a, b)
            cvd = {kind: delta_e(simulate(a, kind), simulate(b, kind)) for kind in _DICHROMAT}
            row = f"  {first + '/' + second:<22}{normal:>9.1f}" + "".join(
                f"{value:>9.1f}" for value in cvd.values()
            )
            print(row)

            if normal < NORMAL_FLOOR:
                failures.append(
                    f"[{mode}] {first}/{second}: visión normal ΔE {normal:.1f} < {NORMAL_FLOOR}"
                )
            worst_kind, worst = min(cvd.items(), key=lambda item: item[1])
            if worst < CVD_TARGET:
                failures.append(
                    f"[{mode}] {first}/{second}: {worst_kind} ΔE {worst:.1f} < {CVD_TARGET}"
                )
        print()

    print("=" * 68)
    if failures:
        print(f"FALLAS ({len(failures)}):")
        for failure in failures:
            print(f"  · {failure}")
        return 1
    print("La paleta pasa todos los umbrales en ambos modos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
