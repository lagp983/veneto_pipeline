"""Constantes da pipeline ETL (CDI, PTAX e IPCA)."""

from datetime import date, datetime
from pathlib import Path

# Série oficial: CDI diário no SGS do BCB (taxa % a.d., só dia útil).
CDI_SGS_CODE = 12
CDI_SGS_COLUMN = "cdi"

# Série PTAX venda no SGS do BCB (R$/US$, só dia útil).
PTAX_SGS_CODE = 1
PTAX_SGS_COLUMN = "ptax"

# Séries da pipeline e seleção na CLI (`--series`; padrão: todas).
SERIES_CDI = "cdi"
SERIES_PTAX = "ptax"
SERIES_IPCA = "ipca"
SERIES_IBOV = "ibov"                                   # ← novo
SERIES_ALL = "all"
SERIES_CHOICES = (SERIES_CDI, SERIES_PTAX, SERIES_IPCA, SERIES_IBOV, SERIES_ALL)
DEFAULT_SERIES = SERIES_ALL
# Janela móvel: os últimos N anos, terminando hoje (fuso de Brasília).
LOOKBACK_YEARS = 5
MARKET_TIMEZONE = "America/Sao_Paulo"
SGS_TIMEOUT_SECONDS = 120
CDI_MAX_MEDIAN_PCT_DAY = 1.0

