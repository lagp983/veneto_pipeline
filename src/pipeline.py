"""Orquestração da pipeline (CDI, PTAX, IPCA e IBOV): extract -> transform -> load, com relatório.

Uso (a partir da raiz do repositório)::

    python -m src.pipeline                      # todas as séries (padrão)
    python -m src.pipeline --series ptax
    python -m src.pipeline --series ibov --start 2021-11-01 --end 2026-11-01

Fluxo, por série (cada série roda de forma independente):
    1. extract   (SGS via python-bcb para CDI/PTAX/IPCA; yfinance para IBOV)
    2. transform (colunas canônicas e derivados da série)
    3. validação dos dados (antes de gravar)
    4. load      (SQLite replace + CSV) -- pulado se a validação dos dados falhar,
       para não sobrescrever saídas boas com dados ruins
    5. validação das saídas (relê SQLite e CSV e compara com o DataFrame)
Com CDI, PTAX e/ou IBOV presentes, ainda há a checagem cruzada de sobreposição
de datas entre todos os pares de séries diárias; o IPCA, por ser mensal, não
entra nessa checagem.

Códigos de saída: 0 aprovado (avisos permitidos), 1 validação reprovada,
2 falha em uma etapa (extract/transform/load) de alguma série.

Compatibilidade: a API anterior, só do CDI, continua igual: ``run_pipeline``,
``PipelineReport``, ``validate_dataframe``, ``summarize``, ``validate_persisted``,
``_same``, ``_check`` e as constantes de limite. As regras em si agora vivem em
``validate.py``; aqui ficam os nomes antigos apontando para elas. O multi-série
é ``run_series`` (uma série) e ``run_all`` (uma ou todas).
"""

from __future__ import annotations

import argparse
import sqlite3  # noqa: F401 - mantido: fazia parte do namespace anterior
import sys
import time
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Callable, TypeVar

import numpy as np  # noqa: F401 - mantido: fazia parte do namespace anterior
import pandas as pd

from .config import (
    CDI_CSV_PATH,
    CDI_SGS_CODE,
    CDI_TABLE,
    COL_CDI_FACTOR_DAY,  # noqa: F401
    COL_CDI_PCT_DAY,  # noqa: F401
    COL_CDI_PCT_YEAR,  # noqa: F401
    COL_DATE,  # noqa: F401
    DB_PATH,
    DEFAULT_SERIES,
    IBOV_CSV_PATH,
    IBOV_TICKER,
    IBOV_TABLE,
    IPCA_CSV_PATH,
    IPCA_SGS_CODE,
    IPCA_TABLE,
    PTAX_CSV_PATH,
    PTAX_SGS_CODE,
    PTAX_TABLE,
    SERIES_ALL,
    SERIES_CDI,
    SERIES_CHOICES,
    SERIES_IBOV,
    SERIES_IPCA,
    SERIES_PTAX,
    IPCA_WARMUP_MONTHS,
    default_window,
)
from .extract import extract_cdi, extract_ibov, extract_ipca, extract_ptax
from .load import (
    CDI_COLUMNS,
    IBOV_COLUMNS,
    IPCA_COLUMNS,
    PTAX_COLUMNS,
    load_cdi,
    load_ibov,
    load_ipca,
    load_ptax,
)
from .transform import (  # noqa: F401 - BUSINESS_DAYS_PER_YEAR reexportado (API anterior)
    BUSINESS_DAYS_PER_YEAR,
    transform_cdi,
    transform_ibov,
    transform_ipca,
    transform_ptax,
)
from .validate import (  # noqa: F401 - limites e helpers reexportados (API anterior)
    EDGE_TOLERANCE_DAYS,
    FAIL,
    FLOAT_RTOL,
    MAX_GAP_DAYS,
    MIN_BUSINESS_DAY_COVERAGE,
    OK,
    PLAUSIBLE_PCT_YEAR,
    SKIPPED,
    TYPICAL_MEDIAN_PCT_DAY,
    WARN,
    Check,
    _check,
    summarize_cdi,
    summarize_ibov,
    summarize_ipca,
    summarize_ptax,
    validate_cdi,
    validate_date_overlap,
    validate_ibov,
    validate_ipca,
    validate_ptax,
)
from .validate import _same as _same_columns
from .validate import validate_persisted as _validate_persisted

