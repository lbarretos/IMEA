# Notas sobre a fonte (API do IMEA)

O que já foi testado contra `https://api1.imea.com.br` e o que se concluiu, para não repetir os mesmos testes. Testes feitos em 01/11/2026 com uma conta comum.

## Comportamento da API

| Fato | Evidência / consequência |
|---|---|
| `/api/seriehistorica` devolve **no máximo 10 linhas** por requisição, qualquer que seja o `pageSize` | Testado com pageSize 1000 e 5000. O parâmetro `page` devolve sempre as mesmas 10 linhas, então não há paginação. O extrator divide a janela ao meio quando a resposta chega a 10 (`API_ROW_CAP`). |
| O filtro `tipolocalidade: ["1"]` restringe ao estado (MT) | Sem ele, as 10 linhas se enchem de regiões IMEA (Centro-Sul, Médio-Norte...) e o dado de MT pode sumir. O valor `"2"` = macrorregiões; `"3"` não devolveu nada para colheita. |
| `/api/safra/seriehistoricageral` devolve **0 registros** | Por isso o filtro `safra` sai sempre vazio, sem efeito. Mandar `safra: []` dá o mesmo resultado. |
| `Id` dos registros é um snowflake crescente | Serve para escolher a revisão mais recente quando um mesmo ponto aparece duas vezes. |
| As séries **não trazem data de publicação**, só a data de referência (`Data`) | Não dá para reconstruir quando cada ponto foi publicado nem o histórico de revisões a partir da API. |
| `GET /api/indicador` traz as datas da última atualização por área | Ex. em 01/11/2026: `DataAgricultura` 30/10/2026, `DataPecuaria` 30/10/2026, `DataConjuntura` 30/09/2026. É um bom sinal barato de "houve atualização?". |
| `POST /api/seriehistorica/indicadores` existe mas também corta em 10 linhas | Não serve como catálogo de indicadores. |
| `GET /api/cadeia` lista as cadeias (paginado, 14 itens) | 1 = Algodão, 3 = Milho, 4 = Soja. |
| Endpoints inexistentes (404/405) | `/api/indicadores`, `/api/safra`, `/api/safras`, `/api/grupo`; `/api/indicadorfinal` (POST) dá timeout. |
| TLS do servidor exige `OP_LEGACY_SERVER_CONNECT` | É o motivo do `TLSAdapter`. |

### Endpoint de cotações (`/api/v2/mobile/cadeias/{id}/cotacoes`)

- É um **retrato**: traz o último valor de cada indicador × localidade × safra, **não uma série histórica**. Cotações diárias (R$/sc, R$/t, R$/@) aparecem com uma única data, a mais recente. Histórico de preços só se forma rodando o extrator com frequência, ou consultando `/api/seriehistorica` com o ID do indicador de preço (ex. `701194393889800192` = preço mensal de comercialização de milho).
- `IndicadorFinalId` é o que diz o que o número é. Sem ele, o mesmo `Localidade/Safra/Unidade` vira ambíguo.
- `Localidade` às vezes não é um lugar: "Convencional" e "Transgênica" (tipos de semente).
- A própria API repete linhas: em 12.161 linhas, 344 repetem a chave, 120 são idênticas e 38 chaves têm valores diferentes. `Variacao = -99` aparece como valor sentinela nessas repetições.
- Cerca de 1/3 das linhas vem sem `DataPublicacao`.
- Também traz os indicadores de avanço (%), com o último ponto de cada safra.

## Hipóteses já testadas

| Hipótese | Resultado |
|---|---|
| A colheita de algodão 22/23, 23/24, 25/26 e a de milho 22/23, 23/24 faltam porque o ID do indicador muda por safra | **Falsa.** Buscando o grupo "Colheita" inteiro (`697311317415952385`) por cadeia, com `tipolocalidade` 1, 2, 3 e vazio, nos meses de colheita, não aparece nenhum outro ID. A fonte não publica esses dados. |
| O extrator perde dados anteriores a 2023 | **Falsa.** A extração completa desde 2021 só traz dados a partir de jan/2023 nas séries de avanço. Algumas cotações e alguns últimos pontos vão até 2021–2022. |
| A queda da colheita de soja 23/24 em 29/01/2024 (21,51 → 12,82) é bug do extrator | **Falsa.** A API devolve exatamente esse ponto: uma segunda-feira, repetindo o valor de 19/01. É erro da fonte e aparece como `non_monotonic` no relatório de cobertura. |
| O aviso de "limite de 10 registros" era cosmético | **Falsa.** O limite é real, mas com um indicador e janela mensal o volume fica abaixo de 10 (máx. 5 semanas, ou 3 safras na comercialização). A divisão automática da janela cobre o resto. |

