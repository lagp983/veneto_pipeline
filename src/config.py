"""Constantes da pipeline ETL (CDI / SGS 12)."""

from pathlib import Path

# Série oficial: CDI diário no SGS do BCB (taxa % a.d., só dia útil).
CDI_SGS_CODE = 12
CDI_SGS_COLUMN = "cdi"

DEFAULT_START = "2021-11-01"
DEFAULT_END = "2026-11-01"

# Colunas canônicas após o transform (não usar `value` sem unidade).
COL_DATE = "date"
COL_CDI_PCT_DAY = "cdi_pct_day"
COL_CDI_FACTOR_DAY = "cdi_factor_day"
COL_CDI_PCT_YEAR = "cdi_pct_year"

CDI_TABLE = "cdi"

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "market.sqlite"
CDI_CSV_PATH = DATA_DIR / "cdi.csv"
