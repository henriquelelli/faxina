# Automações do faxina

Limpeza periódica e determinística de espaço em disco no macOS, com relatório em HTML (HyperText Markup Language). O uso da faxina em si está no `README.md`.

## Automações

### scripts: scripts/encerrar-mac.sh

- **O que faz:** encerra o trabalho e reinicia o Mac de forma limpa. Avisa se sobrou agente vivo nos terminais, roda a faxina, para o Colima, encerra o `manter-ligado` e o `caffeinate`, retoma aplicativos pausados e pede confirmação antes de reiniciar.
- **Por que existe:** em 20/11/2026, com 26 dias ligado e o swap cheio, o macOS pausou o Terminal no meio de uma noite de orquestração. O reinício zera a memória; o script faz a parte repetível dele sem perder trabalho.
- **Dependências:** `bash`, `osascript`, `colima` (opcional), `python3.12` ou `python3.11` para a faxina.
- **Como usar:** `~/Work/faxina/scripts/encerrar-mac.sh` (ou com `--simular` para só mostrar os passos). Ele não pede checkpoint aos agentes: isso vem antes, em cada sessão.
