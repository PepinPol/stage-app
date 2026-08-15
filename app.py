import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import urllib.parse
import google.generativeai as genai
import json
import time

# Configuration de la page
st.set_page_config(page_title="Tracker de Stage PM", page_icon="🎯", layout="wide")

st.title("🎯 Mon Espace de Recherche PM/PO")

# --- INITIALISATION DE L'IA ---
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    gemini_configured = True
    model = genai.GenerativeModel('gemini-3.5-flash') 
except KeyError:
    gemini_configured = False
    st.error("⚠️ La clé GEMINI_API_KEY n'est pas trouvée.")

# --- INITIALISATION DE LA MÉMOIRE ---
if "presets" not in st.session_state:
    st.session_state.presets = {
        "🎯 Product Manager - Paris": {"job": "product manager", "loc": "Paris"},
        "🚀 Product Owner - Paris": {"job": "product owner", "loc": "Paris"}
    }

if "last_analyzed_text" not in st.session_state:
    st.session_state.last_analyzed_text = ""
if "cache_resume" not in st.session_state:
    st.session_state.cache_resume = None
if "cache_entretien" not in st.session_state:
    st.session_state.cache_entretien = None

# Création des onglets
tab1, tab2, tab3 = st.tabs(["📊 Tracker", "🎯 Hub de Recherche", "🤖 Copilote IA"])

# ----------------------------------------
# ONGLET 1 : LE TRACKER
# ----------------------------------------
with tab1:
    st.markdown("Modifie tes candidatures directement dans le tableau !")
    conn = st.connection("gsheets", type=GSheetsConnection)
    try:
        df = conn.read(dtype=str).fillna("")
        edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True, height=500)
        
        if st.button("💾 Sauvegarder les modifications"):
            with st.spinner('Mise à jour du Google Sheet en cours...'):
                conn.update(data=edited_df)
                st.cache_data.clear() 
                st.success("✅ Google Sheet mis à jour avec succès !")
    except Exception as e:
        st.error(f"Erreur de connexion : {e}")

# ----------------------------------------
# ONGLET 2 : HUB DE RECHERCHE AVANCÉ
# ----------------------------------------
with tab2:
    st.subheader("1. Gérer les recherches sauvegardées")
    col_select, col_btn_save, col_btn_del = st.columns([2, 1, 1])
    
    with col_select:
        preset_options = list(st.session_state.presets.keys())
        if not preset_options:
            st.session_state.presets["Défaut"] = {"job": "product manager", "loc": "Paris"}
            preset_options = ["Défaut"]
        selected_preset = st.selectbox("Recherche sauvegardée :", preset_options)
        current_data = st.session_state.presets[selected_preset]

    st.subheader("2. Critères de recherche")
    col1, col2 = st.columns(2)
    with col1: job_title = st.text_input("Intitulé du poste", value=current_data["job"])
    with col2: location = st.text_input("Lieu", value=current_data["loc"])

    with col_btn_save:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕ Sauvegarder"):
            new_name = f"⭐ {job_title.title()} - {location.title()}"
            st.session_state.presets[new_name] = {"job": job_title, "loc": location}
            st.success(f"Sauvegardée !")
            st.rerun()

    with col_btn_del:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🗑️ Supprimer"):
            if len(st.session_state.presets) > 1:
                del st.session_state.presets[selected_preset]
                st.success("Supprimée !")
                st.rerun()

    job_encoded = urllib.parse.quote(job_title)
    loc_encoded = urllib.parse.quote(location)
    is_paris = location.strip().lower() == "paris"
    
    url_linkedin = f"https://www.linkedin.com/jobs/search/?keywords={job_encoded}&location={loc_encoded}&f_JT=I&sortBy=DD"
    if is_paris: url_linkedin += "&distance=10"
    
    url_hellowork = f"https://www.hellowork.com/fr-fr/emploi/recherche.html?k={job_encoded}&l={loc_encoded}&c=Stage&st=date"
    if is_paris: url_hellowork += "&ray=20"

    url_jobteaser = f"https://audencia.jobteaser.com/fr/job-offers?contract=internship&q={job_encoded}&contract_duration=6&start_date=2027_01&start_date=2027_02&study_levels=4&work_experience_code=young_graduate&sort=recency"
    if is_paris:
        url_jobteaser += "&radius=20&lat=48.853495&lng=2.348391&location=France%3A%3A%C3%8Ele-de-France%3A%3AParis%3A%3AParis%3A%3AbG9jYWxpdHk6ZnI6Y2l0eTpmemVIZnJnZDJQekhETTNCZXE0NlUyL3pFMG89"
    else:
        url_jobteaser += f"&location={loc_encoded}"

    st.markdown("---")
    st.subheader("3. Lancer les recherches")
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    with btn_col1: st.link_button("💼 LinkedIn", url_linkedin, use_container_width=True)
    with btn_col2: st.link_button("👋 HelloWork", url_hellowork, use_container_width=True)
    with btn_col3: st.link_button("🎓 JobTeaser", url_jobteaser, use_container_width=True)