T = TypeVar("T")


@dataclass(frozen=True)
class SeriesSpec:
    """Tudo o que a pipeline precisa saber de uma série."""

    name: str
    title: str
    extract: Callable[[str, str], pd.DataFrame]
    transform: Callable[[pd.DataFrame], pd.DataFrame]
    validate: Callable[[pd.DataFrame, str, str], list[Check]]
    summarize: Callable[[pd.DataFrame], list[str]]
    load: Callable[[pd.DataFrame, Path, Path, str], int]
    columns: list[str]
    table: str
    csv_path: Path
    # Meses extras buscados antes de `start` para os derivados da primeira
    # linha; o excedente é descartado após o transform. 0 = sem warmup.
    warmup_months: int = 0

# Rótulos usados no relatório (checagem cruzada e títulos).
_DISPLAY: dict[str, str] = {
    SERIES_CDI: "CDI",
    SERIES_PTAX: "PTAX",
    SERIES_IPCA: "IPCA",
    SERIES_IBOV: "IBOV",
}

# Séries diárias: únicas que participam da checagem cruzada de datas.
# O IPCA é mensal e fica de fora por construção.
_DAILY_SERIES = (SERIES_CDI, SERIES_PTAX, SERIES_IBOV)

SERIES: dict[str, SeriesSpec] = {
    SERIES_CDI: SeriesSpec(
        name=SERIES_CDI,
        title=f"Pipeline CDI (SGS {CDI_SGS_CODE})",
        extract=extract_cdi,
        transform=transform_cdi,
        validate=validate_cdi,
        summarize=summarize_cdi,
        load=load_cdi,
        columns=CDI_COLUMNS,
        table=CDI_TABLE,
        csv_path=CDI_CSV_PATH,
    ),
    SERIES_PTAX: SeriesSpec(
        name=SERIES_PTAX,
        title=f"Pipeline PTAX venda (SGS {PTAX_SGS_CODE})",
        extract=extract_ptax,
        transform=transform_ptax,
        validate=validate_ptax,
        summarize=summarize_ptax,
        load=load_ptax,
        columns=PTAX_COLUMNS,
        table=PTAX_TABLE,
        csv_path=PTAX_CSV_PATH,
    ),
    SERIES_IPCA: SeriesSpec(
        name=SERIES_IPCA,
        title=f"Pipeline IPCA (SGS {IPCA_SGS_CODE})",
        extract=extract_ipca,
        transform=transform_ipca,
        validate=validate_ipca,
        summarize=summarize_ipca,
        load=load_ipca,
        columns=IPCA_COLUMNS,
        table=IPCA_TABLE,
        csv_path=IPCA_CSV_PATH,
        warmup_months=IPCA_WARMUP_MONTHS,
    ),
        SERIES_IBOV: SeriesSpec(
        name=SERIES_IBOV,
        title=f"Pipeline IBOVESPA (yfinance {IBOV_TICKER})",
        extract=extract_ibov,
        transform=transform_ibov,
        validate=validate_ibov,
        summarize=summarize_ibov,
        load=load_ibov,
        columns=IBOV_COLUMNS,
        table=IBOV_TABLE,
        csv_path=IBOV_CSV_PATH,
    ),
}


@dataclass(frozen=True)
class Stage:
    name: str
    status: str
    detail: str
    seconds: float


