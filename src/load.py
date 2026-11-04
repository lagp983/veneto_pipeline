"""Carga das séries: tabela SQLite (replace) e CSV em ``data/``.

Recebe o DataFrame já transformado. Não baixa, não limpa, não calcula.
``load_table`` é o núcleo comum; ``load_cdi``, ``load_ptax`` e ``load_ipca``
só fixam colunas, tabela e arquivos de cada série.
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
    COL_IPCA_FACTOR_MONTH,
    COL_IPCA_PCT_12M,
    COL_IPCA_PCT_MONTH,
    COL_PTAX_RET_DAY,
    COL_PTAX_VENDA,
    DB_PATH,
    IPCA_CSV_PATH,
    IPCA_TABLE,
    PTAX_CSV_PATH,
    PTAX_TABLE,
)

CDI_COLUMNS = [COL_DATE, COL_CDI_PCT_DAY, COL_CDI_FACTOR_DAY, COL_CDI_PCT_YEAR]
PTAX_COLUMNS = [COL_DATE, COL_PTAX_VENDA, COL_PTAX_RET_DAY]
IPCA_COLUMNS = [COL_DATE, COL_IPCA_PCT_MONTH, COL_IPCA_FACTOR_MONTH, COL_IPCA_PCT_12M]
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

    Veja ``load_table`` para o comportamento e os erros. Devolve o número de
    linhas gravadas.
    """
    return load_table(df, CDI_COLUMNS, db_path, csv_path, table, label="CDI")


def load_ptax(
    df: pd.DataFrame,
    db_path: Path | str = DB_PATH,
    csv_path: Path | str = PTAX_CSV_PATH,
    table: str = PTAX_TABLE,
) -> int:
    """Grava o PTAX transformado em SQLite (tabela substituída) e em CSV.

    Mesmo banco do CDI, tabela própria: só a tabela do PTAX é substituída.
    Veja ``load_table`` para o comportamento e os erros. Devolve o número de
    linhas gravadas.
    """
    return load_table(df, PTAX_COLUMNS, db_path, csv_path, table, label="PTAX")

def load_ipca(
    df: pd.DataFrame,
    db_path: Path | str = DB_PATH,
    csv_path: Path | str = IPCA_CSV_PATH,
    table: str = IPCA_TABLE,
) -> int:
    """Grava o IPCA transformado em SQLite (tabela substituída) e em CSV.

    Mesmo banco do CDI e do PTAX, tabela própria: só a tabela do IPCA é
    substituída. ``ipca_pct_month`` pode ser negativo (deflação); a coluna é
    ``REAL NOT NULL``, o que aceita negativos sem tratamento especial.
    Veja ``load_table`` para o comportamento e os erros. Devolve o número de
    linhas gravadas.
    """
    return load_table(df, IPCA_COLUMNS, db_path, csv_path, table, label="IPCA")
    
def load_table(
    df: pd.DataFrame,
    columns: list[str],
    db_path: Path | str,
    csv_path: Path | str,
    table: str,
    label: str,
) -> int:
    """Grava ``df`` em SQLite (tabela substituída) e em CSV.

    - SQLite: ``DROP TABLE IF EXISTS`` + ``CREATE TABLE`` + inserts em uma
      única transação; se algo falhar, a tabela anterior permanece e as demais
      tabelas do banco não são tocadas. A primeira coluna (``date``) é ``TEXT``
      ISO e chave primária; as demais são ``REAL NOT NULL``.
    - CSV: sobrescrito de forma atômica (arquivo temporário + ``os.replace``),
      sem índice, com as mesmas colunas e datas ISO.

    Cria o diretório de destino se não existir. Devolve o número de linhas.

    Raises
    ------
    ValueError
        DataFrame vazio, colunas diferentes de ``columns``, ``date`` que não
        seja datetime, datas duplicadas, valores nulos, ou nome de tabela
        inválido.
    """
    _validate(df, columns, table, label)

    db_path, csv_path = Path(db_path), Path(csv_path)
    out = df[columns].copy()
    out[COL_DATE] = out[COL_DATE].dt.strftime(DATE_FORMAT)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_csv = csv_path.with_name(csv_path.name + ".tmp")
    try:
        out.to_csv(tmp_csv, index=False)
        _replace_table(out, columns, db_path, table)
        os.replace(tmp_csv, csv_path)
    finally:
        tmp_csv.unlink(missing_ok=True)

    return len(out)


def _replace_table(
    out: pd.DataFrame, columns: list[str], db_path: Path, table: str
) -> None:
    date_col, *value_cols = columns
    column_defs = [f'"{date_col}" TEXT PRIMARY KEY'] + [
        f'"{c}" REAL NOT NULL' for c in value_cols
    ]
    placeholders = ", ".join("?" for _ in columns)

    conn = sqlite3.connect(db_path)
    try:
        with conn:  # commit no sucesso, rollback na exceção
            # O sqlite3 do Python só abre transação sozinho em INSERT/UPDATE/DELETE;
            # sem este BEGIN, o DROP/CREATE rodariam fora da transação.
            conn.execute("BEGIN")
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            conn.execute(f'CREATE TABLE "{table}" ({", ".join(column_defs)})')
            conn.executemany(
                f'INSERT INTO "{table}" VALUES ({placeholders})',
                out.itertuples(index=False, name=None),
            )
    finally:
        conn.close()


def _validate(df: pd.DataFrame, columns: list[str], table: str, label: str) -> None:
    if columns[0] != COL_DATE:
        raise ValueError(f"A primeira coluna deve ser {COL_DATE!r}, recebido {columns}.")
    if not _IDENTIFIER.match(table):
        raise ValueError(f"Nome de tabela inválido: {table!r}.")
    if df is None or df.empty:
        raise ValueError(f"load_{label.lower()} recebeu um DataFrame vazio.")
    if list(df.columns) != columns:
        raise ValueError(
            f"Colunas inesperadas: {list(df.columns)}. Esperado: {columns}."
        )
    if not pd.api.types.is_datetime64_any_dtype(df[COL_DATE]):
        raise ValueError(f"Coluna {COL_DATE!r} deve ser datetime.")
    if df[columns].isna().any().any():
        raise ValueError(f"DataFrame do {label} contém valores nulos.")
    if df[COL_DATE].duplicated().any():
        raise ValueError(f"DataFrame do {label} contém datas duplicadas.")