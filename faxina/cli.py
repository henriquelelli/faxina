"""Linha de comando: executar, simular, instalar, desinstalar, status e relatorio."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__, agenda, relatorio
from .config import Config, ConfigInvalida, caminho_da_config, carregar
from .disco import abreviar, espaco_do_disco, formatar_bytes
from .tarefas import (
    ERRO,
    compactar_git,
    executar_comandos,
    limpar_pastas,
    limpar_projetos,
    pastas_em_uso,
)


def _agora() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def _notificar(titulo: str, texto: str) -> None:
    script = f"display notification {json.dumps(texto)} with title {json.dumps(titulo)}"
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _proxima(cfg: Config, estado: dict) -> datetime | None:
    ultima = estado.get("ultima_execucao")
    if not ultima:
        return None
    return datetime.fromisoformat(ultima) + timedelta(days=cfg.intervalo_dias)


def faxinar(cfg: Config, simular: bool) -> dict:
    """Roda as tarefas na ordem fixa e devolve o registro da execução."""
    inicio = _agora()
    agora = time.time()
    t0 = time.monotonic()
    total, livre_antes = espaco_do_disco()
    ambiente = dict(os.environ)
    processos = pastas_em_uso() if cfg.projetos.ativo else []

    resultados = []
    resultados += executar_comandos(cfg.comandos, simular, ambiente)
    resultados += limpar_pastas(cfg.pastas, simular, agora)
    resultados += limpar_projetos(cfg.projetos, simular, agora, processos)
    resultados += compactar_git(cfg.git, simular)

    _, livre_depois = espaco_do_disco()
    return {
        "versao": __version__,
        "modo": "simulacao" if simular else "execucao",
        "inicio": inicio.isoformat(),
        "segundos": round(time.monotonic() - t0, 1),
        "proxima": None if simular else (inicio + timedelta(days=cfg.intervalo_dias)).isoformat(),
        "disco_total": total,
        "livre_antes": livre_antes,
        "livre_depois": livre_depois,
        "regras": {
            "intervalo_dias": cfg.intervalo_dias,
            "dias_sem_uso": cfg.projetos.dias_sem_uso,
            "raizes": [abreviar(r) for r in cfg.projetos.raizes] if cfg.projetos.ativo else [],
            "regeneraveis": [r.pasta for r in cfg.projetos.regeneraveis] if cfg.projetos.ativo else [],
            "config": abreviar(cfg.origem),
        },
        "resultados": [asdict(r) for r in resultados],
    }


def cmd_executar(args) -> int:
    cfg = carregar(caminho_da_config(args.config))
    with agenda.trava(cfg.estado_dir) as conseguiu:
        if not conseguiu:
            print("Já existe uma faxina em andamento.")
            return 0
        estado = agenda.ler_estado(cfg.estado_dir)
        proxima = _proxima(cfg, estado)
        if not args.simular and not args.forcar and proxima and _agora() < proxima:
            print(f"A última faxina foi em {relatorio.data_longa(estado['ultima_execucao'])}. "
                  f"A próxima pode rodar a partir de {relatorio.data_longa(proxima.isoformat())}. "
                  "Use --forcar para rodar agora.")
            return 0

        registro = faxinar(cfg, args.simular)
        historico = relatorio.historico(cfg.relatorios_dir)
        if not args.simular:
            historico = (historico + [{"inicio": registro["inicio"],
                                       "bytes": relatorio.total_liberado(registro["resultados"]),
                                       "atual": True}])[-12:]
        registro["historico"] = historico
        pagina, _ = relatorio.salvar(registro, cfg.relatorios_dir)
        if not args.simular:
            agenda.gravar_estado(cfg.estado_dir, {"ultima_execucao": registro["inicio"],
                                                  "ultimo_relatorio": os.fspath(pagina)})

    resultados = registro["resultados"]
    erros = sum(1 for r in resultados if r["situacao"] == ERRO)
    certo, teto = relatorio.somas(resultados)
    if args.simular:
        resumo = f"{formatar_bytes(certo)} seriam liberados com certeza"
        if teto:
            resumo += f", e os comandos podem liberar até mais {formatar_bytes(teto)}"
    else:
        resumo = f"{formatar_bytes(certo)} liberados"
    if erros:
        resumo += f"; {erros} item(ns) precisam de atenção"
    print(f"{resumo}.\nRelatório: {pagina}")

    if not args.nao_abrir:
        if cfg.abrir_relatorio:
            subprocess.run(["open", os.fspath(pagina)], capture_output=True)
        if cfg.notificar and not args.simular:
            _notificar("Faxina concluída", resumo + ".")
    return 1 if erros else 0


def cmd_instalar(args) -> int:
    caminho = caminho_da_config(args.config)
    cfg = carregar(caminho)
    destino = agenda.instalar(caminho, cfg.estado_dir, args.hora, args.minuto, args.python)
    print(f"Agendado: todo dia às {args.hora:02d}:{args.minuto:02d} a faxina confere se já passaram "
          f"{cfg.intervalo_dias} dias desde a última e, se sim, roda.\nArquivo do launchd: {destino}")
    return 0


def cmd_desinstalar(args) -> int:
    removido = agenda.desinstalar()
    print("Agendamento removido." if removido else "Não havia agendamento instalado.")
    return 0


def cmd_status(args) -> int:
    caminho = caminho_da_config(args.config)
    cfg = carregar(caminho)
    estado = agenda.ler_estado(cfg.estado_dir)
    proxima = _proxima(cfg, estado)
    print(f"Configuração: {caminho}")
    print(f"Agendamento: {'ativo' if agenda.agendado() else 'não instalado'}")
    if estado.get("ultima_execucao"):
        print(f"Última faxina: {relatorio.data_longa(estado['ultima_execucao'])} "
              f"às {relatorio.hora(estado['ultima_execucao'])}")
        print(f"Próxima a partir de: {relatorio.data_longa(proxima.isoformat())}")
        print(f"Último relatório: {estado.get('ultimo_relatorio', '-')}")
    else:
        print("Nenhuma faxina registrada ainda; a próxima chamada do agendamento roda.")
    return 0


def cmd_relatorio(args) -> int:
    """Gera de novo o HTML a partir do JSON de uma execução."""
    origem = Path(args.json).expanduser()
    registro = json.loads(origem.read_text(encoding="utf-8"))
    destino = origem.with_suffix(".html")
    destino.write_text(relatorio.renderizar(registro), encoding="utf-8")
    print(destino)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="faxina", description="Limpeza periódica e determinística de espaço em disco.")
    p.add_argument("--version", action="version", version=f"faxina {__version__}")
    sub = p.add_subparsers(dest="comando", required=True)

    ex = sub.add_parser("executar", help="roda a faxina (respeita o intervalo, a não ser com --forcar)")
    ex.add_argument("--config")
    ex.add_argument("--simular", action="store_true", help="não apaga nada; só mostra o que faria")
    ex.add_argument("--forcar", action="store_true", help="ignora o intervalo desde a última faxina")
    ex.add_argument("--nao-abrir", action="store_true", help="não abre o relatório nem notifica")
    ex.set_defaults(funcao=cmd_executar)

    ins = sub.add_parser("instalar", help="agenda no launchd")
    ins.add_argument("--config")
    ins.add_argument("--hora", type=int, default=11, choices=range(24), metavar="0-23")
    ins.add_argument("--minuto", type=int, default=0, choices=range(60), metavar="0-59")
    ins.add_argument("--python", help="interpretador a usar (padrão: o atual)")
    ins.set_defaults(funcao=cmd_instalar)

    sub.add_parser("desinstalar", help="remove o agendamento").set_defaults(funcao=cmd_desinstalar)

    st = sub.add_parser("status", help="mostra a última e a próxima faxina")
    st.add_argument("--config")
    st.set_defaults(funcao=cmd_status)

    rel = sub.add_parser("relatorio", help="gera de novo o HTML a partir do JSON de uma execução")
    rel.add_argument("json")
    rel.set_defaults(funcao=cmd_relatorio)

    args = p.parse_args(argv)
    try:
        return args.funcao(args)
    except ConfigInvalida as erro:
        print(f"Erro na configuração: {erro}", file=sys.stderr)
        return 2
