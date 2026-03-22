import streamlit as st 
import streamlit.components.v1 as components 
import folium 
from streamlit_folium import st_folium 
from folium.plugins import Draw 
import requests 
import pandas as pd 
import numpy as np 
from shapely.geometry import Polygon, Point, LineString, box 
from shapely import affinity 
from shapely.prepared import prep 
import time 
import math 
import branca.colormap as cm 
import plotly.graph_objects as go 
import matplotlib.pyplot as plt
import matplotlib.tri as tri

# =========================================================================
# --- 1. CONFIGURATION DE LA PAGE ---
# =========================================================================
st.set_page_config(layout="wide", page_title="Coastal & Marine Master Planning")

# =========================================================================
# --- 2. SYSTÈME D'AUTHENTIFICATION ---
# =========================================================================
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    st.title("🔒 Accès Sécurisé - Terminal Master Planning")
    with st.form("login_form"):
        pwd_input = st.text_input("Code d'accès :", type="password")
        submitted = st.form_submit_button("Se connecter")
        if submitted:
            passwords = st.secrets.get("passwords", {"default": "admin"})
            if pwd_input in passwords.values():
                st.session_state["authenticated"] = True
                st.rerun() 
            else:
                st.error("Code d'accès incorrect. 🛑")
    st.stop()

# =========================================================================
# --- 3. L'APPLICATION PRINCIPALE ---
# =========================================================================
st.title("⚓ Coastal, Marine & Earthworks Optimizer")

if st.sidebar.button("Se déconnecter 🚪"):
    st.session_state["authenticated"] = False
    st.rerun()

# Injection CSS (Gestion de l'impression des onglets SANS transparence)
st.markdown(""" 
<style> 
@media print { 
    @page { size: A3 landscape; margin: 10mm; }
    body { background-color: white !important; }
    .stSidebar, header, footer, .stButton { display: none !important; } 
    div[data-baseweb="tab-panel"], div[role="tabpanel"], div[hidden] {
        display: block !important; visibility: visible !important;
        position: relative !important; height: auto !important;
        overflow: visible !important; opacity: 1 !important; 
    }
    div[role="tablist"] { display: none !important; }
    .leaflet-layer, .leaflet-pane, .leaflet-tile { opacity: 1 !important; }
} 
</style> 
""", unsafe_allow_html=True) 

# --- MEMOIRE DE SESSION --- 
if 'raw_df' not in st.session_state: st.session_state['raw_df'] = None 
if 'master_raw_df' not in st.session_state: st.session_state['master_raw_df'] = None
if 'geoms' not in st.session_state: st.session_state['geoms'] = {'poly': None} 
if 'master_geoms' not in st.session_state: st.session_state['master_geoms'] = {'poly': None}
if 'map_center' not in st.session_state: st.session_state['map_center'] = [43.325, 5.340] 
if 'last_buffer' not in st.session_state: st.session_state['last_buffer'] = 50 
if 'rect_data' not in st.session_state: st.session_state['rect_data'] = {'coords': [], 'area': 0.0, 'type': 'Rectangle'}

# --- BARRE LATERALE : RECHERCHE --- 
st.sidebar.header("Localisation") 
search_query = st.sidebar.text_input("Port ou coordonnées GPS (ex: 43.32, 5.34)") 
if st.sidebar.button("Aller à cette position"): 
    if search_query: 
        try: 
            if "," in search_query and any(c.isdigit() for c in search_query): 
                lat, lon = map(float, search_query.split(",")) 
                st.session_state['map_center'] = [lat, lon] 
            else: 
                url = f"https://nominatim.openstreetmap.org/search?q={search_query}&format=json&limit=1" 
                res = requests.get(url, headers={'User-Agent': 'TopoApp/1.0'}).json() 
                if res: st.session_state['map_center'] = [float(res[0]['lat']), float(res[0]['lon'])] 
        except: pass 

# --- BARRE LATERALE : API MNT MIXTE --- 
st.sidebar.markdown("---") 
st.sidebar.header("Source Topo/Bathy") 
api_choice = st.sidebar.selectbox("Fournisseur de MNT", [
    "GEBCO 2020 (Mixte Terre/Mer)", 
    "NOAA ETOPO1 (Mixte)", 
    "Open-Meteo (Terre uniquement)", 
    "Google Maps API (Terre uniquement)",
    "Fichier Local (CSV)"
]) 

api_key = ""
uploaded_mnt = None
if "Google" in api_choice: 
    api_key = st.sidebar.text_input("Clé API", type="password") 
elif "Fichier Local" in api_choice:
    uploaded_mnt = st.sidebar.file_uploader("Importer MNT (CSV)", type=['csv', 'txt'])
    st.sidebar.caption("Colonnes attendues: Lat, Lon, Z_Ext")

# --- BARRE LATERALE : COTES DU PROJET --- 
st.sidebar.markdown("---") 
st.sidebar.header("Cotes Altimétriques (Design 3D)") 
z_terreplein = st.sidebar.number_input("Cote Terre-Plein / Quai (m MSL)", value=3.0, step=0.5, help="La hauteur de la plateforme hors d'eau.")
z_chenal = st.sidebar.number_input("Cote Fond Dragage / Bassin (m MSL)", value=-15.0, step=0.5, help="La profondeur du bassin ou chenal.")

st.sidebar.subheader("Pente du Fond (Optionnel)")
design_slope_pct = st.sidebar.number_input("Pente du Chenal (%)", value=0.0, step=0.1) 
rotation_offset = st.sidebar.slider("Axe de Pente (°)", -180, 180, 0, step=1) 
allow_reclamation = st.sidebar.toggle("Autoriser Réclamation (Remblai)", value=True, help="Si désactivé, l'IA ne comblera pas les trous naturels.")

# --- BARRE LATERALE : GENIE CIVIL & GEOTECHNIQUE --- 
st.sidebar.markdown("---") 
st.sidebar.header("Géotechnique & Talus") 
soil_types = { 
    "Rocher Massif (Déroctage) (1:1)": 1.0, 
    "Rocher Fracturé / Corail (1:1.5)": 1.5, 
    "Argile Raide / Marne (1:2)": 2.0, 
    "Sable / Sol Standard (1:3)": 3.0, 
    "Vase / Argile Molle (1:4)": 4.0, 
    "Fonds très saturés (1:5)": 5.0 
} 
slope_ratio = soil_types[st.sidebar.selectbox("Nature des Fonds (Pente)", list(soil_types.keys()), index=3)] * st.sidebar.number_input("Facteur de Sécurité (FoS)", min_value=1.0, value=1.2, step=0.1) 

pavement_thickness = st.sidebar.number_input("Épaisseur Chaussée / Surprofondeur (cm)", 0, 200, 50, step=10) / 100.0 
buffer_size = st.sidebar.slider("Débord d'Étude Bathymétrique (m)", 0, 500, 100, step=25) 
user_grid_res = st.sidebar.number_input("Maillage d'Analyse (m)", value=10.0, step=1.0) 

