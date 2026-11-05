# Veneto Pipeline

**Pipeline de coleta,transformaçao e disponibilizaçao de dados do mercado financeiro**

## sobre o projeto

O app se trata de um projeto de ETL (Extract,Transform,Load) em python para coleta automatica de dados que os exibe em um dashboard no formato de graficos e indicadores.
O pipeline trabalha com dados dos ultimos cinco anos, tendo suporte as 4 series seguintes:
-CDI: referencia de juros e rentabilidade de investimentos
-IPCA: indicador da inflaçao brasileira
-PTAX: referencia para o cambio real - dolar
-IBOVESPA: indicador de desempenho de açoes B3

## Como o projeto funciona

O projeto faz uso das bibliotecas python-bcb e yfinance para extraçao de dados relativos as series, e os trata por meio principalmente da biblioteca Pandas. Finalmente, os dados tratados sao salvos em um banco de dados baseado na biblioteca SQLite. Alem disso, para funcionamento do dashboard, e utilizado a biblioteca streamlit.

## Como executar

Antes de executar, os pre-requisitos sao:
- python, idealmente 3.10 ou superior
- git
- pip

### 1- Clonar:

rode o comando a seguir no terminal:

```bash
git clone https://github.com/lagp983/veneto_pipeline.git
```

posteriormente, acesse a pasta por meio de:

```bash
cd veneto_pipeline
```

### 2- criaçao de um ambiente virtual

É recomendado utilizar um ambiente virtual para isolar as dependências do projeto. Por isso, rode os comandos a seguir para sua criaçao e ativaçao.

Em windows:

```bash
python -m venv venv
venv\Scripts\activate
```

Em linux/MacOs

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3- Instalaçao de dependencias

```bash
pip install -r requirements.txt
```

### 4- execuçao da pipeline

Com as preparaçoes prontas, rode:

```bash
python -m src.pipeline
```

### 5-Executar o dashboard

```bash
streamlit run dashboard/app.py
```
### 6- Atualizar dados (opcional)
Apos rodar o dashboard, o usuario pode optar por pressionar o botao "atualizar dados" para obter novos dados, atualizando os graficos exibidos no momento. Caso nao haja dados a mais para serem obtidos, vale notar que as informaçoes serao as mesmas que antes da atualizaçao.
## Decisoes e limitaçoes
###IPCA com 12 observaçoes
Foi optado por calcular o IPCA pelo acumulado de 12 meses. Por isso, a pipeline exige >= 12 observaçoes descartando as 11 primeiras. Em compensaçao, foi implementado para o IPCA obter 11 meses a mais que as demais series e descartalos. Em contrapartida, isso torna os dados irreproduziveis para alguem que tentar recalcular os valores baseados nos dados visiveis. Alem disso, para periodos especificos que nao possuem dados anteriores causaria erros especiais.
###Reaproveitar padrao PTAX para IBOV
Foram usados os helpers criados para o PTAX para atender as necessidades do IBOV, aproveitando suas caracteristicas similares. Essa estrutura tambem faz com que funcione sob as suposiçoes apropriadas ao PTAX. Por exigir menos codigo novo, abre menos espaço para bugs, mas atende de forma aproximada a nova serie.
###Descarte de dados ao atualizar
Foi optado pela soluçao nao incremental, em que ao executar a pipeline novamente ou pressionar o botao de "atualizar dados", os dados atuais sao descartados e todos os dados do periodo escolhido sao requisitados.
###Periodo especifico
Os graficos e indicadores exibem dados especificamente dos ultimos 5 anos. Nos ETLs, foram preparados alguns aspectos para permitir a escolha do periodo, porem nao foi implementado para que o dashboard altere o periodo.



### Escalabilidade

As soluçoes utilizadas foram escolhidas por se tratar de um numero relativamente pequeno de dados.
Caso haja a necessidade de evoluir o projeto para uma escala maior, multiplas reformas na implementaçao serao necessarias
