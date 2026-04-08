# app.py
import streamlit as st
import pandas as pd
import numpy as np
import json
import datetime
from io import BytesIO
import plotly.express as px

# Configuração da página
st.set_page_config(page_title="Renda Fixa + Correções (CDI/IPCA)", layout="wide")
st.title("📄 Renda Fixa + Correções CDI/IPCA")
st.caption("Envie o JSON, processamos os dados e calculamos as curvas por emissor. Baixe em Excel e visualize os gráficos.")

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ Configurações")
    uploaded_file = st.file_uploader("Envie o arquivo JSON", type=["json"])

    # Slider de ano máximo de vencimento (default: 2035)
    ano_atual = datetime.date.today().year
    venc_ano_max = st.slider(
        "Corte de vencimento (ano máximo)",
        min_value=ano_atual, max_value=ano_atual + 30, value=2035, step=1
    )
    st.caption("O corte temporal das séries e gráficos é limitado pelo ano selecionado.")

# Interpolação sempre ativada
INTERPOLAR = True

# Constantes/colunas
DROP_COLS_SUMMARY = [
    'iofTax', 'isLiquidity', 'isOnlinePosition', 'isRepo', 'issuerCGECode',
    'securityCode', 'ticker', 'operatingHours', 'availableRedeem', 'txLimitHour',
    'minAmountRedeem', 'isDailyLiquidity', 'blockRedemptionD0', 'approvalTimeLimit',
    'enableMarketValue', 'enableApplicationValue', 'isin', 'projection', 'lag',
    'interfaceDate', 'debtEarlyTerminationSchedules', 'multipleAcquisitions',
    'hasAcquisitionObjective', 'schedulingRedeem'
]
DROP_COLS_ACQUISITION = [
    'accountNumber', 'iofTax', 'positionDate', 'securityCode', 'yieldToMaturity',
    'tradeId', 'interfaceDate', 'priceVirtualIof', 'transferId', 'prices',
    'maturityDate', 'ticker', 'isLiquidity', 'clearing', 'interfaceDatePerformance'
]

def parse_json(content_bytes: bytes):
    """Carrega JSON a partir de bytes do upload."""
    for encoding in ["utf-8", "latin-1"]:
        try:
            return json.loads(content_bytes.decode(encoding))
        except Exception:
            continue
    st.error("Falha ao ler o JSON (UTF-8/Latin-1).")
    return None

