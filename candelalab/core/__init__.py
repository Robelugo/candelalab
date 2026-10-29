"""
CandelaLab core: núcleo geométrico puro (sin dependencias de Blender).

Todo lo que es matemática, mallado, métricas, comprobaciones y exportación vive
aquí y puede probarse con un intérprete de Python normal:

    python -m unittest discover -s tests -v

La capa de Blender (props.py, build.py, operators.py, panels.py) sólo traduce
entre las propiedades de la interfaz y estas funciones; nunca hace geometría.
"""

SCHEMA_VERSION = "0.14.0"
