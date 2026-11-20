# Automações do faxina

Limpeza periódica e determinística de espaço em disco no macOS, com relatório em HTML (HyperText Markup Language). O uso da faxina em si está no `README.md`.

## Automações

### scripts: scripts/encerrar-mac.sh

- **O que faz:** encerra o trabalho e reinicia o Mac de forma limpa, sem depender de clique. Avisa se sobrou agente vivo nos terminais, roda a faxina, para o Colima, encerra o `manter-ligado` e o `caffeinate`, mostra um aviso com contagem de 20 segundos (dá para cancelar; sem clique, segue), pede a cada aplicativo aberto para sair, força os que não saírem em 15 segundos e reinicia. O Claude é encerrado direto, porque responde ao pedido de sair com um alerta que trava a fila. Aplicativo forçado perde o que não salvou.
- **Por que existe:** em 20/11/2026, com 26 dias ligado e o swap cheio, o macOS pausou o Terminal no meio de uma noite de orquestração. O reinício zera a memória; o script faz a parte repetível dele sem perder trabalho.
- **Dependências:** `bash`, `osascript`, `perl`, `colima` (opcional), `python3.12` ou `python3.11` para a faxina.
- **Como usar:** `~/Work/faxina/scripts/encerrar-mac.sh`; com `--simular` só mostra os passos. Com agente vivo nos terminais ele pergunta; rodando sem terminal, aborta. O andamento da fase final fica em `~/Library/Logs/encerrar-mac.log`. Ele não pede checkpoint aos agentes: isso vem antes, em cada sessão.