# --- BARRE LATERALE : OPTIMISATION FONCIER --- 
st.sidebar.markdown("---") 
st.sidebar.header("IA : Génération de Forme") 
forme_optimisation = st.sidebar.radio("Forme à inscrire", ["Rectangle", "Triangle Rectangle", "Losange (Parallélogramme)"])
auto_angle = st.sidebar.toggle("Rotation Automatique (IA)", value=True)
manual_angle = st.sidebar.slider("Forcer l'angle (°)", 0, 180, 0, step=1, disabled=auto_angle)
yard_margin = st.sidebar.slider("Retrait de sécurité (m)", 0, 50, 5, step=1) 

col_opt1, col_opt2 = st.sidebar.columns(2)
if col_opt1.button("🚀 CALCULER FORME", type="primary", use_container_width=True): st.session_state['trigger_rect_calc'] = True
if col_opt2.button("🗑️ EFFACER", use_container_width=True): st.session_state['rect_data'] = {'coords': [], 'area': 0.0, 'type': 'Rectangle'}

# --- BARRE LATERALE : LOGISTIQUE --- 
st.sidebar.markdown("---") 
st.sidebar.header("Logistique & Cadençage") 
equip_ratios = {"TSHD (Aspiratrice)": 2500, "CSD (Désagrégateur)": 1500, "Excavatrices (Terre)": 400} 
prod_m3_h = equip_ratios[st.sidebar.selectbox("Flotte Principale", list(equip_ratios.keys()))] 
target_days = st.sidebar.number_input("Durée Cible (Jours)", value=120) 
hours_per_day = st.sidebar.slider("Heures Opération / Jour", 1, 24, 20)
efficiency_rate = st.sidebar.slider("Efficacité (Météo/Pannes) (%)", 10, 100, 75) / 100.0

st.sidebar.subheader("Capacité Terminal")
target_annual_teu = st.sidebar.number_input("Trafic Annuel (TEU)", value=100000, min_value=1) 
dwell_time = st.sidebar.number_input("Temps de Séjour (Jours)", value=7, min_value=1) 
lane_cap = st.sidebar.number_input("Capacite / Voie Gate", value=25000, min_value=1)
admin_sqm = st.sidebar.number_input("Batiments (m2)", value=1500) 
util_rate = st.sidebar.slider("Taux de Remplissage (%)", 10, 100, 75) / 100.0 

