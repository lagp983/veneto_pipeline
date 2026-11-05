"""Acesso do dashboard aos dados: leitura do SQLite do ETL e disparo da atualização.

Sem Streamlit aqui, para poder ser testado sem a interface. Só ``src.config`` e
``src.load`` (caminhos, nomes de tabela e colunas) são importados no topo;
``src.pipeline``, que puxa python-bcb e yfinance, só é importado dentro de
``update_all``. Assim o dashboard abre mesmo que uma dessas bibliotecas falhe.

Contrato de leitura (o mesmo que ``src.load`` grava):

- ``cdi``, ``ptax``, ``ipca`` e ``ibov``: uma tabela por série, na ordem de
  colunas de ``CDI_COLUMNS``, ``PTAX_COLUMNS``, ``IPCA_COLUMNS`` e ``IBOV_COLUMNS``.
- ``date`` é texto ISO (``YYYY-MM-DD``); as demais colunas são ``REAL``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd

from src.config import (
    CDI_TABLE,
    COL_DATE,
    DB_PATH,
    IBOV_TABLE,
    IPCA_TABLE,
    PTAX_TABLE,
    SERIES_ALL,
    SERIES_CDI,
    SERIES_IBOV,
    SERIES_IPCA,
    SERIES_PTAX,
)
from src.load import (
    CDI_COLUMNS,
    DATE_FORMAT,
    IBOV_COLUMNS,
    IPCA_COLUMNS,
    PTAX_COLUMNS,
)

# Sobrescreve o caminho do banco (usado pelos testes).
ENV_DB_PATH = "MARKET_DB_PATH"
# Espera máxima por um lock do SQLite (ETL gravando) antes de desistir.
SQLITE_TIMEOUT_SECONDS = 10

# Nome da série -> (tabela, colunas esperadas, na ordem em que o ETL grava).
SERIES_TABLES: dict[str, tuple[str, list[str]]] = {
    SERIES_CDI: (CDI_TABLE, CDI_COLUMNS),
    SERIES_PTAX: (PTAX_TABLE, PTAX_COLUMNS),
    SERIES_IPCA: (IPCA_TABLE, IPCA_COLUMNS),
    SERIES_IBOV: (IBOV_TABLE, IBOV_COLUMNS),
}


class StoreError(Exception):
    """Não foi possível ler a série (banco travado/ilegível, schema ou datas fora do contrato).

    A mensagem já é legível para o usuário final; o app pode exibi-la como está.
    """


def db_path() -> Path:
    """Caminho do SQLite: ``MARKET_DB_PATH`` se definida, senão ``DB_PATH`` do ETL."""
    override = os.environ.get(ENV_DB_PATH)
    return Path(override) if override else Path(DB_PATH)


def db_mtime(path: Path | str | None = None) -> int | None:
    """``st_mtime_ns`` do banco, ou ``None`` se o arquivo não existir.

    Serve de chave de cache no app: quando o ETL regrava o arquivo (pelo botão
    ou por fora), o valor muda e a leitura em cache deixa de valer.
    """
    target = Path(path) if path is not None else db_path()
    try:
        return target.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def read_series(name: str, path: Path | str | None = None) -> pd.DataFrame | None:
    """Lê uma série do SQLite (somente leitura), ordenada por data.

    Devolve um DataFrame com as colunas canônicas da série e ``date`` como
    ``datetime64``, ou ``None`` se não houver dado: arquivo ausente, tabela
    ausente (ETL rodado só para outras séries) ou tabela vazia.

    Raises
    ------
    ValueError
        ``name`` não é uma das séries de ``SERIES_TABLES`` (erro de programação).
    StoreError
        Banco ocupado ou ilegível, colunas diferentes das de ``src.load`` ou
        datas fora do formato ISO.
    """
    if name not in SERIES_TABLES:
        raise ValueError(f"Série desconhecida: {name!r}. Opções: {list(SERIES_TABLES)}.")
    table, columns = SERIES_TABLES[name]

    target = Path(path) if path is not None else db_path()
    if not target.is_file():
        return None

    # Cursor direto, sem pandas.read_sql_query: o pandas embrulha os erros do
    # SQLite em outra exceção e esconderia o "database is locked".
    try:
        conn = _connect_readonly(target)
        try:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if has_table is None:
                return None
            cursor = conn.execute(f'SELECT * FROM "{table}" ORDER BY "{COL_DATE}"')
            found = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise StoreError(_sqlite_message(exc)) from exc

    if found != columns:
        raise StoreError(
            f"Tabela '{table}' com colunas fora do contrato do ETL: {found}. "
            f"Esperado: {columns}."
        )
    if not rows:
        return None

    df = pd.DataFrame(rows, columns=columns)
    try:
        df[COL_DATE] = pd.to_datetime(df[COL_DATE], format=DATE_FORMAT)
    except (ValueError, TypeError) as exc:
        raise StoreError(
            f"Tabela '{table}': datas fora do formato {DATE_FORMAT} ({exc})."
        ) from exc
    value_columns = columns[1:]
    df[value_columns] = df[value_columns].astype("float64")
    return df


def update_all() -> tuple[int, str]:
    """Roda o ETL completo (todas as séries) e devolve ``(exit_code, relatório)``.

    ``exit_code`` segue o da CLI do ETL: 0 aprovado, 1 validação reprovada
    (as saídas anteriores são preservadas), 2 falha em alguma etapa. O texto é
    o relatório completo (``PipelineRun.render``).

    Grava no banco de ``db_path()``; os CSVs vão para os caminhos padrão do ETL.
    Qualquer exceção, inclusive a de importar o pipeline (python-bcb ou yfinance
    ausentes), vira ``(2, "Tipo: mensagem")`` em vez de derrubar o app.
    Bloqueia até o fim: são vários downloads, com timeout de até 120 s cada.
    """
    try:
        from src.pipeline import run_all  # import tardio: ver docstring do módulo

        run = run_all(SERIES_ALL, db_path=db_path())
        return run.exit_code, run.render()
    except Exception as exc:  # noqa: BLE001 - qualquer falha vira resultado exibível
        return 2, f"{type(exc).__name__}: {exc}"


def _connect_readonly(path: Path) -> sqlite3.Connection:
    # URI mode=ro: o dashboard nunca grava nem cria o arquivo. resolve() porque
    # Path.as_uri() exige caminho absoluto e já escapa espaços, '?' e '#'.
    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_SECONDS)


def _sqlite_message(exc: sqlite3.Error) -> str:
    text = str(exc)
    if "locked" in text.lower() or "busy" in text.lower():
        return "Banco ocupado (o ETL pode estar gravando). Tente de novo em instantes."
    return f"Não foi possível ler o SQLite: {text}"