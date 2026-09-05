import streamlit as st
import pandas as pd
import gspread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.service_account import Credentials
import io
from datetime import datetime

# Configuración de página adaptativa
st.set_page_config(
    page_title="Almacén Rotonda",
    layout="wide",
    page_icon="📦",
    initial_sidebar_state="collapsed"
)

st.title("📦 Almacén Rotonda")

# ---------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------
# ID de la Unidad Compartida (Shared Drive) de Google Drive.
# Al ser una Unidad Compartida, el almacenamiento pertenece a la
# organización, no a la Service Account, por lo que no hay problemas
# de cuota 0 bytes ni de transferencia de propiedad.
DRIVE_FOLDER_ID = "0AH09sQFmvTNvUk9PVA"

# Dominio de tu organización, usado como respaldo si Google Workspace
# bloquea compartir archivos "para cualquier persona con el enlace".
DOMINIO_ORG = "sankare.com"

MAX_FOTOS_POR_POSICION = 5


# ---------------------------------------------------------
# CONEXIÓN CON GOOGLE SHEETS Y GOOGLE DRIVE
# ---------------------------------------------------------
@st.cache_resource(ttl=0)
def conectar_servicios():
    try:
        creds_dict = dict(st.secrets["connections"]["gsheets"])

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]

        client_sheets = gspread.service_account_from_dict(creds_dict)
        sheet_url = creds_dict["spreadsheet"]
        worksheet = client_sheets.open_by_url(sheet_url).sheet1

        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        drive_service = build('drive', 'v3', credentials=credentials)

        return worksheet, drive_service
    except Exception as e:
        st.error(f"Error de conexión con Google APIs: {e}")
        st.stop()


worksheet, drive_service = conectar_servicios()


# ---------------------------------------------------------
# UTILIDADES DE GOOGLE DRIVE
# ---------------------------------------------------------
def _extension_archivo(archivo, default="jpg"):
    nombre = getattr(archivo, "name", None)
    if nombre and "." in nombre:
        return nombre.rsplit(".", 1)[-1].lower()
    return default


def _otorgar_permiso_visualizacion(file_id):
    """Intenta hacer el archivo visible por enlace público.
    Si la política de la organización bloquea compartir fuera del
    dominio, cae de forma automática a un permiso restringido al
    dominio de la empresa."""
    try:
        drive_service.permissions().create(
            fileId=file_id,
            body={'type': 'anyone', 'role': 'reader'},
            supportsAllDrives=True
        ).execute()
    except Exception:
        try:
            drive_service.permissions().create(
                fileId=file_id,
                body={
                    'type': 'domain',
                    'role': 'reader',
                    'domain': DOMINIO_ORG,
                    'allowFileDiscovery': False
                },
                supportsAllDrives=True
            ).execute()
        except Exception as e:
            st.warning(
                f"El archivo se subió, pero no se pudo generar un enlace "
                f"para verlo (revisa las políticas de tu organización): {e}"
            )


def subir_archivo_a_drive(archivo, nombre_archivo):
    """Sube un único archivo a la Unidad Compartida y devuelve su link."""
    try:
        metadata = {
            'name': nombre_archivo,
            'parents': [DRIVE_FOLDER_ID]
        }

        media = io.BytesIO(archivo.getvalue())
        media_body = MediaIoBaseUpload(media, mimetype=archivo.type, resumable=True)

        file = drive_service.files().create(
            body=metadata,
            media_body=media_body,
            fields='id, webViewLink',
            supportsAllDrives=True
        ).execute()

        file_id = file.get('id')
        _otorgar_permiso_visualizacion(file_id)

        return file.get('webViewLink')
    except Exception as e:
        st.error(f"Error al subir '{nombre_archivo}' a Google Drive: {e}")
        return None


def subir_multiples_fotos(archivos, ubicacion):
    """Sube una lista de archivos y devuelve la lista de links exitosos."""
    links = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for idx, archivo in enumerate(archivos, start=1):
        ext = _extension_archivo(archivo)
        nombre_archivo = f"foto_{ubicacion}_{timestamp}_{idx}.{ext}"
        link = subir_archivo_a_drive(archivo, nombre_archivo)
        if link:
            links.append(link)
    return links


# ---------------------------------------------------------
# DATOS
# ---------------------------------------------------------
def cargar_datos():
    data = worksheet.get_all_records()
    return pd.DataFrame(data)