# =========================================================================
# --- ETAPE 1 : CARTE DE SAISIE MNT ---
# =========================================================================
m = folium.Map(location=st.session_state['map_center'], zoom_start=15, tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri') 
Draw(export=True, draw_options={'polyline':False, 'polygon':True, 'rectangle':False, 'circle':False, 'marker':False}).add_to(m) 

if st.session_state.get('master_geoms') and st.session_state['master_geoms'].get('poly'):
    m_poly = st.session_state['master_geoms']['poly']
    sw_input = [min(p[1] for p in m_poly), min(p[0] for p in m_poly)]
    ne_input = [max(p[1] for p in m_poly), max(p[0] for p in m_poly)]
    m.fit_bounds([sw_input, ne_input])

col1, col2 = st.columns([2, 1]) 

with col1: 
    st.subheader("1. Délimitation du Périmètre Mixte") 
    st.caption("Tracez votre zone d'étude (terre et/ou mer) pour télécharger la topographie/bathymétrie.")
    output = st_folium(m, width="100%", height=600, key="input_map") 

with col2: 
    st.subheader("2. Extraction & Gestion MNT") 
    
    if st.button("1️⃣ TÉLÉCHARGER LE MNT/LEVÉ (API)", use_container_width=True, type="primary"): 
        poly_coords = None 
        if output["all_drawings"]: 
            polys = [d for d in output["all_drawings"] if d["geometry"]["type"] == "Polygon"]
            if polys: poly_coords = polys[-1]["geometry"]["coordinates"][0] 
         
        if poly_coords: 
            with st.spinner("Acquisition des données mixtes en cours..."): 
                poly = Polygon(poly_coords) 
                c_lat = (poly.bounds[1] + poly.bounds[3]) / 2 
                c_lon = (poly.bounds[0] + poly.bounds[2]) / 2
                
                buffered_poly = poly.buffer(buffer_size / 111000) if buffer_size > 0 else poly 
                min_lon, min_lat, max_lon, max_lat = buffered_poly.bounds 
                area_m2 = poly.area * (111000**2) * math.cos(math.radians(c_lat)) 
                actual_res = user_grid_res if (area_m2 / (user_grid_res**2)) < 1500 else math.ceil(math.sqrt(area_m2 / 1500)) 
                 
                if "Fichier Local" in api_choice and uploaded_mnt is not None:
                    try:
                        local_df = pd.read_csv(uploaded_mnt)
                        lat_col = next((c for c in local_df.columns if c.lower() in ['lat', 'y', 'latitude']), None)
                        lon_col = next((c for c in local_df.columns if c.lower() in ['lon', 'x', 'longitude']), None)
                        z_col = next((c for c in local_df.columns if c.lower() in ['z', 'alt', 'elevation', 'elevation_m', 'z_ext']), None)
                        
                        if lat_col and lon_col and z_col:
                            filtered_pts = []
                            for _, row in local_df.iterrows():
                                pt = Point(row[lon_col], row[lat_col])
                                if buffered_poly.contains(pt):
                                    filtered_pts.append({
                                        'Lat': row[lat_col], 'Lon': row[lon_col], 'Z_Ext': row[z_col], 'In_Project': poly.contains(pt)
                                    })
                            if filtered_pts:
                                new_df = pd.DataFrame(filtered_pts)
                                st.session_state['raw_df'] = new_df
                                st.session_state['master_raw_df'] = new_df.copy()
                                st.session_state['geoms'] = {'poly': poly_coords}
                                st.session_state['master_geoms'] = {'poly': poly_coords}
                                st.session_state['proj_info'] = {'area_m2': area_m2, 'center': [c_lat, c_lon], 'res': actual_res}
                                st.session_state['last_buffer'] = buffer_size
                                st.session_state['rect_data'] = {'coords': [], 'area': 0.0, 'type': 'Rectangle'}
                                st.success("Levé Local extrait avec succès.")
                            else: st.error("Aucun point dans le polygone.")
                        else: st.error("Colonnes Lat, Lon et Z introuvables.")
                    except Exception as e: st.error(f"Erreur CSV : {e}")
                else:
                    lon_pts = np.arange(min_lon, max_lon, actual_res / 111000) 
                    lat_pts = np.arange(min_lat, max_lat, actual_res / 111000) 
                    valid_pts, in_project_flags = [], [] 
                    for lat in lat_pts: 
                        for lon in lon_pts: 
                            pt = Point(lon, lat) 
                            if buffered_poly.contains(pt): 
                                valid_pts.append((lat, lon)) 
                                in_project_flags.append(poly.contains(pt)) 
                     
                    elevs, successful_pts, successful_flags = [], [], [] 
                    for i in range(0, len(valid_pts), 50): 
                        chunk = valid_pts[i:i+50] 
                        chunk_flags = in_project_flags[i:i+50] 
                        lats, lons = [p[0] for p in chunk], [p[1] for p in chunk] 
                        
                        try: 
                            locs = "|".join([f"{lt},{ln}" for lt,ln in zip(lats,lons)]) 
                            if "Google" in api_choice: 
                                r = requests.get(f"https://maps.googleapis.com/maps/api/elevation/json?locations={locs}&key={api_key.strip()}").json() 
                                if r.get('status') == 'OK': elevs.extend([res['elevation'] for res in r['results']]) 
                            elif "Open-Meteo" in api_choice: 
                                r = requests.get("https://api.open-meteo.com/v1/elevation", params={"latitude": ",".join(map(str, lats)), "longitude": ",".join(map(str, lons))}).json() 
                                elevs.extend(r['elevation']) 
                            elif "GEBCO" in api_choice or "ETOPO1" in api_choice:
                                api_url = "gebco2020" if "GEBCO" in api_choice else "etopo1"
                                r = requests.get(f"https://api.opentopodata.org/v1/{api_url}?locations={locs}").json()
                                if 'results' in r: elevs.extend([res['elevation'] for res in r['results']])
                                time.sleep(1.1) 
                                        
                            successful_pts.extend(chunk) 
                            successful_flags.extend(chunk_flags) 
                        except: pass 
                     
                    if elevs: 
                        new_df = pd.DataFrame({'Lat': [p[0] for p in successful_pts], 'Lon': [p[1] for p in successful_pts], 'Z_Ext': elevs, 'In_Project': successful_flags})
                        st.session_state['raw_df'] = new_df
                        st.session_state['master_raw_df'] = new_df.copy()
                        st.session_state['geoms'] = {'poly': poly_coords}
                        st.session_state['master_geoms'] = {'poly': poly_coords}
                        st.session_state['proj_info'] = {'area_m2': area_m2, 'center': [c_lat, c_lon], 'res': actual_res}
                        st.session_state['last_buffer'] = buffer_size
                        st.session_state['rect_data'] = {'coords': [], 'area': 0.0, 'type': 'Rectangle'}
                        st.success("MNT Mixte téléchargé avec succès.") 

    if st.button("2️⃣ ACTUALISER LE FILTRE ZONE (Local)", use_container_width=True):
        if st.session_state['master_raw_df'] is not None:
            if output["all_drawings"]:
                polys = [d for d in output["all_drawings"] if d["geometry"]["type"] == "Polygon"]
                if polys:
                    new_poly_coords = polys[-1]["geometry"]["coordinates"][0]
                    new_poly = Polygon(new_poly_coords)
                    df_master = st.session_state['master_raw_df'].copy()
                    df_master['In_Project'] = df_master.apply(lambda r: new_poly.contains(Point(r['Lon'], r['Lat'])), axis=1)
                    
                    st.session_state['raw_df'] = df_master
                    st.session_state['geoms']['poly'] = new_poly_coords
                    c_lat = (new_poly.bounds[1] + new_poly.bounds[3]) / 2 
                    new_area = new_poly.area * (111000**2) * math.cos(math.radians(c_lat))
                    
                    st.session_state['proj_info']['area_m2'] = new_area
                    st.session_state['proj_info']['center'] = [c_lat, (new_poly.bounds[0]+new_poly.bounds[2])/2]
                    st.session_state['rect_data'] = {'coords': [], 'area': 0.0, 'type': 'Rectangle'}
                    st.success("Zone restreinte localement.")
        else: st.error("Aucun MNT en mémoire mère.")

    col_btn1, col_btn2 = st.columns(2)
    if col_btn1.button("REVENIR AU MASTER", use_container_width=True):
        if st.session_state['master_raw_df'] is not None:
            st.session_state['raw_df'] = st.session_state['master_raw_df'].copy()
            st.session_state['geoms'] = st.session_state['master_geoms'].copy()
            st.rerun()
            
    if st.session_state['master_raw_df'] is not None:
        mnt_csv = st.session_state['master_raw_df'][['Lat', 'Lon', 'Z_Ext']].to_csv(index=False).encode('utf-8')
        col_btn2.download_button("📥 SAUVER LEVÉ BRUT (CSV)", data=mnt_csv, file_name="topo_bathy_master.csv", mime="text/csv", use_container_width=True)

# =========================================================================
# --- ETAPE 2 : MOTEUR 3D, DESSIN QUAI/TERRE-PLEIN & CALCULS ---
# =========================================================================
if st.session_state['raw_df'] is not None: 
    if not st.session_state['raw_df']['In_Project'].any():
        st.error("⚠️ Le polygone dessiné ne contient aucun point. Élargissez-le.")
        st.stop()

    df = st.session_state['raw_df'].copy() 
    c_lat, c_lon = st.session_state['proj_info']['center'] 
    area_m2 = st.session_state['proj_info']['area_m2'] 
    actual_res = st.session_state['proj_info']['res'] 

    def to_m(lon, lat): return (lon-c_lon)*111000*math.cos(math.radians(c_lat)), (lat-c_lat)*111000 
    def m_to_latlon(x, y): return y / 111000 + c_lat, x / (111000 * math.cos(math.radians(c_lat))) + c_lon 

    df['X'], df['Y'] = zip(*[to_m(ln, lt) for lt, ln in zip(df['Lat'], df['Lon'])]) 
    
    # -------------------------------------------------------------
    # EXTRACTION DES DESSINS (QUAI & TERRE-PLEIN) DEPUIS LA CARTE RESULTAT
    # -------------------------------------------------------------
    res_map_state = st.session_state.get("res_map", {})
    drawn_polys_res, drawn_lines_res = [], []
    if res_map_state and res_map_state.get("all_drawings"):
        for d in res_map_state["all_drawings"]:
            if d["geometry"]["type"] == "Polygon": drawn_polys_res.append(d["geometry"]["coordinates"][0])
            elif d["geometry"]["type"] == "LineString": drawn_lines_res.append(d["geometry"]["coordinates"])
            
    # Détermination de la forme du Terre-Plein (Priorité : Dessin manuel -> Forme IA)
    term_coords = None
    if drawn_polys_res:
        term_coords = drawn_polys_res[-1]
    elif st.session_state['rect_data']['coords']:
        term_coords = st.session_state['rect_data']['coords'][0]
        
    quay_coords = drawn_lines_res[-1] if drawn_lines_res else None

    # -------------------------------------------------------------
    # GENERATION DU MNT PROJET 3D (Z_FGL_Target)
    # -------------------------------------------------------------
    app_slope = design_slope_pct 
    app_az = rotation_offset 
    S_s = app_slope / 100.0 
    ux_s, uy_s = math.sin(math.radians(app_az)), math.cos(math.radians(app_az)) 
    
    # Base de l'eau (Chenal avec pente éventuelle)
    df['Z_sh_base'] = z_chenal - S_s * (df['X']*ux_s + df['Y']*uy_s) 

    new_z_target = []
    if term_coords:
        term_poly_m = Polygon([to_m(lon, lat) for lon, lat in term_coords])
        quay_line_m = LineString([to_m(lon, lat) for lon, lat in quay_coords]) if quay_coords else None
        
        # Securité geometry valide
        if not term_poly_m.is_valid: term_poly_m = term_poly_m.buffer(0)
        prep_term = prep(term_poly_m)
        
        for x, y, z_base in zip(df['X'], df['Y'], df['Z_sh_base']):
            pt = Point(x, y)
            if prep_term.contains(pt):
                # Sur le plateau / terminal
                new_z_target.append(z_terreplein)
            else:
                # Dans l'eau / talus
                dist_to_term = term_poly_m.distance(pt)
                is_quay = False
                
                # Détection Mur de Quai vs Talus
                if quay_line_m:
                    dist_to_quay = quay_line_m.distance(pt)
                    # Tolérance de grille pour considérer que c'est un "mur"
                    if dist_to_quay <= dist_to_term + (actual_res * 1.5):
                        is_quay = True
                
                if is_quay:
                    new_z_target.append(z_base) # Chute verticale
                else:
                    # Pente du talus
                    z_talus = z_terreplein - (dist_to_term / slope_ratio)
                    new_z_target.append(max(z_base, z_talus))
    else:
        # Aucune infrastructure dessinée, c'est juste le fond de dragage
        new_z_target = df['Z_sh_base'].tolist()
        
    df['Z_FGL_Target'] = new_z_target
    
    # -------------------------------------------------------------
    # CALCUL DES VOLUMES ET PROFONDEURS FINALES
    # -------------------------------------------------------------
    if not allow_reclamation:
        # Si on n'autorise pas le remblaiement, on ne comble pas les trous naturels
        mask_deep = df['Z_Ext'] <= df['Z_FGL_Target']
        df['Z_FGL'] = df['Z_FGL_Target'].copy()
        df.loc[mask_deep, 'Z_FGL'] = df.loc[mask_deep, 'Z_Ext']
        
        df['Z_Sub'] = df['Z_FGL'] - pavement_thickness
        df.loc[mask_deep, 'Z_Sub'] = df.loc[mask_deep, 'Z_Ext'] 
    else:
        df['Z_FGL'] = df['Z_FGL_Target']
        df['Z_Sub'] = df['Z_FGL'] - pavement_thickness

    df['Diff_Earth'] = df['Z_Sub'] - df['Z_Ext'] 
    df_p_out = df[df['In_Project']] 
    
    # Ventilation Mixte Terre/Mer des Volumes
    is_land = df_p_out['Z_Ext'] > 0
    is_sea = df_p_out['Z_Ext'] <= 0
    is_cut = df_p_out['Diff_Earth'] < 0
    is_fill = df_p_out['Diff_Earth'] > 0

    # Dragage / Déblai (Excavation)
    vol_deblai_terre = abs(df_p_out[is_land & is_cut]['Diff_Earth'].sum()) * (actual_res**2)
    vol_dragage_mer = abs(df_p_out[is_sea & is_cut]['Diff_Earth'].sum()) * (actual_res**2)
    total_cut = vol_deblai_terre + vol_dragage_mer

    # Réclamation / Remblai (Fill)
    vol_remblai_terre = df_p_out[is_land & is_fill]['Diff_Earth'].sum() * (actual_res**2)
    
    sea_fill_df = df_p_out[is_sea & is_fill]
    vol_remblai_sousmer = (np.minimum(sea_fill_df['Z_Sub'], 0) - sea_fill_df['Z_Ext']).sum() * (actual_res**2)
    vol_remblai_surmer = np.maximum(sea_fill_df['Z_Sub'], 0).sum() * (actual_res**2)
    
    total_fill = vol_remblai_terre + vol_remblai_sousmer + vol_remblai_surmer
    bilan_net = total_fill - total_cut 

    # -------------------------------------------------------------
    # ALGORITHMES IA (Rectangle, Triangle, Losange)
    # -------------------------------------------------------------
    def get_max_inscribed_rect_robust(poly, is_auto, man_angle):
        if poly.is_empty: return None, 0
        best_rect, best_area = None, 0
        centroid = (poly.centroid.x, poly.centroid.y)
        angles_to_test = range(0, 180, 5) if is_auto else [man_angle]
            
        for angle in angles_to_test:
            rot_poly = affinity.rotate(poly, -angle, origin=centroid, use_radians=False)
            minx, miny, maxx, maxy = rot_poly.bounds
            xs, ys = np.linspace(minx, maxx, 20), np.linspace(miny, maxy, 20)
            
            for i in range(len(xs)):
                for j in range(len(xs)-1, i, -1):
                    w = xs[j] - xs[i]
                    if w * (maxy - miny) <= best_area: break
                    for k in range(len(ys)):
                        for l in range(len(ys)-1, k, -1):
                            h = ys[l] - ys[k]
                            area = w * h
                            if area > best_area:
                                cand = box(xs[i], ys[k], xs[j], ys[l])
                                if cand.within(rot_poly):
                                    best_area = area
                                    best_rect = affinity.rotate(cand, angle, origin=centroid, use_radians=False)
                                    break
                            else: break
        return best_rect, best_area

    def get_max_inscribed_right_triangle_robust(poly, is_auto, man_angle):
        if poly.is_empty: return None, 0
        best_tri, best_area = None, 0
        centroid = (poly.centroid.x, poly.centroid.y)
        angles_to_test = range(0, 180, 10) if is_auto else [man_angle]
            
        for angle in angles_to_test:
            rot_poly = affinity.rotate(poly, -angle, origin=centroid, use_radians=False)
            minx, miny, maxx, maxy = rot_poly.bounds
            xs, ys = np.linspace(minx, maxx, 15), np.linspace(miny, maxy, 15)
            
            for i in range(len(xs)):
                for j in range(len(xs)-1, i, -1):
                    w = xs[j] - xs[i]
                    if w * (maxy - miny) * 0.5 <= best_area: break
                    for k in range(len(ys)):
                        for l in range(len(ys)-1, k, -1):
                            h = ys[l] - ys[k]
                            area = 0.5 * w * h
                            if area > best_area:
                                t1 = Polygon([(xs[i], ys[k]), (xs[j], ys[k]), (xs[i], ys[l])])
                                t2 = Polygon([(xs[i], ys[k]), (xs[j], ys[k]), (xs[j], ys[l])])
                                t3 = Polygon([(xs[i], ys[k]), (xs[i], ys[l]), (xs[j], ys[l])])
                                t4 = Polygon([(xs[j], ys[k]), (xs[j], ys[l]), (xs[i], ys[l])])
                                
                                for cand in [t1, t2, t3, t4]:
                                    if cand.area > best_area and cand.within(rot_poly):
                                        best_area = cand.area
                                        best_tri = affinity.rotate(cand, angle, origin=centroid, use_radians=False)
                            else: break
        return best_tri, best_area

    def get_max_inscribed_parallelogram_robust(poly, is_auto, man_angle):
        if poly.is_empty: return None, 0
        best_para, best_area = None, 0
        centroid = (poly.centroid.x, poly.centroid.y)
        angles_to_test = range(0, 180, 10) if is_auto else [man_angle]
        shear_angles = range(-45, 46, 15) 
            
        for angle in angles_to_test:
            rot_poly = affinity.rotate(poly, -angle, origin=centroid, use_radians=False)
            
            for shear in shear_angles:
                skewed_poly = affinity.skew(rot_poly, xs=-shear, origin=centroid)
                minx, miny, maxx, maxy = skewed_poly.bounds
                xs, ys = np.linspace(minx, maxx, 15), np.linspace(miny, maxy, 15)
                
                for i in range(len(xs)):
                    for j in range(len(xs)-1, i, -1):
                        w = xs[j] - xs[i]
                        if w * (maxy - miny) <= best_area: break
                        for k in range(len(ys)):
                            for l in range(len(ys)-1, k, -1):
                                h = ys[l] - ys[k]
                                area = w * h
                                if area > best_area:
                                    cand = box(xs[i], ys[k], xs[j], ys[l])
                                    if cand.within(skewed_poly):
                                        best_area = area
                                        para = affinity.skew(cand, xs=shear, origin=centroid)
                                        best_para = affinity.rotate(para, angle, origin=centroid, use_radians=False)
                                        break
                                else: break
        return best_para, best_area

    # Exécution via le bouton IA
    if st.session_state.get('trigger_rect_calc', False):
        st.session_state['trigger_rect_calc'] = False
        poly_coords_m = [to_m(lon, lat) for lon, lat in st.session_state['geoms']['poly']] 
        inner_poly_shp = Polygon(poly_coords_m)
        if inner_poly_shp and not inner_poly_shp.is_empty:
            core_poly = inner_poly_shp.buffer(-yard_margin)
            if not core_poly.is_empty:
                with st.spinner(f"Calcul IA : {forme_optimisation} optimal..."):
                    if forme_optimisation == "Rectangle":
                        final_shape, final_area = get_max_inscribed_rect_robust(core_poly, auto_angle, manual_angle)
                    elif forme_optimisation == "Triangle Rectangle":
                        final_shape, final_area = get_max_inscribed_right_triangle_robust(core_poly, auto_angle, manual_angle)
                    else:
                        final_shape, final_area = get_max_inscribed_parallelogram_robust(core_poly, auto_angle, manual_angle)
                        
                    if final_shape:
                        st.session_state['rect_data'] = {
                            'coords': [[m_to_latlon(x, y) for x, y in final_shape.exterior.coords]],
                            'area': final_area,
                            'type': forme_optimisation
                        }
                    # Force un rerun pour inclure l'IA comme Terre-Plein dans le df 3D
                    st.rerun()

    best_shape_ll = st.session_state['rect_data']['coords']
    operational_area_m2 = st.session_state['rect_data']['area']
    current_shape_type = st.session_state['rect_data'].get('type', 'Forme Optimisée')

    col_pdf1, col_pdf2 = st.columns([4, 1]) 
    with col_pdf2: 
        st.caption("💡 Imprimez au format Paysage (Landscape)")
        if st.button("🖨️ IMPRIMER LE RAPPORT PDF", type="secondary", use_container_width=True): 
            components.html("<script>window.parent.print();</script>", height=0) 

    # ========================================================= 
    # ONGLETS DE RESULTATS (3 ONGLETS)
    # ========================================================= 
    tab_civil, tab_hydro, tab_topo = st.tabs(["Opérations Maritimes & Quantités", "Météocéan & Hydrodynamique", "Plan Topo & Contours"]) 

    with tab_civil: 
        st.subheader("Bilan des Opérations : Terre & Mer") 
        
        r1, r2, r3 = st.columns(3) 
        with r1: 
            st.write("### Excavation (Cut)") 
            st.write(f"**Dragage Marin :** <span style='color:red;'>{vol_dragage_mer:,.0f} m³</span>", unsafe_allow_html=True) 
            st.write(f"**Déblai Terrestre :** <span style='color:darkred;'>{vol_deblai_terre:,.0f} m³</span>", unsafe_allow_html=True) 
            st.write(f"**Total Excavé :** {total_cut:,.0f} m³") 
             
        with r2: 
            st.write("### Apport (Fill / Réclamation)") 
            st.write(f"**Remblai Terrestre :** <span style='color:green;'>{vol_remblai_terre:,.0f} m³</span>", unsafe_allow_html=True) 
            st.write(f"**Remblai Sous-Marin :** <span style='color:blue;'>{vol_remblai_sousmer:,.0f} m³</span>", unsafe_allow_html=True) 
            st.write(f"**Réclamation (Sur Mer) :** <span style='color:darkblue;'>{vol_remblai_surmer:,.0f} m³</span>", unsafe_allow_html=True) 
            st.write(f"**Total Apport :** {total_fill:,.0f} m³")

        with r3: 
            st.write("### Planification Logistique") 
            st.write(f"**Flotte :** {selected_equip.split('(')[0].strip()}") 
            daily_prod = prod_m3_h * hours_per_day * efficiency_rate
            total_work_vol = total_cut + total_fill
            est_days = total_work_vol / daily_prod if daily_prod > 0 else 0
            
            st.write(f"**Rendement Effectif :** {daily_prod:,.0f} m³/j")
            if est_days <= target_days:
                st.markdown(f"<span style='color:green; font-weight:bold;'>Durée Est. : {est_days:,.0f} jours (Dans les délais)</span>", unsafe_allow_html=True)
            else:
                st.markdown(f"<span style='color:red; font-weight:bold;'>Durée Est. : {est_days:,.0f} jours (Dépassement)</span>", unsafe_allow_html=True)
            
        st.markdown("---")
        st.write("### Capacité Foncier (Export Google Earth)")
        col_dl1, col_dl2, col_dl3 = st.columns(3)
        
        # Résumé textuel Foncier
        net_area_val = max(0.0, area_m2 - admin_sqm - (target_annual_teu / lane_cap * 500)) 
        area_needed = ((target_annual_teu * dwell_time) / 365) * (30 / util_rate) 
        
        col_dl1.markdown(f"**Zone d'étude initiale :** {area_m2:,.0f} m²", unsafe_allow_html=True)
        col_dl2.markdown(f"**Foncier Total (Polygone dessiné) :**<br>Requis: {area_needed:,.0f} m²", unsafe_allow_html=True)
        col_dl3.markdown(f"**{current_shape_type} IA (Jaune) :**<br>{operational_area_m2:,.0f} m²", unsafe_allow_html=True)
        
        # Boutons Export
        poly_export = [] 
        for i, (lon, lat) in enumerate(st.session_state['geoms']['poly']): poly_export.append({"Nom": "1_Zone_Etude", "Lat": lat, "Lon": lon, "Ordre": i}) 
        if term_coords: 
            for i, (lon, lat) in enumerate(term_coords): poly_export.append({"Nom": "2_Emprise_Terminal", "Lat": lat, "Lon": lon, "Ordre": i}) 
        if best_shape_ll:
            for i, (lat, lon) in enumerate(best_shape_ll[0]): poly_export.append({"Nom": f"3_{current_shape_type}_IA", "Lat": lat, "Lon": lon, "Ordre": i})
        csv_export = pd.DataFrame(poly_export).to_csv(index=False).encode('utf-8') 
        st.download_button("📥 EXPORTER LES CONTOURS (.CSV pour GIS)", csv_export, "contours_projet.csv", "text/csv", use_container_width=True)

        # ========================================================= 
        # CARTE PRINCIPALE INTERACTIVE
        # ========================================================= 
        st.markdown("---") 
        st.subheader("🏗️ Conception 3D du Projet & Mouvements de Terre") 
        st.info("🎨 **Outils de Dessin :** Sur la carte ci-dessous, dessinez un **Polygone** pour définir le Terre-Plein, et une **Ligne** pour forcer un Mur de Quai. Le reste sera raccordé en Talus. Les calculs et les coupes se mettent à jour automatiquement !")
         
        m_res = folium.Map(location=st.session_state['proj_info']['center'], zoom_start=17, tiles='OpenStreetMap') 
        Draw(export=False, draw_options={'polyline':True, 'polygon':True, 'rectangle':False, 'circle':False, 'marker':False}).add_to(m_res)
        
        folium.Polygon(locations=[(p[1], p[0]) for p in st.session_state['geoms']['poly']], color='black', weight=2, fill=False).add_to(m_res) 
         
        max_d = max(abs(df['Diff_Earth'].min()), abs(df['Diff_Earth'].max())) 
        if max_d == 0: max_d = 0.1
        colormap = cm.LinearColormap(colors=['red', 'white', 'blue'], index=[-max_d, 0, max_d], vmin=-max_d, vmax=max_d) 
        colormap.add_to(m_res) 
         
        for _, r in df.iterrows(): 
            folium.CircleMarker([r['Lat'], r['Lon']], radius=4 if r['In_Project'] else 2, color=colormap(r['Diff_Earth']), fill=True, fill_opacity=0.8 if r['In_Project'] else 0.4).add_to(m_res) 

        if term_coords: 
            folium.Polygon(locations=[(lat, lon) for lon, lat in term_coords], color='#FF00FF', weight=3, dash_array='5,5', fill=False, tooltip="Terre-Plein / Terminal").add_to(m_res) 
            
        if quay_coords:
            folium.PolyLine(locations=[(lat, lon) for lon, lat in quay_coords], color='black', weight=6, tooltip="Mur de Quai Vertical").add_to(m_res)

        if best_shape_ll:
            folium.Polygon(locations=best_shape_ll[0], color='#FFD700', weight=4, fill=True, fill_color='#FFD700', fill_opacity=0.4, tooltip=f"{current_shape_type} Maximum").add_to(m_res)

        # Rendu de la carte et interception des dessins (pour le rerun automatique)
        res_map_output = st_folium(m_res, width=1200, height=500, key="res_map")

        # ========================================================= 
        # MOTEUR DES COUPES 
        # ========================================================= 
        st.markdown("---") 
        st.subheader("Coupes d'Exécution Transversales (A-A' et B-B')") 
        st.caption("Visualisez votre Terre-Plein, le mur de Quai et les Talus en coupe 2D.")
         
        col_c1, col_c2, col_c3 = st.columns(3) 
        with col_c1: angle_coupe = st.slider("Rotation des Axes de Coupe (°)", 0, 180, 0, step=1) 
        theta_cut = math.radians(angle_coupe) 
        df['X_cut'] = df['X'] * math.cos(theta_cut) + df['Y'] * math.sin(theta_cut) 
        df['Y_cut'] = -df['X'] * math.sin(theta_cut) + df['Y'] * math.cos(theta_cut) 
        min_x_c, max_x_c = float(df['X_cut'].min()), float(df['X_cut'].max()) 
        min_y_c, max_y_c = float(df['Y_cut'].min()), float(df['Y_cut'].max()) 
         
        with col_c2: offset_A = st.slider("Ligne A-A' (Transversale)", min_y_c, max_y_c, (min_y_c+max_y_c)/2, step=float(actual_res)) 
        with col_c3: offset_B = st.slider("Ligne B-B' (Longitudinale)", min_x_c, max_x_c, (min_x_c+max_x_c)/2, step=float(actual_res)) 

        def cut_to_gps(xc, yc): 
            x_loc = xc * math.cos(theta_cut) - yc * math.sin(theta_cut) 
            y_loc = xc * math.sin(theta_cut) + yc * math.cos(theta_cut) 
            return m_to_latlon(x_loc, y_loc) 

        gps_A1, gps_A2 = cut_to_gps(min_x_c, offset_A), cut_to_gps(max_x_c, offset_A) 
        gps_B1, gps_B2 = cut_to_gps(offset_B, min_y_c), cut_to_gps(offset_B, max_y_c) 
        
        # Mini carte pour localiser les coupes
        m_coupes = folium.Map(location=st.session_state['proj_info']['center'], zoom_start=16, tiles='OpenStreetMap')
        folium.Polygon(locations=[(p[1], p[0]) for p in st.session_state['geoms']['poly']], color='black', weight=2, fill=False).add_to(m_coupes) 
        folium.PolyLine(locations=[gps_A1, gps_A2], color='darkorange', weight=3, dash_array='5,5').add_to(m_coupes) 
        folium.Marker(gps_A1, icon=folium.DivIcon(html="<div style='font-size:14px; color:darkorange; font-weight:bold; background:white; border:1px solid black; padding:2px;'>A</div>")).add_to(m_coupes) 
        folium.Marker(gps_A2, icon=folium.DivIcon(html="<div style='font-size:14px; color:darkorange; font-weight:bold; background:white; border:1px solid black; padding:2px;'>A'</div>")).add_to(m_coupes) 
        folium.PolyLine(locations=[gps_B1, gps_B2], color='darkgreen', weight=3, dash_array='5,5').add_to(m_coupes) 
        folium.Marker(gps_B1, icon=folium.DivIcon(html="<div style='font-size:14px; color:darkgreen; font-weight:bold; background:white; border:1px solid black; padding:2px;'>B</div>")).add_to(m_coupes) 
        folium.Marker(gps_B2, icon=folium.DivIcon(html="<div style='font-size:14px; color:darkgreen; font-weight:bold; background:white; border:1px solid black; padding:2px;'>B'</div>")).add_to(m_coupes) 
        st_folium(m_coupes, width=1200, height=250, key="carte_execution_civile") 

        tol = max(actual_res, 3.0) 
        slice_A = df[abs(df['Y_cut'] - offset_A) <= tol].copy() 
        slice_A['Dist'] = slice_A['X_cut'].round(0) 
        slice_A = slice_A.groupby('Dist').agg({'Z_Ext':'mean', 'Z_FGL':'mean', 'Z_Sub':'mean', 'In_Project':'first'}).reset_index() 

        slice_B = df[abs(df['X_cut'] - offset_B) <= tol].copy() 
        slice_B['Dist'] = slice_B['Y_cut'].round(0) 
        slice_B = slice_B.groupby('Dist').agg({'Z_Ext':'mean', 'Z_FGL':'mean', 'Z_Sub':'mean', 'In_Project':'first'}).reset_index() 
         
        col_prof1, col_prof2 = st.columns([1, 1]) 
        with col_prof1: fix_ratio = st.toggle("Verrouiller Ratio X:Y (Echelle Proportionnelle)") 
        with col_prof2: z_exag = st.slider("Exageration Verticale (Z)", 1.0, 20.0, 1.0, step=1.0) if fix_ratio else 1.0 
         
        def create_profile_fig(df_slice, title): 
            fig = go.Figure() 
            if not df_slice.empty: 
                min_dist, max_dist = df_slice['Dist'].min(), df_slice['Dist'].max()
                fig.add_trace(go.Scatter(x=[min_dist, max_dist], y=[0, 0], mode='lines', name='Niveau Zéro (Mer)', line=dict(color='cyan', width=1, dash='dashdot')))

                # Remplissage Dragage
                fig.add_trace(go.Scatter(x=df_slice['Dist'], y=df_slice['Z_FGL'], mode='lines', line=dict(width=0), showlegend=False, hoverinfo='skip'))
                z_cut = np.maximum(df_slice['Z_FGL'], df_slice['Z_Ext'])
                fig.add_trace(go.Scatter(x=df_slice['Dist'], y=z_cut, mode='none', fill='tonexty', fillcolor='rgba(255, 0, 0, 0.4)', name='Dragage/Déblai'))
                
                # Remplissage Remblai
                fig.add_trace(go.Scatter(x=df_slice['Dist'], y=df_slice['Z_FGL'], mode='lines', line=dict(width=0), showlegend=False, hoverinfo='skip'))
                z_fill = np.minimum(df_slice['Z_FGL'], df_slice['Z_Ext'])
                if allow_reclamation:
                    fig.add_trace(go.Scatter(x=df_slice['Dist'], y=z_fill, mode='none', fill='tonexty', fillcolor='rgba(0, 0, 255, 0.4)', name='Remblai/Réclamation'))

                fig.add_trace(go.Scatter(x=df_slice['Dist'], y=df_slice['Z_Ext'], mode='lines', name='Fonds / Terrain Naturel', line=dict(color='brown', width=2))) 
                fig.add_trace(go.Scatter(x=df_slice['Dist'], y=df_slice['Z_FGL'], mode='lines', name='Profil Projet (Quai/Talus/Plateau)', line=dict(color='black', width=3))) 
                 
                in_p = df_slice[df_slice['In_Project']] 
                if not in_p.empty: 
                    d_min, d_max = in_p['Dist'].min(), in_p['Dist'].max() 
                    fig.add_vline(x=d_min, line_width=1, line_dash="solid", line_color="black") 
                    fig.add_vline(x=d_max, line_width=1, line_dash="solid", line_color="black") 

            fig.update_layout(title=title, height=350, margin=dict(l=20, r=20, t=40, b=20), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), xaxis_title="Distance de Coupe (m)", yaxis_title="Cote Z (m MSL)") 
            if fix_ratio: fig.update_yaxes(scaleanchor="x", scaleratio=z_exag) 
            return fig 

        cg1, cg2 = st.columns(2) 
        with cg1: st.plotly_chart(create_profile_fig(slice_A, "Coupe Transversale A - A'"), use_container_width=True) 
        with cg2: st.plotly_chart(create_profile_fig(slice_B, "Coupe Longitudinale B - B'"), use_container_width=True) 

    # ONGLET HYDROLOGIE 
    with tab_hydro: 
        st.write("### Hydrologie & Assainissement") 
        
        def get_climate_params(lat, lon, freq_str):
            if 30 <= lat <= 38 and -10 <= lon <= 12: 
                zone = "Maghreb / Climat Semi-Aride"
                b_val = 0.55
                a_vals = {"1": 1.5, "2": 2.5, "5": 4.0, "7": 4.8, "10": 6.0, "20": 8.5, "50": 12.0}
            elif 38 < lat <= 45 and -5 <= lon <= 20: 
                zone = "Europe Méditerranéenne"
                b_val = 0.50
                a_vals = {"1": 1.2, "2": 2.2, "5": 3.5, "7": 4.2, "10": 5.5, "20": 7.5, "50": 10.5}
            elif 45 < lat <= 60 and -10 <= lon <= 30: 
                zone = "Europe Tempérée"
                b_val = 0.65
                a_vals = {"1": 2.0, "2": 3.0, "5": 4.5, "7": 5.2, "10": 6.5, "20": 8.5, "50": 11.5}
            elif -20 <= lat <= 20: 
                zone = "Zone Tropicale"
                b_val = 0.40
                a_vals = {"1": 2.5, "2": 4.0, "5": 6.5, "7": 8.0, "10": 10.0, "20": 14.0, "50": 20.0}
            else: 
                zone = "Standard / Modéré"
                b_val = 0.60
                a_vals = {"1": 1.8, "2": 2.8, "5": 4.2, "7": 4.9, "10": 6.0, "20": 8.0, "50": 11.0}
                
            if "1 an" in freq_str: return zone, a_vals["1"], b_val
            elif "2 ans" in freq_str: return zone, a_vals["2"], b_val
            elif "5 ans" in freq_str: return zone, a_vals["5"], b_val
            elif "7 ans" in freq_str: return zone, a_vals["7"], b_val
            elif "10 ans" in freq_str: return zone, a_vals["10"], b_val
            elif "20 ans" in freq_str: return zone, a_vals["20"], b_val
            else: return zone, a_vals["50"], b_val

        col_h1, col_h2 = st.columns(2)
        with col_h1:
            st.subheader("1. Pluie de Projet (Loi de Montana)")
            freq = st.selectbox("Période de retour", ["1 an (Très fréquent)", "2 ans (Biennale)", "5 ans (Quinquennale)", "7 ans", "10 ans (Décennale)", "20 ans (Vicennale)", "50 ans (Cinquantennale)"], index=4)
            
            zone_name, def_a, def_b = get_climate_params(c_lat, c_lon, freq)
            st.info(f"🌍 Zone détectée : **{zone_name}**")
            
            montana_a = st.number_input("Coefficient Montana 'a'", value=float(def_a), step=0.5)
            montana_b = st.number_input("Coefficient Montana 'b'", value=float(def_b), step=0.05)
            duree_h = st.number_input("Durée de la pluie (heures)", value=2.0, step=0.5)
            
            pluie_mm = montana_a * ((duree_h * 60) ** (1 - montana_b))
            st.success(f"Hauteur de pluie générée : **{pluie_mm:.1f} mm**")
            
        with col_h2:
            st.subheader("2. Bassin Versant")
            net_area_bv = max(0.0, area_m2 - admin_sqm - (target_annual_teu / lane_cap * 500)) 
            surface_bv = st.number_input("Surface à drainer (m²)", value=float(net_area_bv), step=100.0)
            
            type_sol = st.selectbox("Type de Revêtement", [
                "Asphalte / Béton (Cr = 0.95)",
                "Pavage lourd (Cr = 0.80)",
                "Grave bitume (Cr = 0.65)",
                "Terre (Cr = 0.30)",
                "Saisie Manuelle"
            ])
            if type_sol == "Saisie Manuelle":
                cr = st.slider("Coefficient de Ruissellement (Cr)", 0.1, 1.0, 0.9)
            else:
                cr = float(type_sol.split("=")[1].replace(")", "").strip())
                st.write(f"Coefficient appliqué : **{cr}**")
                
            fuite = st.number_input("Débit de fuite autorisé (L/s/ha)", value=10.0, step=1.0)
            
        st.markdown("---")
        st.subheader("3. Dimensionnement Bassin de Rétention")
        
        v_pluie = (surface_bv * cr) * (pluie_mm / 1000.0) 
        v_evac = (fuite * (surface_bv / 10000.0) / 1000.0) * (duree_h * 3600) 
        v_ret = max(0.0, v_pluie - v_evac) 
        
        col_res1, col_res2, col_res3 = st.columns(3)
        col_res1.metric("Volume total ruisselé", f"{v_pluie:,.0f} m³")
        col_res2.metric("Volume dissipé (Fuite)", f"{v_evac:,.0f} m³")
        col_res3.metric("BASSIN DE RÉTENTION REQUIS", f"{v_ret:,.0f} m³")

    # ONGLET LIGNES DE CONTOUR
    with tab_topo:
        st.write("### Plan Topographique & Bathymétrique")
        
        col_t1, col_t2, col_t3 = st.columns([2, 1, 1])
        with col_t1:
            topo_display = st.radio("Affichage :", ["Courbes de Niveau (Isolignes)", "Vecteurs d'écoulement (Pentes)"], horizontal=True)
        with col_t2:
            step_contour = st.slider("Équidistance des courbes (m)", 0.2, 5.0, 1.0, step=0.2)
        with col_t3:
            opacite_sat = st.slider("Opacité Vue Aérienne", 0.0, 1.0, 0.5, step=0.1)
            
        m_contour = folium.Map(location=st.session_state['proj_info']['center'], zoom_start=17, tiles=None)
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', 
            attr='Esri',
            name='Esri Satellite',
            opacity=opacite_sat
        ).add_to(m_contour)
        
        folium.Polygon(locations=[(p[1], p[0]) for p in st.session_state['geoms']['poly']], color='white', weight=3, fill=False).add_to(m_contour)
        
        if topo_display == "Courbes de Niveau (Isolignes)":
            try:
                fig, ax = plt.subplots()
                triang = tri.Triangulation(df['Lon'], df['Lat'])
                zmin, zmax = df['Z_Ext'].min(), df['Z_Ext'].max()
                
                if zmax > zmin:
                    levels = np.arange(math.floor(zmin), math.ceil(zmax) + step_contour, step_contour)
                    if len(levels) > 1:
                        contour_lines = ax.tricontour(triang, df['Z_Ext'], levels=levels)
                        cmap = cm.LinearColormap(colors=['red', 'yellow', 'green', 'blue', 'darkblue'], vmin=zmin, vmax=zmax)
                        m_contour.add_child(cmap)
                        
                        if hasattr(contour_lines, 'allsegs'):
                            for i, level_segs in enumerate(contour_lines.allsegs):
                                if i < len(levels):
                                    level = levels[i]
                                    color = cmap(level)
                                    for seg in level_segs:
                                        if len(seg) >= 2:
                                            folium.PolyLine(locations=[[y, x] for x, y in seg], color=color, weight=2, opacity=0.8, tooltip=f"Z: {level:.1f} m").add_to(m_contour)
                        else:
                            for level, collection in zip(levels, contour_lines.collections):
                                color = cmap(level)
                                for path in collection.get_paths():
                                    coords = path.vertices
                                    if len(coords) >= 2:
                                        folium.PolyLine(locations=[[y, x] for x, y in coords], color=color, weight=2, opacity=0.8, tooltip=f"Z: {level:.1f} m").add_to(m_contour)
                plt.close(fig)
            except Exception as e:
                st.error(f"Erreur Contour: {e}")
        else:
            res_5x = actual_res * 5 
            df_topo = df.copy()  
            df_topo['X_bin'] = (df_topo['X'] // res_5x) * res_5x 
            df_topo['Y_bin'] = (df_topo['Y'] // res_5x) * res_5x 
            df_sampled = df_topo.groupby(['X_bin', 'Y_bin']).first().reset_index() 

            for _, r in df_sampled.iterrows(): 
                html_txt = f"<div style='font-size: 11px; font-weight: bold; color: yellow; text-shadow: 1px 1px 2px black;'>{r['Z_Ext']:.1f}</div>" 
                folium.Marker([r['Lat'], r['Lon']], icon=folium.DivIcon(html=html_txt)).add_to(m_contour) 
                folium.CircleMarker([r['Lat'], r['Lon']], radius=2, color='yellow', fill=True).add_to(m_contour) 

            min_x_t, max_x_t = df_topo['X'].min(), df_topo['X'].max() 
            min_y_t, max_y_t = df_topo['Y'].min(), df_topo['Y'].max() 
            step_x = (max_x_t - min_x_t) / 12 
            step_y = (max_y_t - min_y_t) / 12 

            df_topo['Grid_X'] = ((df_topo['X'] - min_x_t) // step_x) 
            df_topo['Grid_Y'] = ((df_topo['Y'] - min_y_t) // step_y) 

            for name, group in df_topo.groupby(['Grid_X', 'Grid_Y']): 
                if len(group) >= 3: 
                    A_q = np.c_[group['X'], group['Y'], np.ones(group.shape[0])] 
                    c_q = np.linalg.lstsq(A_q, group['Z_Ext'], rcond=None)[0] 
                    loc_slope = math.hypot(c_q[0], c_q[1]) * 100 
                    if loc_slope > 0.5: 
                        loc_az = (math.degrees(math.atan2(-c_q[0], -c_q[1])) + 360) % 360 
                        cx, cy = group['X'].mean(), group['Y'].mean() 
                        L_arr = 25 
                        end_x = cx + L_arr * math.sin(math.radians(loc_az)) 
                        end_y = cy + L_arr * math.cos(math.radians(loc_az)) 
                        gps_origin = m_to_latlon(cx, cy) 
                        gps_end = m_to_latlon(end_x, end_y) 
                        folium.PolyLine(locations=[gps_origin, gps_end], color='cyan', weight=5, tooltip=f"Pente: {loc_slope:.1f}%").add_to(m_contour) 
                        head_L = 10
                        a1, a2 = math.radians(loc_az + 150), math.radians(loc_az - 150) 
                        folium.PolyLine(locations=[gps_end, m_to_latlon(end_x + head_L * math.sin(a1), end_y + head_L * math.cos(a1))], color='cyan', weight=5).add_to(m_contour) 
                        folium.PolyLine(locations=[gps_end, m_to_latlon(end_x + head_L * math.sin(a2), end_y + head_L * math.cos(a2))], color='cyan', weight=5).add_to(m_contour) 
            
        st_folium(m_contour, width=1200, height=600, key=f"carte_contours_{topo_display}_{opacite_sat}")
