"""Transformação das séries (CDI, PTAX, IPCA e IBOV): colunas canônicas e derivados.

- CDI: fator diário e anualização em 252 dias úteis.
- PTAX: variação diária (simples, %).
- IPCA: fator mensal e acumulado em 12 meses.
- IBOV: variação diária (simples, %) sobre o fechamento (pontos do índice).

Não baixa dados, não grava e não preenche observações ausentes.
"""


from __future__ import annotations

import numpy as np
import pandas as pd

from .config import (
    CDI_SGS_CODE,
    CDI_SGS_COLUMN,
    COL_CDI_FACTOR_DAY,
    COL_CDI_PCT_DAY,
    COL_CDI_PCT_YEAR,
    COL_DATE,
    COL_IBOV_CLOSE,
    COL_IBOV_RET_DAY,
    COL_IPCA_FACTOR_MONTH,
    COL_IPCA_PCT_12M,
    COL_IPCA_PCT_MONTH,
    COL_PTAX_RET_DAY,
    COL_PTAX_VENDA,
    IBOV_COLUMN,
    IBOV_MIN_ROWS,
    IBOV_PRICE_BOUNDS_EXCLUSIVE,
    IBOV_TICKER,
    IPCA_PCT_MONTH_BOUNDS_EXCLUSIVE,
    IPCA_SGS_CODE,
    IPCA_SGS_COLUMN,
    IPCA_WINDOW_MONTHS,
    PTAX_PRICE_BOUNDS_EXCLUSIVE,
    PTAX_SGS_CODE,
    PTAX_SGS_COLUMN,
)

# Convenção de dias úteis por ano usada na anualização do CDI.
BUSINESS_DAYS_PER_YEAR = 252

# SGS 12 é % a.d. Valores fora de (0, 1) não são taxa diária plausível.
CDI_MIN_PCT_DAY_EXCLUSIVE = 0.0
CDI_MAX_PCT_DAY_EXCLUSIVE = 1.0


def transform_cdi(raw: pd.DataFrame) -> pd.DataFrame:
    """Converte a série bruta do CDI (SGS 12) na tabela canônica.

    Colunas devolvidas, nesta ordem:

    - ``date``: ``datetime64[ns]``, sem fuso, ordenada e sem duplicatas.
    - ``cdi_pct_day``: taxa em % a.d. (como vem do SGS 12), ``float64``.
    - ``cdi_factor_day``: ``1 + cdi_pct_day / 100``, ``float64``.
    - ``cdi_pct_year``: ``(cdi_factor_day ** 252 - 1) * 100`` (% a.a.), ``float64``.

    Só as datas presentes na entrada aparecem na saída: não reindexa,
    não preenche fim de semana/feriado e não interpola. Linhas sem valor
    numérico são descartadas (não preenchidas).

    Raises
    ------
    ValueError
        Entrada vazia; coluna de valor não encontrada; índice que não seja
        data; datas duplicadas; ou valores fora de (0, 1) % a.d.
    """
    if raw is None or raw.empty:
        raise ValueError("transform_cdi recebeu um DataFrame vazio.")

    values = _extract_values(raw)
    dates = _normalize_dates(values.index, "série CDI")

    df = pd.DataFrame(
        {
            COL_DATE: dates,
            COL_CDI_PCT_DAY: values.to_numpy(dtype="float64"),
        }
    )

    if df[COL_DATE].duplicated().any():
        dups = df.loc[df[COL_DATE].duplicated(), COL_DATE].dt.date.tolist()[:5]
        raise ValueError(f"Datas duplicadas na série CDI (ex.: {dups}).")

    _check_pct_day_range(df[COL_CDI_PCT_DAY])

    df = df.sort_values(COL_DATE, kind="stable").reset_index(drop=True)
    df[COL_CDI_FACTOR_DAY] = 1.0 + df[COL_CDI_PCT_DAY] / 100.0
    df[COL_CDI_PCT_YEAR] = (
        df[COL_CDI_FACTOR_DAY] ** BUSINESS_DAYS_PER_YEAR - 1.0
    ) * 100.0

    return df[[COL_DATE, COL_CDI_PCT_DAY, COL_CDI_FACTOR_DAY, COL_CDI_PCT_YEAR]]


