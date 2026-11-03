CONTEXTO
 Projeto: pipeline ETL de indicadores de mercado. Series: CDI (diario), IPCA (mensal), Dolar PTAX venda(diario), IBOVESPA (diario). Dashboard final em Streamlit.
 Stack: Python 3.11, pandas, python-bcb, yfinance, SQLite. Saidas em data/.
 Séries validadas: CDI=SGS 12, IPCA=SGS 433, PTAX venda=SGS 1, Ibovespa=yfinance ^BVSP.
 Periodo: 5 ultimos anos ( 2021-11-01 a 2026-11-01)

TAREFA
 Implementar `extract_cdi(start, end)` em `src/extract.py`.

REQUISITOS
 Usar python-bcb (não requests cru).

Criterio
  Rodar sem erro para 2021-11-01 a 2026-11-01 sem erro

RESTRIÇOES
 Nao inventar codigo SGS. Se a serie 12 nao for a diaria usada, interrompa a açao atual e avise.

APOS
 Criar comando de teste e validaçao. Apontar possiveis areas de erro


