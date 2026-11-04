"""Transformação do CDI: colunas canônicas, fator diário e anualização (252).

Não baixa dados, não grava e não preenche dias sem observação.
"""

from __future__ import annotations

import pandas as pd

from .config import (
    CDI_SGS_CODE,
    CDI_SGS_COLUMN,
    COL_CDI_FACTOR_DAY,
    COL_CDI_PCT_DAY,
    COL_CDI_PCT_YEAR,
    COL_DATE,
    COL_PTAX_RET_DAY,
    COL_PTAX_VENDA,
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
    dates = _normalize_dates(values.index)

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
    code: int = CDI_SGS_CODE,
    label: str = "CDI",
) -> pd.Series:
    """Localiza a coluna de valor, converte para número e descarta NaN.

    Os padrões são os do CDI; o PTAX passa a sua coluna, código e rótulo.
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


def _normalize_dates(index: pd.Index) -> pd.Series:
    """Índice -> datas ``datetime64[ns]`` sem fuso e sem hora."""
    if not isinstance(index, pd.DatetimeIndex):
        try:
            index = pd.DatetimeIndex(pd.to_datetime(index))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "O índice da série CDI não é interpretável como data."
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
    dates = _normalize_dates(values.index)

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