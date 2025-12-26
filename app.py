from io import BytesIO
import streamlit as st
import pandas as pd
import json

st.set_page_config(page_title="Renda Fixa - Parser JSON", layout="wide")
st.title("📄 Parser de Renda Fixa a partir de JSON")
st.caption("Envie o arquivo JSON, aplicamos o tratamento e exibimos a tabela final.")

uploaded_file = st.file_uploader("Envie o arquivo JSON", type=["json"])

# Lista de colunas a remover conforme seu código original
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
    try:
        text = content_bytes.decode("utf-8")
        return json.loads(text)
    except Exception as e:
        st.error(f"Falha ao ler o JSON: {e}")
        return None

def build_tables(summary_json: dict):
    """Replica o tratamento do seu código, com robustez adicional."""
    if not isinstance(summary_json, dict):
        st.error("O conteúdo do arquivo não é um objeto JSON válido.")
        return None

    # Encontra o índice do item com id == 'RF'
    summary_list = summary_json.get('summary', [])
    if not isinstance(summary_list, list):
        st.error("A chave 'summary' não está presente ou não é uma lista.")
        return None

    i = next((idx for idx, conta in enumerate(summary_list) if conta.get('id') == 'RF'), None)
    if i is None:
        st.warning("Não foi encontrado nenhum item com id == 'RF' em 'summary'.")
        return None

    # Extrai a lista de fixedIncomes
    fixed_incomes = summary_list[i].get('assets', {}).get('fixedIncomes', [])
    if not isinstance(fixed_incomes, list) or len(fixed_incomes) == 0:
        st.warning("A lista 'assets.fixedIncomes' está vazia ou ausente.")
        return None

    # DataFrame base e limpeza de colunas
    summary_df = pd.DataFrame(fixed_incomes).dropna(how='all', axis='columns')
    cols_to_drop = [c for c in summary_df.columns if c in DROP_COLS_SUMMARY]
    if cols_to_drop:
        summary_df = summary_df.drop(columns=cols_to_drop)

    # Garante existência da coluna 'fixedIncomeAcquisitions'
    if 'fixedIncomeAcquisitions' not in summary_df.columns:
        summary_df['fixedIncomeAcquisitions'] = [[] for _ in range(len(summary_df))]

    # Adiciona 'rowIndex' dentro de cada dict da coluna 'fixedIncomeAcquisitions'
    def add_row_index(row):
        lst = row.get('fixedIncomeAcquisitions', [])
        lst = lst if isinstance(lst, list) else []
        return [{**d, 'rowIndex': row.name} for d in lst]

    summary_df['fixedIncomeAcquisitions'] = summary_df.apply(add_row_index, axis=1)

    # Converte 'maturityDate' (datas inválidas viram NaT)
    # Tenta com o formato exato; se falhar, tenta parsing genérico
    summary_df['maturityDate'] = pd.to_datetime(
        summary_df.get('maturityDate', pd.Series(dtype='object')),
        format="%Y-%m-%dT%H:%M:%S.%f%z",
        errors='coerce'
    )
    if summary_df['maturityDate'].isna().all():
        summary_df['maturityDate'] = pd.to_datetime(summary_df['maturityDate'], errors='coerce')

    # Achata a lista de aquisições
    acquisitions_flat = [
        d
        for lst in summary_df['fixedIncomeAcquisitions']
        for d in (lst if isinstance(lst, list) else [])
    ]

    if len(acquisitions_flat) == 0:
        st.warning("Não há aquisições em 'fixedIncomeAcquisitions' para montar a tabela final.")
        return None

    acquisition_df = pd.DataFrame(acquisitions_flat)

    # Se não existir 'rowIndex', não é possível fazer join consistente
    if 'rowIndex' not in acquisition_df.columns:
        st.error("As aquisições não contêm 'rowIndex' — não foi possível relacionar com os ativos.")
        return None

    acquisition_df = acquisition_df.set_index('rowIndex')

    # Remove colunas desnecessárias nas aquisições
    acq_cols_to_drop = [c for c in acquisition_df.columns if c in DROP_COLS_ACQUISITION]
    if acq_cols_to_drop:
        acquisition_df = acquisition_df.drop(columns=acq_cols_to_drop, errors='ignore')

    # Converte 'acquisitionDate'
    acquisition_df['acquisitionDate'] = pd.to_datetime(
        acquisition_df.get('acquisitionDate', pd.Series(dtype='object')),
        format="%Y-%m-%dT%H:%M:%S.%f%z",
        errors='coerce'
    )
    if acquisition_df['acquisitionDate'].isna().all():
        acquisition_df['acquisitionDate'] = pd.to_datetime(acquisition_df['acquisitionDate'], errors='coerce')

    # Monta a tabela final como no seu código
    base_cols = ['accountingGroupCode', 'issuer', 'yield', 'referenceIndexValue', 'referenceIndexName', 'maturityDate']
    existing_base_cols = [c for c in base_cols if c in summary_df.columns]

    join_cols = ['acquisitionDate', 'initialInvestmentValue']
    existing_join_cols = [c for c in join_cols if c in acquisition_df.columns]

    if len(existing_base_cols) == 0 or len(existing_join_cols) == 0:
        st.error("Colunas necessárias ausentes para montar o dataframe final.")
        return None

    renda_fixa_df = summary_df[existing_base_cols].join(acquisition_df[existing_join_cols], how='inner')

    # Ordenação e formatação de datas
    sort_cols = [c for c in ['maturityDate', 'acquisitionDate'] if c in renda_fixa_df.columns]
    if sort_cols:
        renda_fixa_df = renda_fixa_df.sort_values(sort_cols)

    if 'maturityDate' in renda_fixa_df.columns:
        renda_fixa_df['maturityDate'] = renda_fixa_df['maturityDate'].dt.strftime('%d/%m/%Y')
    if 'acquisitionDate' in renda_fixa_df.columns:
        renda_fixa_df['acquisitionDate'] = renda_fixa_df['acquisitionDate'].dt.strftime('%d/%m/%Y')

    final_cols = [
        'accountingGroupCode', 'acquisitionDate', 'issuer', 'initialInvestmentValue',
        'yield', 'referenceIndexValue', 'referenceIndexName', 'maturityDate'
    ]
    existing_final_cols = [c for c in final_cols if c in renda_fixa_df.columns]

    return renda_fixa_df[existing_final_cols]

if uploaded_file is not None:
    summary_json = parse_json(uploaded_file.read())
    if summary_json is not None:
        with st.spinner("Processando o arquivo..."):
            result_df = build_tables(summary_json)

        if result_df is not None and not result_df.empty:
            st.success("✅ Tabela processada com sucesso!")
            st.dataframe(result_df, use_container_width=True)

            # (Opcional) botão para baixar Excel
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                result_df.to_excel(writer, index=False, sheet_name="RendaFixa")
            buffer.seek(0)
            
            st.download_button(
                label="⬇️ Baixar Excel (.xlsx)",
                data=buffer.getvalue(),
                file_name="renda_fixa.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            # (Opcional) mostra amostra do JSON
            with st.expander("Ver amostra do JSON bruto"):
                st.json(summary_json)
        else:
            st.info("Não há dados para exibir na tabela resultante.")
else:
    st.info("Envie um arquivo JSON para começar.")
