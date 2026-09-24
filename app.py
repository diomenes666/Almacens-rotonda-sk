import streamlit as st
import pandas as pd
import gspread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.service_account import Credentials
import io
import re
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
DRIVE_FOLDER_ID = "0AH09sQFmvTNvUk9PVA"
DOMINIO_ORG = "sankare.com"
MAX_FOTOS_POR_POSICION = 5

COLUMNA_UBICACION = "Ultima Ubicación"
PRIMERA_FILA_DATOS = 2

CAMPOS_REQUERIDOS = ["Area"]


# ---------------------------------------------------------
# CONEXIÓN CON GOOGLE SHEETS Y GOOGLE DRIVE
# ---------------------------------------------------------
@st.cache_resource
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


@st.cache_data(ttl=300, show_spinner=False)
def obtener_encabezados():
    return [h.strip() for h in worksheet.row_values(1) if h.strip()]


ENCABEZADOS = obtener_encabezados()
COLUMNAS_CONTENIDO = [h for h in ENCABEZADOS if h != COLUMNA_UBICACION]

_faltantes = [c for c in [COLUMNA_UBICACION] + CAMPOS_REQUERIDOS if c not in ENCABEZADOS]
if _faltantes:
    st.error(
        f"La hoja no tiene la(s) columna(s): {', '.join(_faltantes)}. "
        f"Verifica el encabezado (fila 1) del Excel antes de continuar."
    )
    st.stop()


# ---------------------------------------------------------
# UTILIDADES DE GOOGLE DRIVE
# ---------------------------------------------------------
def _extension_archivo(archivo, default="jpg"):
    nombre = getattr(archivo, "name", None)
    if nombre and "." in nombre:
        return nombre.rsplit(".", 1)[-1].lower()
    return default


def _id_desde_link(url):
    if not url:
        return None
    m = re.search(r"/d/([A-Za-z0-9_-]{10,})", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", url)
    return m.group(1) if m else None


def _otorgar_permiso_visualizacion(file_id):
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
                f"para verlo: {e}"
            )


def subir_archivo_a_drive(archivo, nombre_archivo):
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
    links = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for idx, archivo in enumerate(archivos, start=1):
        ext = _extension_archivo(archivo)
        nombre_archivo = f"foto_{ubicacion}_{timestamp}_{idx}.{ext}"
        link = subir_archivo_a_drive(archivo, nombre_archivo)
        if link:
            links.append(link)
    return links


def eliminar_fotos_de_drive(urls):
    for url in urls:
        file_id = _id_desde_link(url)
        if not file_id:
            continue
        try:
            drive_service.files().update(
                fileId=file_id,
                body={'trashed': True},
                supportsAllDrives=True
            ).execute()
        except Exception as e:
            st.warning(f"No se pudo eliminar una foto de Drive: {e}")


# ---------------------------------------------------------
# DATOS
# ---------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def cargar_datos():
    return pd.DataFrame(worksheet.get_all_records())


def parsear_fotos(valor_celda):
    if not valor_celda:
        return []
    return [url.strip() for url in str(valor_celda).split(",") if url.strip()]


def calcular_estado_posiciones(df):
    if df.empty or COLUMNA_UBICACION not in df.columns:
        return {}
    cols = [c for c in COLUMNAS_CONTENIDO if c in df.columns]
    if not cols:
        return {}
    tiene_datos = df[cols].astype(str).apply(
        lambda fila: any(v.strip() and v.strip().lower() != "nan" for v in fila),
        axis=1
    )
    ocupadas = df.loc[tiene_datos].copy()
    ocupadas[COLUMNA_UBICACION] = ocupadas[COLUMNA_UBICACION].astype(str).str.strip()
    ocupadas = ocupadas[~ocupadas[COLUMNA_UBICACION].isin(["", "nan"])]

    estado = {}
    for _, fila in ocupadas.iterrows():
        concepto = str(fila.get("Concepto", "") or "").strip()
        estado[fila[COLUMNA_UBICACION]] = concepto
    return estado