def montar_renda_fixa_df(summary_json: dict):
    """Replica seu pipeline para construir renda_fixa_df (com PRE e pós)."""
    summary_list = summary_json.get('summary', [])
    i = next((idx for idx, conta in enumerate(summary_list) if conta.get('id') == 'RF'), None)
    if i is None:
        st.warning("Não foi encontrado nenhum item com id == 'RF' em 'summary'.")
        return None

    fixed_incomes = summary_list[i].get('assets', {}).get('fixedIncomes', [])
    if not isinstance(fixed_incomes, list) or len(fixed_incomes) == 0:
        st.warning("A lista 'assets.fixedIncomes' está vazia ou ausente.")
        return None

    summary_df = pd.DataFrame(fixed_incomes).dropna(how='all', axis='columns')
    if 'referenceIndexValue' in summary_df.columns:
        summary_df['referenceIndexValue'] = pd.to_numeric(summary_df['referenceIndexValue'], errors='coerce')

    cols_to_drop = [c for c in summary_df.columns if c in DROP_COLS_SUMMARY]
    if cols_to_drop:
        summary_df = summary_df.drop(columns=cols_to_drop, errors='ignore')

    if 'fixedIncomeAcquisitions' not in summary_df.columns:
        summary_df['fixedIncomeAcquisitions'] = [[] for _ in range(len(summary_df))]

    def add_row_index(row):
        lst = row.get('fixedIncomeAcquisitions', [])
        lst = lst if isinstance(lst, list) else []
        return [{**d, 'rowIndex': row.name} for d in lst]
    summary_df['fixedIncomeAcquisitions'] = summary_df.apply(add_row_index, axis=1)

    summary_df['maturityDate'] = pd.to_datetime(
        summary_df.get('maturityDate', pd.Series(dtype='object')),
        format="%Y-%m-%dT%H:%M:%S.%f%z",
        errors='coerce'
    ).dt.tz_localize(None)

    acquisitions_flat = [d for lst in summary_df['fixedIncomeAcquisitions'] for d in (lst if isinstance(lst, list) else [])]
    if len(acquisitions_flat) == 0:
        st.warning("Não há aquisições em 'fixedIncomeAcquisitions' para montar a tabela final.")
        return None

    acquisition_df = pd.DataFrame(acquisitions_flat)
    if 'rowIndex' not in acquisition_df.columns:
        st.error("As aquisições não contêm 'rowIndex' — não foi possível relacionar com os ativos.")
        return None
    acquisition_df = acquisition_df.set_index('rowIndex')

    acq_cols_to_drop = [c for c in acquisition_df.columns if c in DROP_COLS_ACQUISITION]
    if acq_cols_to_drop:
        acquisition_df = acquisition_df.drop(columns=acq_cols_to_drop, errors='ignore')

    acquisition_df['acquisitionDate'] = pd.to_datetime(
        acquisition_df.get('acquisitionDate', pd.Series(dtype='object')),
        format="%Y-%m-%dT%H:%M:%S.%f%z",
        errors='coerce'
    ).dt.tz_localize(None)

    base_cols = ['accountingGroupCode', 'issuer', 'yield', 'referenceIndexValue', 'referenceIndexName', 'maturityDate']
    print(acquisition_df.columns)
    join_cols = ['acquisitionDate', 'initialInvestmentValue', 'grossValue', 'netValue']
    renda_fixa_df = summary_df[base_cols].join(acquisition_df[join_cols], how='inner').sort_values(['maturityDate', 'acquisitionDate'])

    renda_fixa_df['referenceIndexValue'] = renda_fixa_df['referenceIndexValue'].astype(float)
    renda_fixa_df.loc[renda_fixa_df['yield'] == renda_fixa_df['referenceIndexValue'], 'yield'] = np.nan
    renda_fixa_df.loc[renda_fixa_df['referenceIndexName'] == 'PRE', 'yield'] = renda_fixa_df.loc[renda_fixa_df['referenceIndexName'] == 'PRE', 'referenceIndexValue']
    renda_fixa_df.loc[renda_fixa_df['referenceIndexName'] == 'PRE', 'referenceIndexValue'] = np.nan

    renda_fixa_df = renda_fixa_df.rename(columns={
        'accountingGroupCode': 'Tipo',
        'acquisitionDate': 'Início',
        'issuer': 'Nome',
        'initialInvestmentValue': 'ValorAplicado',
        'grossValue': 'ValorBruto',
        'netValue': 'ValorLiquido',
        'yield': 'Taxa',
        'referenceIndexValue': 'PosFixado',
        'referenceIndexName': 'Índice',
        'maturityDate': 'Vencimento'
    })
    renda_fixa_df = renda_fixa_df[['Tipo', 'Início', 'Nome', 'ValorAplicado', 'ValorBruto', 'ValorLiquido', 'Taxa', 'PosFixado', 'Índice', 'Vencimento']]
    if 'PosFixado' in renda_fixa_df.columns:
        renda_fixa_df['PosFixado'] = renda_fixa_df['PosFixado'] / 100

    renda_fixa_df.reset_index(drop=True, inplace=True)
    return renda_fixa_df

def carregar_serie_sgs(codigo, data_inicial, data_final):
    """Carrega série SGS (BCB) em CSV (data;valor)."""
    url = f"http://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados?formato=csv&dataInicial={data_inicial}&dataFinal={data_final}"
    df = pd.read_csv(url, delimiter=';', decimal=',', engine='python')
    df.columns = ['data', 'value']
    df['data'] = pd.to_datetime(df['data'], dayfirst=True)
    df.set_index('data', inplace=True)
    df['value'] = pd.to_numeric(df['value'], errors='coerce')
    return df

