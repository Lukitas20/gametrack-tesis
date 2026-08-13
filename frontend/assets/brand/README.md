# Assets de marca

Dos archivos, ninguno de los dos en el repo todavía:

| Archivo | Qué es | Estado |
|---|---|---|
| `logo.png` | el caldero (violeta / verde agua / oro) | **falta** |
| `mascota.png` | el maguito encapuchado de ojos turquesa | **falta** |

## Por qué faltan

Los PNG que se pasaron (`Logo.png` y `Maguito.png`) **no tienen canal alfa**:
son `mode=RGB`, con el damero de transparencia horneado como píxeles. Sobre el
violeta de la aplicación se verían como un rectángulo a cuadritos en vez de
recortados. Comprobado así:

```bash
python -c "from PIL import Image; im=Image.open('Maguito.png'); print(im.mode)"
# RGB   <- sin la A, no hay transparencia
```

No se recortó el fondo a mano a propósito: los bordes están suavizados contra
el damero, así que cualquier recorte deja un halo gris alrededor de la silueta,
que sobre fondo violeta se nota más que el damero mismo. La solución es
re-exportar desde el original con transparencia real (PNG-32 / RGBA).

## Qué pasa mientras tanto

Nada se rompe. `mascot()` (en `js/ui.js`) se saca sola del DOM si la imagen no
carga, así que la pantalla de carga muestra el círculo rúnico —exactamente lo
que se veía antes— en vez del ícono de imagen rota. El logo del header es un
caldero dibujado en SVG inline, que además es el favicon.

Cuando lleguen los archivos con alfa, ponerlos acá con estos nombres alcanza:
la mascota aparece sola en las cargas de pantalla completa, y reemplazar el
SVG del header por `logo.png` es cambiar un elemento en `index.html`.
