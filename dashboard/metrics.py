"""Cálculos do dashboard: base 100, perda máxima e retorno nominal x real.

Funções puras sobre pandas/numpy, sem Streamlit e sem acesso ao banco, para
poderem ser testadas com contas feitas à mão. Séries diárias são ``Series``
com índice de datas; o IPCA entra como o DataFrame lido do banco (mensal, com
``date`` no 1º dia de cada mês).

Convenções:

- **Base 100**: todas as séries valem 100 na mesma data, a primeira em que
  todas existem. O CDI acumula o fator diário a partir dessa data (o fator do
  próprio dia-base não conta, como o fechamento de uma cotação).
- **IPCA diário**: o IPCA do mês m só vale ao fim do mês, então o índice é
  interpolado de forma geométrica (juros compostos) entre o 1º dia de m e o 1º
  dia de m+1. Antes do 1º mês e depois do último mês divulgado não há valor
  (NaN): o IPCA não é extrapolado.
- **Retorno real**: ``(1 + nominal) / (1 + IPCA) - 1``, ou seja, o índice
  nominal dividido pelo índice do IPCA.
- **Perda máxima** (max drawdown): a maior queda percentual do pico anterior
  até o fundo seguinte, dentro do período recebido.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.config import (
    COL_CDI_FACTOR_DAY,
    COL_DATE,
    COL_IPCA_FACTOR_MONTH,
)

COL_IPCA_NAME = "IPCA"


# --------------------------------------------------------------------------
# Período e séries
# --------------------------------------------------------------------------

def period_start(end: pd.Timestamp, months: int | None) -> pd.Timestamp | None:
    """Data inicial de um período de ``months`` meses terminando em ``end``.

    ``None`` (sem limite, "Tudo") devolve ``None``.
    """
    if months is None:
        return None
    return pd.Timestamp(end) - pd.DateOffset(months=months)


def from_date(df: pd.DataFrame, start: pd.Timestamp | None) -> pd.DataFrame:
    """Linhas com ``date >= start`` (limite inclusivo); ``None`` devolve tudo."""
    if start is None:
        return df
    return df[df[COL_DATE] >= start]


def series(df: pd.DataFrame, column: str) -> pd.Series:
    """Coluna ``column`` como série indexada por ``date``."""
    return df.set_index(COL_DATE)[column]


def cdi_level(df: pd.DataFrame) -> pd.Series:
    """Nível acumulado do CDI: produto do fator diário até cada data."""
    return series(df, COL_CDI_FACTOR_DAY).cumprod()


def rebase_100(s: pd.Series) -> pd.Series:
    """Divide pela primeira observação válida e multiplica por 100."""
    valid = s.dropna()
    if valid.empty:
        return s
    return s / valid.iloc[0] * 100.0


def align_daily(levels: dict[str, pd.Series]) -> pd.DataFrame:
    """Une séries diárias num calendário comum (união das datas).

    Nos dias em que uma série não tem observação (feriado só dela), repete o
    último valor, sem preencher o que vem antes da primeira observação.
    """
    return pd.concat(levels, axis=1).sort_index().ffill()


# --------------------------------------------------------------------------
# IPCA
# --------------------------------------------------------------------------

def ipca_daily_index(ipca: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.Series:
    """Índice do IPCA (base 100 no 1º mês) em cada data de ``dates``.

    Interpola de forma geométrica entre os inícios de mês. Datas antes do 1º
    mês ou depois do fim do último mês da tabela ficam NaN.

    Raises
    ------
    ValueError
        IPCA vazio ou com meses faltando (a interpolação distribuiria a
        inflação de um mês por dois).
    """
    if ipca is None or ipca.empty:
        raise ValueError("IPCA vazio.")

    starts = pd.DatetimeIndex(ipca[COL_DATE].dt.to_period("M").dt.to_timestamp())
    month_number = starts.year * 12 + starts.month
    if (np.diff(month_number) != 1).any():
        raise ValueError("IPCA com meses faltando ou fora de ordem.")

    # Pontos nos inícios de mês, mais o início do mês seguinte ao último.
    points = starts.append(pd.DatetimeIndex([starts[-1] + pd.DateOffset(months=1)]))
    log_level = np.concatenate([[0.0], np.log(ipca[COL_IPCA_FACTOR_MONTH].to_numpy()).cumsum()])

    values = np.interp(_days(dates), _days(points), log_level, left=np.nan, right=np.nan)
    return pd.Series(100.0 * np.exp(values), index=dates, name=COL_IPCA_NAME)


def _days(index: pd.DatetimeIndex) -> np.ndarray:
    return index.to_numpy().astype("datetime64[D]").astype("int64")


# --------------------------------------------------------------------------
# Base 100
# --------------------------------------------------------------------------

def base_100(levels: dict[str, pd.Series], ipca: pd.DataFrame | None = None) -> pd.DataFrame:
    """Séries diárias (e o IPCA, se dado) com valor 100 na primeira data comum.

    A primeira data comum é a mais tardia entre as primeiras observações. O IPCA
    não é repetido além do fim do último mês: a linha dele simplesmente termina.
    Devolve um DataFrame vazio se não houver o que mostrar.
    """
    if not levels:
        return pd.DataFrame()
    frame = align_daily(levels)
    if ipca is not None:
        frame[COL_IPCA_NAME] = ipca_daily_index(ipca, frame.index)

    frame = frame.dropna(axis=1, how="all")
    if frame.empty:
        return frame
    start = max(frame[c].first_valid_index() for c in frame.columns)
    frame = frame.loc[start:]
    return frame / frame.iloc[0] * 100.0


# --------------------------------------------------------------------------
# Perda máxima
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Drawdown:
    max_pct: float  # <= 0, em %
    peak_date: pd.Timestamp
    trough_date: pd.Timestamp


def drawdown_curve(s: pd.Series) -> pd.Series:
    """Queda percentual de cada data em relação ao maior valor até ela (<= 0)."""
    return (s / s.cummax() - 1.0) * 100.0


def max_drawdown(s: pd.Series) -> Drawdown | None:
    """Maior queda do pico ao fundo e as datas de cada um; ``None`` se a série for vazia.

    Sem nenhuma queda, ``max_pct`` é 0 e pico e fundo são a mesma data.
    """
    s = s.dropna()
    if s.empty:
        return None
    curve = drawdown_curve(s)
    trough = curve.idxmin()
    before = s.loc[:trough]
    peak = before[before == before.max()].index[-1]
    return Drawdown(float(curve.loc[trough]), peak, trough)


# --------------------------------------------------------------------------
# Retorno nominal x real
# --------------------------------------------------------------------------

def total_return(s: pd.Series) -> float:
    """Variação percentual do primeiro ao último valor."""
    return float((s.iloc[-1] / s.iloc[0] - 1.0) * 100.0)


def nominal_vs_real(price: pd.Series, ipca: pd.DataFrame, name: str) -> pd.DataFrame:
    """Índice nominal, índice do IPCA e índice real, todos com 100 na primeira data comum.

    Colunas: ``"<name> nominal"``, ``"IPCA"`` e ``"<name> real"`` (nominal
    dividido pelo IPCA). Só entram as datas em que o IPCA existe; DataFrame vazio
    se sobrarem menos de 2.

    Raises
    ------
    ValueError
        IPCA vazio ou com meses faltando (ver ``ipca_daily_index``).
    """
    deflator = ipca_daily_index(ipca, price.index)
    both = pd.concat([price.rename("nominal"), deflator.rename("ipca")], axis=1).dropna()
    if len(both) < 2:
        return pd.DataFrame()
    nominal = rebase_100(both["nominal"])
    inflation = rebase_100(both["ipca"])
    return pd.DataFrame({
        f"{name} nominal": nominal,
        COL_IPCA_NAME: inflation,
        f"{name} real": nominal / inflation * 100.0,
    })