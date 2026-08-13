# Assets de marca

| Archivo | Qué es | Dónde se usa |
|---|---|---|
| `logo.png` | el caldero; el humo entrelazado **es la G y la T** | logo del header (36px) |
| `mascota.png` | el maguito encapuchado de ojos turquesa | pantallas de carga; a futuro, avatar del chatbot |
| `favicon-32.png` | el logo a 32px | pestaña del navegador |
| `apple-touch-icon.png` | el logo a 180px sobre violeta | pantalla de inicio en iOS |

Los dos primeros son 512×512 RGBA, recortados al contenido y centrados en un
cuadrado. Se renderizan a través de `mascot()` y de `.brand-mark`, nunca con
rutas sueltas repetidas por el código.

## De dónde salieron

Los originales (`Logo.png` y `Maguito.png`) llegaron **sin canal alfa**:
`mode=RGB`, con el damero de transparencia pintado como píxeles. Sobre el
violeta de la aplicación se veían como un rectángulo a cuadritos.

La transparencia se reconstruyó en vez de recortar a mano. El arte es oscuro y
saturado y el fondo es gris claro desaturado, así que se separan por saturación
y luminancia; el fondo se toma sólo donde está **conectado al borde** de la
imagen, para no perforar zonas claras interiores. Los píxeles del borde, que
están mezclados con el gris del damero, se des-premultiplican
(`F = (C - (1-a)·B) / a`) en lugar de dejarlos como están: por eso no queda el
halo gris que deja un recorte directo. Verificado componiendo sobre violeta,
sobre blanco y sobre negro, con zoom ×4 en los bordes.

Quedó 72% transparente el logo y 63% la mascota, con menos del 0,2% de píxeles
de borde suave — consistente con arte de color plano y contorno marcado.

Si en algún momento aparecen los originales con alfa real (exportados en
PNG-32 desde la fuente), conviene reemplazarlos: la reconstrucción es muy
buena, pero parte de una imagen que ya perdió información.
