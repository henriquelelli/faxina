"""Relatório HTML da faxina.

`renderizar` é uma função pura: recebe o registro da execução (o mesmo dicionário
gravado em JSON) e devolve sempre o mesmo HTML para o mesmo registro. Datas e
números são formatados aqui, sem depender do idioma do sistema nem do relógio.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path

from .disco import abreviar, formatar_bytes
from .tarefas import ERRO, LIMPO, MANTIDO, SIMULADO

MODELO = Path(__file__).with_name("modelo.html")

MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro")

GRUPOS = (
    ("projetos", "Projetos parados"),
    ("comandos", "Caches de ferramentas"),
    ("pastas", "Pastas de cache"),
    ("git", "Repositórios git compactados"),
)

e = html.escape


# --- formatação ------------------------------------------------------------


def _data(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def data_longa(iso: str) -> str:
    d = _data(iso)
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def data_curta(iso: str) -> str:
    return _data(iso).strftime("%d/%m/%Y")


def hora(iso: str) -> str:
    return _data(iso).strftime("%H:%M")


def duracao(segundos: float) -> str:
    s = int(round(segundos))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s} s" if s else f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


def total_liberado(resultados: list[dict]) -> int:
    """Bytes efetivamente liberados (tarefas limpas ou com erro parcial)."""
    return sum(r["bytes"] for r in resultados if r["situacao"] in (LIMPO, ERRO))


def somas(resultados: list[dict]) -> tuple[int, int]:
    """(certo, teto): `certo` soma o que foi ou seria apagado com tamanho medido;
    `teto` soma as estimativas dos comandos simulados, que são um limite máximo."""
    feitos = [r for r in resultados if r["situacao"] in (LIMPO, SIMULADO, ERRO)]
    certo = sum(r["bytes"] for r in feitos if not r.get("estimado"))
    teto = sum(r["bytes"] for r in feitos if r.get("estimado"))
    return certo, teto


def _pct(parte: int, todo: int) -> float:
    return 0.0 if todo <= 0 else max(0.0, min(100.0, parte * 100 / todo))


# --- blocos ----------------------------------------------------------------


def _valor(r: dict) -> str:
    if r["situacao"] == SIMULADO and not r["bytes"] and not r.get("estimado"):
        return "—"
    texto = formatar_bytes(r["bytes"])
    if r.get("estimado"):
        return f'<span class="estimado">até</span> {e(texto)}'
    return e(texto)


def _linha(r: dict, com_valor: bool = True) -> str:
    if r["tarefa"] == "projetos":
        nome = Path(r["alvo"]).parent.name or r["alvo"]
        caminho = abreviar(r["alvo"])
        detalhe = f'{r["nome"]}: {r["detalhe"]}' if r["detalhe"] else r["nome"]
    else:
        nome = r["nome"]
        caminho = r["alvo"] if r["tarefa"] == "comandos" else abreviar(r["alvo"])
        detalhe = r["detalhe"]
    topo = f'<span class="linha-nome">{e(nome)}</span>'
    if com_valor:
        topo += f'<span class="pontos"></span><span class="valor">{_valor(r)}</span>'
    partes = [f'<li class="linha"><div class="regua">{topo}</div>',
              f'<p class="linha-caminho">{e(caminho)}</p>']
    if detalhe:
        partes.append(f'<p class="linha-detalhe">{e(detalhe)}</p>')
    partes.append("</li>")
    return "".join(partes)


def _grupos(feitos: list[dict], simulacao: bool) -> str:
    blocos = []
    for chave, titulo in GRUPOS:
        linhas = [r for r in feitos if r["tarefa"] == chave]
        if not linhas:
            continue
        subtotal = formatar_bytes(sum(r["bytes"] for r in linhas))
        ate = '<span class="estimado">até</span> ' if any(r.get("estimado") for r in linhas) else ""
        blocos.append(
            f'<div class="grupo"><h3 class="grupo-titulo regua"><span>{e(titulo)}</span>'
            f'<span class="pontos"></span><span class="valor">{ate}{e(subtotal)}</span></h3>'
            f'<ul class="linhas">{"".join(_linha(r) for r in linhas)}</ul></div>'
        )
    if not blocos:
        texto = ("Nada seria limpo agora." if simulacao else "Nada foi limpo desta vez.")
        return f'<p class="vazio">{texto} O que ficou de fora, e por quê, está mais abaixo.</p>'
    certo, teto = somas(feitos)
    blocos.append(
        f'<div class="total regua"><span>Total</span><span class="pontos"></span>'
        f'<span class="valor">{e(formatar_bytes(certo))}</span></div>'
    )
    if teto:
        blocos.append(f'<p class="total-nota">Fora do total: os comandos podem liberar até mais '
                      f'{e(formatar_bytes(teto))}. Esse valor é o tamanho atual do que eles limpam, '
                      f'não uma medida.</p>')
    return "".join(blocos)


def _barra(reg: dict, liberado_tarefas: int, simulacao: bool) -> str:
    """Barra do espaço livre: o que já estava livre mais o que a faxina liberou.
    Mostrar o disco inteiro esconderia o ganho, quase sempre pequeno perto dele."""
    total = reg["disco_total"]
    antes, depois = reg["livre_antes"], reg["livre_depois"]
    if simulacao:
        livre, liberado = depois, liberado_tarefas
        rotulos = ("Livre agora", "Pode ser liberado")
        resumo = (f"Espaço livre: {formatar_bytes(livre)} agora e {formatar_bytes(livre + liberado)} "
                  f"depois da faxina, num disco de {formatar_bytes(total)}.")
    else:
        livre, liberado = min(antes, depois), max(0, depois - antes)
        rotulos = ("Já estava livre", "Liberado nesta faxina")
        resumo = (f"Espaço livre: {formatar_bytes(antes)} antes e {formatar_bytes(depois)} depois, "
                  f"num disco de {formatar_bytes(total)}.")
    soma = livre + liberado
    p_lib = _pct(liberado, soma)
    if liberado > 0:
        p_lib = max(p_lib, 1.5)  # sempre visível, mesmo quando é pouco
    previsto = " previsto" if simulacao else ""
    return (
        f'<div class="barra" role="img" aria-label="{e(resumo)}">'
        f'<span class="seg antes" style="--w:{100 - p_lib:.2f}%"></span>'
        f'<span class="seg liberado{previsto}" style="--w:{p_lib:.2f}%"></span></div>'
        f'<ul class="legenda">'
        f'<li><span class="amostra antes"></span>{e(rotulos[0])} <b>{e(formatar_bytes(livre))}</b></li>'
        f'<li><span class="amostra liberado"></span>{e(rotulos[1])} <b>{e(formatar_bytes(liberado))}</b></li>'
        f"</ul>"
        f'<p class="barra-resumo">{e(resumo)}</p>'
    )


def _erros(erros: list[dict]) -> str:
    if not erros:
        return ""
    return (
        '<section class="erros" aria-labelledby="titulo-erros">'
        '<h2 id="titulo-erros">Precisa de atenção</h2>'
        f'<ul class="linhas">{"".join(_linha(r) for r in erros)}</ul></section>'
    )


def _mantidos(mantidos: list[dict]) -> str:
    if not mantidos:
        return ""
    linhas = [r for chave, _ in GRUPOS for r in mantidos if r["tarefa"] == chave]
    return (
        f'<details><summary>Mantido de propósito ({len(linhas)})</summary>'
        f'<ul class="linhas">{"".join(_linha(r, com_valor=False) for r in linhas)}</ul></details>'
    )


def _historico(itens: list[dict], simulacao: bool) -> str:
    if not itens:
        texto = ("Nenhuma faxina de verdade foi registrada ainda." if simulacao
                 else "Esta é a primeira faxina registrada.")
        return f'<p class="vazio">{texto}</p>'
    maior = max(i["bytes"] for i in itens) or 1
    linhas = []
    for i in itens:
        classe = ' class="atual"' if i.get("atual") else ""
        linhas.append(
            f'<li{classe}><span class="data">{e(data_curta(i["inicio"]))}</span>'
            f'<span class="trilho"><span style="--w:{_pct(i["bytes"], maior):.2f}%"></span></span>'
            f'<span class="valor">{e(formatar_bytes(i["bytes"]))}</span></li>'
        )
    return f'<ul class="historico">{"".join(linhas)}</ul>'


def _regras(reg: dict) -> str:
    r = reg["regras"]
    itens = [("Intervalo", f'a cada {r["intervalo_dias"]} dias')]
    if r.get("raizes"):
        itens.append(("Projetos", f'sem alterações há {r["dias_sem_uso"]} dias ou mais, em {", ".join(r["raizes"])}'))
    if r.get("regeneraveis"):
        itens.append(("Pastas regeneráveis", ", ".join(r["regeneraveis"])))
    itens.append(("Configuração", r["config"]))
    itens.append(("Versão", f'faxina {reg["versao"]}'))
    return '<dl class="regras">' + "".join(f"<dt>{e(a)}</dt><dd>{e(b)}</dd>" for a, b in itens) + "</dl>"


# --- página ----------------------------------------------------------------


def preencher(modelo: str, campos: dict[str, str]) -> str:
    """Troca cada {{chave}} pelo valor. Exige que modelo e campos tenham
    exatamente as mesmas chaves, para nada sair em branco sem aviso."""
    chaves = set(re.findall(r"\{\{(\w+)\}\}", modelo))
    if chaves != set(campos):
        faltam, sobram = sorted(chaves - set(campos)), sorted(set(campos) - chaves)
        raise ValueError(f"modelo e dados não batem; faltam {faltam}, sobram {sobram}")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: campos[m.group(1)], modelo)


def renderizar(reg: dict, modelo: str | None = None) -> str:
    simulacao = reg["modo"] == "simulacao"
    resultados = reg["resultados"]
    feitos = [r for r in resultados if r["situacao"] in (LIMPO, SIMULADO)]
    erros = [r for r in resultados if r["situacao"] == ERRO]
    mantidos = [r for r in resultados if r["situacao"] == MANTIDO]

    liberado, teto = somas(resultados)
    dia = data_longa(reg["inicio"])

    contexto = f'Começou às {hora(reg["inicio"])} e levou {duracao(reg["segundos"])}.'
    if reg.get("proxima"):
        contexto += f' A próxima pode rodar a partir de {data_longa(reg["proxima"])}.'

    if simulacao:
        legenda = "seriam liberados com certeza."
        if teto:
            legenda += f" Os comandos de limpeza podem liberar até mais {formatar_bytes(teto)}."
        aviso = ('<p class="aviso">Nada foi apagado. Esta página mostra o que a faxina faria agora. '
                 'Nos itens marcados com “até”, o valor é o tamanho atual do que o comando limpa.</p>')
    else:
        ganho = reg["livre_depois"] - reg["livre_antes"]
        legenda = (f"liberados nesta faxina. O disco registrou "
                   f"{formatar_bytes(max(0, ganho))} a mais de espaço livre no mesmo período.")
        aviso = ""

    campos = {
        "titulo_pagina": e(f'{"Simulação" if simulacao else "Faxina"} de {dia}'),
        "titulo": e(f'{"Simulação" if simulacao else "Faxina"} de {dia}'),
        "contexto": e(contexto),
        "aviso": aviso,
        "numero": e(formatar_bytes(liberado)),
        "numero_legenda": e(legenda),
        "barra": _barra(reg, liberado, simulacao),
        "titulo_itens": "O que seria limpo" if simulacao else "O que foi limpo",
        "grupos": _grupos(feitos, simulacao),
        "erros": _erros(erros),
        "mantidos": _mantidos(mantidos),
        "historico": _historico(reg.get("historico", []), simulacao),
        "regras": _regras(reg),
        "dados": json.dumps(reg, ensure_ascii=False, sort_keys=True).replace("</", "<\\/"),
    }
    return preencher(modelo if modelo is not None else MODELO.read_text(encoding="utf-8"), campos)


# --- arquivos --------------------------------------------------------------


def nome_base(reg: dict) -> str:
    sufixo = "_simulacao" if reg["modo"] == "simulacao" else ""
    return _data(reg["inicio"]).strftime("%Y-%m-%d_%H%M%S") + sufixo


def salvar(reg: dict, pasta: Path) -> tuple[Path, Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    base = nome_base(reg)
    caminho_json = pasta / f"{base}.json"
    caminho_html = pasta / f"{base}.html"
    caminho_json.write_text(json.dumps(reg, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pagina = renderizar(reg)
    caminho_html.write_text(pagina, encoding="utf-8")
    (pasta / "ultimo.html").write_text(pagina, encoding="utf-8")
    return caminho_html, caminho_json


def historico(pasta: Path, limite: int = 12) -> list[dict]:
    """Faxinas de verdade já registradas (simulações ficam de fora), da mais
    antiga para a mais nova."""
    itens = []
    for arquivo in sorted(pasta.glob("*.json")) if pasta.is_dir() else []:
        try:
            reg = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if reg.get("modo") != "execucao":
            continue
        itens.append({"inicio": reg["inicio"], "bytes": total_liberado(reg.get("resultados", []))})
    itens.sort(key=lambda i: i["inicio"])
    return itens[-limite:]