@dataclass
class PipelineReport:
    """Relatório de uma série.

    Os cinco primeiros campos e a ordem dos demais são os da versão anterior;
    ``title`` e ``df`` entraram no fim, com padrão, para não quebrar quem
    constrói o relatório de forma posicional.
    """

    start: str
    end: str
    db_path: Path
    csv_path: Path
    table: str
    stages: list[Stage] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    stage_error: bool = False
    title: str = f"Pipeline CDI (SGS {CDI_SGS_CODE})"
    df: pd.DataFrame | None = field(default=None, repr=False)  # saída do transform

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
            f"=== {self.title} ===",
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
            lines += _render_checks(self.checks)
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


@dataclass
class PipelineRun:
    """Resultado de ``run_all``: um relatório por série e a checagem cruzada."""

    reports: list[PipelineReport]
    cross_checks: list[Check] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        code = max((r.exit_code for r in self.reports), default=0)
        if code == 0 and any(c.status == FAIL for c in self.cross_checks):
            code = 1
        return code

    def render(self) -> str:
        parts = [r.render() for r in self.reports]
        if self.cross_checks:
            parts.append(
                "\n".join(["=== Qualidade cruzada ==="] + _render_checks(self.cross_checks))
            )
        if len(self.reports) > 1:
            n_checks = sum(len(r.checks) for r in self.reports) + len(self.cross_checks)
            n_fail = sum(r.n_failures for r in self.reports) + sum(
                c.status == FAIL for c in self.cross_checks
            )
            n_warn = sum(r.n_warnings for r in self.reports) + sum(
                c.status == WARN for c in self.cross_checks
            )
            verdict = {0: "APROVADO", 1: "REPROVADO"}.get(self.exit_code, "ERRO EM ETAPA")
            parts.append(
                f"RESULTADO GERAL: {verdict} ({len(self.reports)} séries, "
                f"{n_checks} verificações, {n_fail} falha(s), {n_warn} aviso(s))"
            )
        return "\n\n".join(parts)


def _render_checks(checks: list[Check]) -> list[str]:
    width = max(len(c.name) for c in checks)
    return [f"  {f'[{c.status}]':<9} {c.name:<{width}}  {c.detail}" for c in checks]


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
    """Executa a pipeline do CDI e devolve o relatório (não levanta por falha de etapa).

    Mesma assinatura e comportamento da versão anterior. Sem ``start``/``end``,
    usa os últimos ``LOOKBACK_YEARS`` anos até hoje. Para PTAX, IPCA ou várias
    séries, use ``run_series`` / ``run_all``.
    """
    return run_series(SERIES[SERIES_CDI], start, end, db_path, csv_path, table, extract_fn)


def run_series(
    spec: SeriesSpec,
    start: str | None = None,
    end: str | None = None,
    db_path: Path | str = DB_PATH,
    csv_path: Path | str | None = None,
    table: str | None = None,
    extract_fn: Callable[[str, str], pd.DataFrame] | None = None,
) -> PipelineReport:
    """Executa a pipeline de uma série e devolve o relatório (não levanta por falha de etapa).

    Sem ``start``/``end``, usa os últimos ``LOOKBACK_YEARS`` anos até hoje.

    Se ``spec.warmup_months > 0`` (caso do IPCA), o extract é chamado
    ``warmup_months`` meses antes de ``start`` para que a primeira linha da
    janela pedida já tenha os dados anteriores necessários ao cálculo dos
    derivados (ex.: ``ipca_pct_12m``). O excedente é descartado após o
    transform, de modo que ``report.df`` cobre exatamente ``[start, end]`` e
    todas as validações continuam usando a janela pedida, sem alteração.
    """
    default_start, default_end = default_window()
    start = start or default_start
    end = end or default_end
    table = table or spec.table
    extract_fn = extract_fn or spec.extract
    extract_start = _shift_months_back(start, spec.warmup_months)

    report = PipelineReport(
        start, end, Path(db_path), Path(csv_path or spec.csv_path), table, title=spec.title
    )
    try:
        raw = _stage(
            report, "extract",
            lambda: extract_fn(extract_start, end),
            lambda r: f"{len(r)} linhas brutas" + (
                f" (warmup: janela {extract_start} a {end})"
                if extract_start != start else ""
            ),
        )
        df = _stage(
            report, "transform", lambda: spec.transform(raw),
            lambda d: f"{len(d)} linhas canônicas",
        )

        if spec.warmup_months:
            before = len(df)
            df = df[df[COL_DATE] >= pd.Timestamp(start)].reset_index(drop=True)
            if before > len(df):
                report.info.append(
                    f"Warmup: {before - len(df)} linha(s) antes de {start} "
                    f"usada(s) só no cálculo dos derivados e descartada(s)."
                )

        report.df = df
        report.checks += spec.validate(df, start, end)
        report.info += spec.summarize(df)

        if report.n_failures:
            report.stages.append(
                Stage("load", SKIPPED,
                      "validação dos dados reprovada; saídas anteriores preservadas", 0.0)
            )
            return report

        _stage(
            report, "load",
            lambda: spec.load(df, report.db_path, report.csv_path, table),
            lambda n: f"{n} linhas gravadas",
        )
        report.checks += _validate_persisted(
            df, spec.columns, report.db_path, report.csv_path, table
        )
    except _StageFailed:
        report.stage_error = True
    return report


