# Base IMEA (Mato Grosso) — leia antes de consultar

Dados do **IMEA** (Instituto Mato-grossense de Economia Agropecuária): avanço de plantio, colheita e comercialização de soja, milho e algodão em MT, mais cotações e custos. Este arquivo é regenerado a cada atualização da base; não edite aqui.

Gerado pelo projeto https://github.com/lbarretos/IMEA. Última atualização desta pasta: **{UPDATED_AT}**.

## Onde estão os arquivos

| O quê | Caminho |
|---|---|
| Banco SQLite (use este) | `{SHARED_DIR}/imea.db` |
| CSVs | `{SHARED_DIR}/datasets/` |

## Quando a base muda

- Atualização automática **seg–sex às 19:00 (Brasília)**. Se o Mac estiver dormindo, roda quando ele acordar.
- **Nem toda execução traz dado novo.** É normal a maioria das séries não mudar de um dia para o outro:
  - plantio e colheita: um ponto por semana, data de referência **sexta-feira**, só durante a janela da safra (soja: plantio set–dez, colheita jan–abr; milho: plantio jan–mar, colheita mai–ago; algodão: plantio dez–fev, colheita jun–set);
  - comercialização: um ponto por mês, no **último dia útil**, publicado na 2ª segunda-feira do mês seguinte;
  - cotações diárias (R$/sc, R$/t, R$/@): dias úteis.
- Para saber se algo mudou, compare `max(date)` por série ou `max(publication_date)` nos preços, não o horário dos arquivos.

## Tabelas do `imea.db`

### `crop_percentages` — formato longo, uma linha por observação
`date, year, month, crop, state, harvest_season, percentage, activity`
- `date`: **data de referência** do dado (não a data de publicação), `YYYY-MM-DD`.
- `crop`: `Soy`, `Corn`, `Cotton`. `activity`: `Planting`, `Harvest`, `Commercialization`.
- `harvest_season`: safra, ex. `25/26`. Na comercialização, 2–3 safras ficam abertas ao mesmo tempo.
- `percentage`: % acumulado da safra, de 0 a 100.
- `first_seen_at`: quando a atualização automática viu o ponto pela primeira vez (revisões posteriores não mudam esse valor). Vazio para pontos que já existiam em 01/11/2026.

### `crop_summary` — formato largo, uma linha por data × cultura × safra
`planted_percentage, harvested_percentage, commercialized_percentage`
- **Vazio (NULL) significa "não publicado nessa data", não 0%.** Plantio e colheita caem em sextas e comercialização no fim do mês, então quase toda linha tem colunas NULL.
- Para o valor mais recente de uma atividade, filtre `IS NOT NULL` (veja os exemplos).

### `crop_prices` — cotações, custos e estimativas
- **`IndicadorFinalId` diz o que o número é** (preço disponível, preço médio mensal, custo, área...). Sempre filtre por ele ou, no mínimo, por `UnidadeSigla`. Os arquivos misturam R$/sc, R$/t, R$/@, R$/ha, ha, t, sc/ha e %.
- `indicator_name` traz o nome do indicador (ex. "Preço soja disponível compra"). Fica vazio para indicadores sem nenhum dado na API, que em geral também vêm sem `value`. A lista completa está na tabela `indicators` (`IndicadorFinalId`, `indicator_name`, `indicator_group`); para achar um ID, busque por nome: `SELECT * FROM indicators WHERE indicator_name LIKE '%milho%'`.
- `Localidade`: município, região ou "Mato Grosso". Às vezes é um tipo de semente ("Convencional", "Transgênica").
- `publication_date` pode vir vazio. O IMEA só fornece o valor mais recente de cada cotação, então o histórico de preços diários começa em 01/11/2026 e cresce a cada execução.
- IDs úteis: `708192508838936580` = soja disponível R$/sc por praça; `708192508838936581` = milho disponível R$/sc por praça; `701194393889800192` = preço médio mensal de comercialização de milho (R$/sc); `702651198968954880` = idem soja.

### `coverage_report` — qualidade e lacunas
Uma linha por cultura × atividade × safra, com `status`:
`ok`, `incomplete` (parou antes de 100%), `non_monotonic` (caiu dentro da safra; erro ou revisão da fonte), `gap` (intervalo maior que a cadência), `missing` (safra esperada que o IMEA não publicou).
**Consulte antes de concluir que um dado falta ou está errado.** Lacunas conhecidas da própria fonte: nada antes de 2023; sem colheita de algodão 22/23, 23/24 e 25/26; sem colheita de milho 22/23 e 23/24.

## Exemplos

```python
import sqlite3, pandas as pd
conn = sqlite3.connect('{SHARED_DIR}/imea.db')

# Último ponto de cada atividade da soja, por safra
pd.read_sql_query("""
SELECT activity, harvest_season, MAX(date) AS date, percentage
FROM crop_percentages
WHERE crop = 'Soy'
GROUP BY activity, harvest_season
ORDER BY activity, harvest_season DESC
""", conn)

# Plantio de soja mais recente (ignorando datas sem publicação)
pd.read_sql_query("""
SELECT date, harvest_season, planted_percentage
FROM crop_summary
WHERE crop = 'Soy' AND planted_percentage IS NOT NULL
ORDER BY date DESC LIMIT 5
""", conn)

# Soja disponível (R$/sc) por praça na data mais recente
pd.read_sql_query("""
SELECT Localidade, value, variation, publication_date
FROM crop_prices
WHERE IndicadorFinalId = 708192508838936580
  AND publication_date = (SELECT MAX(publication_date) FROM crop_prices
                          WHERE IndicadorFinalId = 708192508838936580)
ORDER BY Localidade
""", conn)

# Problemas de cobertura conhecidos
pd.read_sql_query("SELECT * FROM coverage_report WHERE status <> 'ok'", conn)
```

Detalhes da fonte (limites da API, hipóteses já testadas, calendário de divulgação): `docs/SOURCE_NOTES.md` no repositório.