def _today_local() -> date:
    """Data de hoje no fuso do mercado; cai para a data do sistema sem tzdata."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(MARKET_TIMEZONE)).date()
    except Exception:  # noqa: BLE001 - sem tzdata (ex.: Windows sem o pacote)
        return date.today()


def default_window(
    today: date | None = None, years: int = LOOKBACK_YEARS
) -> tuple[str, str]:
    """Devolve ``(start, end)`` em ``YYYY-MM-DD``: os últimos ``years`` anos até hoje.

    ``end`` é ``today`` e ``start`` é a mesma data ``years`` anos antes
    (29/fev vira 28/fev em ano não bissexto). Chame a cada execução; não
    guarde o resultado em processos de longa duração (ex.: dashboard).
    """
    if years < 1:
        raise ValueError(f"years deve ser >= 1, recebido {years}.")
    end = today or _today_local()
    try:
        start = end.replace(year=end.year - years)
    except ValueError:  # 29/fev em ano de destino não bissexto
        start = end.replace(year=end.year - years, day=28)
    return start.isoformat(), end.isoformat()


# Mantidos por compatibilidade; fixados no momento da importação.
# Prefira ``default_window()``, que reflete a data de cada execução.
DEFAULT_START, DEFAULT_END = default_window()

# Colunas canônicas após o transform (não usar `value` sem unidade).
COL_DATE = "date"
COL_CDI_PCT_DAY = "cdi_pct_day"
COL_CDI_FACTOR_DAY = "cdi_factor_day"
COL_CDI_PCT_YEAR = "cdi_pct_year"
COL_PTAX_VENDA = "ptax_venda"  # R$ por US$
COL_PTAX_RET_DAY = "ptax_ret_day"  # variação simples (%) vs. observação anterior

CDI_TABLE = "cdi"
PTAX_TABLE = "ptax"

# Limites do PTAX (R$/US$) usados no extract, transform e validação.
PTAX_MEDIAN_RANGE = (1.0, 20.0)  # extract: mediana fora disso = unidade/série errada
PTAX_PRICE_BOUNDS_EXCLUSIVE = (0.0, 20.0)  # transform: fora de (0, 20) = erro
PTAX_TYPICAL_RANGE = (3.0, 8.0)  # validação: fora disso = aviso (a calibrar)
PTAX_RET_WARN_ABS_PCT = 8.0  # |variação diária| acima disso = aviso
PTAX_RET_FAIL_ABS_PCT = 25.0  # |variação diária| acima disso = falha

# Qualidade cruzada: sobreposição de datas entre CDI e PTAX (>= limite = OK).
MIN_DATE_OVERLAP = 0.98

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "market.sqlite"
CDI_CSV_PATH = DATA_DIR / "cdi.csv"
PTAX_CSV_PATH = DATA_DIR / "ptax.csv"
MIN_BUSINESS_DAY_COVERAGE = 0.90  # feriados nacionais ficam em ~96%
EDGE_TOLERANCE_DAYS = 5
MAX_GAP_DAYS = 7
# --- IPCA (SGS 433) ---
IPCA_SGS_CODE = 433
IPCA_SGS_COLUMN = "ipca"
IPCA_TABLE = "ipca"
IPCA_CSV_PATH = DATA_DIR / "ipca.csv"

COL_IPCA_PCT_MONTH = "ipca_pct_month"       # variação % mensal (pode ser negativa)
COL_IPCA_FACTOR_MONTH = "ipca_factor_month" # 1 + pct_month / 100
COL_IPCA_PCT_12M = "ipca_pct_12m"           # acumulado em 12 meses (produto dos fatores)

# Limites de plausibilidade (valores mensais do IPCA raramente saem de (-3%, +3%),
# mas a série histórica tem eventos extremos; adotar faixa conservadora).
IPCA_PCT_MONTH_BOUNDS_EXCLUSIVE = (-5.0, 5.0)   # % a.m.
IPCA_WINDOW_MONTHS = 12  # janela do acumulado em 12 meses; mínimo de observações
IPCA_WARMUP_MONTHS = 12
IPCA_MEDIAN_RANGE = (-1.0, 2.0)                 # mediana histórica ~0,3–0,5%
IPCA_PCT_12M_BOUNDS = (-10.0, 30.0)             # % acumulado 12m
IPCA_MIN_MONTHLY_COVERAGE = 0.98                # quase 100% (não há “feriados” mensais)
IPCA_MAX_GAP_MONTHS = 2                         # buraco máximo tolerado (em meses)
IPCA_EDGE_TOLERANCE_MONTHS = 1

# --- IBOVESPA (yfinance ^BVSP) ---
# Índice da B3 obtido via yfinance (Yahoo Finance). Fonte distinta do SGS/BCB,
# mas a estrutura canônica espelha o PTAX: preço de fechamento + variação
# diária simples (%). O yfinance é importado de forma lazy em `extract.py`,
# então quem não usa `--series ibov` não precisa da dependência instalada.
IBOV_TICKER = "^BVSP"                    # símbolo no Yahoo Finance
IBOV_COLUMN = "ibov"                     # nome interno após rename de "Close"
IBOV_TABLE = "ibov"
IBOV_CSV_PATH = DATA_DIR / "ibov.csv"
IBOV_TZ = "America/Sao_Paulo"            # fuso do pregão (evita shift UTC→local)
IBOV_YF_TIMEOUT = 120                    # alinhado a SGS_TIMEOUT_SECONDS

# Colunas canônicas após o transform (mesma convenção do PTAX).
COL_IBOV_CLOSE = "ibov_close"            # pontos do índice (Close), float
COL_IBOV_RET_DAY = "ibov_ret_day"        # variação simples (%) vs. obs. anterior

# Limites de plausibilidade (pontos do índice). Histórico recente: ~15k (2016)
# a ~170k (2024/2025). A faixa "típica" só gera AVISO; o FAIL em ±20% cobre
# com folga o pior dia histórico (~-12% em 1998 e 2020).
IBOV_MEDIAN_RANGE = (10_000.0, 500_000.0)        # extract: mediana fora disso = série/unidade errada
IBOV_PRICE_BOUNDS_EXCLUSIVE = (0.0, 500_000.0)   # transform: fora de (0, 500k) = erro
IBOV_TYPICAL_RANGE = (30_000.0, 250_000.0)       # validação: fora disso = aviso (a calibrar)
IBOV_RET_WARN_ABS_PCT = 8.0                      # |variação diária| acima disso = aviso
IBOV_RET_FAIL_ABS_PCT = 20.0                     # |variação diária| acima disso = falha
IBOV_MIN_ROWS = 2                                # mínimo p/ calcular o retorno diário