def fila_de(ubicacion, df):
    if df.empty or COLUMNA_UBICACION not in df.columns:
        return None
    coincidencias = df.index[
        df[COLUMNA_UBICACION].astype(str).str.strip() == str(ubicacion).strip()
    ]
    return int(coincidencias[0]) + PRIMERA_FILA_DATOS if len(coincidencias) else None


def construir_fila(valores):
    return [valores.get(h, "") for h in ENCABEZADOS]


df = cargar_datos()

# Navegación
st.sidebar.header("📍 Navegación")
estante_sel = st.sidebar.selectbox("Seleccionar Estante:", ["A (CONTABILIDAD)", "B (FINANZAS)", "C (GTH)"])
codigo_estante = estante_sel[0]

# Buscador
st.sidebar.subheader("🔍 Buscar")
busqueda = st.sidebar.text_input("Ingresa término:")
if busqueda and not df.empty:
    res = df[df.apply(lambda row: row.astype(str).str.contains(busqueda, case=False, na=False).any(), axis=1)]
    if not res.empty:
        for _, r in res.iterrows():
            st.sidebar.info(f"📍 **{r.get(COLUMNA_UBICACION, '')}**: {r.get('Concepto', '')}")
    else:
        st.sidebar.warning("Sin resultados")
elif busqueda:
    st.sidebar.warning("Sin resultados")

# Configuración de estantes
config_estantes = {"A": {1: 2, 2: 3, 3: 2}, "B": {1: 2, 2: 2, 3: 3}, "C": {1: 2, 2: 2, 3: 2}}
posiciones_bloqueadas = []
estado_posiciones = calcular_estado_posiciones(df)


