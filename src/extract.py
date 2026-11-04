"""Extração bruta das séries. Não limpa, não anualiza, não grava."""

from __future__ import annotations

import pandas as pd
from bcb import sgs

from .config import (
    CDI_SGS_CODE,
    CDI_SGS_COLUMN,
    PTAX_MEDIAN_RANGE,
    PTAX_SGS_CODE,
    PTAX_SGS_COLUMN,
)

# Janela de 5 anos cabe em uma chamada; timeout explícito por tentativa HTTP.
SGS_TIMEOUT_SECONDS = 120

# SGS 12 é % a.d. (~0.007 a 0.06). Mediana > 1 indica série/unidade errada.
CDI_MAX_MEDIAN_PCT_DAY = 1.0


def extract_cdi(start, end) -> pd.DataFrame:
    """Baixa a série bruta do CDI (SGS 12) entre ``start`` e ``end``.

    Devolve o DataFrame ainda cru (índice de data e coluna de valor da API).
    Não preenche fim de semana, não interpola e não persiste.

    Raises
    ------
    ValueError
        Se a extração vier vazia ou a mediana dos valores for ``> 1``
        (unidade incompatível com taxa % a.d.).
    """
    raw = sgs.get(
        {CDI_SGS_COLUMN: CDI_SGS_CODE},
        start=start,
        end=end,
        timeout=SGS_TIMEOUT_SECONDS,
    )
    if raw is None or raw.empty:
        raise ValueError(
            f"Extração SGS {CDI_SGS_CODE} vazia para start={start!r} end={end!r}."
        )

    values = _cdi_raw_values(raw)
    median = float(values.median())
    if median > CDI_MAX_MEDIAN_PCT_DAY:
        raise ValueError(
            "Unidade errada na série CDI (SGS 12): esperado % a.d. "
            f"(ordem ~0.007 a 0.06), mediana={median:.4g}. "
            "Não usar 4389/4391 nem tratar o valor como % a.a."
        )
    return raw


def _cdi_raw_values(raw: pd.DataFrame) -> pd.Series:
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
        raise ValueError("Série CDI sem valores numéricos após a extração.")
    return values


def extract_ptax(start, end) -> pd.DataFrame:
    """Baixa a série bruta do PTAX venda (SGS 1, R$/US$) entre ``start`` e ``end``.

    Devolve o DataFrame ainda cru (índice de data e coluna de valor da API).
    Não preenche fim de semana, não interpola, não calcula retorno e não persiste.

    Raises
    ------
    ValueError
        Se a extração vier vazia, sem valores numéricos, ou se a mediana
        ficar fora de ``PTAX_MEDIAN_RANGE`` (unidade/série incompatível com
        cotação em R$/US$).
    """
    raw = sgs.get(
        {PTAX_SGS_COLUMN: PTAX_SGS_CODE},
        start=start,
        end=end,
        timeout=SGS_TIMEOUT_SECONDS,
    )
    if raw is None or raw.empty:
        raise ValueError(
            f"Extração SGS {PTAX_SGS_CODE} vazia para start={start!r} end={end!r}."
        )

    values = _ptax_raw_values(raw)
    median = float(values.median())
    low, high = PTAX_MEDIAN_RANGE
    if not (low <= median <= high):
        raise ValueError(
            f"Unidade errada na série PTAX (SGS {PTAX_SGS_CODE}): esperado "
            f"cotação em R$/US$ (mediana entre {low:g} e {high:g}), "
            f"mediana={median:.4g}."
        )
    return raw


def _ptax_raw_values(raw: pd.DataFrame) -> pd.Series:
    if PTAX_SGS_COLUMN in raw.columns:
        series = raw[PTAX_SGS_COLUMN]
    elif str(PTAX_SGS_CODE) in raw.columns:
        series = raw[str(PTAX_SGS_CODE)]
    elif PTAX_SGS_CODE in raw.columns:
        series = raw[PTAX_SGS_CODE]
    elif raw.shape[1] == 1:
        series = raw.iloc[:, 0]
    else:
        raise ValueError(
            f"Coluna de valor do PTAX não encontrada. Colunas: {list(raw.columns)}"
        )
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        raise ValueError("Série PTAX sem valores numéricos após a extração.")
    return values