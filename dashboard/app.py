"""Dashboard de indicadores de mercado (Streamlit).

Executar na raiz do repositório::

    streamlit run dashboard/app.py

O app só lê o SQLite que o ETL grava (``data/market.sqlite``); o botão
"Atualizar dados" roda o ETL completo. Gráficos: base 100, perda máxima
(Ibovespa e dólar PTAX) e retorno nominal x real contra o IPCA (Ibovespa e
dólar PTAX). Os cálculos estão em ``dashboard/metrics.py``.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# `streamlit run` só põe dashboard/ no sys.path; sem a raiz, `from src...` falha.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt  # noqa: E402 - já vem com o Streamlit
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from dashboard import metrics, store  # noqa: E402

IBOV, PTAX, CDI, IPCA = "Ibovespa", "Dólar PTAX", "CDI", "IPCA"
COLORS = {CDI: "#1f77b4", IPCA: "#d62728", PTAX: "#ff7f0e", IBOV: "#2ca02c"}
REAL_COLOR = "#9467bd"
SERIES_NAMES = {"cdi": CDI, "ipca": IPCA, "ptax": PTAX, "ibov": IBOV}
PERIODS = {"6 meses": 6, "1 ano": 12, "3 anos": 36, "Tudo": None}


# --------------------------------------------------------------------------
# Formatação (pt-BR feito à mão: locale.setlocale é instável em servidor)
# --------------------------------------------------------------------------

def fmt(value: float, decimals: int = 2, suffix: str = "", sign: bool = False) -> str:
    """``1234.5`` -> ``1.234,50``; com ``sign``, positivos levam ``+``."""
    if round(value, decimals) == 0:
        value = 0.0  # evita "-0,00"
    text = f"{value:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    if sign and value > 0:
        text = "+" + text
    return text + suffix


def fmt_date(ts: pd.Timestamp) -> str:
    return ts.strftime("%d/%m/%Y")


def line_chart(frame: pd.DataFrame, colors: list[str], y_label: str) -> None:
    """Uma linha por coluna de ``frame`` (índice = datas), na ordem de ``colors``.

    Usa Altair (que já vem com o Streamlit) porque ``st.line_chart`` não deixa
    tirar o zero do eixo Y, e as séries em base 100 ficariam espremidas. O eixo
    X usa ``mm/aaaa``: o vega escreveria os meses em inglês.
    """
    long = (
        frame.rename_axis("Data").reset_index()
        .melt("Data", var_name="Série", value_name="Valor")
        .dropna()
    )
    chart = alt.Chart(long).mark_line(point=alt.OverlayMarkDef(opacity=0, size=40)).encode(
        x=alt.X("Data:T", title=None, axis=alt.Axis(format="%m/%Y")),
        y=alt.Y("Valor:Q", title=y_label, scale=alt.Scale(zero=False)),
        color=alt.Color(
            "Série:N",
            scale=alt.Scale(domain=list(frame.columns), range=colors),
            legend=alt.Legend(orient="bottom", title=None),
        ),
        tooltip=[
            alt.Tooltip("Data:T", format="%d/%m/%Y"),
            "Série:N",
            alt.Tooltip("Valor:Q", format=",.2f"),
        ],
    )
    st.altair_chart(chart)


# --------------------------------------------------------------------------
# Dados
# --------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def cached_series(name: str, mtime: int | None) -> pd.DataFrame | None:
    """Leitura em cache; ``mtime`` (do arquivo) entra na chave: ETL novo = leitura nova."""
    return store.read_series(name)


def load_frames() -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Devolve (séries lidas, problemas por série). Série ausente não derruba o app."""
    mtime = store.db_mtime()
    frames: dict[str, pd.DataFrame] = {}
    problems: dict[str, str] = {}
    for name in store.SERIES_TABLES:
        try:
            df = cached_series(name, mtime)
        except store.StoreError as exc:
            problems[name] = str(exc)
            continue
        if df is None:
            problems[name] = "sem dados no banco"
        else:
            frames[name] = df
    return frames, problems


def update_data() -> None:
    """Roda o ETL (bloqueia até o fim), limpa o cache e guarda o resultado para exibir."""
    with st.spinner("Atualizando os dados (pode levar alguns minutos)..."):
        code, report = store.update_all()
    st.cache_data.clear()
    st.session_state["update"] = (code, report, datetime.now())