# ---------------------------------------------------------
# VENTANA EMERGENTE (MODAL)
# ---------------------------------------------------------
@st.dialog("📝 Detalle de Ubicación")
def abrir_modal_registro(ubicacion):
    st.markdown(f"### Posición: **{ubicacion}**")

    pos_data = df[df[COLUMNA_UBICACION].astype(str).str.strip() == ubicacion] \
        if COLUMNA_UBICACION in df.columns else pd.DataFrame()

    def _val(col):
        if pos_data.empty or col not in pos_data.columns:
            return ""
        v = pos_data[col].values[0]
        return "" if pd.isna(v) else str(v)

    concepto_val = _val("Concepto")
    area_val = _val("Area")
    periodos_val = _val("Periodos")
    anio_val = _val("Año")
    detalle_val = _val("Detalle")
    obs_val = _val("Observaciones")
    lista_fotos_existentes = parsear_fotos(_val("Foto"))

    concepto = st.text_input("Concepto / Título", value=concepto_val)

    col_i1, col_i2 = st.columns(2)
    with col_i1:
        area = st.text_input("Área *", value=area_val, help="Campo obligatorio")
    with col_i2:
        anio = st.text_input("Año", value=anio_val)

    periodos = st.text_input("Periodos", value=periodos_val)
    detalle = st.text_area("Detalle del Contenido", value=detalle_val, height=80)
    obs = st.text_input("Observaciones", value=obs_val)

    st.caption("* Campo obligatorio")
    st.divider()

    st.markdown("**📸 Fotografías**")

    fotos_a_conservar = []
    if lista_fotos_existentes:
        st.caption("Fotos registradas — desmarca las que quieras eliminar al guardar:")
        opciones = {f"Foto {i + 1}": url for i, url in enumerate(lista_fotos_existentes)}
        seleccion = st.multiselect(
            "Fotos registradas",
            options=list(opciones.keys()),
            default=list(opciones.keys()),
            key=f"fotos_existentes_{ubicacion}",
            label_visibility="collapsed",
        )
        for etiqueta in seleccion:
            fotos_a_conservar.append(opciones[etiqueta])
        for etiqueta, url in opciones.items():
            st.markdown(f"🔗 [{etiqueta}]({url})")

    st.caption(f"Agregar evidencia (opcional) — máximo {MAX_FOTOS_POR_POSICION} fotos en total por posición.")

    fotos_galeria = st.file_uploader(
        "Subir imágenes desde Galería/PC",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key=f"file_{ubicacion}"
    )

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

    st.divider()

    guardar = st.button(
        "💾 Guardar Registro",
        type="primary",
        use_container_width=True,
        disabled=total_final > MAX_FOTOS_POR_POSICION,
        key=f"guardar_{ubicacion}",
    )

    if guardar:
        if not area.strip():
            st.error("El campo **Área** es obligatorio.")
        else:
            archivos_nuevos = list(fotos_galeria or []) + list(canasta)
            links_nuevos = []
            if archivos_nuevos:
                with st.spinner(f"Subiendo {len(archivos_nuevos)} foto(s) a Google Drive..."):
                    links_nuevos = subir_multiples_fotos(archivos_nuevos, ubicacion)

            fotos_finales = fotos_a_conservar + links_nuevos
            descartadas = [u for u in lista_fotos_existentes if u not in fotos_a_conservar]

            nueva_fila = construir_fila({
                COLUMNA_UBICACION: ubicacion,
                "Concepto": concepto,
                "Area": area,
                "Periodos": periodos,
                "Detalle": detalle,
                "Año": anio,
                "Observaciones": obs,
                "Foto": ", ".join(fotos_finales),
            })

            try:
                fila = fila_de(ubicacion, df)
                ultima_col = chr(ord('A') + len(ENCABEZADOS) - 1)
                if fila:
                    worksheet.update([nueva_fila], f"A{fila}:{ultima_col}{fila}")
                else:
                    worksheet.append_row(nueva_fila)

                if descartadas:
                    eliminar_fotos_de_drive(descartadas)

                st.session_state[f"canasta_{ubicacion}"] = []
                cargar_datos.clear()

                st.toast(f"¡Posición {ubicacion} guardada con éxito!", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Error al guardar: {e}")

    if not pos_data.empty:
        with st.expander("🧹 Vaciar esta posición"):
            st.caption("Elimina la fila de la hoja y envía sus fotos a la papelera de Drive.")
            confirmar = st.checkbox(
                f"Confirmo liberar {ubicacion}",
                key=f"conf_vaciar_{ubicacion}"
            )
            if st.button(
                "Liberar posición",
                key=f"vaciar_{ubicacion}",
                disabled=not confirmar,
                use_container_width=True
            ):
                try:
                    fila = fila_de(ubicacion, df)
                    if fila:
                        worksheet.delete_rows(fila)
                    if lista_fotos_existentes:
                        eliminar_fotos_de_drive(lista_fotos_existentes)

                    st.session_state[f"canasta_{ubicacion}"] = []
                    cargar_datos.clear()

                    st.toast(f"Posición {ubicacion} liberada", icon="🧹")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al liberar la posición: {e}")


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
                            concepto = estado_posiciones.get(cod)
                            ocup = concepto is not None

                            # LÓGICA INVERTIDA DE VISUALIZACIÓN:
                            # 1) Ocupado: Muestra el Concepto completo en el botón y la ubicación en el caption.
                            # 2) Libre: Muestra la ubicación dentro del botón.
                            if ocup:
                                texto_boton = f"🔴 {concepto if concepto else cod}"
                            else:
                                texto_boton = f"🟢 {cod}"

                            if st.button(
                                texto_boton,
                                key=f"btn_{cod}",
                                use_container_width=True,
                                help=f"{cod} - {concepto}" if concepto else cod,
                            ):
                                abrir_modal_registro(cod)

                            # Ubicación técnica debajo únicamente cuando está ocupado
                            if ocup:
                                st.caption(f"📍 {cod}")

# Botón auxiliar para descargar el archivo completo desde la interfaz si se desea
st.sidebar.markdown("---")
st.sidebar.download_button(
    label="📥 Descargar app.py",
    data=open(__file__, "rb") if "__file__" in locals() else "",
    file_name="app.py",
    mime="text/x-python"
)