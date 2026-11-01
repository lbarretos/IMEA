# IMEA Direct Data Extractor 🌾📈

A powerful **single-file** Python tool for extracting comprehensive agricultural data from IMEA (Instituto Mato-grossense de Economia Agropecuária) API, providing crucial insights into Brazil's agricultural sector for equity analysts, traders, and researchers.

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Data Source: IMEA](https://img.shields.io/badge/Data%20Source-IMEA-green.svg)](https://www.imea.com.br/)

## 🎯 Why This Matters for Equity Analysts

### Critical Market Intelligence from Brazil's Agricultural Heartland

Brazil is the world's largest exporter of soybeans and a major producer of corn and cotton. Mato Grosso alone accounts for:
- **32%** of Brazil's soybean production
- **28%** of Brazil's corn production  
- **65%** of Brazil's cotton production

This extractor provides **real agricultural progress data** that directly impacts:

#### 📊 **Commodity Price Movements**
- **Planting Progress**: Early indicators of potential supply (September-December)
- **Harvest Progress**: Real-time production estimates (January-August)
- **Commercialization Progress**: Market flow and pricing pressure (Year-round)

#### 🏢 **Equity Impact Analysis**
- **Agricultural Companies**: ADM, Cargill, Bunge, Amaggi
- **Equipment Manufacturers**: John Deere, CNH Industrial, AGCO
- **Fertilizer Companies**: Nutrien, Mosaic, Yara
- **Food & Beverage**: Tyson Foods, JBS, BRF
- **Biofuel Producers**: Renewable Energy Group, Archer Daniels

## ✨ Key Features

### 🔧 **Technical Implementation**
- ✅ **Single Self-Contained File**: No external dependencies, runs independently
- ✅ **Comprehensive Historical Coverage**: Complete data from **2021-2025**
- ✅ **High-Speed Parallel Processing**: 15 concurrent workers for fast extraction
- ✅ **Monthly Granular Requests**: 513 individual API requests for complete coverage
- ✅ **Smart Deduplication**: Removes duplicates while preserving data integrity
- ✅ **Robust Error Handling**: Built-in retry logic and comprehensive logging

### 📊 **Proven Results** ⭐
**Latest Test Run (Highly Successful):**
- **📊 Total Records**: 509 unique historical records extracted
- **📅 Date Coverage**: 2022-01-07 to 2025-06-09
- **🌾 Harvest Seasons**: 6 seasons covered (20/21 through 25/26)
- **⚡ Performance**: 513 monthly requests completed successfully
- **📁 Files Created**: 9 separate CSV files with 508 total records

## 🚀 Quick Start (2 minutes)

### 1. Setup Environment
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Set Your Credentials
Copy the template and fill in your IMEA account (and, optionally, where to store the data):
```bash
cp .env.example .env
```

**🔒 Security Note:** Never commit the `.env` file to version control! It's already included in `.gitignore`.

### 3. Extract Data

```bash
# Modo Incremental (padrão, rápido ~10s: busca últimos 60 dias, mescla dados e cotações)
python3 imea_extractor.py

# Modo Histórico Completo (varre todo o histórico desde 2021)
python3 imea_extractor.py --mode full

# Apenas cotações e preços atuais
python3 imea_extractor.py --mode prices-only

# Customizar dias para o modo incremental
python3 imea_extractor.py --mode incremental --days 90
```

## 📊 Arquivos e Formatos Gerados

### 1. Séries de Avanço Agrícola (9 arquivos CSV)
- `BR_IMEA_SOY_PLANTING_PERCENTAGE.csv`
- `BR_IMEA_SOY_HARVEST_PERCENTAGE.csv`
- `BR_IMEA_SOY_COMMERCIALIZATION_PERCENTAGE.csv`
- `BR_IMEA_CORN_PLANTING_PERCENTAGE.csv`
- `BR_IMEA_CORN_HARVEST_PERCENTAGE.csv`
- `BR_IMEA_CORN_COMMERCIALIZATION_PERCENTAGE.csv`
- `BR_IMEA_COTTON_PLANTING_PERCENTAGE.csv`
- `BR_IMEA_COTTON_HARVEST_PERCENTAGE.csv`
- `BR_IMEA_COTTON_COMMERCIALIZATION_PERCENTAGE.csv`

### 2. Cotações de Mercado e Preços (4 arquivos CSV)
- `BR_IMEA_CURRENT_PRICES.csv` (~12.000 registros com cotações consolidadas)
- `BR_IMEA_SOY_PRICES.csv` (Cotações de Soja)
- `BR_IMEA_CORN_PRICES.csv` (Cotações de Milho)
- `BR_IMEA_COTTON_PRICES.csv` (Cotações de Algodão)

Cada linha de preço traz `IndicadorFinalId`, que identifica o que o número representa (preço, custo, área...). Os arquivos misturam unidades (`UnidadeSigla`: R$/sc, R$/ha, ha, t, %), então filtre sempre por indicador e unidade. Arquivos gravados no formato antigo, sem essa coluna, são movidos para `*.bak` na primeira execução.

### 3. Relatório de cobertura (`BR_IMEA_COVERAGE_REPORT.csv`)
Uma linha por cultura × atividade × safra com primeira e última data, número de observações, máximo, maior intervalo entre observações e `status`:
- `ok`
- `incomplete`: série acumulada parou antes de 100%
- `non_monotonic`: série acumulada caiu dentro da safra (erro ou revisão da fonte)
- `gap`: intervalo maior que a cadência da série
- `missing`: safra esperada ausente (por exemplo, colheita de uma safra 100% plantada)

O comportamento da API, as hipóteses já testadas e o calendário de cada série estão em [docs/SOURCE_NOTES.md](docs/SOURCE_NOTES.md). Leia antes de investigar lacunas ou anomalias.

As séries de avanço trazem só o que o IMEA publicou: datas sem publicação ficam vazias (NaN) no `crop_summary`, nunca 0. A API do IMEA só tem estas séries a partir de 2023, e não publica colheita de algodão para 22/23, 23/24 e 25/26 nem colheita de milho para 22/23 e 23/24.

### 4. Banco de Dados SQLite (`imea.db`)
Consolidação completa com tabelas indexadas para SQL de alta performance e agentes de IA (LLMs):
- `crop_percentages`: Séries de avanço percentual detalhadas.
- `crop_summary`: Tabela pivoteada com plantio, colheita e comercialização lado a lado.
- `crop_prices`: Cotações de mercado e variações.
- `coverage_report`: O relatório de cobertura acima.

## ⏰ Atualização automática

Um agente do launchd roda `scripts/update_daily.sh` (modo incremental) de **segunda a sexta às 19:00**, horário do Mac. O horário foi escolhido para depois do Boletim Semanal das segundas (17:00 em Cuiabá, 18:00 em Brasília); o calendário completo está em [docs/SOURCE_NOTES.md](docs/SOURCE_NOTES.md).

- Se o Mac estiver dormindo às 19:00, a execução acontece quando ele acordar. Se estiver desligado, a execução do dia é perdida e a seguinte recupera os últimos 60 dias.
- O script exige `IMEA_DATASETS_DIR` no `.env`. Se a pasta não existir (por exemplo, disco externo desmontado), ele aborta sem gravar nada.
- Logs: `~/Library/Logs/imea/update.log`.

```bash
# instalar ou atualizar / remover
bash scripts/install_schedule.sh
bash scripts/install_schedule.sh --remove
# rodar agora / ver status
launchctl kickstart gui/$(id -u)/com.imea.daily-update
launchctl print gui/$(id -u)/com.imea.daily-update
```

## 💾 Armazenamento e Compartilhamento
Configurado no `.env` (veja `.env.example`):
- `IMEA_DATASETS_DIR`: onde ficam os CSVs e o `imea.db` (padrão: `./datasets`). Pode apontar para um disco externo.
- `IMEA_SHARED_DIR`: se definido, cada execução copia a base para essa pasta com leitura liberada para todos os usuários do Mac (útil para outras ferramentas e LLMs).

## 🏗️ Estrutura do Projeto

```
IMEA/
├── imea_extractor.py    # 🎯 Extrator com CLI, SQLite e modo incremental
├── datasets/            # 📁 Atalho/pasta para os datasets gerados
│   ├── imea.db          # 🗄️ Banco SQLite indexado
│   ├── *.csv            # 📊 CSVs de progresso e cotações
├── requirements.txt     # 📦 Dependências do projeto
└── README.md           # 📖 Documentação
```

## 🎯 Data Quality Verification

### **Sample Data Validation**
Recent extractions show clean, reliable data:
- **Soy Planting**: Typical progression from 1.79% to 100% over planting season
- **Corn Commercialization**: Steady progression throughout marketing year
- **Cotton Harvest**: Clear seasonal patterns matching agricultural cycles
- **Date Consistency**: Proper weekly data points with no gaps
- **Percentage Logic**: Values follow expected agricultural progression patterns

## ⚡ Dependencies

**Minimal Requirements:**
```txt
requests>=2.28.0
pandas>=1.5.0
urllib3>=1.26.0
python-dotenv>=1.0.0
python-dateutil>=2.8.0
```

**Optional for advanced analysis:**
```txt
aiohttp>=3.8.0
numpy>=1.24.0
```

## 🔒 Security & Best Practices

- ✅ **Secure Credential Management**: Uses `.env` file and environment variables
- ✅ **No Hardcoded Credentials**: Credentials never stored in source code
- ✅ **Protected .env File**: Automatically excluded from version control
- ✅ **SSL/TLS Security**: Custom TLS adapter for secure IMEA connections
- ✅ **Local Data Storage**: All data stored securely in local `datasets/` directory
- ✅ **Error Handling**: Graceful handling of missing credentials with clear error messages

## 🆕 Latest Updates (Current Version)

### **Implementation Status: ✅ COMPLETE & TESTED**
- 🎯 **Single File Design**: Completely self-contained, no external utils
- 📊 **Proven Data Extraction**: Successfully extracted 509 records across 6 seasons
- ⚡ **High-Speed Processing**: 15 concurrent workers with 513 monthly requests
- 🧹 **Smart Data Cleaning**: Automatic deduplication and validation
- 📁 **9 Specialized Files**: Individual CSV files for each crop-activity combination

### **Key Achievements**
- ✅ **Zero Dependencies Issues**: Self-contained design eliminates import problems
- ✅ **Comprehensive Coverage**: 2021-2025 data with future projections
- ✅ **Fast Execution**: Complete extraction in under 2 minutes
- ✅ **Clean Data Output**: Standardized CSV format for easy analysis
- ✅ **Robust Error Handling**: Built-in retry logic and comprehensive logging

## 📊 Business Impact

### **For Agricultural Traders:**
- Real-time crop progress for better timing decisions
- Historical patterns for seasonal forecasting
- Commercialization data for market flow analysis

### **For Equity Analysts:**
- Supply indicators for agricultural commodity companies
- Production estimates for earnings forecasts
- Seasonal trends for sector rotation strategies

### **For Risk Managers:**
- Historical volatility data for hedging models
- Weather correlation analysis capabilities
- Supply shock early warning indicators

## 🤝 Contributing

**Current Status: Production Ready** ✅

The extractor is fully functional and tested. Future enhancements could include:
- Additional Brazilian states (Rio Grande do Sul, Paraná)
- More crop types (wheat, coffee, sugarcane)
- Real-time alerting capabilities
- Advanced data visualization features

## 📄 License

MIT License - This project is open source and free to use.

## 🙏 Créditos

Este projeto deriva do IMEA Direct Data Extractor original de Guilherme Palhares ([pedronipalhares/IMEA](https://github.com/pedronipalhares/IMEA)), também sob licença MIT. Esta versão recomeça o histórico do Git e acrescenta o modo incremental, o SQLite, as correções de confiabilidade dos dados, o relatório de cobertura, as notas sobre a fonte e o agendamento.

## ⚠️ Disclaimer

This tool is for informational purposes only. Users are responsible for:
- Complying with IMEA's Terms of Service
- Ensuring proper API usage and rate limits
- Validating data accuracy for trading decisions
- Understanding that agricultural data can be volatile

## 🆘 Support

- 🐛 **Issues**: Report problems with the extractor
- 💡 **Feature Requests**: Suggest improvements
- 📊 **Data Questions**: Discuss agricultural data interpretation
- 🔧 **Technical Help**: Get assistance with setup and execution

---

**🎯 Ready to extract Brazilian agricultural data? Just run:**
```bash
python3 imea_extractor.py
```

**Made with ❤️ for the agricultural finance community**

*"Single file. Comprehensive data. Proven results."* 