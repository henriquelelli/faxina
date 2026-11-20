#!/usr/bin/env bash
# o que faz: encerra o trabalho e reinicia o Mac de forma limpa e sem depender de clique:
#   confere se sobrou agente vivo nos terminais, roda a faxina, para o Colima (Docker), mostra
#   um aviso com contagem de 20 segundos (que dá para cancelar), pede a cada aplicativo aberto
#   para sair, força os que não saírem em 15 segundos e reinicia.
# por que existe: com 26 dias ligado e o swap cheio, o macOS pausou o Terminal no meio de uma
#   noite de orquestração (20/11/2026). O reinício zera a memória. A primeira versão só pedia o
#   reinício ao macOS, e um aplicativo com aviso de "ainda trabalhando" o cancelava.
# como usar: ~/Work/faxina/scripts/encerrar-mac.sh [--simular]
#   --simular mostra cada passo sem executar nada.
#   A única confirmação é o aviso com contagem: sem clique, ele segue sozinho.
#   Com agente vivo nos terminais, pergunta; rodando sem terminal, aborta.
#   ATENÇÃO: aplicativo que não sair em 15 segundos é encerrado à força e perde o que não salvou.
#   O andamento da fase final fica em ~/Library/Logs/encerrar-mac.log.
# dependências: bash, osascript, perl (para desligar o script do terminal que o chamou),
#   colima (opcional), python3.12 ou python3.11 para a faxina.
#
# autoria:
# - criado   | sessão 53afb314-d0d7-4209-9f37-49eaf4eac130 | 2026-11-20T13:10:00-03:00 | Claude Code | claude-fable-5-2
# - alterado | sessão 53afb314-d0d7-4209-9f37-49eaf4eac130 | 2026-11-20T13:25:00-03:00 | Claude Code | claude-fable-5-2
# - alterado | sessão 53afb314-d0d7-4209-9f37-49eaf4eac130 | 2026-11-20T13:35:00-03:00 | Claude Code | claude-fable-5-2

set -u
MODO="${1:-}"
LOG="$HOME/Library/Logs/encerrar-mac.log"

# Uma linha "pid identificador" por aplicativo com janela, menos o Finder (o macOS o fecha sozinho).
aplicativos_abertos() {
  osascript -e 'tell application "System Events"
    set saida to ""
    repeat with p in (processes whose background only is false)
      try
        set saida to saida & (unix id of p) & " " & (bundle identifier of p) & linefeed
      end try
    end repeat
    return saida
  end tell' | grep -v ' com.apple.finder$' | grep .
}

# Fase final: roda desligada do terminal, porque fecha o próprio aplicativo que a chamou.
if [ "$MODO" = "--fase-final" ]; then
  exec >>"$LOG" 2>&1
  echo "== $(date '+%F %T') fase final"
  # Único ponto de confirmação: sem clique em 20 s, segue. "Cancelar" faz o osascript falhar.
  osascript -e 'tell application "System Events"
    activate
    display dialog "O Mac vai reiniciar em 20 segundos." & return & return & "Aplicativos que não fecharem sozinhos serão encerrados à força e perdem o que não foi salvo." with title "encerrar-mac" buttons {"Cancelar", "Reiniciar agora"} default button 2 cancel button 1 giving up after 20 with icon caution
  end tell' >/dev/null || { echo "cancelado no aviso"; exit; }
  apps=$(aplicativos_abertos)
  echo "$apps"
  while read -r pid id; do
    # O Claude responde ao pedido de sair com um alerta "ainda trabalhando" que trava a fila;
    # com SIGTERM ele fecha direto, e as conversas já estão gravadas em disco.
    if [ "$id" = "com.anthropic.claudefordesktop" ]; then kill -TERM "$pid"; continue; fi
    osascript -e "tell application id \"$id\" to quit" >/dev/null 2>&1 &
  done <<<"$apps"
  sleep 15
  for sinal in TERM KILL; do
    while read -r pid id; do
      kill -0 "$pid" 2>/dev/null && { echo "não saiu, kill -$sinal: $id"; kill -"$sinal" "$pid"; }
    done <<<"$apps"
    sleep 5
  done
  sync
  echo "pedindo o reinício"
  osascript -e 'tell application "System Events" to restart' ||
    osascript -e 'tell application "loginwindow" to «event aevtrrst»'
  echo "saída do pedido de reinício: $?"
  exit
fi

SIMULAR=0
[ "$MODO" = "--simular" ] && SIMULAR=1
# </dev/null: sem isso, a faxina e o colima consomem as respostas digitadas adiante
rodar() { echo "+ $*"; [ "$SIMULAR" = 1 ] || "$@" </dev/null; }

echo "== 1. Agentes vivos nos terminais"
# "claude bg-spare" é o processo reserva do daemon, não uma sessão de trabalho
agentes=$(ps -axo pid=,tty=,command= | awk '$2 ~ /^ttys/ && $3 ~ /(^|\/)(claude|codex|agy)$/ && $4 != "bg-spare"')
if [ -n "$agentes" ]; then
  echo "$agentes" | cut -c1-110
  echo "Esses agentes perdem o que não estiver salvo. Peça o checkpoint e saia com /exit."
  # sem terminal não há quem responda, então aborta: é o único passo que perde trabalho
  if [ "$SIMULAR" = 0 ]; then
    [ -t 0 ] || exit 1
    read -r -p "? Continuar mesmo assim? [s/N] " r; [ "$r" = "s" ] || exit 1
  fi
else
  echo "nenhum"
fi

echo "== 2. Faxina (antes de parar o Docker, para ela limpar o Docker também)"
PY=$(command -v python3.12 || command -v python3.11)
if [ -n "$PY" ]; then
  (cd "$(dirname "$0")/.." && rodar "$PY" -m faxina executar --forcar --nao-abrir)
else
  echo "sem python3.12 nem python3.11: faxina pulada"
fi

echo "== 3. Colima (para os contêineres de forma ordenada, para os bancos fecharem direito)"
if command -v colima >/dev/null && colima status >/dev/null 2>&1; then
  rodar colima stop
else
  echo "colima não está rodando"
fi

echo "== 4. Processos de fundo dos terminais e aplicativos pausados"
rodar pkill -f /usr/local/bin/manter-ligado
rodar pkill -x caffeinate
# SIGCONT é inofensivo para quem não está pausado; aplicativo pausado não responde ao pedido de sair
rodar pkill -CONT -u "$(id -u)" 2>/dev/null

echo "== 5. Aviso de 20 segundos, fechar os aplicativos e reiniciar"
echo "Depois de reiniciar (ou se cancelar no aviso): colima start && docker ps"
if [ "$SIMULAR" = 1 ]; then
  echo "Aplicativos que receberiam o pedido de sair:"
  aplicativos_abertos
  exit 0
fi
echo "Fase final em andamento; acompanhe em $LOG"
# setsid: a fase final precisa sobreviver ao fechamento do Terminal ou do Claude que a iniciou
perl -MPOSIX -e 'setsid(); exec @ARGV' "$0" --fase-final </dev/null >/dev/null 2>&1 &
