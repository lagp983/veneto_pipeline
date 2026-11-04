"""Extração bruta das séries (CDI, PTAX, IPCA). Não limpa, não anualiza, não grava."""

from __future__ import annotations

import pandas as pd
from bcb import sgs

from .config import (
    CDI_SGS_CODE,
    CDI_SGS_COLUMN,
    PTAX_MEDIAN_RANGE,
    PTAX_SGS_CODE,
    PTAX_SGS_COLUMN,
    IPCA_SGS_CODE,
    IPCA_SGS_COLUMN,
    IPCA_MEDIAN_RANGE,
    SGS_TIMEOUT_SECONDS,
    CDI_MAX_MEDIAN_PCT_DAY,
)


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

    values = _raw_values(raw, CDI_SGS_COLUMN, CDI_SGS_CODE, "CDI")
    median = float(values.median())
    if median > CDI_MAX_MEDIAN_PCT_DAY:
        raise ValueError(
            "Unidade errada na série CDI (SGS 12): esperado % a.d. "
            f"(ordem ~0.007 a 0.06), mediana={median:.4g}. "
            "Não usar 4389/4391 nem tratar o valor como % a.a."
        )
    return raw


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

    values = _raw_values(raw, PTAX_SGS_COLUMN, PTAX_SGS_CODE, "PTAX")
    median = float(values.median())
    low, high = PTAX_MEDIAN_RANGE
    if not (low <= median <= high):
        raise ValueError(
            f"Unidade errada na série PTAX (SGS {PTAX_SGS_CODE}): esperado "
            f"cotação em R$/US$ (mediana entre {low:g} e {high:g}), "
            f"mediana={median:.4g}."
        )
    return raw


def extract_ipca(start, end) -> pd.DataFrame:
    """Baixa a série bruta do IPCA (SGS 433, variação % mensal) entre ``start`` e ``end``.

    Devolve o DataFrame ainda cru (índice de data — 1º dia do mês — e coluna de
    valor da API). Não preenche meses ausentes, não interpola, não acumula e não
    persiste.

    Raises
    ------
    ValueError
        Se a extração vier vazia, sem valores numéricos, ou se a mediana
        ficar fora de ``IPCA_MEDIAN_RANGE`` (unidade/série incompatível com
        variação % mensal; valores negativos são permitidos).
    """
    raw = sgs.get(
        {IPCA_SGS_COLUMN: IPCA_SGS_CODE},
        start=start,
        end=end,
        timeout=SGS_TIMEOUT_SECONDS,
    )
    if raw is None or raw.empty:
        raise ValueError(
            f"Extração SGS {IPCA_SGS_CODE} vazia para start={start!r} end={end!r}."
        )

    values = _raw_values(raw, IPCA_SGS_COLUMN, IPCA_SGS_CODE, "IPCA")
    median = float(values.median())
    low, high = IPCA_MEDIAN_RANGE
    if not (low <= median <= high):
        raise ValueError(
            f"Unidade errada na série IPCA (SGS {IPCA_SGS_CODE}): esperado "
            f"variação % mensal (mediana entre {low:g} e {high:g}), "
            f"mediana={median:.4g}."
        )
    return raw


def _raw_values(raw: pd.DataFrame, column: str, code: int, label: str) -> pd.Series:
    """Localiza a coluna de valor de ``raw``, converte para número e descarta NaN.

    Aceita a coluna pelo nome (``column``), pelo código em string, pelo código
    numérico ou, na falta deles, a única coluna do DataFrame. Padrão comum às
    séries CDI, PTAX e IPCA.
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
        raise ValueError(f"Série {label} sem valores numéricos após a extração.")
    return values


# --------------------------------------------------------------------------
# Compatibilidade: nomes privados usados por testes/chamadas anteriores.
# Delegação para ``_raw_values`` mantém as mensagens de erro e o retorno.
# --------------------------------------------------------------------------

def _cdi_raw_values(raw: pd.DataFrame) -> pd.Series:
    return _raw_values(raw, CDI_SGS_COLUMN, CDI_SGS_CODE, "CDI")


def _ptax_raw_values(raw: pd.DataFrame) -> pd.Series:
    return _raw_values(raw, PTAX_SGS_COLUMN, PTAX_SGS_CODE, "PTAX")