def parsear_fotos(valor_celda):
    """Convierte el contenido de la celda 'Foto' (links separados por
    coma) en una lista limpia de URLs."""
    if not valor_celda:
        return []
    return [url.strip() for url in str(valor_celda).split(",") if url.strip()]


df = cargar_datos()

# Navegación
st.sidebar.header("📍 Navegación")
estante_sel = st.sidebar.selectbox("Seleccionar Estante:", ["A (CONTABILIDAD)", "B (FINANZAS)", "C (GTH)"])
codigo_estante = estante_sel[0]

# Buscador
st.sidebar.subheader("🔍 Buscar")
busqueda = st.sidebar.text_input("Ingresa término:")
if busqueda:
    res = df[df.apply(lambda row: row.astype(str).str.contains(busqueda, case=False).any(), axis=1)]
    if not res.empty:
        for _, r in res.iterrows():
            st.sidebar.info(f"📍 **{r.get('Ultima Ubicación', '')}**: {r.get('Concepto', '')}")
    else:
        st.sidebar.warning("Sin resultados")

# Configuración de estantes
config_estantes = {"A": {1: 2, 2: 3, 3: 2}, "B": {1: 2, 2: 2, 3: 3}, "C": {1: 2, 2: 2, 3: 2}}
posiciones_bloqueadas = ["B15D1", "B15D2", "B15P1", "B15P2", "B16D1", "B16D2", "B16P1", "B16P2"]
ubicaciones_ocupadas = set(df["Ultima Ubicación"].dropna().astype(str).tolist()) if "Ultima Ubicación" in df.columns else set()