def run_all(
    series: str = DEFAULT_SERIES,
    start: str | None = None,
    end: str | None = None,
    db_path: Path | str = DB_PATH,
    csv_paths: dict[str, Path | str] | None = None,
    extract_fns: dict[str, Callable[[str, str], pd.DataFrame]] | None = None,
) -> PipelineRun:
    """Executa uma série ou todas (``series="all"``), cada uma de forma independente.

    ``csv_paths`` troca o CSV de cada série (chave: ``"cdi"``/``"ptax"``/``"ipca"``/``"ibov"``);    
    sem ele, usa os caminhos do ``config.py``. ``extract_fns`` troca a extração por
    série (testes). Todas as séries usam a mesma janela.

    A checagem cruzada de sobreposição de datas ocorre entre todos os pares
    de séries diárias presentes (CDI, PTAX, IBOV). O IPCA, por ser mensal,
    não é comparado por data com as demais. A checagem é executada sempre
    que pelo menos duas séries diárias estiverem nos relatórios,
    independentemente de a chamada ser ``series="all"`` ou uma combinação
    que inclua as duas.
    """
    if series not in SERIES_CHOICES:
        raise ValueError(f"Série desconhecida: {series!r}. Opções: {SERIES_CHOICES}.")
    specs = list(SERIES.values()) if series == SERIES_ALL else [SERIES[series]]

    default_start, default_end = default_window()
    start, end = start or default_start, end or default_end

    reports = [
        run_series(
            spec, start, end, db_path,
            (csv_paths or {}).get(spec.name),
            extract_fn=(extract_fns or {}).get(spec.name),
        )
        for spec in specs
    ]

    run = PipelineRun(reports)
    by_name = {spec.name: report for spec, report in zip(specs, reports)}
    daily_present = [name for name in _DAILY_SERIES if name in by_name]
    for name_a, name_b in combinations(daily_present, 2):
        run.cross_checks.append(
            _overlap_check(by_name[name_a], by_name[name_b], name_a, name_b)
        )
    return run


def _overlap_check(
    a: PipelineReport,
    b: PipelineReport,
    name_a: str = SERIES_CDI,
    name_b: str = SERIES_PTAX,
) -> Check:
    """Checagem cruzada de sobreposição de datas entre duas séries diárias.

    Os defaults (``cdi``, ``ptax``) preservam a chamada da versão anterior;
    ``run_all`` passa explicitamente o par para as combinações novas.
    """
    label = f"sobreposição de datas {_DISPLAY[name_a]} x {_DISPLAY[name_b]}"
    if a.df is None or b.df is None:
        return Check(label, SKIPPED, "uma das séries não chegou a ser transformada")
    return validate_date_overlap(
        a.df, b.df, _DISPLAY[name_a], _DISPLAY[name_b]
    )


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

