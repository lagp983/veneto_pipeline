"""Orquestração da pipeline do CDI: extract -> transform -> load, com relatório.

Uso (a partir da raiz do repositório)::

    python -m src.pipeline
    python -m src.pipeline --start 2021-11-01 --end 2026-11-01

Fluxo:
    1. extract   (SGS 12 via python-bcb)
    2. transform (colunas canônicas, fator diário, anualização 252)
    3. validação dos dados (antes de gravar)
    4. load      (SQLite replace + CSV) -- pulado se a validação dos dados falhar,
       para não sobrescrever saídas boas com dados ruins
    5. validação das saídas (relê SQLite e CSV e compara com o DataFrame)

Códigos de saída: 0 aprovado (avisos permitidos), 1 validação reprovada,
2 falha em uma etapa (extract/transform/load).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TypeVar

import numpy as np
import pandas as pd

from .config import (
    CDI_CSV_PATH,
    CDI_SGS_CODE,
    CDI_TABLE,
    COL_CDI_FACTOR_DAY,
    COL_CDI_PCT_DAY,
    COL_CDI_PCT_YEAR,
    COL_DATE,
    DB_PATH,
    default_window,
)
from .extract import extract_cdi
from .load import CDI_COLUMNS, load_cdi
from .transform import BUSINESS_DAYS_PER_YEAR, transform_cdi

OK, WARN, FAIL, SKIPPED = "OK", "AVISO", "FALHA", "PULADO"

# Faixas usadas só na validação (o transform já garante 0 < x < 1).
TYPICAL_MEDIAN_PCT_DAY = (0.007, 0.06)
PLAUSIBLE_PCT_YEAR = (5.0, 20.0)
MIN_BUSINESS_DAY_COVERAGE = 0.90  # feriados nacionais ficam em ~96%
EDGE_TOLERANCE_DAYS = 5
MAX_GAP_DAYS = 7
FLOAT_RTOL = 1e-12

T = TypeVar("T")


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class Stage:
    name: str
    status: str
    detail: str
    seconds: float


@dataclass
class PipelineReport:
    start: str
    end: str
    db_path: Path
    csv_path: Path
    table: str
    stages: list[Stage] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    stage_error: bool = False

    @property
    def n_failures(self) -> int:
        return sum(c.status == FAIL for c in self.checks)

    @property
    def n_warnings(self) -> int:
        return sum(c.status == WARN for c in self.checks)

    @property
    def exit_code(self) -> int:
        if self.stage_error:
            return 2
        return 1 if self.n_failures else 0

    def render(self) -> str:
        lines = [
            f"=== Pipeline CDI (SGS {CDI_SGS_CODE}) ===",
            f"Janela pedida: {self.start} a {self.end}",
            f"SQLite: {self.db_path} (tabela '{self.table}', replace)",
            f"CSV:    {self.csv_path}",
            "",
            "Etapas",
        ]
        for s in self.stages:
            lines.append(
                f"  {f'[{s.status}]':<9} {s.name:<10} {s.detail} ({s.seconds:.1f}s)"
            )
        if self.checks:
            lines += ["", "Validação"]
            width = max(len(c.name) for c in self.checks)
            for c in self.checks:
                lines.append(f"  {f'[{c.status}]':<9} {c.name:<{width}}  {c.detail}")
        if self.info:
            lines += ["", "Resumo dos dados"]
            lines += [f"  {i}" for i in self.info]

        if self.stage_error:
            verdict = "ERRO EM ETAPA"
        elif self.n_failures:
            verdict = "REPROVADO"
        else:
            verdict = "APROVADO"
        lines += [
            "",
            f"RESULTADO: {verdict} "
            f"({len(self.checks)} verificações, {self.n_failures} falha(s), "
            f"{self.n_warnings} aviso(s))",
        ]
        return "\n".join(lines)


class _StageFailed(Exception):
    """Interrompe a pipeline depois de registrar a falha da etapa."""


def run_pipeline(
    start: str | None = None,
    end: str | None = None,
    db_path: Path | str = DB_PATH,
    csv_path: Path | str = CDI_CSV_PATH,
    table: str = CDI_TABLE,
    extract_fn: Callable[[str, str], pd.DataFrame] = extract_cdi,
) -> PipelineReport:
    """Executa a pipeline completa e devolve o relatório (não levanta por falha de etapa).

    Sem ``start``/``end``, usa os últimos ``LOOKBACK_YEARS`` anos até hoje.
    """
    default_start, default_end = default_window()
    start = start or default_start
    end = end or default_end
    report = PipelineReport(start, end, Path(db_path), Path(csv_path), table)
    try:
        raw = _stage(
            report, "extract", lambda: extract_fn(start, end),
            lambda r: f"{len(r)} linhas brutas",
        )
        df = _stage(
            report, "transform", lambda: transform_cdi(raw),
            lambda d: f"{len(d)} linhas canônicas",
        )

        report.checks += validate_dataframe(df, start, end)
        report.info += summarize(df)

        if report.n_failures:
            report.stages.append(
                Stage("load", SKIPPED,
                      "validação dos dados reprovada; saídas anteriores preservadas", 0.0)
            )
            return report

        _stage(
            report, "load",
            lambda: load_cdi(df, report.db_path, report.csv_path, table),
            lambda n: f"{n} linhas gravadas",
        )
        report.checks += validate_persisted(df, report.db_path, report.csv_path, table)
    except _StageFailed:
        report.stage_error = True
    return report


def _stage(
    report: PipelineReport,
    name: str,
    fn: Callable[[], T],
    describe: Callable[[T], str],
) -> T:
    t0 = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - qualquer falha vira relatório
        report.stages.append(
            Stage(name, FAIL, f"{type(exc).__name__}: {exc}", time.perf_counter() - t0)
        )
        raise _StageFailed from exc
    report.stages.append(Stage(name, OK, describe(result), time.perf_counter() - t0))
    return result


# --------------------------------------------------------------------------
# Validação dos dados (antes do load)
# --------------------------------------------------------------------------

def _check(name: str, ok: bool, detail: str, severity: str = FAIL) -> Check:
    return Check(name, OK if ok else severity, detail)


def validate_dataframe(df: pd.DataFrame, start: str, end: str) -> list[Check]:
    """Verificações sobre o DataFrame transformado."""
    if df is None or df.empty:
        return [Check("dados não vazios", FAIL, "DataFrame vazio")]

    d = df[COL_DATE]
    pct = df[COL_CDI_PCT_DAY].to_numpy(dtype="float64")
    factor = df[COL_CDI_FACTOR_DAY].to_numpy(dtype="float64")
    year = df[COL_CDI_PCT_YEAR].to_numpy(dtype="float64")
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    first, last = d.min(), d.max()
    checks: list[Check] = []

    checks.append(_check(
        "colunas canônicas", list(df.columns) == CDI_COLUMNS,
        ", ".join(df.columns),
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


def summarize(df: pd.DataFrame) -> list[str]:
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
# Validação das saídas (depois do load)
# --------------------------------------------------------------------------

def validate_persisted(
    df: pd.DataFrame, db_path: Path, csv_path: Path, table: str
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
        ok, detail = _same(stored, df)
        checks.append(_check("SQLite == DataFrame", ok, detail))
        checks.append(_check(
            f"SQLite: chave primária em {COL_DATE}", pk == [COL_DATE], f"pk={pk}"
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("SQLite == DataFrame", FAIL, f"{type(exc).__name__}: {exc}"))

    try:
        stored = pd.read_csv(csv_path)
        ok, detail = _same(stored, df)
        checks.append(_check("CSV == DataFrame", ok, detail))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("CSV == DataFrame", FAIL, f"{type(exc).__name__}: {exc}"))
    return checks


def _same(stored: pd.DataFrame, df: pd.DataFrame) -> tuple[bool, str]:
    if list(stored.columns) != CDI_COLUMNS:
        return False, f"colunas diferentes: {list(stored.columns)}"
    if len(stored) != len(df):
        return False, f"{len(stored)} linhas lidas vs {len(df)} gravadas"
    got = pd.to_datetime(stored[COL_DATE]).to_numpy().astype("datetime64[D]")
    want = df[COL_DATE].to_numpy().astype("datetime64[D]")
    if not (got == want).all():
        return False, "datas diferentes"
    for col in CDI_COLUMNS[1:]:
        if not np.allclose(
            stored[col].to_numpy(dtype="float64"),
            df[col].to_numpy(dtype="float64"),
            rtol=FLOAT_RTOL, atol=0,
        ):
            return False, f"valores diferentes em {col}"
    return True, f"{len(df)} linhas idênticas"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.pipeline",
        description="Pipeline do CDI (SGS 12): extract, transform, load e relatório de validação.",
    )
    parser.add_argument(
        "--start",
        help="data inicial (YYYY-MM-DD); padrão: 5 anos antes de --end",
    )
    parser.add_argument(
        "--end", help="data final (YYYY-MM-DD); padrão: hoje (fuso de Brasília)"
    )
    parser.add_argument("--db", default=str(DB_PATH), help="arquivo SQLite de saída")
    parser.add_argument("--csv", default=str(CDI_CSV_PATH), help="arquivo CSV de saída")
    args = parser.parse_args(argv)

    try:
        end = args.end or default_window()[1]
        start = args.start or default_window(pd.Timestamp(end).date())[0]
        if pd.Timestamp(start) >= pd.Timestamp(end):
            parser.error("--start deve ser anterior a --end")
    except ValueError:
        parser.error("--start/--end devem estar no formato YYYY-MM-DD")

    report = run_pipeline(start, end, args.db, args.csv)
    print(report.render())
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())