def show_update_result() -> None:
    result = st.session_state.get("update")
    if result is None:
        return
    code, report, when = result
    stamp = when.strftime("%d/%m/%Y %H:%M")
    if code == 0:
        st.success(f"Dados atualizados em {stamp}.")
    elif code == 1:
        st.warning(
            f"Atualização de {stamp}: a validação reprovou algum dado. "
            "A série reprovada manteve os dados anteriores."
        )
    else:
        st.error(
            f"Atualização de {stamp}: falha em alguma etapa do ETL. "
            "A série que falhou manteve os dados anteriores."
        )
    with st.expander("Relatório do ETL"):
        st.code(report, language=None)


# --------------------------------------------------------------------------
# Seções da página
# --------------------------------------------------------------------------

def show_kpis(frames: dict[str, pd.DataFrame]) -> None:
    """Último valor de cada série (sem filtro de período), com a data de referência."""
    cols = st.columns(4)

    # A data de referência vai no rótulo ("... · 04/11/2026"): as séries terminam em dias diferentes.
    if "cdi" in frames:
        last = frames["cdi"].iloc[-1]
        cols[0].metric(
            f"CDI (% a.a.) · {fmt_date(last['date'])}", fmt(last["cdi_pct_year"], 2, "%"),
        )
    if "ipca" in frames:
        last = frames["ipca"].iloc[-1]
        cols[1].metric(
            f"IPCA 12 meses · {last['date']:%m/%Y}", fmt(last["ipca_pct_12m"], 2, "%"),
        )
    if "ptax" in frames:
        last = frames["ptax"].iloc[-1]
        cols[2].metric(
            f"Dólar PTAX (R$) · {fmt_date(last['date'])}", fmt(last["ptax_venda"], 4),
            fmt(last["ptax_ret_day"], 2, "%", sign=True),
        )
    if "ibov" in frames:
        last = frames["ibov"].iloc[-1]
        cols[3].metric(
            f"Ibovespa (pontos) · {fmt_date(last['date'])}", fmt(last["ibov_close"], 0),
            fmt(last["ibov_ret_day"], 2, "%", sign=True),
        )


def build_levels(frames: dict[str, pd.DataFrame], start: pd.Timestamp | None) -> dict[str, pd.Series]:
    """Níveis diários (CDI acumulado, cotação do dólar, pontos do Ibovespa) no período."""
    builders = {
        "cdi": (CDI, metrics.cdi_level),
        "ptax": (PTAX, lambda df: metrics.series(df, "ptax_venda")),
        "ibov": (IBOV, lambda df: metrics.series(df, "ibov_close")),
    }
    levels: dict[str, pd.Series] = {}
    for key, (name, build) in builders.items():
        if key not in frames:
            continue
        df = metrics.from_date(frames[key], start)
        if not df.empty:
            levels[name] = build(df)
    return levels


def show_base_100(levels: dict[str, pd.Series], ipca: pd.DataFrame | None) -> None:
    st.subheader("Base 100")
    if not levels:
        st.info("Sem dados diários no período.")
        return
    try:
        frame = metrics.base_100(levels, ipca)
    except ValueError as exc:
        st.warning(f"IPCA fora do gráfico: {exc}")
        frame = metrics.base_100(levels)
    if frame.empty:
        st.info("Sem dados em comum no período.")
        return

    line_chart(frame, [COLORS[c] for c in frame.columns], "Base 100")
    caption = f"Todas as séries valem 100 em {fmt_date(frame.index[0])}, a primeira data com todas elas. "
    caption += "CDI: fator diário acumulado. Dólar: cotação PTAX de venda. Ibovespa: pontos de fechamento. "
    if IPCA in frame.columns:
        caption += (
            f"IPCA: inflação acumulada, interpolada dia a dia dentro de cada mês, "
            f"até {fmt_date(frame[IPCA].dropna().index[-1])} (fim do último mês divulgado)."
        )
    st.caption(caption)


