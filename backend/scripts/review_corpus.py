"""Corpus de frases en español para generar reseñas sintéticas.

Cada frase está asociada a un aspecto y a una polaridad. El generador del
seed las combina para producir reseñas que mencionan explícitamente los
cuatro aspectos del módulo ABSA (jugabilidad, gráficos, historia y
optimización), de modo que el análisis tenga señal real que detectar.

Ampliar este corpus mejora la variedad léxica del dataset sin tocar el
script de seed.
"""

from app.models.enums import Aspect, Sentiment

# ---------------------------------------------------------------------------
# Frases por aspecto y polaridad
# ---------------------------------------------------------------------------

ASPECT_PHRASES: dict[Aspect, dict[Sentiment, list[str]]] = {
    Aspect.GAMEPLAY: {
        Sentiment.POSITIVE: [
            "El sistema de combate es preciso y responde perfecto, cada movimiento se siente exactamente como uno espera.",
            "La jugabilidad es adictiva: el bucle de juego engancha desde el primer minuto y no te suelta más.",
            "Los controles son impecables, nunca sentí que una muerte fuera culpa del juego.",
            "Las mecánicas están muy bien pensadas y se van complejizando a un ritmo ideal.",
            "Se nota el pulido en el gameplay, es fluido y satisfactorio de principio a fin.",
            "La curva de dificultad está perfectamente balanceada, siempre exigente pero justa.",
            "Cada arma y cada habilidad aporta algo distinto, la variedad mecánica es enorme.",
            "El movimiento es una delicia, da gusto simplemente desplazarse por el mapa.",
        ],
        Sentiment.NEUTRAL: [
            "La jugabilidad cumple, no inventa nada pero tampoco molesta.",
            "El gameplay es correcto aunque le falta algo de profundidad para destacar.",
            "Las mecánicas funcionan bien, si bien son las mismas de siempre en el género.",
            "El combate entretiene al principio y después se vuelve algo rutinario.",
            "Los controles se sienten decentes una vez que te acostumbrás.",
            "La jugabilidad está bien resuelta, sin grandes sorpresas.",
        ],
        Sentiment.NEGATIVE: [
            "Los controles son torpes y poco responsivos, pelearse con el mando arruina la experiencia.",
            "La jugabilidad se vuelve repetitiva enseguida, terminás haciendo lo mismo durante horas.",
            "Las mecánicas están mal explicadas y el tutorial no ayuda en absoluto.",
            "El combate es plano y sin peso, los enemigos ni siquiera reaccionan a los golpes.",
            "La dificultad está pésimamente balanceada, hay picos injustos sin ninguna lógica.",
            "El diseño de misiones es perezoso: ir del punto A al punto B una y otra vez.",
            "La cámara juega en contra en cada pelea en espacios cerrados.",
            "El sistema de progresión es tedioso y te obliga a farmear sin sentido.",
        ],
    },
    Aspect.GRAPHICS: {
        Sentiment.POSITIVE: [
            "Visualmente es una obra de arte, hay planos que parecen cuadros.",
            "La dirección artística es deslumbrante y envejece mucho mejor que el realismo puro.",
            "Los gráficos son espectaculares, el nivel de detalle en los escenarios es impresionante.",
            "La iluminación y los efectos de partículas están a un nivel altísimo.",
            "El apartado visual es una maravilla, me la pasé sacando capturas.",
            "Las animaciones son fluidas y naturales, se nota el trabajo que hay detrás.",
            "El diseño de personajes y escenarios tiene una personalidad tremenda.",
        ],
        Sentiment.NEUTRAL: [
            "Gráficamente está bien, sin destacar ni molestar.",
            "El apartado visual es correcto para lo que propone el juego.",
            "Los gráficos son sencillos pero acompañan bien la propuesta.",
            "Visualmente cumple, aunque no es lo que más se le va a recordar.",
            "La estética es simple y funciona, no busca impresionar a nadie.",
        ],
        Sentiment.NEGATIVE: [
            "Los gráficos están claramente desactualizados para el año en que salió.",
            "Las texturas tardan en cargar y muchas se ven borrosas de cerca.",
            "El apartado visual es pobre, los escenarios se sienten vacíos y sin vida.",
            "Las animaciones faciales son acartonadas y rompen toda la inmersión.",
            "El diseño artístico es genérico, todo se ve igual después de un rato.",
            "Hay popping constante de objetos a pocos metros de la cámara.",
        ],
    },
    Aspect.STORY: {
        Sentiment.POSITIVE: [
            "La historia me atrapó de principio a fin, hacía años que un guion no me hacía esto.",
            "Los personajes están escritos con una profundidad admirable, todos tienen motivaciones creíbles.",
            "El guion es magnífico y el final me dejó pensando durante días.",
            "La narrativa está muy bien dosificada, cada revelación llega en el momento justo.",
            "Las misiones secundarias tienen tanto peso narrativo como la trama principal.",
            "El desarrollo emocional de los protagonistas es de lo mejor que jugué.",
            "La construcción del mundo es riquísima, cada detalle aporta al relato.",
        ],
        Sentiment.NEUTRAL: [
            "La historia es correcta y cumple su función de sostener el juego.",
            "El guion está bien, aunque no es el punto fuerte del título.",
            "La trama es sencilla pero acompaña sin estorbar.",
            "La narrativa es aceptable, ni memorable ni mala.",
        ],
        Sentiment.NEGATIVE: [
            "La historia es predecible y los giros se ven venir a kilómetros.",
            "El guion es flojísimo y los diálogos suenan completamente artificiales.",
            "Los personajes son planos y no logré empatizar con ninguno.",
            "La trama arranca bien pero se desinfla por completo en el último tramo.",
            "El final es apresurado y deja demasiados cabos sueltos sin resolver.",
            "La narrativa es una excusa y ni siquiera se molesta en disimularlo.",
        ],
    },
    Aspect.PERFORMANCE: {
        Sentiment.POSITIVE: [
            "Corre impecable, ni un solo tirón en toda la partida.",
            "La optimización es excelente, se mantiene en sesenta cuadros estables hasta en las escenas más cargadas.",
            "No encontré un solo bug en toda la campaña, un nivel de pulido que ya casi no se ve.",
            "Los tiempos de carga son casi inexistentes y el juego es sorprendentemente liviano.",
            "Funciona perfecto incluso en equipos modestos, se nota el trabajo técnico.",
            "Rendimiento sólido y sin un solo crasheo en decenas de horas.",
        ],
        Sentiment.NEUTRAL: [
            "El rendimiento es aceptable, con alguna caída puntual en las zonas más cargadas.",
            "Tuve un par de bugs menores pero nada que rompiera la partida.",
            "Corre bien la mayor parte del tiempo, con algún tirón aislado.",
            "Técnicamente está correcto, sin brillar ni preocupar.",
        ],
        Sentiment.NEGATIVE: [
            "El rendimiento es un desastre, los tirones son constantes incluso bajando toda la configuración.",
            "Crashea cada media hora y perdí progreso más de una vez.",
            "Está plagado de bugs: enemigos que atraviesan paredes, misiones que no se completan y texturas que nunca cargan.",
            "Los tiempos de carga son eternos y hay stuttering permanente al recorrer el mapa.",
            "Lanzarlo en este estado técnico fue una falta de respeto al jugador.",
            "Consume muchísimos recursos para lo poco que muestra en pantalla.",
            "Los parches mejoraron algo, pero sigue lejos de funcionar como debería.",
        ],
    },
}

