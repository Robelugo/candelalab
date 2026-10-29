# CandelaLab — Arquitectura

## 1. Comparación de alternativas

| Criterio | A. Python + API | B. Geometry Nodes | C. Híbrida (núcleo Python + GN) |
|---|---|---|---|
| Exactitud matemática | Doble precisión, ecuaciones explícitas, verificable con pruebas | `float32`, sin acceso simbólico; la ecuación queda dispersa en nodos | Igual que A (la geometría la decide Python) |
| Parámetros en tiempo real | Callbacks `update=`; ~30 ms a 32×32 | Excelente (nativo) | Igual que A; GN aporta instanciación instantánea |
| Rendimiento con complejidad | Python puro se vuelve lento > ~100k vértices | Muy bueno | Python para la definición, GN para multiplicar módulos |
| Interfaz | Paneles, listas, operadores, validación | Sólo entradas del modificador | Completa (Python) |
| Addon profesional | Sí (extensiones 4.2+) | Difícil de versionar y probar | Sí |
| Exportar a FEA | Directo: nodos, elementos, conjuntos de borde, apoyos | Hay que leer la malla evaluada; se pierde la semántica | Directo |
| Cimbras y documentación | Lógica, cuantificación, informes | Sólo geometría | Lógica en Python, visualización en GN |
| Mantenimiento | Código diferenciable, probado fuera de Blender | Árboles de nodos frágiles entre versiones de la API | Bueno si GN se limita a la visualización |

## 2. Decisión: C, con el núcleo en Python como única fuente de verdad

* **`core/`**: Python puro, sin `bpy`. Ecuaciones, mallado, métricas, comprobaciones y exportación.
  Se prueba con un intérprete normal (22 pruebas) y produce el mismo resultado dentro y fuera de Blender.
* **Capa Blender**: sólo traduce. `props.py` (parámetros) → `core.pipeline.evaluate()` → `build.py`
  (escribe mallas, curvas y atributos).
* **Geometry Nodes**: queda reservado para lo que hace mejor y donde no compromete la exactitud:
  instanciar cientos de módulos idénticos (v0.2) y efectos de visualización. En el MVP no hay nada
  que GN haga mejor, así que no se usa: introducirlo ahora sólo añadiría fragilidad.

Por qué no B: la constructibilidad y el FEA necesitan saber *qué* es cada cosa (qué arista es borde,
qué nodo es apoyo, qué cara es intradós, cuál es la ecuación), no sólo dónde están los vértices.
Eso es lógica y datos, no geometría procedural.

## 3. Componentes y flujo de datos

```
Object.candela (PropertyGroup)        ← interfaz, variantes, JSON importado
        │ settings_to_dict()  (diccionario plano; mismas claves en todas partes)
        ▼
core.pipeline.evaluate(params)
   ├─ core.model.build_model("HYPAR")   registro de generadores → ShellModel
   │     └─ core.hypar                  ecuación → QuadraticSurface + QuadDomain
   ├─ core.meshing                      sólido / superficie, topología verificada
   ├─ core.rulings                      generatrices exactas (o curvas desfasadas)
   ├─ core.elements                     apoyos, vigas de borde
   ├─ core.metrics                      áreas, volumen (Steiner), extremos exactos
   └─ core.checks                       avisos geométricos / de constructibilidad
        │ Evaluation (mallas + informe)
        ▼
build.py → Mesh del cascarón + hijos (Generatrices, Normales, Apoyos, Vigas)
         → report_json (guardado en el .blend)
```

**Modelo de datos.** Un `ShellModel` es una lista de `ShellPatch` (superficie exacta + dominio en
planta), las limas que comparten (`Crease`), un `ThicknessSpec` y apoyos. La hypar tiene un parche;
la bóveda radial N (dominios `SectorDomain`: sector con cuerda o arco). Cada familia registra su
generador y su mallador (`register_generator`, `register_mesher`). Métricas, generatrices y
exportación recorren los parches, así que sirven para cualquier familia sin cambios.

**Superficie.** `QuadraticSurface`: `z = c0 + c1·x + c2·y + c3·x² + c4·xy + c5·y²`, hypar si
`4·c3·c5 − c4² < 0`. Cubre todas las hypars de eje vertical, que es el caso de la teoría de membrana
que usó Candela (z = kxy más un plano). Las hypars de eje inclinado se obtienen hoy girando el objeto;
como tipo de parche propio quedan para después.

