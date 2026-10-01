"""Leitura e validação da configuração (TOML).

Toda decisão da faxina vem daqui: nada é apagado se não estiver descrito na
configuração. Chaves desconhecidas são rejeitadas para que um erro de digitação
não vire uma regra silenciosamente ignorada.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CAMINHO_PADRAO = "~/.config/faxina/config.toml"

NAO_ENTRAR_PADRAO = (".git", ".venv", "venv", "__pycache__", ".Trash")


class ConfigInvalida(Exception):
    pass


def expandir(valor: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(valor))).absolute()


def caminho_da_config(informado: str | None) -> Path:
    return expandir(informado or os.environ.get("FAXINA_CONFIG") or CAMINHO_PADRAO)


@dataclass(frozen=True)
class Regeneravel:
    nome: str
    pasta: str
    exige_um_destes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Projetos:
    ativo: bool = False
    raizes: tuple[Path, ...] = ()
    dias_sem_uso: int = 14
    profundidade_maxima: int = 6
    ignorar: tuple[str, ...] = ()
    nao_entrar: tuple[str, ...] = NAO_ENTRAR_PADRAO
    regeneraveis: tuple[Regeneravel, ...] = ()


@dataclass(frozen=True)
class Pasta:
    nome: str
    caminho: Path
    idade_minima_dias: int = 0


@dataclass(frozen=True)
class Comando:
    nome: str
    comando: tuple[str, ...]
    medir: tuple[Path, ...] = ()
    condicao: tuple[str, ...] = ()
    tempo_limite_s: int = 900


@dataclass(frozen=True)
class RepoGit:
    nome: str
    caminho: Path
    min_objetos_soltos: int = 5000
    tempo_limite_s: int = 3600


@dataclass(frozen=True)
class Config:
    origem: Path
    intervalo_dias: int = 15
    abrir_relatorio: bool = True
    notificar: bool = True
    relatorios_dir: Path = field(default_factory=lambda: expandir("~/.local/share/faxina/relatorios"))
    estado_dir: Path = field(default_factory=lambda: expandir("~/.local/state/faxina"))
    projetos: Projetos = Projetos()
    pastas: tuple[Pasta, ...] = ()
    comandos: tuple[Comando, ...] = ()
    git: tuple[RepoGit, ...] = ()


# --- leitura tipada -------------------------------------------------------


def _conferir_chaves(tabela: dict, permitidas: set[str], contexto: str) -> None:
    sobrando = sorted(set(tabela) - permitidas)
    if sobrando:
        raise ConfigInvalida(f"{contexto}: chave desconhecida {', '.join(sobrando)}")


def _texto(tabela: dict, chave: str, contexto: str, padrao: str | None = None) -> str:
    valor = tabela.get(chave, padrao)
    if not isinstance(valor, str) or not valor.strip():
        raise ConfigInvalida(f"{contexto}: '{chave}' precisa ser um texto não vazio")
    return valor


def _inteiro(tabela: dict, chave: str, contexto: str, padrao: int) -> int:
    valor = tabela.get(chave, padrao)
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        raise ConfigInvalida(f"{contexto}: '{chave}' precisa ser um inteiro >= 0")
    return valor


def _booleano(tabela: dict, chave: str, contexto: str, padrao: bool) -> bool:
    valor = tabela.get(chave, padrao)
    if not isinstance(valor, bool):
        raise ConfigInvalida(f"{contexto}: '{chave}' precisa ser true ou false")
    return valor


def _textos(tabela: dict, chave: str, contexto: str, padrao: tuple[str, ...] = ()) -> tuple[str, ...]:
    valor = tabela.get(chave, list(padrao))
    if not isinstance(valor, list) or not all(isinstance(v, str) and v for v in valor):
        raise ConfigInvalida(f"{contexto}: '{chave}' precisa ser uma lista de textos")
    return tuple(valor)


def _tabelas(dados: dict, chave: str) -> list[dict]:
    valor = dados.get(chave, [])
    if not isinstance(valor, list) or not all(isinstance(v, dict) for v in valor):
        raise ConfigInvalida(f"'{chave}' precisa ser uma lista de tabelas ([[{chave}]])")
    return valor


def _exigir_pasta_segura(caminho: Path, contexto: str) -> None:
    """Pastas esvaziadas pela faxina precisam estar dentro da pasta pessoal e a
    pelo menos dois níveis dela (ex.: ~/Library/Caches/X), nunca a própria casa
    nem uma pasta de primeiro nível como ~/Documents."""
    casa = Path(os.path.realpath(Path.home()))
    real = Path(os.path.realpath(caminho))
    if not real.is_relative_to(casa) or len(real.relative_to(casa).parts) < 2:
        raise ConfigInvalida(
            f"{contexto}: '{caminho}' precisa ficar dentro de {casa} e a pelo menos dois níveis dela"
        )


# --- carga ----------------------------------------------------------------


def carregar(caminho: Path) -> Config:
    if not caminho.is_file():
        raise ConfigInvalida(
            f"configuração não encontrada em {caminho}. Copie config.exemplo.toml para lá e ajuste."
        )
    with caminho.open("rb") as f:
        try:
            dados = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigInvalida(f"TOML inválido em {caminho}: {e}") from e
    return interpretar(dados, caminho)


def interpretar(dados: dict, origem: Path) -> Config:
    _conferir_chaves(
        dados,
        {"intervalo_dias", "abrir_relatorio", "notificar", "relatorios_dir", "estado_dir",
         "projetos", "pastas", "comandos", "git"},
        "raiz",
    )
    base = Config(origem=origem)
    intervalo = _inteiro(dados, "intervalo_dias", "raiz", base.intervalo_dias)
    if intervalo < 1:
        raise ConfigInvalida("raiz: 'intervalo_dias' precisa ser pelo menos 1")

    return Config(
        origem=origem,
        intervalo_dias=intervalo,
        abrir_relatorio=_booleano(dados, "abrir_relatorio", "raiz", base.abrir_relatorio),
        notificar=_booleano(dados, "notificar", "raiz", base.notificar),
        relatorios_dir=expandir(dados["relatorios_dir"]) if "relatorios_dir" in dados else base.relatorios_dir,
        estado_dir=expandir(dados["estado_dir"]) if "estado_dir" in dados else base.estado_dir,
        projetos=_projetos(dados.get("projetos")),
        pastas=tuple(_pasta(t, i) for i, t in enumerate(_tabelas(dados, "pastas"), 1)),
        comandos=tuple(_comando(t, i) for i, t in enumerate(_tabelas(dados, "comandos"), 1)),
        git=tuple(_repo(t, i) for i, t in enumerate(_tabelas(dados, "git"), 1)),
    )


def _projetos(t: dict | None) -> Projetos:
    if t is None:
        return Projetos()
    if not isinstance(t, dict):
        raise ConfigInvalida("'projetos' precisa ser uma tabela ([projetos])")
    ctx = "[projetos]"
    _conferir_chaves(
        t,
        {"ativo", "raizes", "dias_sem_uso", "profundidade_maxima", "ignorar", "nao_entrar", "regeneraveis"},
        ctx,
    )
    regras = []
    for i, r in enumerate(_tabelas(t, "regeneraveis"), 1):
        c = f"[[projetos.regeneraveis]] nº {i}"
        _conferir_chaves(r, {"nome", "pasta", "exige_um_destes"}, c)
        pasta = _texto(r, "pasta", c)
        if "/" in pasta or pasta in {".", ".."}:
            raise ConfigInvalida(f"{c}: 'pasta' é só o nome da pasta (ex.: node_modules), sem barras")
        regras.append(Regeneravel(_texto(r, "nome", c), pasta, _textos(r, "exige_um_destes", c)))
    raizes = tuple(expandir(r) for r in _textos(t, "raizes", ctx))
    for r in raizes:
        if r == Path(r.anchor):
            raise ConfigInvalida(f"{ctx}: a raiz do sistema não pode ser usada em 'raizes'")
    return Projetos(
        ativo=_booleano(t, "ativo", ctx, True),
        raizes=raizes,
        dias_sem_uso=_inteiro(t, "dias_sem_uso", ctx, 14),
        profundidade_maxima=_inteiro(t, "profundidade_maxima", ctx, 6),
        ignorar=tuple(os.path.expanduser(p) for p in _textos(t, "ignorar", ctx)),
        nao_entrar=_textos(t, "nao_entrar", ctx, NAO_ENTRAR_PADRAO),
        regeneraveis=tuple(regras),
    )


def _pasta(t: dict, i: int) -> Pasta:
    c = f"[[pastas]] nº {i}"
    _conferir_chaves(t, {"nome", "caminho", "idade_minima_dias"}, c)
    caminho = expandir(_texto(t, "caminho", c))
    _exigir_pasta_segura(caminho, c)
    return Pasta(_texto(t, "nome", c), caminho, _inteiro(t, "idade_minima_dias", c, 0))


def _comando(t: dict, i: int) -> Comando:
    c = f"[[comandos]] nº {i}"
    _conferir_chaves(t, {"nome", "comando", "medir", "condicao", "tempo_limite_s"}, c)
    comando = _textos(t, "comando", c)
    if not comando:
        raise ConfigInvalida(f"{c}: 'comando' não pode ser vazio")
    return Comando(
        nome=_texto(t, "nome", c),
        comando=comando,
        medir=tuple(expandir(m) for m in _textos(t, "medir", c)),
        condicao=_textos(t, "condicao", c),
        tempo_limite_s=_inteiro(t, "tempo_limite_s", c, 900),
    )


def _repo(t: dict, i: int) -> RepoGit:
    c = f"[[git]] nº {i}"
    _conferir_chaves(t, {"nome", "caminho", "min_objetos_soltos", "tempo_limite_s"}, c)
    return RepoGit(
        nome=_texto(t, "nome", c),
        caminho=expandir(_texto(t, "caminho", c)),
        min_objetos_soltos=_inteiro(t, "min_objetos_soltos", c, 5000),
        tempo_limite_s=_inteiro(t, "tempo_limite_s", c, 3600),
    )