def show_drawdown(levels: dict[str, pd.Series]) -> None:
    st.subheader("Perda máxima")
    assets = {name: levels[name] for name in (IBOV, PTAX) if name in levels}
    if not assets:
        st.info("Sem Ibovespa nem dólar no período.")
        return

    curves = metrics.align_daily({name: metrics.drawdown_curve(s) for name, s in assets.items()})
    line_chart(curves, [COLORS[c] for c in curves.columns], "Queda desde o pico (%)")

    for col, (name, s) in zip(st.columns(len(assets)), assets.items()):
        dd = metrics.max_drawdown(s)
        with col:
            st.metric(f"Perda máxima: {name}", fmt(dd.max_pct, 2, "%"))
            if dd.max_pct == 0:
                st.caption("Sem queda no período.")
            else:
                st.caption(f"Do pico em {fmt_date(dd.peak_date)} ao fundo em {fmt_date(dd.trough_date)}.")
    st.caption(
        "Maior queda do valor máximo anterior até o fundo seguinte, dentro do período escolhido. "
        "Para o dólar, é a maior queda da cotação (valorização do real)."
    )


def show_nominal_vs_real(levels: dict[str, pd.Series], ipca: pd.DataFrame | None) -> None:
    st.subheader("Retorno nominal x real")
    if ipca is None:
        st.info("IPCA indisponível: sem como calcular o retorno real.")
        return

    assets = [name for name in (IBOV, PTAX) if name in levels]
    if not assets:
        st.info("Sem Ibovespa nem dólar no período.")
        return

    for col, name in zip(st.columns(len(assets)), assets):
        with col:
            try:
                frame = metrics.nominal_vs_real(levels[name], ipca, name)
            except ValueError as exc:
                st.warning(f"{name}: {exc}")
                continue
            if frame.empty:
                st.info(f"{name}: sem datas com IPCA no período.")
                continue

            st.markdown(f"**{name}**")
            nominal_col, real_col = f"{name} nominal", f"{name} real"
            line_chart(frame, [COLORS[name], COLORS[IPCA], REAL_COLOR], "Base 100")
            a, b, c = st.columns(3)
            a.metric("Nominal", fmt(metrics.total_return(frame[nominal_col]), 2, "%"))
            b.metric("IPCA", fmt(metrics.total_return(frame[IPCA]), 2, "%"))
            c.metric("Real", fmt(metrics.total_return(frame[real_col]), 2, "%"))
            st.caption(f"{fmt_date(frame.index[0])} a {fmt_date(frame.index[-1])}.")
    st.caption(
        "Retorno real = índice nominal dividido pelo índice do IPCA, ou (1 + nominal) / (1 + IPCA) - 1. "
        "O IPCA é interpolado dia a dia dentro de cada mês e o gráfico vai até o fim do último mês divulgado."
    )


# --------------------------------------------------------------------------
# Página
# --------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="Indicadores de mercado", layout="wide")

    title_col, button_col = st.columns([4, 1], vertical_alignment="bottom")
    title_col.title("Indicadores de mercado")
    if button_col.button("Atualizar dados"):
        update_data()
    show_update_result()

    frames, problems = load_frames()
    if not frames:
        st.info("Sem dados no banco. Clique em **Atualizar dados** para rodar o ETL.")
        for key, message in problems.items():
            if message != "sem dados no banco":
                st.error(f"{SERIES_NAMES[key]}: {message}")
        st.stop()
    if problems:
        st.warning(
            "Séries indisponíveis: "
            + "; ".join(f"{SERIES_NAMES[k]} ({msg})" for k, msg in problems.items())
            + "."
        )

    show_kpis(frames)

    label = st.selectbox("Período", list(PERIODS), index=len(PERIODS) - 1)
    end = max(df["date"].max() for df in frames.values())
    start = metrics.period_start(end, PERIODS[label])

    levels = build_levels(frames, start)
    ipca = frames.get("ipca")
    show_base_100(levels, ipca)
    show_drawdown(levels)
    show_nominal_vs_real(levels, ipca)

    st.divider()
    st.caption(
        "Fontes: Banco Central (SGS 12 CDI, 433 IPCA, 1 dólar PTAX de venda) e Yahoo Finance (^BVSP). "
        f"Dados lidos de {store.db_path()}."
    )


main()