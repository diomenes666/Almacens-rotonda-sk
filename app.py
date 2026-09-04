import streamlit as st
import pandas as pd
import gspread

# Configuración de página
st.set_page_config(page_title="Almacén Rotonda", layout="wide", page_icon="📦")
st.title("📦 Sistema de Control de Almacén - Rotonda")

# Conexión con Google Sheets mediante gspread
@st.cache_resource(ttl=0)
def conectar_gsheets():
    try:
        # Lee las credenciales de .streamlit/secrets.toml
        credentials = dict(st.secrets["connections"]["gsheets"])
        client = gspread.service_account_from_dict(credentials)
        
        # Abre el archivo usando la URL guardada en secrets
        sheet_url = credentials["spreadsheet"]
        sh = client.open_by_url(sheet_url)
        return sh.sheet1
    except Exception as e:
        st.error(f"Error al conectar con Google Sheets: {e}")
        st.stop()

worksheet = conectar_gsheets()

# Función para cargar datos
def cargar_datos():
    data = worksheet.get_all_records()
    return pd.DataFrame(data)

df = cargar_datos()

# Manejo de parámetros QR (ej. ?estante=A)
query_params = st.query_params
estante_qr = query_params.get("estante", "A").upper()

st.sidebar.header("📍 Menú de Control")
estante_sel = st.sidebar.selectbox(
    "Seleccionar Estante:",
    ["A (CONTABILIDAD)", "B (FINANZAS)", "C (GTH)"],
    index=["A", "B", "C"].index(estante_qr) if estante_qr in ["A", "B", "C"] else 0
)

codigo_estante = estante_sel[0] # 'A', 'B' o 'C'

# Buscador de documentos
st.sidebar.subheader("🔍 Buscar Documento")
busqueda = st.sidebar.text_input("Ingresa término:")
if busqueda:
    res = df[df.apply(lambda row: row.astype(str).str.contains(busqueda, case=False).any(), axis=1)]
    if not res.empty:
        for _, r in res.iterrows():
            st.sidebar.info(f"📍 **{r['Ultima Ubicación']}**: {r['Concepto']} ({r['Año']})")
    else:
        st.sidebar.warning("Sin resultados")

# Configuración física por estante
config_estantes = {
    "A": {1: 2, 2: 3, 3: 2},
    "B": {1: 2, 2: 2, 3: 3},
    "C": {1: 2, 2: 2, 3: 2}
}

posiciones_bloqueadas = ["B15D1", "B15D2", "B15P1", "B15P2", "B16D1", "B16D2", "B16P1", "B16P2"]
ubicaciones_ocupadas = set(df["Ultima Ubicación"].dropna().astype(str).tolist()) if "Ultima Ubicación" in df.columns else set()

st.subheader(f"Estante {codigo_estante}")
tabs = st.tabs([f"Subestante {s}" for s in [1, 2, 3]])

for sub_idx, sub_num in enumerate([1, 2, 3]):
    with tabs[sub_idx]:
        num_cols = config_estantes[codigo_estante][sub_num]
        for piso in range(8, 0, -1):
            st.markdown(f"**Piso {piso}**")
            gui_cols = st.columns(num_cols)
            for c_idx in range(1, num_cols + 1):
                with gui_cols[c_idx - 1]:
                    cod_p = f"{codigo_estante}{sub_num}{piso}P{c_idx}"
                    cod_d = f"{codigo_estante}{sub_num}{piso}D{c_idx}"
                    for cod in [cod_p, cod_d]:
                        if cod in posiciones_bloqueadas:
                            st.button(f"🚫 {cod}", key=cod, disabled=True)
                        else:
                            ocup = cod in ubicaciones_ocupadas
                            lbl = f"🔴 {cod}" if ocup else f"🟢 {cod}"
                            if st.button(lbl, key=cod, use_container_width=True):
                                st.session_state["ub_activa"] = cod

# Formulario de edición y guardado
if "ub_activa" in st.session_state:
    ub = st.session_state["ub_activa"]
    st.divider()
    st.markdown(f"### 📝 Registrando en: **{ub}**")
    
    pos_data = df[df["Ultima Ubicación"] == ub] if "Ultima Ubicación" in df.columns else pd.DataFrame()
    
    with st.form("form_inventario"):
        c1, c2 = st.columns(2)
        with c1:
            concepto = st.text_input("Concepto", value=pos_data["Concepto"].values[0] if not pos_data.empty else "")
            periodos = st.text_input("Periodos", value=pos_data["Periodos"].values[0] if not pos_data.empty else "")
            anio = st.text_input("Año", value=str(pos_data["Año"].values[0]) if not pos_data.empty else "")
        with c2:
            detalle = st.text_area("Detalle", value=pos_data["Detalle"].values[0] if not pos_data.empty else "")
            obs = st.text_input("Observaciones", value=pos_data["Observaciones"].values[0] if not pos_data.empty else "")
            
        if st.form_submit_button("💾 Guardar"):
            nueva_fila = [ub, concepto, periodos, detalle, anio, obs]
            
            # Si la ubicación ya existe, actualiza la fila; si no, agrega una nueva al final
            cell = None
            try:
                cell = worksheet.find(ub)
            except Exception:
                pass
            
            if cell:
                # Actualiza la fila existente
                worksheet.update(f"A{cell.row}:F{cell.row}", [nueva_fila])
            else:
                # Agrega una nueva fila
                worksheet.append_row(nueva_fila)
            
            st.success(f"¡Posición {ub} guardada exitosamente!")
            st.rerun()