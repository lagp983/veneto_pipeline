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


def _extract_values(raw: pd.DataFrame) -> pd.Series:
    """Localiza a coluna de valor, converte para número e descarta NaN."""
    if CDI_SGS_COLUMN in raw.columns:
        series = raw[CDI_SGS_COLUMN]
    elif str(CDI_SGS_CODE) in raw.columns:
        series = raw[str(CDI_SGS_CODE)]
    elif CDI_SGS_CODE in raw.columns:
        series = raw[CDI_SGS_CODE]
    elif raw.shape[1] == 1:
        series = raw.iloc[:, 0]
    else:
        raise ValueError(
            f"Coluna de valor do CDI não encontrada. Colunas: {list(raw.columns)}"
        )

    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        raise ValueError("Série CDI sem valores numéricos para transformar.")
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