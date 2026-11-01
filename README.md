# IMEA Data Extractor

Extrai da API do **IMEA** (Instituto Mato-grossense de Economia Agropecuária) o avanço de plantio, colheita e comercialização de soja, milho e algodão em Mato Grosso, além das cotações, custos e estimativas que o IMEA publica. Grava CSVs e um banco SQLite, atualiza de forma incremental e pode rodar sozinho em dias úteis.

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Requer uma conta no portal do IMEA. O que a API entrega, o que não entrega e as hipóteses já testadas estão em [docs/SOURCE_NOTES.md](docs/SOURCE_NOTES.md). Leia antes de investigar lacunas ou anomalias.

## Início rápido

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencha IMEA_USERNAME, IMEA_PASSWORD e, se quiser, IMEA_DATASETS_DIR
```

O `.env` está no `.gitignore`. Nunca o envie para o repositório.

```bash
# Incremental (padrão): últimos 60 dias das séries de avanço + cotações, mesclados à base existente
python3 imea_extractor.py

# Histórico completo (a API só tem as séries de avanço a partir de 2023)
python3 imea_extractor.py --mode full

# Só cotações
python3 imea_extractor.py --mode prices-only

# Outras opções
python3 imea_extractor.py --days 90 --no-sqlite --output-dir /caminho/da/base
```

Uma execução incremental leva menos de um minuto. A primeira execução após aparecerem indicadores de preço novos leva mais, porque busca o nome de cada um (ver abaixo).

## O que é gerado

Tudo vai para `IMEA_DATASETS_DIR` (padrão `./datasets`).

### Séries de avanço (9 CSVs)

`BR_IMEA_{SOY,CORN,COTTON}_{PLANTING,HARVEST,COMMERCIALIZATION}_PERCENTAGE.csv`, com as colunas `date, year, month, crop, state, harvest_season, percentage, first_seen_at`.

- `date` é a data de referência do dado, não a de publicação.
- `percentage` é o % acumulado da safra.
- `first_seen_at` é quando a atualização automática viu o ponto pela primeira vez. Fica vazio para pontos anteriores a 01/11/2026.
- Só entra o que o IMEA publicou. Datas sem publicação ficam vazias (NaN) no `crop_summary`, nunca 0.
- Lacunas da própria fonte: nada antes de 2023; sem colheita de algodão 22/23, 23/24 e 25/26; sem colheita de milho 22/23 e 23/24.

### Cotações, custos e estimativas (4 CSVs)

`BR_IMEA_CURRENT_PRICES.csv` (todas as cadeias) e `BR_IMEA_{SOY,CORN,COTTON}_PRICES.csv`.

- `IndicadorFinalId` diz o que o número é (preço disponível, preço médio mensal, custo, área...). Os arquivos misturam unidades (`UnidadeSigla`: R$/sc, R$/t, R$/@, R$/ha, ha, t, sc/ha, %), então filtre sempre pelo indicador.
- O endpoint de cotações só traz o valor mais recente de cada indicador. O histórico de preços diários começa em 01/11/2026 e cresce a cada execução.
- Arquivos no formato antigo, sem as colunas da chave, são movidos para `*.bak` na primeira execução.

### Nomes dos indicadores (`BR_IMEA_INDICATOR_NAMES.csv`)

O endpoint de cotações não traz o nome do indicador. O extrator busca cada `IndicadorFinalId` novo em `/api/seriehistorica` e guarda `indicator_name` e `indicator_group` nesse arquivo, que funciona como cache: só IDs desconhecidos são consultados. Cerca de um terço dos IDs não tem nenhum dado na API e fica sem nome; esses são consultados de novo a cada 30 dias.

### Relatório de cobertura (`BR_IMEA_COVERAGE_REPORT.csv`)

Uma linha por cultura × atividade × safra, com primeira e última data, número de observações, máximo, maior intervalo entre observações e `status`:

- `ok`;
- `incomplete`: a série acumulada parou antes de 100%;
- `non_monotonic`: a série acumulada caiu dentro da safra (erro ou revisão da fonte);
- `gap`: intervalo maior que a cadência da série;
- `missing`: safra esperada ausente (por exemplo, a colheita de uma safra 100% plantada).

### Banco SQLite (`imea.db`)

Reconstruído a partir dos CSVs a cada execução:

| Tabela | Conteúdo |
|---|---|
| `crop_percentages` | as 9 séries de avanço em formato longo, com a coluna `activity` |
| `crop_summary` | uma linha por data × cultura × safra, com plantio, colheita e comercialização lado a lado |
| `crop_prices` | cotações, com `indicator_name` logo depois de `IndicadorFinalId` |
| `indicators` | `IndicadorFinalId`, `indicator_name`, `indicator_group` |
| `coverage_report` | o relatório de cobertura |

## Confiabilidade

- `/api/seriehistorica` devolve no máximo 10 linhas por requisição. Quando a resposta chega a 10, o extrator divide a janela ao meio e consulta cada metade.
- Um mesmo ponto pode aparecer duas vezes com valores diferentes. Fica o registro de `Id` mais alto (a revisão mais recente), e o conflito vai para o log.
- O merge com a base existente é por chave (`date` + `harvest_season` nas séries; indicador, localidade, safra, unidade e data nos preços). Se o merge falhar, o arquivo existente não é tocado.
- Requisições com erro de rede, timeout, 429 ou 5xx são repetidas até 3 vezes. Se alguma ainda falhar, o que chegou é salvo, o log lista cada falha e a execução termina com **exit 1**.
- Argumentos desconhecidos na linha de comando (ex. `--mdoe`) interrompem a execução.

## Atualização automática (macOS)

Um agente do launchd roda `scripts/update_daily.sh` (modo incremental) de **segunda a sexta às 19:00**, horário do Mac. O horário fica depois do Boletim Semanal das segundas (17:00 em Cuiabá, 18:00 em Brasília); o calendário completo está em [docs/SOURCE_NOTES.md](docs/SOURCE_NOTES.md).

- Se o Mac estiver dormindo às 19:00, a execução acontece quando ele acordar. Se estiver desligado, a execução do dia é perdida e a seguinte recupera os últimos 60 dias.
- O script exige `IMEA_DATASETS_DIR` no `.env`. Se a pasta não existir (por exemplo, disco externo desmontado), ele aborta sem gravar nada. Antes de rodar, espera a rede por até 5 minutos.
- Logs: `~/Library/Logs/imea/update.log`.

```bash
bash scripts/install_schedule.sh            # instalar ou atualizar
bash scripts/install_schedule.sh --remove   # remover
launchctl kickstart gui/$(id -u)/com.imea.daily-update   # rodar agora
launchctl print gui/$(id -u)/com.imea.daily-update       # ver status
```

## Pasta compartilhada

Se `IMEA_SHARED_DIR` estiver definido no `.env`, cada execução copia os CSVs e o `imea.db` para essa pasta, com leitura liberada para todos os usuários do Mac (útil para outras ferramentas e agentes LLM). Junto vai um `README.md` gerado a partir de [docs/SHARED_README.md](docs/SHARED_README.md), com caminhos, tabelas, calendário e exemplos de consulta. Edite o modelo, não a cópia.

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Os testes não acessam a API do IMEA nem a pasta compartilhada: usam diretórios temporários e um servidor HTTP local.

## Estrutura

```
imea_extractor.py          extrator e CLI
scripts/                   agendamento (launchd) e script de atualização diária
docs/SOURCE_NOTES.md       comportamento da API, hipóteses testadas, calendário
docs/SHARED_README.md      modelo do README da pasta compartilhada
tests/                     testes automatizados
.env.example               variáveis de ambiente
```

## Créditos

Este projeto deriva do IMEA Direct Data Extractor original de Guilherme Palhares ([pedronipalhares/IMEA](https://github.com/pedronipalhares/IMEA)), também sob licença MIT. Esta versão recomeça o histórico do Git e acrescenta o modo incremental, o SQLite, as correções de confiabilidade dos dados, o relatório de cobertura, os nomes dos indicadores, as notas sobre a fonte, o agendamento e os testes.

## Aviso

Ferramenta para fins informativos. Quem a usa é responsável por respeitar os termos de uso do IMEA e por validar os dados antes de qualquer decisão.