**Espesor.** La superficie de referencia (intradós, media o extradós) es la hypar exacta. Las otras
caras se desfasan:
* `VERTICAL`: las tres capas son hypars exactas; el espesor real ≈ t·cos α varía con la pendiente.
* `NORMAL`: espesor real constante; las capas desfasadas **no** son hypars ni superficies regladas.

Valor por defecto: referencia = intradós y espesor normal. Así la cara que lleva la cimbra es
exactamente reglada y el espesor del concreto es constante.

## 4. Desafíos técnicos principales

1. **Una sola fuente de verdad.** Parámetros → geometría siempre en una dirección. Las ediciones
   manuales de la malla se sobrescriben; para conservarlas existe «Congelar».
2. **Desfase de superficies.** El desfase normal de una hypar se pliega cuando `t ≥ ρ_min`. Las capas
   desfasadas pierden el doble reglado, y eso afecta directamente a la cimbra.
3. **Uniones entre parches.** Resuelto en la v0.2 para la bóveda radial. Las limas son
   generatrices compartidas (continuidad C0 exacta) y la malla queda soldada (un solo vértice por
   punto de lima y uno en la clave). El espesor normal se desfasa con inglete:
   m = (n1 + n2)/(1 + n1·n2) en las limas y mínimos cuadrados en la clave. El volumen analítico
   suma Steiner por lóbulo más la cuña de inglete σ·tan(δ/2)·(s1² − s0²) por unidad de longitud;
   su error residual es O(t³). Falta generalizarlo a pliegues que no sean generatrices y a vértices
   sin simetría.
4. **Recortes generales.** Resuelto para planos inclinados en la bóveda radial (v0.3): el dominio es
   estrellado desde la clave y se malla con el mismo abanico. Falta aplicarlo a cortes arbitrarios
   de la hypar rectangular y a dominios no estrellados.
5. **Malla para FEA.** Elementos casi planos (S4/MITC4) o cuadráticos, refinamiento cerca de bordes
   y apoyos, unidades, y conjuntos de nodos que sobrevivan al remallado.
6. **Rendimiento.** Python puro alcanza ~30 ms a 32×32 y ~170 ms a 96×96. Más allá habrá que
   vectorizar con NumPy (incluido en Blender) o diferir la regeneración mientras se arrastra un
   control.
7. **Geometría ≠ seguridad.** Los cascarones delgados son sensibles al pandeo y a las imperfecciones.
   La v0.4 necesita análisis no lineal con imperfecciones, no sólo análisis lineal elástico.

## 5. Hoja de ruta

* **v0.2 (hecho) Bóveda radial.** N lóbulos sobre planta circular o poligonal, con limas soldadas,
  inglete y tipos de partida (cúpula, lóbulos tipo Manantiales, bóveda por arista).
* **v0.3 (hecho) Bordes por planos inclinados (tipo Manantiales).** El dominio del lóbulo es la
  región g = f − plano > 0 dentro de la cuña de limas (`SectorDomain(cut=g)`). El borde se obtiene
  como raíz cuadrática exacta por rayo, su derivada por derivación implícita, y las generatrices se
  recortan de forma exacta porque g es lineal sobre ellas.
* **v0.4 (hecho) Paraguas de cuatro hypars.** Cuatro parches bilineales (la interpolación bilineal
  de las 4 esquinas de un cuadrante es exactamente una hypar) en una sola rejilla soldada, con
  limas en los ejes, inglete y vigas de borde sobre el contorno (`ShellModel.outline`).
* **v0.5 (hecho) Retícula modular.** Un módulo evaluado una vez y colocaciones (paso, juntas, espejo
  en columnas o filas alternas, escalonamiento). Los totales son N veces el módulo; cotas, columnas
  y saltos entre bordes contiguos se calculan por posición. Funciona con cualquier tipo de cascarón.
* **v0.6 (hecho) Instanciación con Geometry Nodes.** Es el papel que la arquitectura híbrida
  reservaba a GN desde el principio. Python sigue siendo la fuente de verdad: calcula un módulo y
  las colocaciones. GN sólo repite: *Object Info* de los puntos de colocación → *Instance on
  Points*, con escala ±1 para el espejo. El núcleo decide el modo (umbral de 50 000 vértices) y
  los informes son idénticos en ambos. Con 1600 paraguas: ~60 ms.