def montar_series_correcoes(titulos: pd.DataFrame, INTERPOLAR: bool, venc_ano_max: int):
    """Calcula correções diárias CDI/IPCA por dias corridos, com interpolação opcional."""
    if titulos is None or titulos.empty:
        return None, None, None, None

    corte_final = pd.Timestamp(venc_ano_max, 12, 31)
    agora = datetime.datetime.now()
    titulos = titulos[(titulos['Vencimento'] > agora) & (titulos['Vencimento'] <= corte_final)].copy()
    if titulos.empty:
        st.info("Nenhum título com vencimento futuro dentro do corte selecionado.")
        return None, None, None, None

    data_inicial = titulos['Início'].min().date().strftime('%d/%m/%Y')
    data_final = min(titulos['Vencimento'].max(), corte_final).date().strftime('%d/%m/%Y')
    try:
        series = {
            'CDI': carregar_serie_sgs(12, data_inicial, data_final),
            'IPCA': carregar_serie_sgs(433, data_inicial, data_final)
        }
    except Exception as e:
        st.warning(f"Falha ao carregar séries do BCB (SGS): {e}")
        return titulos, None, None, None

    # CDI diário como fator
    series['CDI']['value'] = 1 + series['CDI']['value'] / 100

    # IPCA mensal -> diário (dias corridos no mês)
    ipca_mensal = series['IPCA'].copy()

    def dias_no_mes(dt):
        inicio_mes = pd.Timestamp(dt.year, dt.month, 1)
        fim_mes = (inicio_mes + pd.offsets.MonthBegin(1))
        return (fim_mes - inicio_mes).days

    ipca_mensal['dias'] = [dias_no_mes(idx) for idx in ipca_mensal.index]

    ipca_diario_frames = []
    for idx, line in zip(ipca_mensal.index, ipca_mensal.to_dict(orient="records")):
        inicio_mes = pd.Timestamp(idx.year, idx.month, 1)
        fim_mes_exclusivo = (inicio_mes + pd.offsets.MonthBegin(1))
        dias_range = pd.date_range(start=inicio_mes, end=fim_mes_exclusivo - pd.Timedelta(days=1), freq='D')
        fator_diario = (1 + line['value'] / 100) ** (1 / line['dias'])
        ipca_diario_frames.append(pd.DataFrame({'data': dias_range, 'value': fator_diario}))
    ipca_diario = pd.concat(ipca_diario_frames).set_index('data')
    series['IPCA'] = ipca_diario

    # Interpolar até maior vencimento (dias corridos)
    if INTERPOLAR:
        for indice, serie in series.items():
            ultimo = serie.index.max()
            horizonte = titulos['Vencimento'].max()
            if pd.isna(ultimo) or pd.isna(horizonte):
                continue
            interpolacao_idx = pd.date_range(start=ultimo + pd.DateOffset(1), end=horizonte, freq='D')
            if len(interpolacao_idx) > 0:
                interpolacao = pd.DataFrame(index=interpolacao_idx)
                interpolacao['value'] = serie.iloc[-1]['value']
                series[indice] = pd.concat([serie, interpolacao])

    # Índice alvo (dias corridos)
    idx = pd.date_range(
        start=titulos["Início"].min(),
        end=titulos["Vencimento"].max(),
        freq="D"
    )

    # Correções por título
    correcoes_list = []
    for row in titulos.itertuples():
        correcao = None

        # Pré-fixado: taxa diária 365
        col_indice = row._asdict().get("Índice")
        if col_indice == 'PRE':
            indice = idx[(idx >= row.Início) & (idx < row.Vencimento)]
            correcao_pre = pd.Series((1 + row.Taxa / 100) ** (1 / 365), index=indice).cumprod()
            correcao = correcao_pre.to_frame(name=row.Index)
        else: # Pós-fixado (CDI/IPCA)
            serie = series[col_indice].loc[row.Início: row.Vencimento].copy()
            correcao_pos = serie.rename(columns={"value": row.Index})
            correcao_pos[row.Index] = (correcao_pos[row.Index].cumprod() - 1) * row.PosFixado + 1
            correcao = correcao_pos if correcao is None else correcao * correcao_pos

        if correcao is not None:
            correcao = correcao * row.ValorAplicado
            correcoes_list.append(correcao)

    correcoes = pd.concat([pd.DataFrame(index=idx)] + correcoes_list, axis=1) if correcoes_list else pd.DataFrame(index=idx)

    # Valores corrigidos no vencimento
    valores_vencimento = []
    for row in titulos.itertuples():
        if row.Index in correcoes.columns:
            valor_final = correcoes.loc[:row.Vencimento, row.Index].dropna().iloc[-1]
            valores_vencimento.append({
                "index_x": row.Vencimento,
                "Emissor": row.Nome,
                "value": valor_final
            })
    vencimentos_df = pd.DataFrame(valores_vencimento).sort_values("index_x")

    # Corte temporal: -2 anos até o fim selecionado pelo slider
    inicio = pd.Timestamp.now() - pd.DateOffset(years=2)
    fim = pd.Timestamp(venc_ano_max, 12, 31)
    vencimentos_df = vencimentos_df[(vencimentos_df["index_x"] >= inicio) & (vencimentos_df["index_x"] <= fim)].copy()

    # Fluxo acumulado por emissor (apenas para curvas individuais, sem global)
    if not vencimentos_df.empty:
        menor_venc = vencimentos_df["index_x"].min()
        maior_venc = vencimentos_df["index_x"].max()
        vencimentos_df["value"] = vencimentos_df.groupby("Emissor")["value"].cumsum()

        linhas_extra = []
        for emissor, grupo in vencimentos_df.groupby("Emissor"):
            ultimo_valor = grupo["value"].iloc[-1]
            linhas_extra.extend([
                {"index_x": menor_venc, "Emissor": emissor, "value": 0},
                {"index_x": maior_venc, "Emissor": emissor, "value": ultimo_valor}
            ])
        vencimentos_df = pd.concat([vencimentos_df, pd.DataFrame(linhas_extra)], ignore_index=True)
        vencimentos_df = vencimentos_df.sort_values(["Emissor", "index_x"]).reset_index(drop=True)

    # Curvas por emissor ao longo do tempo (soma diária)
    if not correcoes.empty and not titulos.empty:
        curvas_por_emissor = (
            correcoes.reset_index()
            .rename(columns={"index": "index_x"})
            .melt(id_vars="index_x", var_name="titulos_index")
            .replace(np.nan, 0)
            .merge(titulos.reset_index(), left_on="titulos_index", right_on="index")
            .groupby(["index_x", "Nome"], as_index=False)["value"].sum()
            .rename(columns={"Nome": "Emissor"})
        )
        curvas_por_emissor = curvas_por_emissor[curvas_por_emissor["index_x"] <= fim]
    else:
        curvas_por_emissor = pd.DataFrame(columns=["index_x", "Emissor", "value"])

    data_base = pd.Timestamp.today()
    taxa_anual = 0.171
    base_dias = 365
    pico_df = curvas_por_emissor.loc[curvas_por_emissor.groupby("Emissor")["value"].idxmax(), ["Emissor", "index_x", "value"]]
    slot_disponivel = pico_df.assign(
        index_x=lambda d: pd.to_datetime(d["index_x"]),
        anos=lambda d: (d["index_x"] - data_base).dt.days / 365,
        slot_disponivel=lambda d: (250_000 - d["value"]) / (1 + taxa) ** d["anos"]
    ).sort_values('slot_disponivel', ascending=True).set_index(['Emissor'])[['slot_disponivel']]

    return titulos, series, correcoes, vencimentos_df, curvas_por_emissor, slot_disponivel

