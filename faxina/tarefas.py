"""As quatro tarefas da faxina.

Cada tarefa devolve uma lista de `Resultado` em ordem estável (a ordem da
configuração e, dentro dela, a ordem alfabética dos caminhos). Com o mesmo
disco, a mesma configuração e o mesmo `agora`, as decisões são as mesmas.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Comando, Pasta, Projetos, Regeneravel, RepoGit
from .disco import (
    RemocaoRecusada,
    apagar,
    formatar_bytes,
    tamanho_em_disco,
    verificar_remocao,
)

DIA = 86400

# situações possíveis de um resultado
LIMPO = "limpo"
SIMULADO = "simulado"
MANTIDO = "mantido"
ERRO = "erro"


@dataclass
class Resultado:
    tarefa: str  # "projetos" | "comandos" | "pastas" | "git"
    nome: str
    alvo: str
    situacao: str
    bytes: int = 0
    estimado: bool = False
    detalhe: str = ""
    segundos: float = 0.0


def _dias(segundos: float) -> str:
    d = int(segundos // DIA)
    if d <= 0:
        return "hoje"
    return "há 1 dia" if d == 1 else f"há {d} dias"


def _ultima_linha(texto: str) -> str:
    linhas = [l.strip() for l in (texto or "").splitlines() if l.strip()]
    return linhas[-1][:240] if linhas else ""


# --- processos com a pasta aberta ------------------------------------------


@dataclass(frozen=True)
class Processo:
    pid: int
    comando: str
    pasta: Path


def pastas_em_uso() -> list[Processo] | None:
    """Pasta de trabalho (cwd) de cada processo do usuário, via lsof.
    Devolve None se não for possível consultar: nesse caso nenhum projeto é
    apagado, porque não dá para garantir que ele não está em uso."""
    try:
        r = subprocess.run(
            ["lsof", "-nP", "-d", "cwd", "-Fpcn"], capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode not in (0, 1) or not r.stdout:
        return None
    processos, pid, comando = [], None, ""
    for linha in r.stdout.splitlines():
        if linha.startswith("p"):
            pid, comando = int(linha[1:]), ""
        elif linha.startswith("c"):
            comando = linha[1:]
        elif linha.startswith("n") and pid is not None:
            processos.append(Processo(pid, comando, Path(os.path.realpath(linha[1:]))))
    return processos


def _processo_dentro(projeto: Path, processos: list[Processo]) -> Processo | None:
    real = Path(os.path.realpath(projeto))
    for p in sorted(processos, key=lambda p: p.pid):
        if p.pasta == real or p.pasta.is_relative_to(real):
            return p
    return None


# --- projetos: pastas regeneráveis sem uso ---------------------------------


def encontrar_candidatos(cfg: Projetos) -> list[tuple[Path, Path, Regeneravel]]:
    """(pasta regenerável, raiz em que foi achada, regra) em ordem alfabética.
    Não segue links simbólicos e não entra nas pastas regeneráveis."""
    regras = {r.pasta: r for r in cfg.regeneraveis}
    achados: list[tuple[Path, Path, Regeneravel]] = []
    for raiz in cfg.raizes:
        if not raiz.is_dir():
            continue
        pilha: list[tuple[Path, int]] = [(raiz, 0)]
        while pilha:
            pasta, profundidade = pilha.pop()
            try:
                entradas = sorted(os.scandir(pasta), key=lambda e: e.name)
            except OSError:
                continue
            filhos = []
            for e in entradas:
                if not e.is_dir(follow_symlinks=False):
                    continue
                if e.name in regras:
                    achados.append((Path(e.path), raiz, regras[e.name]))
                elif e.name not in cfg.nao_entrar and profundidade < cfg.profundidade_maxima:
                    filhos.append((Path(e.path), profundidade + 1))
            pilha.extend(reversed(filhos))
    return sorted(achados, key=lambda a: os.fspath(a[0]))


def ultima_alteracao(projeto: Path, excluir: set[str], limite: float) -> float:
    """Maior data de modificação de arquivo do projeto, sem entrar nas pastas
    de `excluir`. Para assim que acha um arquivo mais novo que `limite`: a essa
    altura já se sabe que o projeto está em uso."""
    maior = 0.0
    pilha = [projeto]
    while pilha:
        atual = pilha.pop()
        try:
            entradas = sorted(os.scandir(atual), key=lambda e: e.name)
        except OSError:
            continue
        for e in entradas:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in excluir:
                        pilha.append(Path(e.path))
                    continue
                m = e.stat(follow_symlinks=False).st_mtime
            except OSError:
                continue
            if m > maior:
                maior = m
                if maior >= limite:
                    return maior
    return maior


def _marcador(projeto: Path, raiz: Path, nomes: tuple[str, ...]) -> Path | None:
    """Procura um dos arquivos `nomes` no projeto ou acima dele, até a raiz
    (cobre monorepos com o lockfile na pasta de cima)."""
    p = projeto
    while True:
        for n in nomes:
            if (p / n).is_file():
                return p / n
        if p == raiz or p.parent == p or not p.is_relative_to(raiz):
            return None
        p = p.parent


def limpar_projetos(
    cfg: Projetos, simular: bool, agora: float, processos: list[Processo] | None
) -> list[Resultado]:
    if not cfg.ativo or not cfg.regeneraveis:
        return []
    limite = agora - cfg.dias_sem_uso * DIA
    excluir = {".git", *cfg.nao_entrar, *(r.pasta for r in cfg.regeneraveis)}
    resultados = []
    for alvo, raiz, regra in encontrar_candidatos(cfg):
        inicio = time.monotonic()
        projeto = alvo.parent
        r = Resultado("projetos", regra.nome, os.fspath(alvo), MANTIDO)
        padrao = next(
            (p for p in cfg.ignorar
             if fnmatch.fnmatch(os.fspath(projeto), p) or fnmatch.fnmatch(os.fspath(alvo), p)),
            None,
        )
        if padrao:
            r.detalhe = f"ignorado pela configuração ({padrao})"
        elif regra.exige_um_destes and not _marcador(projeto, raiz, regra.exige_um_destes):
            r.detalhe = "sem " + " / ".join(regra.exige_um_destes) + ": não daria para reconstruir"
        elif processos is None:
            r.detalhe = "não foi possível verificar os processos abertos (lsof)"
        elif uso := _processo_dentro(projeto, processos):
            r.detalhe = f"pasta aberta por {uso.comando or 'um processo'} (pid {uso.pid})"
        else:
            m = ultima_alteracao(projeto, excluir, limite)
            if m >= limite:
                r.detalhe = f"projeto alterado {_dias(agora - m)}"
            else:
                quando = f"sem alterações {_dias(agora - m)}" if m else "sem arquivos próprios"
                r.bytes = tamanho_em_disco(alvo)
                if simular:
                    r.situacao, r.detalhe = SIMULADO, quando
                else:
                    try:
                        verificar_remocao(alvo, [raiz])
                    except RemocaoRecusada as e:
                        r.bytes, r.detalhe = 0, f"remoção recusada: {e}"
                    else:
                        erros = apagar(alvo)
                        if erros:
                            r.bytes -= tamanho_em_disco(alvo)
                            r.situacao = ERRO
                            r.detalhe = f"{len(erros)} item(ns) não apagado(s); primeiro: {erros[0]}"
                        else:
                            r.situacao, r.detalhe = LIMPO, quando
        r.segundos = round(time.monotonic() - inicio, 2)
        resultados.append(r)
    return resultados


# --- comandos de limpeza ---------------------------------------------------


def executar_comandos(comandos: tuple[Comando, ...], simular: bool, ambiente: dict) -> list[Resultado]:
    resultados = []
    contados: set[Path] = set()  # na simulação, cada pasta medida entra na conta uma vez só
    for c in comandos:
        inicio = time.monotonic()
        r = Resultado("comandos", c.nome, " ".join(c.comando), MANTIDO)
        resultados.append(r)
        if not shutil.which(c.comando[0], path=ambiente.get("PATH")):
            r.detalhe = f"{c.comando[0]} não está instalado"
            continue
        if c.condicao:
            try:
                ok = subprocess.run(
                    list(c.condicao), capture_output=True, timeout=60, env=ambiente
                ).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            if not ok:
                r.detalhe = "condição não atendida: " + " ".join(c.condicao)
                continue
        if simular:
            novos = [m for m in c.medir if m not in contados]
            contados.update(novos)
            r.situacao = SIMULADO
            if not c.medir:
                r.detalhe = "não executado"
            elif not novos:
                r.detalhe = "não executado; mede o mesmo que um comando acima, que já entrou na conta"
            else:
                r.bytes, r.estimado = sum(tamanho_em_disco(m) for m in novos), True
                r.detalhe = "não executado; o valor é o tamanho atual do que ele limpa"
            continue
        antes = sum(tamanho_em_disco(m) for m in c.medir)
        try:
            p = subprocess.run(
                list(c.comando), capture_output=True, text=True, timeout=c.tempo_limite_s, env=ambiente
            )
        except subprocess.TimeoutExpired:
            r.situacao, r.detalhe = ERRO, f"passou do tempo limite de {c.tempo_limite_s} s"
        except OSError as e:
            r.situacao, r.detalhe = ERRO, f"não foi possível executar: {e}"
        else:
            if p.returncode == 0:
                r.situacao = LIMPO
                r.detalhe = _ultima_linha(p.stdout) or "concluído"
            else:
                r.situacao = ERRO
                r.detalhe = f"terminou com código {p.returncode}: {_ultima_linha(p.stderr or p.stdout)}"
        r.bytes = max(0, antes - sum(tamanho_em_disco(m) for m in c.medir))
        r.segundos = round(time.monotonic() - inicio, 2)
    return resultados


# --- pastas de cache -------------------------------------------------------


def limpar_pastas(pastas: tuple[Pasta, ...], simular: bool, agora: float) -> list[Resultado]:
    resultados = []
    for p in pastas:
        inicio = time.monotonic()
        r = Resultado("pastas", p.nome, os.fspath(p.caminho), MANTIDO)
        resultados.append(r)
        if p.caminho.is_symlink() or not p.caminho.is_dir():
            r.detalhe = "pasta não existe"
            continue
        limite = agora - p.idade_minima_dias * DIA
        removidos = mantidos = 0
        erros: list[str] = []
        try:
            entradas = sorted(os.scandir(p.caminho), key=lambda e: e.name)
        except OSError as e:
            r.situacao, r.detalhe = ERRO, f"não foi possível ler a pasta: {e.strerror or e}"
            continue
        for e in entradas:
            try:
                m = e.stat(follow_symlinks=False).st_mtime
            except OSError:
                continue
            if p.idade_minima_dias and m >= limite:
                mantidos += 1
                continue
            alvo = Path(e.path)
            tamanho = tamanho_em_disco(alvo)
            if not simular:
                try:
                    verificar_remocao(alvo, [p.caminho])
                except RemocaoRecusada as x:
                    erros.append(str(x))
                    continue
                falhas = apagar(alvo)
                if falhas:
                    erros.extend(falhas)
                    tamanho -= tamanho_em_disco(alvo)
            r.bytes += tamanho
            removidos += 1
        if removidos == 0 and not erros:
            r.detalhe = "nada para limpar" + (f" ({mantidos} item(ns) recentes mantidos)" if mantidos else "")
        else:
            r.situacao = ERRO if erros else (SIMULADO if simular else LIMPO)
            partes = [f"{removidos} item(ns) {'seriam apagados' if simular else 'apagados'}"]
            if mantidos:
                partes.append(f"{mantidos} recentes mantidos")
            if erros:
                partes.append(f"{len(erros)} erro(s); primeiro: {erros[0]}")
            r.detalhe = "; ".join(partes)
        r.segundos = round(time.monotonic() - inicio, 2)
    return resultados


# --- repositórios git ------------------------------------------------------


def _git(caminho: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", os.fspath(caminho), *args], capture_output=True, text=True, timeout=timeout
    )


def objetos_soltos(caminho: Path) -> tuple[int, int]:
    """(quantidade, bytes) de objetos soltos, segundo `git count-objects -v`."""
    saida = _git(caminho, "count-objects", "-v").stdout
    campos = dict(re.findall(r"^([\w-]+): (\d+)$", saida, flags=re.M))
    return int(campos.get("count", 0)), int(campos.get("size", 0)) * 1024


def compactar_git(repos: tuple[RepoGit, ...], simular: bool) -> list[Resultado]:
    """`git repack -a -d`: junta os objetos soltos num pacote. Não usa prune,
    então nenhum objeto (nem os sem referência) é descartado."""
    resultados = []
    for g in repos:
        inicio = time.monotonic()
        r = Resultado("git", g.nome, os.fspath(g.caminho), MANTIDO)
        resultados.append(r)
        if shutil.which("git") is None:
            r.detalhe = "git não está instalado"
            continue
        if not g.caminho.is_dir():
            r.detalhe = "pasta não existe"
            continue
        dir_git = _git(g.caminho, "rev-parse", "--absolute-git-dir")
        if dir_git.returncode != 0:
            r.detalhe = "não é um repositório git"
            continue
        pasta_git = Path(dir_git.stdout.strip())
        qtd, tamanho_soltos = objetos_soltos(g.caminho)
        if qtd < g.min_objetos_soltos:
            r.detalhe = f"{qtd} objetos soltos; compacta a partir de {g.min_objetos_soltos}"
            continue
        if simular:
            r.situacao, r.bytes, r.estimado = SIMULADO, tamanho_soltos, True
            r.detalhe = f"{qtd} objetos soltos seriam empacotados"
            continue
        antes = tamanho_em_disco(pasta_git)
        try:
            p = _git(g.caminho, "repack", "-a", "-d", "-q", timeout=g.tempo_limite_s)
        except subprocess.TimeoutExpired:
            r.situacao, r.detalhe = ERRO, f"passou do tempo limite de {g.tempo_limite_s} s"
        else:
            depois = tamanho_em_disco(pasta_git)
            r.bytes = max(0, antes - depois)
            if p.returncode == 0:
                r.situacao = LIMPO
                r.detalhe = (f"{qtd} objetos soltos empacotados; .git foi de "
                             f"{formatar_bytes(antes)} para {formatar_bytes(depois)}")
            else:
                r.situacao = ERRO
                r.detalhe = f"git repack terminou com código {p.returncode}: {_ultima_linha(p.stderr)}"
        r.segundos = round(time.monotonic() - inicio, 2)
    return resultados


__all__ = [
    "Resultado", "LIMPO", "SIMULADO", "MANTIDO", "ERRO", "Processo", "pastas_em_uso",
    "encontrar_candidatos", "ultima_alteracao", "limpar_projetos", "executar_comandos",
    "limpar_pastas", "compactar_git", "objetos_soltos",
]
