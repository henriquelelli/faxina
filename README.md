# faxina

Limpeza periódica e determinística de espaço em disco no macOS. Ela apaga apenas o que pode ser reconstruído, mede quanto cada tarefa liberou e, ao fim, gera um relatório em HTML com o que foi feito, o que ficou de fora e o motivo.

Usa só a biblioteca padrão do Python 3.11+. Não tem dependências.

## O que ela limpa

Tudo vem do arquivo de configuração. O que não está descrito lá não é tocado.

| Tarefa | O que faz |
|---|---|
| Projetos parados | Apaga pastas regeneráveis (`node_modules`, `.next`, `.venv`…) de projetos sem nenhum arquivo alterado há N dias |
| Caches de ferramentas | Roda os comandos de limpeza das próprias ferramentas (`brew cleanup`, `npm cache clean`, `docker image prune`…) |
| Pastas de cache | Esvazia pastas de cache, opcionalmente só o que tem mais de N dias |
| Repositórios git | Junta objetos soltos num pacote com `git repack -a -d`, sem descartar nenhum objeto |

## Como ela decide

- **Determinística.** Mesma configuração, mesmo disco e mesmo momento levam às mesmas decisões. Os candidatos são percorridos em ordem alfabética, e as regras são fixas e ficam visíveis no relatório.
- **Projeto parado** é aquele sem nenhum arquivo modificado há `dias_sem_uso` dias. Os arquivos dentro das próprias pastas regeneráveis não contam.
- **Nunca apaga:**
  - uma pasta regenerável sem o arquivo que permite reconstruí-la (por exemplo, `node_modules` sem lockfile). O lockfile pode estar numa pasta acima, como em monorepos;
  - um projeto que algum processo está usando como pasta de trabalho (consultado via `lsof`). Se o `lsof` falhar, nenhum projeto é apagado;
  - links simbólicos, nem nada fora das raízes e pastas configuradas;
  - pastas de cache fora da sua pasta pessoal ou a menos de dois níveis dela.
- **Comandos** são pulados se o programa não estiver instalado ou se a `condicao` configurada falhar (por exemplo, `docker info` com o Docker parado).

## Instalação

```sh
git clone https://github.com/henriquelelli/faxina.git
cd faxina
mkdir -p ~/.config/faxina
cp config.exemplo.toml ~/.config/faxina/config.toml   # ajuste as raízes e o que limpar

python3 -m faxina executar --simular   # mostra o que faria, sem apagar nada
python3 -m faxina instalar --hora 11   # agenda no launchd
```

## Uso

| Comando | O que faz |
|---|---|
| `python3 -m faxina executar --simular` | Gera o relatório do que seria limpo, sem apagar nada |
| `python3 -m faxina executar` | Roda a faxina se já passou o intervalo desde a última |
| `python3 -m faxina executar --forcar` | Roda agora, ignorando o intervalo |
| `python3 -m faxina instalar [--hora H] [--minuto M]` | Agenda no launchd |
| `python3 -m faxina desinstalar` | Remove o agendamento |
| `python3 -m faxina status` | Mostra a última e a próxima faxina |
| `python3 -m faxina relatorio ARQUIVO.json` | Gera de novo o HTML a partir do registro de uma execução |

A configuração é lida de `~/.config/faxina/config.toml`, de `$FAXINA_CONFIG` ou de `--config`.

## Configuração

O [`config.exemplo.toml`](config.exemplo.toml) documenta cada opção. Em resumo:

```toml
intervalo_dias = 15

[projetos]
raizes = ["~/Projetos"]
dias_sem_uso = 14

[[projetos.regeneraveis]]
nome = "Dependências Node"
pasta = "node_modules"
exige_um_destes = ["package-lock.json", "pnpm-lock.yaml", "yarn.lock"]

[[comandos]]
nome = "Homebrew"
comando = ["brew", "cleanup", "--prune=all"]
medir = ["~/Library/Caches/Homebrew"]

[[pastas]]
nome = "Atualizações baixadas do VS Code"
caminho = "~/Library/Caches/com.microsoft.VSCode.ShipIt"
idade_minima_dias = 7

[[git]]
nome = "Repositório com commits automáticos"
caminho = "~/caminho/do/repositorio"
min_objetos_soltos = 5000
```

Chaves desconhecidas dão erro, para que um erro de digitação não vire uma regra ignorada sem aviso.

## Relatório

Cada execução grava dois arquivos em `~/.local/share/faxina/relatorios/`:

- `AAAA-MM-DD_HHMMSS.json`: o registro completo, com cada decisão, tamanho e motivo;
- `AAAA-MM-DD_HHMMSS.html`: o relatório (o mais recente também fica em `ultimo.html`).

O HTML sai de um modelo fixo ([`faxina/modelo.html`](faxina/modelo.html)) preenchido a partir do JSON. Não usa JavaScript nem recursos externos. O mesmo registro gera sempre o mesmo HTML, byte a byte, e por isso o comando `relatorio` consegue refazer qualquer página antiga.

Os tamanhos são os blocos realmente ocupados em disco (`st_blocks`), em unidades decimais como no Finder. Na simulação, o valor dos comandos é um teto (o tamanho atual do que eles limpam) e aparece separado do total certo.

## Agendamento

`instalar` cria `~/Library/LaunchAgents/io.github.faxina.plist`. O launchd chama a faxina todo dia no horário escolhido, ou ao acordar, se o Mac estava dormindo nessa hora. A própria faxina confere se já passou `intervalo_dias` desde a última execução, e assim o intervalo não se perde com reinícios.

O launchd começa com um `PATH` mínimo. Por isso, `instalar` grava o `PATH` de quem rodou o comando, para que `brew`, `npm`, `docker` etc. sejam encontrados. Se instalar ferramentas novas em outras pastas, rode `instalar` de novo. O log fica em `~/.local/state/faxina/faxina.log`.

## Testes

```sh
python3 -m unittest discover -s tests
```