def baixar_excel(renda_fixa_df, vencimentos_df=None):
    """Gera Excel em memória com aba RendaFixa (e opcionalmente Vencimentos)."""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        renda_fixa_df.to_excel(writer, index=False, sheet_name="RendaFixa")
        if vencimentos_df is not None and not vencimentos_df.empty:
            vencimentos_df.to_excel(writer, index=False, sheet_name="Vencimentos")
    buffer.seek(0)
    return buffer

# --- Execução principal ---
if uploaded_file is not None:
    summary_json = parse_json(uploaded_file.read())
    if summary_json is not None:
        with st.spinner("Processando o arquivo..."):
            renda_fixa_df = montar_renda_fixa_df(summary_json)

        if renda_fixa_df is not None and not renda_fixa_df.empty:
            st.success("✅ Tabela processada com sucesso!")
            st.dataframe(renda_fixa_df, use_container_width=True)

            # ⬇️ Download Excel (.xlsx)
            excel_buffer = baixar_excel(renda_fixa_df)
            st.download_button(
                label="⬇️ Baixar Excel (.xlsx)",
                data=excel_buffer.getvalue(),
                file_name="renda_fixa.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            # --- Correções e gráfico por emissor (dias corridos) ---
            with st.spinner("Calculando correções (CDI/IPCA) e preparando gráfico..."):
                titulos, series, correcoes, vencimentos_df, curvas_por_emissor, slot_disponivel = montar_series_correcoes(
                    renda_fixa_df, INTERPOLAR, venc_ano_max
                )

            if series is None:
                st.warning("Séries do BCB não carregadas. O gráfico de correção não será exibido.")
            else:
                if not curvas_por_emissor.empty:
                    fig_emissor = px.line(
                        curvas_por_emissor,
                        x="index_x", y="value", color="Emissor",
                        title=f"Curvas de investimento por Emissor (até {venc_ano_max})"
                    )
                    fig_emissor.add_hline(y=250_000, line_width=3, line_dash="dash", line_color="red")
                    fig_emissor.update_layout(autosize=True)
                    st.plotly_chart(fig_emissor, use_container_width=True)

                    st.success("✅ Slots disponíveis para investir (considerando Limite FGC)!")
                    st.dataframe(slot_disponivel, use_container_width=True)
                else:
                    st.info("Não há dados para desenhar curvas por emissor.")
        else:
            st.info("Não há dados para exibir na tabela resultante.")
else:
    st.info("Envie um arquivo JSON para começar.")
