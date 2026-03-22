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
from shapely.ops import unary_union 
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
st.set_page_config(layout="wide", page_title="Marine & Coastal Master Planning")

st.markdown(""" 
<style> 
@media print { 
    @page { size: A3 landscape; margin: 10mm; }
    body { background-color: white !important; -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
    .stSidebar, header, footer, .stButton, .stSlider { display: none !important; } 
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

# =========================================================================
# --- 2. AUTHENTIFICATION ---
# =========================================================================
if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
if not st.session_state["authenticated"]:
    st.title("🔒 Accès Sécurisé - Simulateur Portuaire")
    with st.form("login_form"):
        if st.form_submit_button("Se connecter") and st.text_input("Code d'accès :", type="password") in st.secrets.get("passwords", {"default": "admin"}).values():
            st.session_state["authenticated"] = True
            st.rerun() 
        else: st.info("Entrez le mot de passe (par défaut: admin)")
    st.stop()

st.title("⚓ Coastal, Marine & Earthworks Optimizer 3D")
if st.sidebar.button("Se déconnecter 🚪"): st.session_state["authenticated"] = False; st.rerun()

# =========================================================================
# --- 3. FONCTIONS & MEMOIRE ---
# =========================================================================
def fetch_meteo(lat, lon):
    try:
        w_res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&past_days=30&hourly=windspeed_10m,winddirection_10m").json()
        dirs = [d for d in w_res.get('hourly', {}).get('winddirection_10m', []) if d is not None]
        spds = [s for s in w_res.get('hourly', {}).get('windspeed_10m', []) if s is not None]
        if not dirs: return None
        rounded = [round(d, -1) % 360 for d in dirs]
        dom_dir = max(set(rounded), key=rounded.count)
        return {'dir': dom_dir, 'spd': round(sum(spds)/len(spds), 1)}
    except: return None

# Initialisation 100% sécurisée des dictionnaires
if 'raw_df' not in st.session_state: st.session_state['raw_df'] = None 
if 'master_df' not in st.session_state: st.session_state['master_df'] = None
if 'proj_info' not in st.session_state: st.session_state['proj_info'] = {'area_m2': 0.0, 'center': [43.325, 5.340], 'res': 10.0}
if 'geoms' not in st.session_state: st.session_state['geoms'] = {'poly': None} 
if 'master_geoms' not in st.session_state: st.session_state['master_geoms'] = {'poly': None}
if 'map_center' not in st.session_state: st.session_state['map_center'] = [43.325, 5.340] 
if 'rect_data' not in st.session_state: st.session_state['rect_data'] = {'coords': [], 'area': 0.0, 'type': 'Rectangle'}
if 'meteo' not in st.session_state: st.session_state['meteo'] = None

# --- UI : BARRE LATERALE ---
st.sidebar.header("Localisation") 
search_q = st.sidebar.text_input("Port ou coordonnées (ex: 43.32, 5.34)") 
if st.sidebar.button("Aller à cette position") and search_q: 
    try: 
        if "," in search_q: st.session_state['map_center'] = [float(x) for x in search_q.split(",")] 
        else: 
            res = requests.get(f"https://nominatim.openstreetmap.org/search?q={search_q}&format=json&limit=1").json()
            if res: st.session_state['map_center'] = [float(res[0]['lat']), float(res[0]['lon'])] 
    except: pass 

st.sidebar.markdown("---") 
st.sidebar.header("Source Topo/Bathy") 
api_choice = st.sidebar.selectbox("Fournisseur", ["GEBCO 2020 (Mixte)", "NOAA ETOPO1", "Open-Meteo", "Fichier Local (CSV)"]) 
uploaded_mnt = st.sidebar.file_uploader("Importer MNT (CSV)", type=['csv']) if "Fichier" in api_choice else None
buffer_size = st.sidebar.slider("Débord d'Étude (m)", 0, 500, 100, step=25) 
user_grid_res = st.sidebar.number_input("Maillage d'Analyse (m)", value=10.0, step=1.0) 

st.sidebar.markdown("---") 
st.sidebar.header("Cotes du Projet 3D (Z MSL)") 
z_terreplein = st.sidebar.number_input("Plateforme Terre-Plein (m)", value=3.0, step=0.5)
z_chenal = st.sidebar.number_input("Fond Marin / Chenal Base (m)", value=-12.0, step=0.5)
z_bassin = st.sidebar.number_input("Bassin Dragué Spécifique (m)", value=-15.0, step=0.5)
z_evitage = st.sidebar.number_input("Cercle d'Évitage (m)", value=-16.0, step=0.5)

# RÉINTÉGRATION DE LA PENTE
st.sidebar.subheader("Pente du Fond Marin")
design_slope_pct = st.sidebar.number_input("Pente (%)", value=0.0, step=0.1)
rotation_offset = st.sidebar.slider("Azimut de Pente (°)", -180, 180, 0, step=1)
allow_reclam = st.sidebar.toggle("Autoriser Réclamation (Remblai sur mer)", value=True)

st.sidebar.markdown("---") 
st.sidebar.header("Météocéan & Digue")
if st.sidebar.button("🌬️ Analyser Vents & Digue"):
    center_coords = st.session_state['proj_info'].get('center', [43.32, 5.34])
    met = fetch_meteo(center_coords[0], center_coords[1])
    if met:
        st.session_state['meteo'] = met
        st.sidebar.success(f"Vent: {met['dir']}°. Digue suggérée: {(met['dir']+90)%360}°")
    else: st.sidebar.error("Erreur API Météo.")
z_digue = st.sidebar.number_input("Cote Crête de Digue (m)", value=5.0, step=0.5)

st.sidebar.markdown("---") 
st.sidebar.header("Géotechnique & Talus") 
soil_ratios = {"Rocher (1:1)": 1.0, "Corail (1:1.5)": 1.5, "Argile (1:2)": 2.0, "Sable (1:3)": 3.0, "Vase (1:5)": 5.0} 
slope_ratio = soil_ratios[st.sidebar.selectbox("Nature du Fond", list(soil_ratios.keys()), index=3)] * st.sidebar.number_input("FoS", value=1.2, step=0.1) 

# RÉINTÉGRATION DE LA HAUTEUR MAX DU TALUS
max_slope_height = st.sidebar.number_input("Hauteur Max Talus avant Ouvrage (m)", value=15.0, step=1.0)
pavement_thick = st.sidebar.number_input("Surprofondeur / Chaussée (cm)", 0, 200, 50, step=10) / 100.0 

st.sidebar.markdown("---") 
st.sidebar.header("Optimisation Foncier IA") 
forme_opt = st.sidebar.radio("Forme", ["Rectangle", "Triangle Rectangle", "Losange (Parallélogramme)"])
auto_angle = st.sidebar.toggle("Rotation Auto", value=True)
manual_angle = st.sidebar.slider("Angle (°)", 0, 180, 0, disabled=auto_angle)
yard_margin = st.sidebar.slider("Retrait Périphérique (m)", 0, 50, 5, step=1)
if st.sidebar.button("🚀 CALCULER FORME IA", type="primary"): st.session_state['trigger_ia'] = True

st.sidebar.markdown("---") 
st.sidebar.header("Logistique & Flotte") 
prod_m3_h = {"TSHD": 2500, "CSD": 1500, "Excavatrices": 400}[st.sidebar.selectbox("Flotte", ["TSHD", "CSD", "Excavatrices"])] 
days, hours, eff = st.sidebar.number_input("Jours", value=120), st.sidebar.slider("Heures/j", 1, 24, 20), st.sidebar.slider("Efficacité %", 10, 100, 75)/100.0

st.sidebar.subheader("Capacité Terminal")
target_annual_teu = st.sidebar.number_input("Trafic Annuel (TEU)", value=100000, min_value=1) 
dwell_time = st.sidebar.number_input("Temps de Séjour (Jours)", value=7, min_value=1) 
lane_cap = st.sidebar.number_input("Capacité / Voie Gate", value=25000, min_value=1)
admin_sqm = st.sidebar.number_input("Batiments (m2)", value=1500) 
util_rate = st.sidebar.slider("Taux de Remplissage (%)", 10, 100, 75) / 100.0 

# =========================================================================
# --- ETAPE 1 : ACQUISITION MNT ---
# =========================================================================
m_input = folium.Map(location=st.session_state['map_center'], zoom_start=15, tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri') 
Draw(export=True, draw_options={'polyline':False, 'polygon':True, 'rectangle':False, 'circle':False, 'marker':False}).add_to(m_input) 
if st.session_state['geoms']['poly']:
    folium.Polygon(locations=[(p[1], p[0]) for p in st.session_state['geoms']['poly']], color='red', fill=False).add_to(m_input)

col1, col2 = st.columns([2, 1]) 
with col1: 
    st.subheader("1. Zone Globale du Projet") 
    st.caption("Tracez le grand polygone englobant l'étude (Terre + Mer).")
    input_map_data = st_folium(m_input, width="100%", height=500, key="input_map") 

with col2: 
    st.subheader("2. Extraction Data") 
    if st.button("1️⃣ TÉLÉCHARGER LE MNT", use_container_width=True, type="primary"): 
        if input_map_data["all_drawings"]: 
            poly_coords = [d["geometry"]["coordinates"][0] for d in input_map_data["all_drawings"] if d["geometry"]["type"] == "Polygon"][-1]
            with st.spinner("Sondage en cours..."): 
                poly = Polygon(poly_coords) 
                c_lat, c_lon = poly.centroid.y, poly.centroid.x
                buf_poly = poly.buffer(buffer_size / 111000) if buffer_size > 0 else poly 
                area_m2 = poly.area * (111000**2) * math.cos(math.radians(c_lat)) 
                actual_res = user_grid_res if (area_m2 / (user_grid_res**2)) < 2000 else math.ceil(math.sqrt(area_m2 / 2000)) 
                
                min_lon, min_lat, max_lon, max_lat = buf_poly.bounds
                lons, lats = np.arange(min_lon, max_lon, actual_res/111000), np.arange(min_lat, max_lat, actual_res/111000)
                pts = [Point(lon, lat) for lat in lats for lon in lons if buf_poly.contains(Point(lon, lat))]
                
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
                                if buf_poly.contains(pt):
                                    filtered_pts.append({'Lat': row[lat_col], 'Lon': row[lon_col], 'Z_Ext': row[z_col]})
                            if filtered_pts:
                                df = pd.DataFrame(filtered_pts)
                                df['In_Project'] = df.apply(lambda r: poly.contains(Point(r['Lon'], r['Lat'])), axis=1)
                                st.session_state['master_df'] = df.copy()
                                st.session_state['raw_df'] = df.copy()
                                st.session_state['geoms']['poly'] = poly_coords
                                st.session_state['proj_info'] = {'area_m2': area_m2, 'center': [c_lat, c_lon], 'res': actual_res}
                                st.success("MNT Local Chargé !")
                                st.rerun()
                    except Exception as e: st.error(f"Erreur lecture CSV: {e}")
                else:
                    elevs = []
                    for i in range(0, len(pts), 50):
                        chunk = pts[i:i+50]
                        locs = "|".join([f"{p.y},{p.x}" for p in chunk])
                        try:
                            if "GEBCO" in api_choice:
                                res = requests.get(f"https://api.opentopodata.org/v1/gebco2020?locations={locs}").json()
                                elevs.extend([r['elevation'] for r in res['results']])
                                time.sleep(1.1)
                            else:
                                res = requests.get(f"https://api.open-meteo.com/v1/elevation?latitude={','.join(str(p.y) for p in chunk)}&longitude={','.join(str(p.x) for p in chunk)}").json()
                                elevs.extend(res['elevation'])
                        except: elevs.extend([0]*len(chunk))
                    
                    if elevs:
                        df = pd.DataFrame({'Lat': [p.y for p in pts], 'Lon': [p.x for p in pts], 'Z_Ext': elevs})
                        df['In_Project'] = df.apply(lambda r: poly.contains(Point(r['Lon'], r['Lat'])), axis=1)
                        st.session_state['master_df'] = df.copy()
                        st.session_state['raw_df'] = df.copy()
                        st.session_state['geoms']['poly'] = poly_coords
                        st.session_state['proj_info'] = {'area_m2': area_m2, 'center': [c_lat, c_lon], 'res': actual_res}
                        st.success("MNT (API) Chargé !")
                        st.rerun()

    if st.button("2️⃣ ACTUALISER LE FILTRE (Local)", use_container_width=True):
        if st.session_state['master_df'] is not None and input_map_data["all_drawings"]:
            new_poly = Polygon([d["geometry"]["coordinates"][0] for d in input_map_data["all_drawings"] if d["geometry"]["type"] == "Polygon"][-1])
            df_m = st.session_state['master_df'].copy()
            df_m['In_Project'] = df_m.apply(lambda r: new_poly.contains(Point(r['Lon'], r['Lat'])), axis=1)
            st.session_state['raw_df'] = df_m
            st.session_state['geoms']['poly'] = list(new_poly.exterior.coords)
            st.success("Filtre appliqué.")
            st.rerun()

    if st.button("🗑️ PURGER TOUT", use_container_width=True):
        st.session_state['raw_df'] = st.session_state['master_df'] = None
        st.rerun()

# =========================================================================
# --- ETAPE 2 : MOTEUR 3D & DESSIN D'INFRASTRUCTURES ---
# =========================================================================
if st.session_state['raw_df'] is not None:
    df = st.session_state['raw_df'].copy()
    
    # Sécurisation totale des variables de session
    proj = st.session_state.get('proj_info', {})
    c_lat, c_lon = proj.get('center', [43.325, 5.340])
    actual_res = proj.get('res', 10.0)
    area_m2 = proj.get('area_m2', 0.0)
    
    def to_m(lon, lat): return (lon-c_lon)*111000*math.cos(math.radians(c_lat)), (lat-c_lat)*111000 
    def m_to_latlon(x, y): return y / 111000 + c_lat, x / (111000 * math.cos(math.radians(c_lat))) + c_lon 
    df['X'], df['Y'] = zip(*[to_m(ln, lt) for lt, ln in zip(df['Lat'], df['Lon'])]) 

    # --- CARTE INTERACTIVE 3D ---
    st.markdown("---")
    st.subheader("3. Modélisation 3D (Terminal, Quai, Digue, Bassins)")
    st.info("🖌️ **Outils :** Polygone = Bassin de dragage | Cercle = Évitage | Ligne 1 = Digue | Ligne 2 = Mur de Quai. (L'IA gère le Terre-Plein via le bouton 'Calculer Forme').")
    
    m_design = folium.Map(location=[c_lat, c_lon], zoom_start=16, tiles='OpenStreetMap')
    folium.Polygon(locations=[(p[1], p[0]) for p in st.session_state['geoms']['poly']], color='black', weight=2, fill=False).add_to(m_design)
    Draw(export=False, draw_options={'polyline':True, 'polygon':True, 'circle':True, 'rectangle':False, 'marker':False}).add_to(m_design)
    
    # Affichage du MNT sous-jacent (échantillonné pour performance)
    df_samp = df[df['In_Project']].sample(min(2000, len(df[df['In_Project']])))
    cmap = cm.LinearColormap(['blue', 'cyan', 'green', 'yellow', 'red'], vmin=df['Z_Ext'].min(), vmax=df['Z_Ext'].max())
    for _, r in df_samp.iterrows(): folium.CircleMarker([r['Lat'], r['Lon']], radius=2, color=cmap(r['Z_Ext']), fill=True).add_to(m_design)
    
    design_map_data = st_folium(m_design, width="100%", height=500, key="design_map")

    # --- EXTRACTION DES DESSINS ---
    d_polys, d_lines, d_circles = [], [], []
    if design_map_data and design_map_data.get("all_drawings"):
        for d in design_map_data["all_drawings"]:
            g_type = d["geometry"]["type"]
            if g_type == "Polygon": d_polys.append(d["geometry"]["coordinates"][0])
            elif g_type == "LineString": d_lines.append(d["geometry"]["coordinates"])
            elif g_type == "Point" and d.get("properties", {}).get("radius"): d_circles.append((d["geometry"]["coordinates"], d["properties"]["radius"]))

    bassin_coords = d_polys[-1] if d_polys else None
    digue_coords = d_lines[0] if len(d_lines) > 0 else None
    quai_coords = d_lines[1] if len(d_lines) > 1 else None
    evitage_circle = d_circles[-1] if d_circles else None

    # --- IA FONCIER (MEGA-BLOCK) ---
    poly_coords_m = [to_m(lon, lat) for lon, lat in st.session_state['geoms']['poly']] 
    main_poly_m = Polygon(poly_coords_m)
    
    if st.session_state.get('trigger_ia', False):
        st.session_state['trigger_ia'] = False
        core_poly = main_poly_m.buffer(-yard_margin)
        if not core_poly.is_empty:
            with st.spinner("IA: Recherche d'encastrement géométrique parfait..."):
                best_shape, best_area = None, 0
                centroid = (core_poly.centroid.x, core_poly.centroid.y)
                angles = range(0, 180, 10) if auto_angle else [manual_angle]
                
                for angle in angles:
                    rot_poly = affinity.rotate(core_poly, -angle, origin=centroid, use_radians=False)
                    minx, miny, maxx, maxy = rot_poly.bounds
                    xs, ys = np.linspace(minx, maxx, 15), np.linspace(miny, maxy, 15)
                    
                    for i in range(len(xs)):
                        for j in range(len(xs)-1, i, -1):
                            w = xs[j] - xs[i]
                            if w * (maxy-miny) <= best_area: break
                            for k in range(len(ys)):
                                for l in range(len(ys)-1, k, -1):
                                    h = ys[l] - ys[k]
                                    if w*h > best_area:
                                        if forme_opt == "Rectangle":
                                            cand = box(xs[i], ys[k], xs[j], ys[l])
                                            if cand.within(rot_poly): best_area, best_shape = w*h, affinity.rotate(cand, angle, origin=centroid, use_radians=False)
                                        elif forme_opt == "Triangle Rectangle":
                                            if 0.5*w*h > best_area:
                                                t = Polygon([(xs[i], ys[k]), (xs[j], ys[k]), (xs[i], ys[l])])
                                                if t.within(rot_poly): best_area, best_shape = 0.5*w*h, affinity.rotate(t, angle, origin=centroid, use_radians=False)
                                        elif forme_opt == "Losange (Parallélogramme)":
                                            for shear in [-30, -15, 15, 30]:
                                                sk_poly = affinity.skew(rot_poly, xs=-shear, origin=centroid)
                                                c_box = box(xs[i], ys[k], xs[j], ys[l])
                                                if c_box.within(sk_poly):
                                                    best_area = w*h
                                                    para = affinity.skew(c_box, xs=shear, origin=centroid)
                                                    best_shape = affinity.rotate(para, angle, origin=centroid, use_radians=False)
                if best_shape:
                    st.session_state['rect_data'] = {'coords': [[m_to_latlon(x, y) for x, y in best_shape.exterior.coords]], 'area': best_area, 'type': forme_opt}

    term_coords_ll = st.session_state['rect_data']['coords'][0] if st.session_state['rect_data']['coords'] else None
    
    # --- MOTEUR 3D : CALCUL DU Z CIBLE (Z_FGL_Target) ---
    z_targets = []
    term_poly = Polygon([to_m(lon, lat) for lon, lat in term_coords_ll]) if term_coords_ll else None
    bassin_poly = Polygon([to_m(lon, lat) for lon, lat in bassin_coords]) if bassin_coords else None
    digue_line = LineString([to_m(lon, lat) for lon, lat in digue_coords]) if digue_coords else None
    quai_line = LineString([to_m(lon, lat) for lon, lat in quai_coords]) if quai_coords else None
    evit_pt = Point(to_m(evitage_circle[0][0], evitage_circle[0][1])) if evitage_circle else None
    evit_rad = evitage_circle[1] if evitage_circle else 0

    if term_poly and not term_poly.is_valid: term_poly = term_poly.buffer(0)
    if bassin_poly and not bassin_poly.is_valid: bassin_poly = bassin_poly.buffer(0)

    # Base de l'eau (Pente intégrée)
    app_slope = design_slope_pct 
    app_az = rotation_offset 
    S_s = app_slope / 100.0 
    ux_s, uy_s = math.sin(math.radians(app_az)), math.cos(math.radians(app_az)) 
    df['Z_sh_base'] = z_chenal - S_s * (df['X']*ux_s + df['Y']*uy_s) 

    for x, y, z_base in zip(df['X'], df['Y'], df['Z_sh_base']):
        pt = Point(x, y)
        z_final = z_base 
        
        # 1. Terre-Plein
        if term_poly and term_poly.contains(pt): z_final = z_terreplein
        else:
            dist_term = term_poly.distance(pt) if term_poly else float('inf')
            is_behind_quay = False
            if quai_line and term_poly:
                if quai_line.distance(pt) < dist_term and dist_term < 50: is_behind_quay = True
            
            z_talus_term = -float('inf') if is_behind_quay else (z_terreplein - (dist_term / slope_ratio))
            z_final = max(z_final, z_talus_term)

        # 2. Digue 
        if digue_line:
            dist_digue = digue_line.distance(pt)
            if dist_digue < 5: z_final = max(z_final, z_digue) 
            else: z_final = max(z_final, z_digue - ((dist_digue - 5) / slope_ratio)) 

        # 3. Bassin & Évitage 
        if bassin_poly and bassin_poly.contains(pt): z_final = min(z_final, z_bassin)
        if evit_pt and pt.distance(evit_pt) <= evit_rad: z_final = min(z_final, z_evitage)

        z_targets.append(z_final)

    df['Z_FGL_Target'] = z_targets
    
    # Interdiction remblai mer
    if not allow_reclam:
        mask_norec = (df['Z_Ext'] <= df['Z_FGL_Target']) & (df['Z_Ext'] <= 0)
        df['Z_FGL'] = df['Z_FGL_Target'].copy()
        df.loc[mask_norec, 'Z_FGL'] = df.loc[mask_norec, 'Z_Ext']
    else:
        df['Z_FGL'] = df['Z_FGL_Target']

    df['Z_Sub'] = df['Z_FGL'] - pavement_thick
    if not allow_reclam: df.loc[mask_norec, 'Z_Sub'] = df.loc[mask_norec, 'Z_Ext']
    
    df['Diff_Earth'] = df['Z_Sub'] - df['Z_Ext']
    df_p = df[df['In_Project']]

    # --- VENTILATION ---
    is_land = df_p['Z_Ext'] > 0
    is_sea = df_p['Z_Ext'] <= 0
    is_cut = df_p['Diff_Earth'] < 0
    is_fill = df_p['Diff_Earth'] > 0

    vol_cut_terre = abs(df_p[is_land & is_cut]['Diff_Earth'].sum()) * (actual_res**2)
    vol_cut_mer = abs(df_p[is_sea & is_cut]['Diff_Earth'].sum()) * (actual_res**2)
    
    vol_fill_terre = df_p[is_land & is_fill]['Diff_Earth'].sum() * (actual_res**2)
    df_fill_sea = df_p[is_sea & is_fill]
    vol_fill_sousmer = (np.minimum(df_fill_sea['Z_Sub'], 0) - df_fill_sea['Z_Ext']).sum() * (actual_res**2)
    vol_fill_surmer = np.maximum(df_fill_sea['Z_Sub'], 0).sum() * (actual_res**2)

    tot_cut = vol_cut_terre + vol_cut_mer
    tot_fill = vol_fill_terre + vol_fill_sousmer + vol_fill_surmer

    # =========================================================================
    # --- RESULTATS & ONGLETS ---
    # =========================================================================
    t_civ, t_hydro, t_topo = st.tabs(["🏗️ Volumes & Coupes", "🌊 Hydrologie & Météocéan", "🗺️ Plan d'Ensemble"])

    with t_civ:
        c_v1, c_v2, c_v3 = st.columns(3)
        c_v1.write("### Excavation (Dragage/Déblai)")
        c_v1.metric("Dragage Marin", f"{vol_cut_mer:,.0f} m³", "Sous l'eau", delta_color="off")
        c_v1.metric("Déblai Terrestre", f"{vol_cut_terre:,.0f} m³", "Sur terre", delta_color="off")
        
        c_v2.write("### Remblaiement (Réclamation)")
        c_v2.metric("Remblai Sous-Marin", f"{vol_fill_sousmer:,.0f} m³", "Fondation", delta_color="off")
        c_v2.metric("Réclamation (Sur l'eau)", f"{vol_fill_surmer:,.0f} m³", "Plateforme", delta_color="off")
        c_v2.metric("Remblai Terrestre", f"{vol_fill_terre:,.0f} m³", "Nivellement", delta_color="off")
        
        c_v3.write("### Bilan & Planning")
        c_v3.metric("Volume Total Manutentionné", f"{(tot_cut+tot_fill):,.0f} m³")
        daily_prod = prod_m3_h * hours_per_day * eff
        d_est = (tot_cut+tot_fill)/daily_prod if daily_prod>0 else 0
        c_v3.metric("Durée Estimée", f"{d_est:,.0f} Jours", f"Cible: {target_days}j", delta_color="inverse" if d_est>target_days else "normal")

        # --- COUPES DYNAMIQUES ---
        st.markdown("---")
        st.subheader("Coupes Topo-Bathymétriques (A-A' et B-B')")
        cc1, cc2, cc3 = st.columns(3)
        angle_c = cc1.slider("Angle de Coupe (°)", 0, 180, 0)
        th = math.radians(angle_c)
        df['Xc'] = df['X']*math.cos(th) + df['Y']*math.sin(th)
        df['Yc'] = -df['X']*math.sin(th) + df['Y']*math.cos(th)
        off_A = cc2.slider("Ligne A-A'", float(df['Yc'].min()), float(df['Yc'].max()), float(df['Yc'].mean()))
        off_B = cc3.slider("Ligne B-B'", float(df['Xc'].min()), float(df['Xc'].max()), float(df['Xc'].mean()))

        def plot_section(df_sec, title, axis):
            df_s = df_sec[abs(df_sec[axis] - (off_A if axis=='Yc' else off_B)) < actual_res].copy()
            if df_s.empty: return go.Figure()
            df_s['D'] = df_s['Xc' if axis=='Yc' else 'Yc'].round(0)
            df_s = df_s.groupby('D').mean().reset_index()
            
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=[df_s['D'].min(), df_s['D'].max()], y=[0,0], mode='lines', name='Niveau 0 (Mer)', line=dict(color='cyan', dash='dash')))
            
            fig.add_trace(go.Scatter(x=df_s['D'], y=df_s['Z_FGL'], line=dict(width=0), showlegend=False))
            fig.add_trace(go.Scatter(x=df_s['D'], y=np.maximum(df_s['Z_FGL'], df_s['Z_Ext']), fill='tonexty', fillcolor='rgba(255,0,0,0.4)', name='Dragage/Déblai', line=dict(width=0)))
            
            fig.add_trace(go.Scatter(x=df_s['D'], y=df_s['Z_FGL'], line=dict(width=0), showlegend=False))
            fig.add_trace(go.Scatter(x=df_s['D'], y=np.minimum(df_s['Z_FGL'], df_s['Z_Ext']), fill='tonexty', fillcolor='rgba(0,0,255,0.4)', name='Remblai/Réclam', line=dict(width=0)))

            fig.add_trace(go.Scatter(x=df_s['D'], y=df_s['Z_Ext'], name='Fond Naturel', line=dict(color='saddlebrown', width=2)))
            fig.add_trace(go.Scatter(x=df_s['D'], y=df_s['Z_FGL'], name='Projet Fini', line=dict(color='black', width=3)))
            
            fig.update_layout(title=title, height=350, margin=dict(l=10, r=10, t=30, b=10))
            return fig

        sc1, sc2 = st.columns(2)
        sc1.plotly_chart(plot_section(df, "Coupe Transversale A-A'", 'Yc'), use_container_width=True)
        sc2.plotly_chart(plot_section(df, "Coupe Longitudinale B-B'", 'Xc'), use_container_width=True)

    with t_hydro:
        st.subheader("Hydrologie Urbaine (Loi de Montana)")
        params = {"1": (2.0, 0.6), "10": (5.5, 0.6), "50": (9.0, 0.6)}
        freq = st.selectbox("Retour", ["1 an", "10 ans (Décennale)", "50 ans (Cinquantennale)"], index=1)
        k = "1" if "1 " in freq else "10" if "10" in freq else "50"
        
        ch1, ch2 = st.columns(2)
        a = ch1.number_input("Coeff 'a'", value=params[k][0], step=0.5)
        b = ch1.number_input("Coeff 'b'", value=params[k][1], step=0.05)
        t = ch1.number_input("Durée (h)", value=2.0)
        h_pluie = a * ((t*60)**(1-b))
        ch1.success(f"Hauteur de pluie: {h_pluie:.1f} mm")
        
        area_net = st.session_state['rect_data']['area'] if st.session_state['rect_data']['area'] > 0 else area_m2
        s_drain = ch2.number_input("Surface (m²)", value=float(area_net))
        cr = ch2.slider("Ruissellement (Cr)", 0.1, 1.0, 0.9)
        q_fuite = ch2.number_input("Fuite (L/s/ha)", value=10.0)
        
        v_in = s_drain * cr * h_pluie / 1000
        v_out = q_fuite * (s_drain/10000) / 1000 * (t*3600)
        ch2.error(f"**Bassin de Rétention Requis : {max(0, v_in - v_out):,.0f} m³**")

    with t_topo:
        st.subheader("Masterplan Topo & Contours")
        vt1, vt2 = st.columns(2)
        step_c = vt1.slider("Equidistance (m)", 0.5, 5.0, 1.0)
        opac = vt2.slider("Opacité Satellite", 0.0, 1.0, 0.6)
        
        m_plan = folium.Map(location=[c_lat, c_lon], zoom_start=16, tiles=None)
        folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', opacity=opac).add_to(m_plan)
        
        # Trace du projet
        if term_coords_ll: folium.Polygon(locations=term_coords_ll, color='magenta', weight=4, fill=True, fill_opacity=0.2, tooltip="Terre-Plein (IA)").add_to(m_plan)
        if digue_coords: folium.PolyLine(locations=[(lat, lon) for lon, lat in digue_coords], color='red', weight=8, tooltip="Digue").add_to(m_plan)
        if quai_coords: folium.PolyLine(locations=[(lat, lon) for lon, lat in quai_coords], color='black', weight=8, tooltip="Quai").add_to(m_plan)
        if bassin_coords: folium.Polygon(locations=[(lat, lon) for lon, lat in bassin_coords], color='blue', weight=2, fill=False, dash_array='5,5', tooltip="Bassin Dragué").add_to(m_plan)
        
        try:
            fig, ax = plt.subplots()
            triang = tri.Triangulation(df['Lon'], df['Lat'])
            levels = np.arange(math.floor(df['Z_Ext'].min()), math.ceil(df['Z_Ext'].max()) + step_c, step_c)
            if len(levels) > 1:
                contour = ax.tricontour(triang, df['Z_Ext'], levels=levels)
                cmp = cm.LinearColormap(['darkblue', 'blue', 'cyan', 'green', 'yellow', 'red'], vmin=df['Z_Ext'].min(), vmax=df['Z_Ext'].max())
                m_plan.add_child(cmp)
                if hasattr(contour, 'allsegs'):
                    for i, segs in enumerate(contour.allsegs):
                        if i < len(levels):
                            for seg in segs:
                                if len(seg)>=2: folium.PolyLine([[y,x] for x,y in seg], color=cmp(levels[i]), weight=2, opacity=0.8).add_to(m_plan)
                else:
                    for lvl, col in zip(levels, contour.collections):
                        for p in col.get_paths():
                            if len(p.vertices)>=2: folium.PolyLine([[y,x] for x,y in p.vertices], color=cmp(lvl), weight=2, opacity=0.8).add_to(m_plan)
            plt.close(fig)
        except: pass
        
        st_folium(m_plan, width=1200, height=600, key="final_topo")
