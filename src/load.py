"""Carga do CDI: tabela SQLite (replace) e CSV em ``data/``.

Recebe o DataFrame já transformado. Não baixa, não limpa, não anualiza.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import pandas as pd

from .config import (
    CDI_CSV_PATH,
    CDI_TABLE,
    COL_CDI_FACTOR_DAY,
    COL_CDI_PCT_DAY,
    COL_CDI_PCT_YEAR,
    COL_DATE,
    DB_PATH,
)

CDI_COLUMNS = [COL_DATE, COL_CDI_PCT_DAY, COL_CDI_FACTOR_DAY, COL_CDI_PCT_YEAR]

# Datas gravadas como texto ISO (YYYY-MM-DD) no SQLite e no CSV.
DATE_FORMAT = "%Y-%m-%d"

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_cdi(
    df: pd.DataFrame,
    db_path: Path | str = DB_PATH,
    csv_path: Path | str = CDI_CSV_PATH,
    table: str = CDI_TABLE,
) -> int:
    """Grava o CDI transformado em SQLite (tabela substituída) e em CSV.

    - SQLite: ``DROP TABLE IF EXISTS`` + ``CREATE TABLE`` + inserts em uma
      única transação; se algo falhar, a tabela anterior permanece.
      ``date`` é ``TEXT`` ISO e chave primária.
    - CSV: sobrescrito de forma atômica (arquivo temporário + ``os.replace``),
      sem índice, com as mesmas colunas e datas ISO.

    Cria o diretório de destino se não existir. Devolve o número de linhas.

    Raises
    ------
    ValueError
        DataFrame vazio, colunas diferentes das canônicas, ``date`` que não
        seja datetime, datas duplicadas, valores nulos, ou nome de tabela
        inválido.
    """
    _validate(df, table)

    db_path, csv_path = Path(db_path), Path(csv_path)
    out = df[CDI_COLUMNS].copy()
    out[COL_DATE] = out[COL_DATE].dt.strftime(DATE_FORMAT)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_csv = csv_path.with_name(csv_path.name + ".tmp")
    try:
        out.to_csv(tmp_csv, index=False)
        _replace_table(out, db_path, table)
        os.replace(tmp_csv, csv_path)
    finally:
        tmp_csv.unlink(missing_ok=True)

    return len(out)


def _replace_table(out: pd.DataFrame, db_path: Path, table: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        with conn:  # transação: commit no sucesso, rollback na exceção
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            conn.execute(
                f'CREATE TABLE "{table}" ('
                f'"{COL_DATE}" TEXT PRIMARY KEY, '
                f'"{COL_CDI_PCT_DAY}" REAL NOT NULL, '
                f'"{COL_CDI_FACTOR_DAY}" REAL NOT NULL, '
                f'"{COL_CDI_PCT_YEAR}" REAL NOT NULL)'
            )
            conn.executemany(
                f'INSERT INTO "{table}" VALUES (?, ?, ?, ?)',
                out.itertuples(index=False, name=None),
            )
    finally:
        conn.close()


def _validate(df: pd.DataFrame, table: str) -> None:
    if not _IDENTIFIER.match(table):
        raise ValueError(f"Nome de tabela inválido: {table!r}.")
    if df is None or df.empty:
        raise ValueError("load_cdi recebeu um DataFrame vazio.")
    if list(df.columns) != CDI_COLUMNS:
        raise ValueError(
            f"Colunas inesperadas: {list(df.columns)}. Esperado: {CDI_COLUMNS}."
        )
    if not pd.api.types.is_datetime64_any_dtype(df[COL_DATE]):
        raise ValueError(f"Coluna {COL_DATE!r} deve ser datetime.")
    if df[CDI_COLUMNS].isna().any().any():
        raise ValueError("DataFrame do CDI contém valores nulos.")
    if df[COL_DATE].duplicated().any():
        raise ValueError("DataFrame do CDI contém datas duplicadas.")