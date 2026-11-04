"""Verificações de qualidade das séries (CDI, PTAX), da carga e entre séries.

Cada verificação devolve um ``Check`` com status OK, AVISO ou FALHA. Só FALHA
reprova a pipeline; AVISO é informativo.

- ``daily_checks``: comuns a qualquer série diária (datas, cobertura, estrutura).
- ``validate_cdi`` / ``validate_ptax``: ``daily_checks`` + regras de cada série.
- ``validate_persisted``: relê SQLite e CSV e compara com o DataFrame gravado.
- ``validate_date_overlap``: sobreposição de datas entre duas séries.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import (
    COL_CDI_FACTOR_DAY,
    COL_CDI_PCT_DAY,
    COL_CDI_PCT_YEAR,
    COL_DATE,
    COL_PTAX_RET_DAY,
    COL_PTAX_VENDA,
    MIN_DATE_OVERLAP,
    PTAX_PRICE_BOUNDS_EXCLUSIVE,
    PTAX_RET_FAIL_ABS_PCT,
    PTAX_RET_WARN_ABS_PCT,
    PTAX_TYPICAL_RANGE,
    MIN_BUSINESS_DAY_COVERAGE,
    EDGE_TOLERANCE_DAYS,
    MAX_GAP_DAYS,
)
from .load import CDI_COLUMNS, PTAX_COLUMNS
from .transform import BUSINESS_DAYS_PER_YEAR

OK, WARN, FAIL, SKIPPED = "OK", "AVISO", "FALHA", "PULADO"

# Comuns às séries diárias.

FLOAT_RTOL = 1e-12

# Específicas do CDI (o transform já garante 0 < x < 1).
TYPICAL_MEDIAN_PCT_DAY = (0.007, 0.06)
PLAUSIBLE_PCT_YEAR = (5.0, 20.0)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str = ""


def _check(name: str, ok: bool, detail: str, severity: str = FAIL) -> Check:
    return Check(name, OK if ok else severity, detail)


# --------------------------------------------------------------------------
# Comuns a séries diárias
# --------------------------------------------------------------------------

def daily_checks(
    df: pd.DataFrame, columns: list[str], start: str, end: str
) -> list[Check]:
    """Estrutura, datas e cobertura de uma série diária de dias úteis."""
    if df is None or df.empty:
        return [Check("dados não vazios", FAIL, "DataFrame vazio")]

    d = df[COL_DATE]
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    first, last = d.min(), d.max()
    checks: list[Check] = []

    checks.append(_check(
        "colunas canônicas", list(df.columns) == columns, ", ".join(df.columns),
    ))
    n_null = int(df.isna().sum().sum())
    checks.append(_check("sem valores nulos", n_null == 0, f"{n_null} nulo(s)"))
    checks.append(_check(
        "datas únicas e crescentes",
        bool(d.is_unique and d.is_monotonic_increasing),
        f"{int(d.duplicated().sum())} duplicada(s)",
    ))
    checks.append(_check(
        "datas dentro da janela pedida", bool(first >= s and last <= e),
        f"{first:%Y-%m-%d} a {last:%Y-%m-%d}",
    ))
    head, tail = (first - s).days, (e - last).days
    checks.append(_check(
        "cobertura nas pontas da janela",
        0 <= head <= EDGE_TOLERANCE_DAYS and 0 <= tail <= EDGE_TOLERANCE_DAYS,
        f"início +{head}d, fim -{tail}d (tolerância {EDGE_TOLERANCE_DAYS}d)",
        WARN,
    ))

    n_weekend = int((d.dt.dayofweek >= 5).sum())
    checks.append(_check(
        "só dias úteis (sem sáb/dom)", n_weekend == 0,
        f"{n_weekend} linha(s) em fim de semana",
    ))

    expected = int(np.busday_count(first.date(), (last + pd.Timedelta(days=1)).date()))
    coverage = len(df) / expected if expected else 0.0
    checks.append(_check(
        "cobertura de dias úteis",
        MIN_BUSINESS_DAY_COVERAGE <= coverage <= 1.0,
        f"{len(df)} linhas / {expected} dias úteis = {coverage:.1%} "
        f"(esperado >= {MIN_BUSINESS_DAY_COVERAGE:.0%}; o resto são feriados)",
    ))
    max_gap = int(d.diff().dt.days.max()) if len(d) > 1 else 0
    checks.append(_check(
        "sem buracos longos", max_gap <= MAX_GAP_DAYS,
        f"maior intervalo entre datas: {max_gap}d (limite {MAX_GAP_DAYS}d)",
        WARN,
    ))
    return checks


# --------------------------------------------------------------------------
# CDI
# --------------------------------------------------------------------------

def validate_cdi(df: pd.DataFrame, start: str, end: str) -> list[Check]:
    """Verificações sobre o DataFrame transformado do CDI."""
    checks = daily_checks(df, CDI_COLUMNS, start, end)
    if df is None or df.empty:
        return checks

    pct = df[COL_CDI_PCT_DAY].to_numpy(dtype="float64")
    factor = df[COL_CDI_FACTOR_DAY].to_numpy(dtype="float64")
    year = df[COL_CDI_PCT_YEAR].to_numpy(dtype="float64")

    checks.append(_check(
        "cdi_pct_day em (0, 1) % a.d.", bool((pct > 0).all() and (pct < 1).all()),
        f"min={pct.min():.5f}, max={pct.max():.5f}",
    ))
    median = float(np.median(pct))
    lo, hi = TYPICAL_MEDIAN_PCT_DAY
    checks.append(_check(
        "mediana de cdi_pct_day típica", lo <= median <= hi,
        f"mediana={median:.5f} (típico {lo} a {hi})", WARN,
    ))
    checks.append(_check(
        "fator = 1 + pct/100",
        bool(np.allclose(factor, 1.0 + pct / 100.0, rtol=FLOAT_RTOL, atol=0)),
        "recalculado e comparado",
    ))
    ref_year = np.expm1(BUSINESS_DAYS_PER_YEAR * np.log1p(pct / 100.0)) * 100.0
    checks.append(_check(
        f"anualização base {BUSINESS_DAYS_PER_YEAR}",
        bool(np.allclose(year, ref_year, rtol=1e-9, atol=0)),
        "recalculada por expm1/log1p (caminho independente)",
    ))
    ylo, yhi = PLAUSIBLE_PCT_YEAR
    checks.append(_check(
        "cdi_pct_year plausível", bool(ylo <= year.min() and year.max() <= yhi),
        f"{year.min():.2f}% a {year.max():.2f}% a.a. (plausível {ylo} a {yhi})", WARN,
    ))
    return checks


def summarize_cdi(df: pd.DataFrame) -> list[str]:
    if df is None or df.empty:
        return []
    d = df[COL_DATE]
    year = df[COL_CDI_PCT_YEAR]
    accumulated = (df[COL_CDI_FACTOR_DAY].prod() - 1.0) * 100.0
    return [
        f"Linhas: {len(df)}",
        f"Período efetivo: {d.min():%Y-%m-%d} a {d.max():%Y-%m-%d}",
        f"CDI % a.a. (anualizado): primeiro={year.iloc[0]:.2f}, último={year.iloc[-1]:.2f}, "
        f"mín={year.min():.2f}, máx={year.max():.2f}",
        f"CDI acumulado no período: {accumulated:.2f}% "
        "(confira com a calculadora do BCB/B3 para a mesma janela)",
    ]


# --------------------------------------------------------------------------
# PTAX
# --------------------------------------------------------------------------

def validate_ptax(df: pd.DataFrame, start: str, end: str) -> list[Check]:
    """Verificações sobre o DataFrame transformado do PTAX."""
    checks = daily_checks(df, PTAX_COLUMNS, start, end)
    if df is None or df.empty:
        return checks

    price = df[COL_PTAX_VENDA].to_numpy(dtype="float64")
    ret = df[COL_PTAX_RET_DAY].to_numpy(dtype="float64")
    dates = df[COL_DATE]

    lo, hi = PTAX_PRICE_BOUNDS_EXCLUSIVE
    checks.append(_check(
        f"ptax_venda em ({lo:g}, {hi:g}) R$/US$",
        bool((price > lo).all() and (price < hi).all()),
        f"min={price.min():.4f}, max={price.max():.4f}",
    ))
    tlo, thi = PTAX_TYPICAL_RANGE
    checks.append(_check(
        "ptax_venda na faixa típica",
        bool(tlo <= price.min() and price.max() <= thi),
        f"{price.min():.4f} a {price.max():.4f} (típico {tlo:g} a {thi:g})",
        WARN,
    ))

    # A 1ª linha depende da observação descartada e não pode ser recalculada.
    ref = np.expm1(np.diff(np.log(np.where(price > 0, price, np.nan)))) * 100.0
    checks.append(_check(
        "variação diária = p_t / p_(t-1) - 1",
        bool(np.allclose(ret[1:], ref, rtol=1e-9, atol=1e-12)),
        "recalculada por log/expm1 (a 1ª linha fica fora: depende da observação descartada)",
    ))

    abs_ret = np.abs(ret)
    i = int(abs_ret.argmax())
    max_abs = float(abs_ret[i])
    if max_abs > PTAX_RET_FAIL_ABS_PCT:
        status = FAIL
    elif max_abs > PTAX_RET_WARN_ABS_PCT:
        status = WARN
    else:
        status = OK
    checks.append(Check(
        "variação diária dentro do esperado",
        status,
        f"maior |variação|={max_abs:.2f}% em {dates.iloc[i]:%Y-%m-%d} "
        f"(aviso > {PTAX_RET_WARN_ABS_PCT:g}%, falha > {PTAX_RET_FAIL_ABS_PCT:g}%)",
    ))
    return checks


def summarize_ptax(df: pd.DataFrame) -> list[str]:
    if df is None or df.empty:
        return []
    d = df[COL_DATE]
    price = df[COL_PTAX_VENDA]
    ret = df[COL_PTAX_RET_DAY]
    change = (price.iloc[-1] / price.iloc[0] - 1.0) * 100.0
    return [
        f"Linhas: {len(df)}",
        f"Período efetivo: {d.min():%Y-%m-%d} a {d.max():%Y-%m-%d}",
        f"PTAX venda (R$/US$): primeiro={price.iloc[0]:.4f}, último={price.iloc[-1]:.4f}, "
        f"mín={price.min():.4f}, máx={price.max():.4f}",
        f"Variação do primeiro ao último valor: {change:.2f}%",
        f"Maior alta diária: {ret.max():.2f}%; maior queda diária: {ret.min():.2f}%",
    ]


# --------------------------------------------------------------------------
# Entre séries
# --------------------------------------------------------------------------

def validate_date_overlap(
    a: pd.DataFrame,
    b: pd.DataFrame,
    name_a: str = "CDI",
    name_b: str = "PTAX",
    minimum: float = MIN_DATE_OVERLAP,
) -> Check:
    """Sobreposição de datas entre duas séries na janela que ambas cobrem.

    ``sobreposição = datas em comum / datas de qualquer uma das duas``, contadas
    só na janela comum (maior das primeiras datas até a menor das últimas), para
    que uma diferença nas pontas, como a 1ª linha descartada do PTAX, não conte.
    OK se ``>= minimum``; AVISO caso contrário.
    """
    name = f"sobreposição de datas {name_a} x {name_b}"
    if a is None or b is None or a.empty or b.empty:
        return Check(name, WARN, "uma das séries está vazia")

    dates_a, dates_b = set(a[COL_DATE]), set(b[COL_DATE])
    lo = max(min(dates_a), min(dates_b))
    hi = min(max(dates_a), max(dates_b))
    if lo > hi:
        return Check(name, WARN, "as séries não têm janela de datas em comum")

    wa = {d for d in dates_a if lo <= d <= hi}
    wb = {d for d in dates_b if lo <= d <= hi}
    union, common = wa | wb, wa & wb
    ratio = len(common) / len(union)
    detail = (
        f"{ratio:.2%} ({len(common)} em comum de {len(union)} datas, "
        f"janela {lo:%Y-%m-%d} a {hi:%Y-%m-%d}; só {name_a}: {len(wa - wb)}, "
        f"só {name_b}: {len(wb - wa)}; mínimo {minimum:.0%})"
    )
    return _check(name, ratio >= minimum - 1e-12, detail, WARN)


# --------------------------------------------------------------------------
# Saídas gravadas (depois do load)
# --------------------------------------------------------------------------

def validate_persisted(
    df: pd.DataFrame,
    columns: list[str],
    db_path: Path,
    csv_path: Path,
    table: str,
) -> list[Check]:
    """Relê SQLite e CSV e compara com o DataFrame que foi gravado."""
    checks: list[Check] = []

    try:
        conn = sqlite3.connect(db_path)
        try:
            stored = pd.read_sql_query(
                f'SELECT * FROM "{table}" ORDER BY "{COL_DATE}"', conn
            )
            pk = [
                r[1] for r in conn.execute(f'PRAGMA table_info("{table}")') if r[5]
            ]
        finally:
            conn.close()
        ok, detail = _same(stored, df, columns)
        checks.append(_check("SQLite == DataFrame", ok, detail))
        checks.append(_check(
            f"SQLite: chave primária em {COL_DATE}", pk == [COL_DATE], f"pk={pk}"
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("SQLite == DataFrame", FAIL, f"{type(exc).__name__}: {exc}"))

    try:
        stored = pd.read_csv(csv_path)
        ok, detail = _same(stored, df, columns)
        checks.append(_check("CSV == DataFrame", ok, detail))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("CSV == DataFrame", FAIL, f"{type(exc).__name__}: {exc}"))
    return checks


def _same(
    stored: pd.DataFrame, df: pd.DataFrame, columns: list[str]
) -> tuple[bool, str]:
    if list(stored.columns) != columns:
        return False, f"colunas diferentes: {list(stored.columns)}"
    if len(stored) != len(df):
        return False, f"{len(stored)} linhas lidas vs {len(df)} gravadas"
    got = pd.to_datetime(stored[COL_DATE]).to_numpy().astype("datetime64[D]")
    want = df[COL_DATE].to_numpy().astype("datetime64[D]")
    if not (got == want).all():
        return False, "datas diferentes"
    for col in columns[1:]:
        if not np.allclose(
            stored[col].to_numpy(dtype="float64"),
            df[col].to_numpy(dtype="float64"),
            rtol=FLOAT_RTOL, atol=0,
        ):
            return False, f"valores diferentes em {col}"
    return True, f"{len(df)} linhas idênticas"