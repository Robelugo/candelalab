"""Ejecución de CalculiX (ccx) como programa externo (sin bpy).

CandelaLab no incluye CalculiX: el usuario indica dónde está (preferencias del
complemento) o se busca en la variable CANDELALAB_CCX y en el PATH. La ruta
puede ser el ejecutable o un .cmd/.bat que lo lance (por ejemplo, uno que
añada al PATH las bibliotecas de un entorno de conda).

El trabajo se llama como el objeto, sólo con letras, cifras, «_» y «-»:
ccx -i <trabajo> lee <trabajo>.inp y escribe .frd, .dat, .sta y .cvg en la
misma carpeta. Su salida de consola se guarda en <trabajo>_ccx.log: de ahí
salen el progreso («STEP n», «Job finished») y los errores («*ERROR ...»).
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import signal
import subprocess
import sys

OUTPUTS = (".frd", ".dat", ".sta", ".cvg")
NAMES = ("ccx", "ccx_2.23", "ccx_2.22", "ccx_2.21", "ccx_static")


def find_ccx(configured: str = "") -> str | None:
    """Ruta de ccx: la configurada (si existe), CANDELALAB_CCX o el primero de NAMES en el PATH."""
    for path in (configured, os.environ.get("CANDELALAB_CCX", "")):
        if path and os.path.isfile(path):
            return path
    return _on_path(os.environ.get("PATH", ""))


@functools.lru_cache(maxsize=4)
def _on_path(_path: str) -> str | None:  # el panel lo pregunta en cada dibujo: se recuerda mientras no cambie el PATH
    return next((p for p in map(shutil.which, NAMES) if p), None)


def job_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "modelo"


def command(ccx: str, job: str) -> list[str]:
    """Orden para calcular <job>.inp. Un .cmd/.bat se lanza con cmd /c (el trabajo ya está saneado)."""
    if ccx.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", ccx, "-i", job]
    return [ccx, "-i", job]


def environment(threads: int) -> dict:
    """Entorno del proceso: hilos de CalculiX (0 = todos los núcleos)."""
    env = dict(os.environ)
    n = str(threads if threads > 0 else os.cpu_count() or 1)
    env["OMP_NUM_THREADS"] = n
    env["CCX_NPROC_EQUATION_SOLVER"] = n
    return env


def clear_outputs(folder: str, job: str) -> None:
    """Borra los resultados de un cálculo anterior del mismo trabajo, para no leerlos por error."""
    for ext in OUTPUTS:
        path = os.path.join(folder, job + ext)
        if os.path.exists(path):
            os.remove(path)


def start(ccx: str, folder: str, job: str, threads: int = 0) -> tuple[subprocess.Popen, object]:
    """Lanza ccx en segundo plano; devuelve el proceso y el archivo abierto de su salida."""
    log = open(os.path.join(folder, job + "_ccx.log"), "w", encoding="utf-8", errors="replace")
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(command(ccx, job), cwd=folder, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=environment(threads), **kw)
    return proc, log


def stop(proc: subprocess.Popen) -> None:
    """Detiene ccx y lo que haya lanzado (con un .cmd, cmd.exe lanza ccx.exe aparte)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.killpg(proc.pid, signal.SIGTERM)
    proc.wait()


def progress(log: str) -> tuple[int, bool]:
    """Último paso empezado y si el trabajo terminó, según la salida de ccx."""
    steps = re.findall(r"^\s*STEP\s+(\d+)\s*$", log, flags=re.M)
    return (int(steps[-1]) if steps else 0), "Job finished" in log


def error(log: str) -> str | None:
    """Primer «*ERROR» de la salida de ccx, en una línea (CalculiX lo parte en varias)."""
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if "*ERROR" in line:
            text = [line.strip()]
            for nxt in lines[i + 1:]:
                if not nxt.strip() or "*ERROR" in nxt or "*WARNING" in nxt:
                    break
                text.append(nxt.strip())
            return re.sub(r"\s+", " ", " ".join(text))
    return None