def _extract_values(
    raw: pd.DataFrame,
    column: str = CDI_SGS_COLUMN,
    code: int | str = CDI_SGS_CODE,
    label: str = "CDI",
) -> pd.Series:
    """Localiza a coluna de valor, converte para número e descarta NaN.

    Os padrões são os do CDI; PTAX, IPCA e IBOV passam a sua coluna, código
    (ou ticker, no IBOV) e rótulo.
    """
    if column in raw.columns:
        series = raw[column]
    elif str(code) in raw.columns:
        series = raw[str(code)]
    elif code in raw.columns:
        series = raw[code]
    elif raw.shape[1] == 1:
        series = raw.iloc[:, 0]
    else:
        raise ValueError(
            f"Coluna de valor do {label} não encontrada. Colunas: {list(raw.columns)}"
        )

    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        raise ValueError(f"Série {label} sem valores numéricos para transformar.")
    return values


def _normalize_dates(index: pd.Index, label: str = "série") -> pd.Series:
    """Índice -> datas ``datetime64[ns]`` sem fuso e sem hora."""
    if not isinstance(index, pd.DatetimeIndex):
        try:
            index = pd.DatetimeIndex(pd.to_datetime(index))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"O índice da {label} não é interpretável como data."
            ) from exc
    if index.tz is not None:
        index = index.tz_localize(None)
    return pd.Series(index.normalize().astype("datetime64[ns]"))


def _check_pct_day_range(pct_day: pd.Series) -> None:
    bad = (pct_day <= CDI_MIN_PCT_DAY_EXCLUSIVE) | (
        pct_day >= CDI_MAX_PCT_DAY_EXCLUSIVE
    )
    if bad.any():
        raise ValueError(
            "CDI fora do intervalo esperado para % a.d. (0 < x < 1): "
            f"{int(bad.sum())} valor(es), min={pct_day.min():.4g}, "
            f"max={pct_day.max():.4g}."
        )


def transform_ptax(raw: pd.DataFrame) -> pd.DataFrame:
    """Converte a série bruta do PTAX venda (SGS 1) na tabela canônica.

    Colunas devolvidas, nesta ordem:

    - ``date``: ``datetime64[ns]``, sem fuso, ordenada e sem duplicatas.
    - ``ptax_venda``: cotação em R$/US$, ``float64``.
    - ``ptax_ret_day``: variação simples em %, ``(p_t / p_{t-1} - 1) * 100``,
      entre observações consecutivas, ``float64``.

    Só as datas presentes na entrada entram na conta: não reindexa, não
    preenche fim de semana/feriado e não interpola. A variação de segunda
    contra sexta atravessa o fim de semana. A primeira observação não tem
    variação e é descartada, então a saída tem uma linha a menos que a
    entrada válida e nenhum valor nulo. Linhas sem valor numérico são
    descartadas antes do cálculo.

    Raises
    ------
    ValueError
        Entrada vazia; coluna de valor não encontrada; índice que não seja
        data; datas duplicadas; cotação fora de (0, 20); ou menos de duas
        observações válidas.
    """
    if raw is None or raw.empty:
        raise ValueError("transform_ptax recebeu um DataFrame vazio.")

    values = _extract_values(raw, PTAX_SGS_COLUMN, PTAX_SGS_CODE, "PTAX")
    dates = _normalize_dates(values.index, "série PTAX")

    df = pd.DataFrame(
        {
            COL_DATE: dates,
            COL_PTAX_VENDA: values.to_numpy(dtype="float64"),
        }
    )

    if df[COL_DATE].duplicated().any():
        dups = df.loc[df[COL_DATE].duplicated(), COL_DATE].dt.date.tolist()[:5]
        raise ValueError(f"Datas duplicadas na série PTAX (ex.: {dups}).")

    _check_ptax_price_range(df[COL_PTAX_VENDA])

    df = df.sort_values(COL_DATE, kind="stable").reset_index(drop=True)
    if len(df) < 2:
        raise ValueError(
            "PTAX precisa de pelo menos 2 observações válidas para calcular "
            "a variação diária."
        )

    df[COL_PTAX_RET_DAY] = (
        df[COL_PTAX_VENDA] / df[COL_PTAX_VENDA].shift(1) - 1.0
    ) * 100.0
    df = df.iloc[1:].reset_index(drop=True)  # 1ª observação não tem variação

    return df[[COL_DATE, COL_PTAX_VENDA, COL_PTAX_RET_DAY]]