Pontos estranhos da fonte que ainda não foram explicados:
- Colheita de algodão 24/25 com um ponto em 20/09/2024, um ano antes do resto da série.
- Comercialização de milho 25/26: dois pontos em julho/2026 (30/07 e 31/07, mesmo valor) e uma queda de 47,30 → 43,63 em maio/2026, provavelmente uma revisão da safra.
- Comercialização de algodão: meses sem ponto em 2026 (intervalos de ~61 dias).

## Calendário dos dados

Esta é a cadência das **datas de referência**, medida nos dados de 2023–2026. A data em que cada ponto é publicado não é conhecida (ver abaixo).

| Série | Cadência | Dia de referência | Janela típica em MT |
|---|---|---|---|
| Plantio — soja | semanal | sexta-feira (100%) | meados de set → início de dez |
| Colheita — soja | semanal | sexta-feira | jan → abr (às vezes começa em dez) |
| Plantio — milho (2ª safra) | semanal | sexta-feira | jan → mar |
| Colheita — milho | semanal | sexta-feira | meados de mai → ago |
| Plantio — algodão | semanal | sexta-feira | dez → fev |
| Colheita — algodão | semanal | sexta-feira | fim de jun → set (só 24/25 disponível) |
| Comercialização (soja, milho, algodão) | mensal | **último dia útil do mês** (97–100% dos pontos; ~70% caem no último dia do calendário) | contínua; cada safra é acompanhada por ~2 anos, com 2–3 safras abertas ao mesmo tempo |
| Cotações diárias (R$/sc, R$/t, R$/@) | diária | dia útil | — |
| Custos de produção (R$/ha, IDs 523–847) | ~anual / semestral | dia 15–20 do mês | — |
| Área / produção / produtividade (ha, t, sc/ha) | estimativas revisadas de tempos em tempos | início do mês (dias 1–6) | — |

### Calendário oficial de divulgação

A página https://www.imea.com.br/imea-site/calendario lê de um endpoint público, sem login:
`GET https://api1.imea.com.br/api/EventosCalendario/GetEventos?DataEventoInicio=<ISO>&DataEventoFim=<ISO>`.
Cada evento traz `TipoArquivo`, `CadeiaNome`, `DataEvento` e `HoraDivulgacao` (sem fuso; o IMEA fica em Cuiabá, UTC−4, então 17:00 provavelmente equivale a 18:00 em Brasília).

Padrão de nov/2025 a nov/2026 para soja, milho e algodão:

| Relatório | Dia | Hora | Observação |
|---|---|---|---|
| Boletim Semanal (3 cadeias) | toda segunda | 17:00 | 159 eventos, todos às segundas |
| Informe de Semeadura | sexta, durante o plantio | 15:00 | soja set–dez, milho jan–mar, algodão dez–fev |
| Informe de Colheita | sexta, jan–abr | 15:00 | **só soja**; colheita de milho e de algodão não tem informe próprio |
| Informe de Comercialização | segunda entre os dias 7 e 13 (2ª segunda do mês) | 15:00 | mensal |
| Oferta e Demanda | 1ª segunda útil do mês | 15:00 | mensal; nem todo mês |
| Custo de Produção | dia 15–17 | 15:00 | quase mensal |
| Boletim Anual | meados de dezembro | 17:00 | |

### Atraso de publicação

O Informe de Semeadura de soja de sexta 30/10/2026 (15:00) estava no calendário, mas no domingo 01/11 a API ainda tinha 23/10 como último ponto do plantio de soja. Hipótese ainda não confirmada: o ponto de sexta só entra na API com o Boletim Semanal de segunda às 17:00. Para confirmar, compare a API na sexta à noite e na segunda à noite.

Em 01/11/2026 (domingo), `DataAgricultura` dizia 30/10, mas o último ponto do plantio de soja era o de 23/10. Ou seja, o ponto da sexta-feira X ainda não estava publicado dois dias depois. Para medir o atraso de verdade, é preciso registrar quando cada ponto aparece pela primeira vez (um `first_seen_at` por linha), já que a API não informa.

### Atualização da base local

Não há agendamento para este projeto (o crontab só tem o CVM_research). A base local foi atualizada pela última vez em 18/10/2026. Uma execução incremental leva menos de 1 minuto, então rodar diariamente é barato, captura as cotações diárias (que o endpoint de cotações não guarda) e pega cada ponto semanal assim que ele sai.
