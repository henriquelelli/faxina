"""Estado entre execuções e agendamento no launchd.

O launchd chama a faxina todo dia no horário escolhido (e logo ao acordar, se
o Mac estava dormindo nessa hora). A própria faxina decide se já passou o
intervalo desde a última execução; assim o intervalo de N dias não se perde
com reinícios nem com o Mac desligado.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROTULO = "io.github.faxina"


# --- estado ----------------------------------------------------------------


def ler_estado(pasta: Path) -> dict:
    try:
        return json.loads((pasta / "estado.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def gravar_estado(pasta: Path, dados: dict) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    temporario = pasta / "estado.json.tmp"
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporario, pasta / "estado.json")


@contextlib.contextmanager
def trava(pasta: Path):
    """Garante uma faxina por vez. Entrega False se outra já está rodando."""
    pasta.mkdir(parents=True, exist_ok=True)
    with open(pasta / "faxina.lock", "w") as arquivo:
        try:
            fcntl.flock(arquivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(arquivo, fcntl.LOCK_UN)


# --- launchd ---------------------------------------------------------------


def caminho_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{ROTULO}.plist"


def path_para_agente(path_atual: str) -> str:
    """O launchd começa com um PATH mínimo; levamos o PATH de quem instalou,
    sem repetições, sem pastas temporárias e só com pastas que existem."""
    vistos, pastas = set(), []
    for p in path_atual.split(os.pathsep):
        if not p or p in vistos or not os.path.isdir(p):
            continue
        if p.startswith(("/private/var/folders", "/var/folders", "/tmp", "/private/tmp")):
            continue
        vistos.add(p)
        pastas.append(p)
    return os.pathsep.join(pastas)


def montar_plist(python: str, config: Path, estado_dir: Path, hora: int, minuto: int, path_env: str) -> dict:
    pasta_projeto = Path(__file__).resolve().parent.parent
    return {
        "Label": ROTULO,
        "ProgramArguments": [python, "-m", "faxina", "executar", "--config", os.fspath(config)],
        "WorkingDirectory": os.fspath(pasta_projeto),
        "EnvironmentVariables": {"PATH": path_env, "PYTHONUNBUFFERED": "1"},
        "StartCalendarInterval": {"Hour": hora, "Minute": minuto},
        "StandardOutPath": os.fspath(estado_dir / "faxina.log"),
        "StandardErrorPath": os.fspath(estado_dir / "faxina.log"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def instalar(config: Path, estado_dir: Path, hora: int, minuto: int, python: str | None = None) -> Path:
    estado_dir.mkdir(parents=True, exist_ok=True)
    destino = caminho_plist()
    destino.parent.mkdir(parents=True, exist_ok=True)
    dados = montar_plist(python or sys.executable, config, estado_dir, hora, minuto,
                         path_para_agente(os.environ.get("PATH", "")))
    with destino.open("wb") as f:
        plistlib.dump(dados, f, sort_keys=True)
    dominio = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{dominio}/{ROTULO}")
    r = _launchctl("bootstrap", dominio, os.fspath(destino))
    if r.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap falhou: {(r.stderr or r.stdout).strip()}")
    return destino


def desinstalar() -> bool:
    destino = caminho_plist()
    _launchctl("bootout", f"gui/{os.getuid()}/{ROTULO}")
    if destino.exists():
        destino.unlink()
        return True
    return False


def agendado() -> bool:
    return _launchctl("print", f"gui/{os.getuid()}/{ROTULO}").returncode == 0