* **v0.7 (hecho) Despiece de cimbra con las generatrices.** Un módulo del núcleo
  (`core/formwork.py`) pone tablas sobre una familia de generatrices del intradós, vigas sobre la
  otra y puntales hasta el desplante. Usa la geometría exacta de cada recta: largo, esviaje de los
  cortes en el plano tangente, giro de la normal y holgura entre tablas contiguas. Esta última es
  ≥ 0 por construcción: proyectar en planta no alarga distancias. Agrupa tipos, calcula el corte de
  piezas comerciales y exporta JSON y CSV. Blender sólo dibuja la malla (tablas subdivididas para
  seguir la torsión) en un hijo con tres materiales. En retículas es un juego reutilizable.
* **v0.8 (hecho) Exportación a FEA con cargas y apoyos.** `core/fea.py` arma, sin bpy, un modelo
  independiente del programa:
  * nodos, láminas S8R/S6 (bloques 2 × 2 de una malla a doble resolución, con todos los nodos sobre
    la superficie exacta) o S4/S3;
  * almas de lámina para las vigas de borde y vigas B32 para las columnas;
  * apoyos por huella, casos de carga como fuerzas nodales consistentes, combinaciones con factores
    del usuario y pasos de pandeo.

  Lo escribe en `.inp` (CalculiX/Abaqus) y en JSON. Antes de exportar comprueba los movimientos de
  sólido rígido (rango de las restricciones), la conectividad y los totales. No resuelve el modelo:
  la integración con un solver sería el paso siguiente.
* **v0.9 (hecho) Resultados .frd sobre el cascarón.** `core/frd.py` lee, sin bpy, el `.frd` ASCII
  por columnas fijas y el `.dat` (factores de pandeo, reacciones totales). Asocia los resultados a
  los vértices del cascarón:
  * con salida 2D, por número de nodo, comprobando coordenadas;
  * con salida 3D (la predeterminada de CalculiX para láminas), por cercanía a los nodos expandidos,
    así que intradós y extradós muestran cada uno su cara.

  Los resultados quedan ligados a una huella de los parámetros que afectan al modelo FE. Blender sólo
  escribe atributos de color y valor y la deformada; el `.frd` se relee si no está en memoria.
* **v0.10 (hecho) Rigidizadores.** `elements.py` construye nervios bajo las limas y la viga del borde
  libre de las bóvedas como prismas verticales sobre franjas en planta (volumen = peralte × área,
  exacto). En el núcleo:
  * los nervios se arman en dos mitades por lima, recortadas por las bisectrices donde concurren varias;
  * la viga del borde se recorta en las limas, a inglete en las esquinas poligonales.

  Entran en métricas, retícula (instanciados), JSON y FEA (almas de lámina bajo las líneas de nodos).
* **v0.11 (hecho) Esfuerzos por unidad de longitud.** `frd.py` asocia a cada nodo de lámina del
  modelo FE los nodos del .frd de sus dos caras (en la normal, a ±t/2). Lleva sus tensiones al plano
  tangente (x = proyección del X global) y calcula N = t·(σ_ext + σ_int)/2 y M = t²/12·(σ_ext − σ_int),
  con sus principales. Con salida 2D sólo hay N. Se muestran como las demás magnitudes y se exportan
  a CSV.
* **v0.12 (hecho) Capiteles.** `core/capitals.py` define un engrosamiento suave bajo cada apoyo
  (Δ·(1 − (r/R)²)²) que baja el intradós.
  * Lo usan la malla del sólido, la altura del intradós (columnas, nervios, vigas) y el modelo FEA
    (superficie media y espesor por nodo).
  * Su volumen se integra en polares dentro de la planta: rayos recortados por los bordes exactos de
    los paños y abanico ajustado por bisección. Con espesor normal usa Steiner punto a punto, más la
    cuña de inglete de las limas bajo el capitel (la misma corrección de `metrics`, ahora con
    `crease_wedge` compartido).