def _check_ptax_price_range(price: pd.Series) -> None:
    low, high = PTAX_PRICE_BOUNDS_EXCLUSIVE
    bad = (price <= low) | (price >= high)
    if bad.any():
        raise ValueError(
            f"PTAX fora do intervalo esperado para R$/US$ ({low:g} < x < {high:g}): "
            f"{int(bad.sum())} valor(es), min={price.min():.4g}, "
            f"max={price.max():.4g}."
        )


def transform_ipca(raw: pd.DataFrame) -> pd.DataFrame:
    """Converte a série bruta do IPCA (SGS 433) na tabela canônica.

    Colunas devolvidas, nesta ordem:

    - ``date``: ``datetime64[ns]``, sem fuso, **1º dia do mês**, ordenada e
      sem duplicatas.
    - ``ipca_pct_month``: variação % mensal (como vem do SGS 433), ``float64``.
      Pode ser negativa (deflação).
    - ``ipca_factor_month``: ``1 + ipca_pct_month / 100``, ``float64``.
    - ``ipca_pct_12m``: acumulado em 12 meses,
      ``(prod(factor_month dos últimos 12 meses) - 1) * 100``, ``float64``.

    Só os meses presentes na entrada aparecem na saída: não reindexa, não
    preenche meses ausentes e não interpola. Linhas sem valor numérico são
    descartadas (não preenchidas). Como ``ipca_pct_12m`` precisa de 12
    observações, as primeiras ``IPCA_WINDOW_MONTHS - 1`` linhas são
    descartadas; a saída tem ``len(entrada_válida) - (IPCA_WINDOW_MONTHS - 1)``
    linhas e nenhum valor nulo.

    Raises
    ------
    ValueError
        Entrada vazia; coluna de valor não encontrada; índice que não seja
        data; datas duplicadas após normalização para o 1º dia do mês;
        valores fora de ``IPCA_PCT_MONTH_BOUNDS_EXCLUSIVE``; ou menos de
        ``IPCA_WINDOW_MONTHS`` observações válidas.
    """
    if raw is None or raw.empty:
        raise ValueError("transform_ipca recebeu um DataFrame vazio.")

    values = _extract_values(raw, IPCA_SGS_COLUMN, IPCA_SGS_CODE, "IPCA")
    dates = _normalize_dates(values.index, "série IPCA")
    # O SGS 433 já vem no 1º dia do mês; normalizar garante a convenção para
    # o SQLite/CSV (TEXT ISO) e para a validação ``monthly_checks``.
    dates = pd.Series(dates.dt.to_period("M").dt.to_timestamp())

    df = pd.DataFrame(
        {
            COL_DATE: dates,
            COL_IPCA_PCT_MONTH: values.to_numpy(dtype="float64"),
        }
    )

    if df[COL_DATE].duplicated().any():
        dups = df.loc[df[COL_DATE].duplicated(), COL_DATE].dt.date.tolist()[:5]
        raise ValueError(f"Datas duplicadas na série IPCA (ex.: {dups}).")

    _check_ipca_pct_month_range(df[COL_IPCA_PCT_MONTH])

    df = df.sort_values(COL_DATE, kind="stable").reset_index(drop=True)
    if len(df) < IPCA_WINDOW_MONTHS:
        raise ValueError(
            f"IPCA precisa de pelo menos {IPCA_WINDOW_MONTHS} observações "
            f"válidas para calcular o acumulado em 12 meses; "
            f"recebidas {len(df)}."
        )

    df[COL_IPCA_FACTOR_MONTH] = 1.0 + df[COL_IPCA_PCT_MONTH] / 100.0
    rolling_prod = (
        df[COL_IPCA_FACTOR_MONTH]
        .rolling(IPCA_WINDOW_MONTHS)
        .apply(np.prod, raw=True)
    )
    df[COL_IPCA_PCT_12M] = (rolling_prod - 1.0) * 100.0
    # As primeiras 11 linhas não têm 12 meses completos; descarta.
    df = df.iloc[IPCA_WINDOW_MONTHS - 1:].reset_index(drop=True)

    return df[
        [COL_DATE, COL_IPCA_PCT_MONTH, COL_IPCA_FACTOR_MONTH, COL_IPCA_PCT_12M]
    ]


