"""Medição de espaço e remoção com travas de segurança."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path


class RemocaoRecusada(Exception):
    pass


def tamanho_em_disco(caminho: Path) -> int:
    """Bytes realmente alocados (st_blocks), sem seguir links simbólicos e
    contando cada arquivo com vários hard links uma única vez."""
    try:
        st = os.lstat(caminho)
    except FileNotFoundError:
        return 0
    total = st.st_blocks * 512
    if not stat.S_ISDIR(st.st_mode):
        return total
    vistos: set[tuple[int, int]] = set()
    pilha = [os.fspath(caminho)]
    while pilha:
        atual = pilha.pop()
        try:
            with os.scandir(atual) as entradas:
                for e in entradas:
                    try:
                        s = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISDIR(s.st_mode):
                        pilha.append(e.path)
                    elif s.st_nlink > 1:
                        chave = (s.st_dev, s.st_ino)
                        if chave in vistos:
                            continue
                        vistos.add(chave)
                    total += s.st_blocks * 512
        except OSError:
            continue
    return total


def espaco_do_disco(caminho: Path | None = None) -> tuple[int, int]:
    """(total, livre) do volume que contém `caminho` (padrão: pasta pessoal)."""
    uso = shutil.disk_usage(caminho or Path.home())
    return uso.total, uso.free


def verificar_remocao(alvo: Path, raizes: list[Path] | tuple[Path, ...]) -> None:
    """Recusa apagar links simbólicos, a pasta pessoal, a raiz do sistema ou
    qualquer coisa que não esteja estritamente dentro de uma das `raizes`."""
    if alvo.is_symlink():
        raise RemocaoRecusada(f"{alvo} é um link simbólico")
    real = Path(os.path.realpath(alvo))
    casa = Path(os.path.realpath(Path.home()))
    if real == casa or real == Path(real.anchor):
        raise RemocaoRecusada(f"{alvo} é a pasta pessoal ou a raiz do sistema")
    for raiz in raizes:
        raiz_real = Path(os.path.realpath(raiz))
        if real != raiz_real and real.is_relative_to(raiz_real):
            return
    raise RemocaoRecusada(f"{alvo} está fora das pastas permitidas")


def apagar(alvo: Path) -> list[str]:
    """Apaga arquivo ou pasta. Devolve a lista de erros (vazia se tudo saiu).
    Pastas sem permissão de escrita (comuns em caches) recebem a permissão do
    dono e a remoção é tentada de novo."""
    erros: list[str] = []

    def tratar(funcao, caminho, excecao):
        try:
            pai = os.path.dirname(caminho)
            os.chmod(pai, os.stat(pai).st_mode | stat.S_IRWXU)
            if os.path.isdir(caminho) and not os.path.islink(caminho):
                os.chmod(caminho, os.stat(caminho).st_mode | stat.S_IRWXU)
            funcao(caminho)
        except FileNotFoundError:
            pass
        except OSError as e:
            erros.append(f"{caminho}: {e.strerror or e}")

    if alvo.is_dir() and not alvo.is_symlink():
        if sys.version_info >= (3, 12):
            shutil.rmtree(alvo, onexc=tratar)
        else:  # pragma: no cover - Python 3.11
            shutil.rmtree(alvo, onerror=lambda f, p, info: tratar(f, p, info[1]))
    else:
        try:
            alvo.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            erros.append(f"{alvo}: {e.strerror or e}")
    return erros


def formatar_bytes(n: int) -> str:
    """Unidades decimais, como o Finder e os Ajustes do macOS, com vírgula."""
    for unidade, fator in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6), ("kB", 10**3)):
        if abs(n) >= fator:
            return f"{n / fator:.1f}\u00a0{unidade}".replace(".", ",")
    return f"{n}\u00a0B"


def abreviar(caminho: str | Path) -> str:
    """Troca o caminho da pasta pessoal por ~ para o relatório."""
    texto = os.fspath(caminho)
    casa = os.fspath(Path.home())
    if texto == casa:
        return "~"
    if texto.startswith(casa + os.sep):
        return "~" + texto[len(casa):]
    return texto