* **v0.13 (hecho) Direcciones principales de N.** `core/directions.py` toma (Nx, Ny, Nxy) de cada nodo
  de lámina y obtiene θ₁ = ½·atan2(2·Nxy, Nx − Ny), d₁ = cos θ₁·e₁ + sin θ₁·e₂ y d₂ = n × d₁.
  * Muestrea una cruz por celda de una rejilla en planta centrada (el nodo más cercano al centro).
  * Cada brazo es la recta en planta q₀ + u·(d_x, d_y): como d es tangente, su longitud sobre la
    superficie es u hasta segundo orden. Cada vértice, también los bordes de la cinta y las esquinas
    de las puntas, se lleva a la cara con el mismo desfase que el sólido (vertical o inglete, con el
    capitel), así que la cinta se dobla con la superficie y en las limas.
  * Los brazos se recortan en el borde por bisección. Blender sólo traslada la malla al primer módulo.
  * Al probarlo se corrigieron dos fallos anteriores. La tolerancia de la asociación 3D ahora usa el
    espesor mayor, el de los capiteles. Y la caché de resultados se invalida al cambiar entre sólido
    y superficie.
* **v0.14 (hecho) Prueba con CalculiX real.** Primera vez que la cadena se resuelve con un solver
  (CalculiX 2.23 de conda-forge, en Windows). `tests/test_calculix.py` exporta, calcula con `ccx` y lee
  los resultados (se salta sin CalculiX). Encontró y corrigió tres fallos:
  * **Apoyo articulado**: impedía las traslaciones de todos los nodos de la huella, lo que también
    impide el giro. Ahora es el nodo más cercano al centro del apoyo, una articulación real. Se probó
    el acoplamiento distribuido de CalculiX (`*DISTRIBUTING COUPLING`): articula bien, pero sus
    reacciones RF salen asimétricas en un problema simétrico. El sólido rígido no se admite en nodos
    de lámina.
  * **Reacciones**: CalculiX suma a RF la carga aplicada en los propios nodos restringidos. `fea.py`
    calcula esa carga (gravedad consistente en láminas, almas y vigas, y fuerzas nodales) y `frd.py`
    la descuenta. Antes avisaba de diferencias de hasta el 1,6 % sin motivo.
  * **Pandeo**: en un paso *BUCKLE, CalculiX escribe primero el estado base (valor 0) y luego los
    modos, sin `1PMODE`. Se tomaba el estado base por el modo 1 y los factores quedaban desplazados.
  * Además: con salida 2D, en los nodos compartidos con el alma de una viga, CalculiX promedia la
    lámina con el alma; en el resto, la N de la salida 2D coincide con la de las dos caras de la 3D.
* **v0.15 (hecho) Calcular desde Blender.** `core/ccx.py` (sin bpy) busca ccx, arma la orden (un
  `.cmd`/`.bat` con `cmd /c`), lo lanza en segundo plano con su salida en `<trabajo>_ccx.log`, lee de
  ahí el progreso («STEP n», «Job finished») y los `*ERROR`, y lo detiene con todo lo que haya lanzado
  (`taskkill /T` en Windows, el grupo de procesos en los demás).
  * `CANDELA_OT_fea_run`: con ventana es modal con un temporizador de 0,5 s, y Blender sigue
    respondiendo; desde un script (`execute`) espera a que termine. No se lanza si hay un mecanismo.
    Al terminar sólo carga si los parámetros siguen siendo los exportados.
  * La ruta de ccx y los hilos van en las preferencias del complemento; la carpeta de cálculo, en
    cada cascarón (no es un parámetro de diseño).
  * Abrir otro archivo, salir de Blender o desactivar el complemento detiene los cálculos en curso.
* **Siguiente.** Por decidir. Candidatos: trayectorias continuas de N (isostáticas) y direcciones
  principales de M.
* **v0.3 Cimbra y materiales.** Tablas reales a lo largo de las generatrices del intradós, vigas
  madrinas, puntales hasta el desplante, despiece y cuantificación.
* **v0.4 Análisis.** Exportar a CalculiX/OpenSees/Code_Aster (elementos lámina, materiales, cargas y
  condiciones de apoyo explícitas) y leer resultados. Una verificación de membrana (teoría clásica de
  la hypar) sólo se mostraría con sus hipótesis declaradas.