def _check_ipca_pct_month_range(pct_month: pd.Series) -> None:
    low, high = IPCA_PCT_MONTH_BOUNDS_EXCLUSIVE
    bad = (pct_month <= low) | (pct_month >= high)
    if bad.any():
        raise ValueError(
            f"IPCA fora do intervalo esperado para % a.m. "
            f"({low:g} < x < {high:g}): "
            f"{int(bad.sum())} valor(es), min={pct_month.min():.4g}, "
            f"max={pct_month.max():.4g}."
        )

def transform_ibov(raw: pd.DataFrame) -> pd.DataFrame:
    """Converte a série bruta do IBOVESPA (``^BVSP``) na tabela canônica.

    Colunas devolvidas, nesta ordem:

    - ``date``: ``datetime64[ns]``, sem fuso, ordenada e sem duplicatas.
    - ``ibov_close``: pontos do índice no fechamento (``Close``), ``float64``.
    - ``ibov_ret_day``: variação simples em %, ``(p_t / p_{t-1} - 1) * 100``,
      entre observações consecutivas, ``float64``.

    Só as datas presentes na entrada entram na conta: não reindexa, não
    preenche dias sem pregão e não interpola. A variação de segunda contra
    sexta atravessa o fim de semana (e feriados); a de um pregão após um
    feriado acumula o período. A primeira observação não tem variação e é
    descartada, então a saída tem uma linha a menos que a entrada válida e
    nenhum valor nulo. Linhas sem valor numérico são descartadas antes do
    cálculo.

    Raises
    ------
    ValueError
        Entrada vazia; coluna de valor não encontrada; índice que não seja
        data; datas duplicadas; fechamento fora de ``IBOV_PRICE_BOUNDS_EXCLUSIVE``;
        ou menos de ``IBOV_MIN_ROWS`` observações válidas.
    """
    if raw is None or raw.empty:
        raise ValueError("transform_ibov recebeu um DataFrame vazio.")

    values = _extract_values(raw, IBOV_COLUMN, IBOV_TICKER, "IBOV")
    dates = _normalize_dates(values.index, "série IBOV")

    df = pd.DataFrame(
        {
            COL_DATE: dates,
            COL_IBOV_CLOSE: values.to_numpy(dtype="float64"),
        }
    )

    if df[COL_DATE].duplicated().any():
        dups = df.loc[df[COL_DATE].duplicated(), COL_DATE].dt.date.tolist()[:5]
        raise ValueError(f"Datas duplicadas na série IBOV (ex.: {dups}).")

    _check_ibov_price_range(df[COL_IBOV_CLOSE])

    df = df.sort_values(COL_DATE, kind="stable").reset_index(drop=True)
    if len(df) < IBOV_MIN_ROWS:
        raise ValueError(
            f"IBOV precisa de pelo menos {IBOV_MIN_ROWS} observações válidas "
            f"para calcular a variação diária; recebidas {len(df)}."
        )

    df[COL_IBOV_RET_DAY] = (
        df[COL_IBOV_CLOSE] / df[COL_IBOV_CLOSE].shift(1) - 1.0
    ) * 100.0
    df = df.iloc[1:].reset_index(drop=True)  # 1ª observação não tem variação

    return df[[COL_DATE, COL_IBOV_CLOSE, COL_IBOV_RET_DAY]]

def _check_ibov_price_range(price: pd.Series) -> None:
    low, high = IBOV_PRICE_BOUNDS_EXCLUSIVE
    bad = (price <= low) | (price >= high)
    if bad.any():
        raise ValueError(
            f"IBOV fora do intervalo esperado para pontos do índice "
            f"({low:g} < x < {high:g}): "
            f"{int(bad.sum())} valor(es), min={price.min():.4g}, "
            f"max={price.max():.4g}."
        )