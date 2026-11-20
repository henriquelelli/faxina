#!/usr/bin/env bash
# o que faz: encerra o trabalho e reinicia o Mac de forma limpa: confere se sobrou agente vivo
#   nos terminais, roda a faxina, para o Colima (Docker), encerra os processos que seguram o
#   Terminal aberto e pede confirmação antes de reiniciar.
# por que existe: com 26 dias ligado e o swap cheio, o macOS pausou o Terminal no meio de uma
#   noite de orquestração (20/11/2026). O reinício zera a memória; este script faz a parte
#   repetível dele sem perder trabalho.
# como usar: ~/Work/faxina/scripts/encerrar-mac.sh [--simular]
#   --simular mostra cada passo sem executar nada.
# dependências: bash, osascript, colima (opcional), python3.12 ou python3.11 para a faxina.
#
# autoria:
# - criado | sessão 53afb314-d0d7-4209-9f37-49eaf4eac130 | 2026-11-20T13:10:00-03:00 | Claude Code | claude-fable-5-2

set -u
SIMULAR=0
[ "${1:-}" = "--simular" ] && SIMULAR=1

rodar() { echo "+ $*"; [ "$SIMULAR" = 1 ] || "$@"; }
perguntar() { # em simulação a resposta é sempre não
  [ "$SIMULAR" = 1 ] && { echo "? $1 (simulação: não)"; return 1; }
  read -r -p "? $1 [s/N] " r; [ "$r" = "s" ]
}

echo "== 1. Agentes vivos nos terminais"
# "claude bg-spare" é o processo reserva do daemon, não uma sessão de trabalho
agentes=$(ps -axo pid=,tty=,command= | awk '$2 ~ /^ttys/ && $3 ~ /(^|\/)(claude|codex|agy)$/ && $4 != "bg-spare"')
if [ -n "$agentes" ]; then
  echo "$agentes" | cut -c1-110
  echo "Esses agentes perdem o que não estiver salvo. Peça o checkpoint e saia com /exit."
  perguntar "Continuar mesmo assim?" || { [ "$SIMULAR" = 1 ] || exit 1; }
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

echo "== 4. Processos que fariam o Terminal bloquear o reinício"
rodar pkill -f /usr/local/bin/manter-ligado
rodar pkill -x caffeinate
# SIGCONT é inofensivo para quem não está pausado; aplicativo pausado não responde ao reinício
rodar pkill -CONT -u "$(id -u)"

echo "== 5. Reinício"
echo "Depois de reiniciar: colima start && docker ps"
echo "Se um aplicativo travar o reinício, o macOS avisa: force o encerramento dele e reinicie de novo."
if perguntar "Reiniciar o Mac agora?"; then
  osascript -e 'tell application "System Events" to restart'
else
  echo "Reinício não feito."
fi