# ---------------------------------------------------------------------------
# Aperturas y cierres según la polaridad global de la reseña
# ---------------------------------------------------------------------------

OPENINGS: dict[Sentiment, list[str]] = {
    Sentiment.POSITIVE: [
        "Uno de los mejores juegos que jugué en años.",
        "Le metí muchísimas horas y no me arrepiento de ninguna.",
        "Superó por completo mis expectativas.",
        "Llegué con dudas y terminé enganchadísimo.",
        "Difícil encontrarle un pero a este juego.",
    ],
    Sentiment.NEUTRAL: [
        "Es un juego con luces y sombras.",
        "Le tengo cariño, pero hay que decir las cosas como son.",
        "Ni tan bueno como dicen ni tan malo como lo pintan.",
        "Lo terminé y me dejó sensaciones encontradas.",
    ],
    Sentiment.NEGATIVE: [
        "Esperaba muchísimo más de este título.",
        "Una decepción enorme considerando todo lo que prometían.",
        "Me costó terminarlo y no se lo recomendaría a nadie.",
        "Lo dejé a mitad de camino y no pienso volver.",
    ],
}

CLOSINGS: dict[Sentiment, list[str]] = {
    Sentiment.POSITIVE: [
        "Totalmente recomendado.",
        "Si te gusta el género, es una compra obligada.",
        "Vale cada peso que cuesta.",
        "Lo volvería a jugar sin dudarlo.",
    ],
    Sentiment.NEUTRAL: [
        "Recomendable en oferta.",
        "Depende mucho de qué estés buscando.",
        "Le doy el beneficio de la duda para lo que viene.",
    ],
    Sentiment.NEGATIVE: [
        "No lo recomiendo al precio actual.",
        "Esperá a que lo arreglen y esté en descuento.",
        "Difícil de recomendar en el estado en el que está.",
    ],
}

TITLES: dict[Sentiment, list[str]] = {
    Sentiment.POSITIVE: [
        "Una joya absoluta",
        "Obra maestra, sin exagerar",
        "Cumple todo lo que promete",
        "De lo mejor que jugué",
        "Vale muchísimo la pena",
        "No lo pude soltar",
    ],
    Sentiment.NEUTRAL: [
        "Bueno, pero con peros",
        "Le falta un poco para ser grande",
        "Sensaciones encontradas",
        "Correcto sin más",
        "Depende de lo que busques",
    ],
    Sentiment.NEGATIVE: [
        "Una decepción",
        "Salió antes de tiempo",
        "No lo recomiendo",
        "Mucho ruido y pocas nueces",
        "Esperaba otra cosa",
    ],
}