def _shift_months_back(date_str: str, months: int) -> str:
    """Subtrai ``months`` meses de ``date_str`` (YYYY-MM-DD) e devolve YYYY-MM-DD.

    ``months <= 0`` devolve ``date_str`` sem alteração. Usa ``pd.DateOffset``,
    que trata fim de mês e ano bissexto (ex.: 2024-03-31 − 1 mês = 2024-02-29).
    """
    if months <= 0:
        return date_str
    return (pd.Timestamp(date_str) - pd.DateOffset(months=months)).strftime("%Y-%m-%d")
# --------------------------------------------------------------------------
# Validação dos dados (antes do load) -- nomes anteriores do CDI; regras em validate.py
# --------------------------------------------------------------------------

def validate_dataframe(df: pd.DataFrame, start: str, end: str) -> list[Check]:
    """Verificações sobre o DataFrame transformado do CDI (ver ``validate.validate_cdi``)."""
    return validate_cdi(df, start, end)


def summarize(df: pd.DataFrame) -> list[str]:
    """Resumo do CDI (ver ``validate.summarize_cdi``)."""
    return summarize_cdi(df)


# --------------------------------------------------------------------------
# Validação das saídas (depois do load) -- nomes anteriores do CDI; regras em validate.py
# --------------------------------------------------------------------------

def validate_persisted(
    df: pd.DataFrame, db_path: Path, csv_path: Path, table: str
) -> list[Check]:
    """Relê SQLite e CSV do CDI e compara com o DataFrame gravado."""
    return _validate_persisted(df, CDI_COLUMNS, db_path, csv_path, table)


def _same(stored: pd.DataFrame, df: pd.DataFrame) -> tuple[bool, str]:
    return _same_columns(stored, df, CDI_COLUMNS)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.pipeline",
        description=(
            "Pipeline CDI, PTAX, IPCA e IBOV: extract, transform, load "
            "e relatório de validação."
        ),
    )
    parser.add_argument(
        "--series", choices=SERIES_CHOICES, default=DEFAULT_SERIES,
        help="série a processar (padrão: %(default)s)",
    )
    parser.add_argument(
        "--start",
        help="data inicial (YYYY-MM-DD); padrão: 5 anos antes de --end",
    )
    parser.add_argument(
        "--end", help="data final (YYYY-MM-DD); padrão: hoje (fuso de Brasília)"
    )
    parser.add_argument("--db", default=str(DB_PATH), help="arquivo SQLite de saída")
    parser.add_argument(
        "--csv", default=str(CDI_CSV_PATH), help="arquivo CSV de saída do CDI"
    )
    parser.add_argument(
        "--ptax-csv", default=str(PTAX_CSV_PATH), help="arquivo CSV de saída do PTAX"
    )
    parser.add_argument(
        "--ipca-csv", default=str(IPCA_CSV_PATH), help="arquivo CSV de saída do IPCA"
    )
    parser.add_argument(
        "--ibov-csv", default=str(IBOV_CSV_PATH),
        help="arquivo CSV de saída do IBOV",
    )
    args = parser.parse_args(argv)

    try:
        end = args.end or default_window()[1]
        start = args.start or default_window(pd.Timestamp(end).date())[0]
        if pd.Timestamp(start) >= pd.Timestamp(end):
            parser.error("--start deve ser anterior a --end")
    except ValueError:
        parser.error("--start/--end devem estar no formato YYYY-MM-DD")

    run = run_all(
        args.series, start, end, args.db,
        {
            SERIES_CDI: args.csv,
            SERIES_PTAX: args.ptax_csv,
            SERIES_IPCA: args.ipca_csv,
            SERIES_IBOV: args.ibov_csv,
        },
    )
    print(run.render())
    return run.exit_code


if __name__ == "__main__":
    sys.exit(main())