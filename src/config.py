"""Constantes da pipeline ETL (CDI / SGS 12)."""

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
SERIES_ALL = "all"
SERIES_CHOICES = (SERIES_CDI, SERIES_PTAX, SERIES_ALL)
DEFAULT_SERIES = SERIES_ALL

# Janela móvel: os últimos N anos, terminando hoje (fuso de Brasília).
LOOKBACK_YEARS = 5
MARKET_TIMEZONE = "America/Sao_Paulo"


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