# ----------------------------------------
# ONGLET 3 : LE COPILOTE IA (SPRINT 4 - AUGMENTÉ)
# ----------------------------------------
with tab3:
    st.header("L'Analyseur d'Offre 🤖")
    
    if gemini_configured:
        # --- CHOIX DU MODE ---
        mode = st.radio("Que veux-tu faire ?", ["📝 Analyser une nouvelle offre", "📂 Consulter une offre sauvegardée"], horizontal=True)
        st.markdown("---")
        
        # ==========================================
        # MODE 1 : CONSULTER UNE OFFRE (Depuis Google Sheets)
        # ==========================================
        if mode == "📂 Consulter une offre sauvegardée":
            conn_tab3 = st.connection("gsheets", type=GSheetsConnection)
            try:
                df_tracker = conn_tab3.read(dtype=str).fillna("")
                
                # Filtrer pour ne garder que les offres qui ont une entreprise et un poste
                saved_offers = []
                for index, row in df_tracker.iterrows():
                    if row.get("Entreprise") and row.get("Poste"):
                        saved_offers.append(f"{row['Entreprise']} - {row['Poste']}")
                
                if not saved_offers:
                    st.info("Aucune offre sauvegardée dans le tracker pour le moment.")
                else:
                    selected_offer = st.selectbox("Sélectionne une offre :", saved_offers)
                    
                    # Retrouver la ligne correspondante
                    row_data = df_tracker[df_tracker['Entreprise'] + " - " + df_tracker['Poste'] == selected_offer].iloc[0]
                    
                    # Affichage des données de l'offre
                    st.subheader(f"🏢 {row_data.get('Entreprise', '')} - {row_data.get('Poste', '')}")
                    st.write(f"📍 **Lieu:** {row_data.get('Lieu', 'Non précisé')} | 💰 **Salaire:** {row_data.get('Salaire', 'Non précisé')} | 🚥 **Statut:** {row_data.get('Statut', 'Non précisé')}")
                    if row_data.get('Lien'):
                        st.markdown(f"[🔗 Lien vers l'offre]({row_data.get('Lien')})")
                    
                    col_show_res, col_show_ent = st.columns(2)
                    with col_show_res:
                        st.markdown("### ✨ Résumé des missions")
                        st.info(row_data.get('Résumé IA', 'Pas de résumé sauvegardé.') if row_data.get('Résumé IA') else 'Pas de résumé sauvegardé.')
                    with col_show_ent:
                        st.markdown("### 🎯 Préparation Entretien")
                        st.success(row_data.get('Entretien IA', 'Pas d\'entretien sauvegardé.') if row_data.get('Entretien IA') else 'Pas d\'entretien sauvegardé.')
                        
                    with st.expander("Voir le texte original de l'offre"):
                        st.write(row_data.get('Texte Offre', 'Texte non sauvegardé.'))
            except Exception as e:
                st.error("Impossible de lire les offres sauvegardées. Vérifie le format de ton tableau.")

        # ==========================================
        # MODE 2 : ANALYSER UNE NOUVELLE OFFRE
        # ==========================================
        else:
            offer_link = st.text_input("🔗 Lien de l'offre (optionnel) :", key="offer_link")
            offer_text = st.text_area("📋 Colle la description de l'offre ici :", height=250, key="offer_text")
            
            # Réinitialisation du cache si le texte change
            if offer_text != st.session_state.last_analyzed_text:
                st.session_state.cache_resume = None
                st.session_state.cache_entretien = None
                st.session_state.last_analyzed_text = offer_text

            col_res, col_ent, col_add = st.columns(3)
            with col_res: btn_resume = st.button("✨ Résumer les missions", use_container_width=True)
            with col_ent: btn_entretien = st.button("🎯 Préparer mon entretien", use_container_width=True)
            with col_add: btn_tracker = st.button("➕ Sauvegarder dans le Tracker", type="primary", use_container_width=True)

            if offer_text.strip():
                # --- ACTION RÉSUMÉ ---
                if btn_resume:
                    if st.session_state.cache_resume:
                        st.info(st.session_state.cache_resume)
                    else:
                        with st.spinner("Analyse de l'offre en cours..."):
                            prompt = f"Agis comme un Product Manager Senior. Résume les missions de cette offre en exactement 3 points à puces clairs et concis.\nOffre : {offer_text}"
                            response = model.generate_content(prompt)
                            st.session_state.cache_resume = response.text
                            st.info(response.text)
                
                # --- ACTION ENTRETIEN ---
                elif btn_entretien:
                    if st.session_state.cache_entretien:
                        st.success("Entretien récupéré !")
                        st.markdown(st.session_state.cache_entretien)
                    else:
                        with st.spinner("Recherche des infos de la boîte et préparation..."):
                            prompt = f"""
                            Agis comme le Lead Product ou Recruteur de cette entreprise. À partir de cette offre de stage et de tes connaissances générales sur l'entreprise citée, divise ta réponse en 2 grandes parties avec des titres en gras :
                            
                            ### 🏢 PARTIE 1 : Fiche d'identité de l'entreprise
                            Retrouve et liste ces informations clés de la boîte (si tu ne sais vraiment pas, mets "Non public") : 
                            - Date de création :
                            - PDG / Fondateurs :
                            - Siège social :
                            - Nombre d'employés :
                            - Principaux concurrents :
                            - Chiffre d'affaires ou Levées de fonds :
                            
                            ### 🎯 PARTIE 2 : Préparation de l'entretien (PM/PO)
                            1. Déduis 3 valeurs clés ou traits de culture.
                            2. Rédige 3 questions techniques pointues (Orientées Product) que tu pourrais me poser.
                            3. Propose-moi une idée de mini-étude de cas que je devrais préparer avant l'entretien.
                            
                            Offre : {offer_text}
                            """
                            response = model.generate_content(prompt)
                            st.session_state.cache_entretien = response.text
                            st.success("Entretien généré !")
                            st.markdown(response.text)
                        
                # --- ACTION AJOUT AU TRACKER ---
                elif btn_tracker:
                    with st.spinner("Extraction et génération de toutes les infos (ça peut prendre 10 secondes)..."):
                        
                        # 1. On s'assure que le résumé est généré
                        if not st.session_state.cache_resume:
                            st.toast("Génération du résumé...")
                            resp_res = model.generate_content(f"Agis comme un Product Manager Senior. Résume les missions de cette offre en exactement 3 points à puces clairs et concis.\nOffre : {offer_text}")
                            st.session_state.cache_resume = resp_res.text
                            
                        # 2. On s'assure que l'entretien est généré
                        if not st.session_state.cache_entretien:
                            st.toast("Génération de l'entretien...")
                            resp_ent = model.generate_content(f"Agis comme le Lead Product. Donne les infos de l'entreprise (Création, PDG, Siège, Employés, Concurrents, CA), puis donne 3 valeurs, 3 questions d'entretien product et un cas pratique.\nOffre : {offer_text}")
                            st.session_state.cache_entretien = resp_ent.text

                        # 3. Extraction JSON de la data brute
                        st.toast("Extraction des données structurées...")
                        prompt_json = f"""
                        Extrais les infos de cette offre sous forme de code JSON valide avec EXACTEMENT ces clés : 
                        "Entreprise", "Poste", "Lieu", "Salaire". (Mets "Inconnu" si introuvable).
                        Ne renvoie QUE le dictionnaire JSON pur.
                        Offre : {offer_text}
                        """
                        try:
                            response = model.generate_content(prompt_json)
                            raw_json = response.text.replace('```json', '').replace('```', '').strip()
                            extracted_data = json.loads(raw_json)
                            
                            # Connexion et mise à jour
                            conn_add = st.connection("gsheets", type=GSheetsConnection)
                            df_current = conn_add.read(dtype=str)
                            
                            new_row = {
                                "Entreprise": extracted_data.get("Entreprise", ""),
                                "Poste": extracted_data.get("Poste", ""),
                                "Lien": offer_link if offer_link else "Non renseigné",
                                "Lieu": extracted_data.get("Lieu", ""),
                                "Salaire": extracted_data.get("Salaire", ""),
                                "Date": "",
                                "Statut": "À postuler",
                                "Date envoie": "",
                                "Date MAJ": "",
                                "Notes": "Auto-généré via IA",
                                "Texte Offre": offer_text,
                                "Résumé IA": st.session_state.cache_resume,
                                "Entretien IA": st.session_state.cache_entretien
                            }
                            
                            new_df = pd.DataFrame([new_row])
                            updated_df = pd.concat([df_current, new_df], ignore_index=True).fillna("")
                            
                            conn_add.update(data=updated_df)
                            st.cache_data.clear() 
                            st.success("✅ Offre, résumé et entretien sauvegardés à vie !")
                            st.balloons()
                            time.sleep(1.5)
                            st.rerun()
                            
                        except json.JSONDecodeError:
                            st.error("L'IA a échoué sur le format JSON. Réessaie !")
                        except Exception as e:
                            st.error(f"Erreur d'ajout : {e}")

            elif btn_resume or btn_entretien or btn_tracker:
                st.warning("⚠️ Colle le texte de l'offre d'abord !")