# ---------------------------------------------------------
# VENTANA EMERGENTE (MODAL)
# ---------------------------------------------------------
@st.dialog("📝 Detalle de Ubicación")
def abrir_modal_registro(ubicacion):
    st.markdown(f"### Posición: **{ubicacion}**")

    pos_data = df[df["Ultima Ubicación"] == ubicacion] if "Ultima Ubicación" in df.columns else pd.DataFrame()

    concepto_val = pos_data["Concepto"].values[0] if not pos_data.empty and "Concepto" in pos_data.columns else ""
    periodos_val = pos_data["Periodos"].values[0] if not pos_data.empty and "Periodos" in pos_data.columns else ""
    anio_val = str(pos_data["Año"].values[0]) if not pos_data.empty and "Año" in pos_data.columns else ""
    detalle_val = pos_data["Detalle"].values[0] if not pos_data.empty and "Detalle" in pos_data.columns else ""
    obs_val = pos_data["Observaciones"].values[0] if not pos_data.empty and "Observaciones" in pos_data.columns else ""
    fotos_existentes = pos_data["Foto"].values[0] if not pos_data.empty and "Foto" in pos_data.columns else ""
    lista_fotos_existentes = parsear_fotos(fotos_existentes)

    # --- Fotos ya registradas ---
    fotos_a_conservar = []
    if lista_fotos_existentes:
        st.markdown("**📸 Fotos registradas**")
        opciones = {f"Foto {i + 1}": url for i, url in enumerate(lista_fotos_existentes)}
        seleccion = st.multiselect(
            "Desmarca las que quieras eliminar al guardar:",
            options=list(opciones.keys()),
            default=list(opciones.keys()),
            key=f"fotos_existentes_{ubicacion}"
        )
        for etiqueta in seleccion:
            fotos_a_conservar.append(opciones[etiqueta])
        for etiqueta, url in opciones.items():
            st.markdown(f"🔗 [{etiqueta}]({url})")

    st.markdown("**📸 Agregar Evidencia Fotográfica (Opcional)**")
    st.caption(f"Máximo {MAX_FOTOS_POR_POSICION} fotos en total por posición.")

    # --- Subida desde galería (múltiple) ---
    fotos_galeria = st.file_uploader(
        "Subir imágenes desde Galería/PC",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key=f"file_{ubicacion}"
    )

    # --- Captura desde cámara, una por una, acumuladas en una "canasta" ---
    st.session_state.setdefault(f"canasta_{ubicacion}", [])
    st.session_state.setdefault(f"cam_counter_{ubicacion}", 0)

    with st.popover("📷 Tomar foto con la cámara"):
        st.caption("Puedes tomar varias fotos: captura una y presiona 'Agregar' antes de tomar la siguiente.")
        cam_key = f"cam_{ubicacion}_{st.session_state[f'cam_counter_{ubicacion}']}"
        foto_capturada = st.camera_input("Capturar", key=cam_key)

        if foto_capturada is not None:
            if st.button("➕ Agregar esta captura", key=f"add_cam_{ubicacion}"):
                st.session_state[f"canasta_{ubicacion}"].append(foto_capturada)
                st.session_state[f"cam_counter_{ubicacion}"] += 1
                st.rerun()

    canasta = st.session_state[f"canasta_{ubicacion}"]
    if canasta:
        st.caption(f"{len(canasta)} foto(s) capturada(s) por cámara, pendientes de guardar:")
        cols_prev = st.columns(min(len(canasta), 5))
        for i, foto in enumerate(canasta):
            with cols_prev[i % len(cols_prev)]:
                st.image(foto, width=80)
                if st.button("🗑️", key=f"del_cam_{ubicacion}_{i}"):
                    canasta.pop(i)
                    st.rerun()

    total_nuevas = len(fotos_galeria or []) + len(canasta)
    total_final = len(fotos_a_conservar) + total_nuevas
    if total_final > MAX_FOTOS_POR_POSICION:
        st.error(
            f"Tienes {total_final} fotos en total, el máximo es "
            f"{MAX_FOTOS_POR_POSICION}. Quita algunas antes de guardar."
        )

    with st.form("form_modal"):
        concepto = st.text_input("Concepto / Título", value=concepto_val)

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            periodos = st.text_input("Periodos", value=periodos_val)
        with col_m2:
            anio = st.text_input("Año", value=anio_val)

        detalle = st.text_area("Detalle del Contenido", value=detalle_val, height=80)
        obs = st.text_input("Observaciones", value=obs_val)

        guardar = st.form_submit_button(
            "💾 Guardar Registro",
            type="primary",
            use_container_width=True,
            disabled=total_final > MAX_FOTOS_POR_POSICION
        )

        if guardar:
            archivos_nuevos = list(fotos_galeria or []) + list(canasta)
            links_nuevos = []
            if archivos_nuevos:
                with st.spinner(f"Subiendo {len(archivos_nuevos)} foto(s) a Google Drive..."):
                    links_nuevos = subir_multiples_fotos(archivos_nuevos, ubicacion)

            fotos_finales = fotos_a_conservar + links_nuevos
            url_fotos = ", ".join(fotos_finales)

            nueva_fila = [ubicacion, concepto, periodos, detalle, anio, obs, url_fotos]

            try:
                cell = worksheet.find(ubicacion)
                if cell:
                    worksheet.update([nueva_fila], f"A{cell.row}:G{cell.row}")
                else:
                    worksheet.append_row(nueva_fila)

                # Limpieza de la canasta de cámara para esta ubicación
                st.session_state[f"canasta_{ubicacion}"] = []

                st.toast(f"¡Posición {ubicacion} guardada con éxito!", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Error al guardar: {e}")


# ---------------------------------------------------------
# GRILLA PRINCIPAL
# ---------------------------------------------------------
st.subheader(f"Estante {codigo_estante}")
tabs = st.tabs([f"Subestante {s}" for s in [1, 2, 3]])

for sub_idx, sub_num in enumerate([1, 2, 3]):
    with tabs[sub_idx]:
        num_cols = config_estantes[codigo_estante][sub_num]

        for piso in range(8, 0, -1):
            st.caption(f"**Piso {piso}**")
            gui_cols = st.columns(num_cols)

            for c_idx in range(1, num_cols + 1):
                with gui_cols[c_idx - 1]:
                    cod_p = f"{codigo_estante}{sub_num}{piso}P{c_idx}"
                    cod_d = f"{codigo_estante}{sub_num}{piso}D{c_idx}"

                    for cod in [cod_p, cod_d]:
                        if cod in posiciones_bloqueadas:
                            st.button(f"🚫 {cod}", key=f"btn_{cod}", disabled=True, use_container_width=True)
                        else:
                            ocup = cod in ubicaciones_ocupadas
                            lbl = f"🔴 {cod}" if ocup else f"🟢 {cod}"

                            if st.button(lbl, key=f"btn_{cod}", use_container_width=True):
                                abrir_modal_registro(cod)
