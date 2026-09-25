import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import urllib.parse
import google.generativeai as genai
import json
import time

st.set_page_config(page_title="Tracker de Stage PM", layout="centered")

st.markdown("<h1 style='text-align: center;'>Espace de Recherche PM/PO</h1>", unsafe_allow_html=True)

# --- INITIALISATION DE L'IA ---
gemini_configured = False
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    gemini_configured = True
except KeyError:
    st.error("La clé GEMINI_API_KEY n'est pas trouvée.")

# Liste ordonnée de modèles avec bascule automatique (Fallback) en cas d'erreur 429
MODELS_FALLBACK_LIST = [
    "gemini-3.5-flash",       # Modèle par défaut
    "gemini-3.5-flash-lite",  # 15 RPM / 500 RPD (Très grand quota)
    "gemini-3.1-flash-lite",  # 15 RPM / 500 RPD (Excellent secours)
    "gemini-3.7-flash",       # 5 RPM / 20 RPD
    "gemini-2.5-flash-lite"   # 10 RPM / 20 RPD
]

def call_gemini_with_fallback(prompt: str) -> str:
    """Tente d'appeler l'API Gemini avec bascule automatique sur un autre modèle en cas de quota dépassé (429)."""
    if not gemini_configured:
        raise Exception("Gemini n'est pas configuré.")
    
    last_error = None
    for model_name in MODELS_FALLBACK_LIST:
        try:
            m = genai.GenerativeModel(model_name)
            response = m.generate_content(prompt)
            if response and response.text:
                return response.text
        except Exception as e:
            error_str = str(e)
            last_error = e
            # Si quota dépassé (429) ou modèle saturé, on bascule vers le suivant
            if "429" in error_str or "quota" in error_str.lower():
                continue
            else:
                # Si c'est une autre erreur, on tente quand même le modèle suivant
                continue
                
    raise Exception(f"Tous les modèles ont échoué. Dernière erreur : {last_error}")

# ==========================================
# GESTION DES PRÉRÉGLAGES VIA "FEUILLE 2"
# ==========================================
conn_gsheets = st.connection("gsheets", type=GSheetsConnection)

def load_presets_from_sheet():
    default_presets = {
        "Product Manager - Paris": {"job": "product manager", "loc": "Paris"},
        "Product Owner - Paris": {"job": "product owner", "loc": "Paris"}
    }
    try:
        df_p = conn_gsheets.read(worksheet="Feuille 2", dtype=str).fillna("")
        if not df_p.empty and "Nom" in df_p.columns and "Poste" in df_p.columns and "Lieu" in df_p.columns:
            presets = {}
            for _, r in df_p.iterrows():
                nom = str(r["Nom"]).strip()
                if nom:
                    presets[nom] = {
                        "job": str(r["Poste"]).strip(),
                        "loc": str(r["Lieu"]).strip()
                    }
            if presets:
                return presets
    except Exception:
        pass
    return default_presets

def save_presets_to_sheet(presets_dict):
    rows = []
    for name, data in presets_dict.items():
        rows.append({"Nom": name, "Poste": data.get("job", ""), "Lieu": data.get("loc", "")})
    df_new = pd.DataFrame(rows)
    conn_gsheets.update(worksheet="Feuille 2", data=df_new)
    st.cache_data.clear()

if "presets" not in st.session_state:
    st.session_state.presets = load_presets_from_sheet()

tab1, tab2 = st.tabs(["1. Recherche & Ajout", "2. Mes Offres & Analyses"])

# ==========================================
# ONGLET 1 : HUB DE RECHERCHE & AJOUT
# ==========================================
with tab1:
    st.markdown("<h2 style='text-align: center;'>Chercher de nouvelles offres</h2>", unsafe_allow_html=True)
    
    col_select, col_btn_save, col_btn_del = st.columns([2, 1, 1])
    with col_select:
        preset_options = list(st.session_state.presets.keys())
        if not preset_options:
            st.session_state.presets["Défaut"] = {"job": "product manager", "loc": "Paris"}
            preset_options = ["Défaut"]
        selected_preset = st.selectbox("Recherche sauvegardée :", preset_options)
        current_data = st.session_state.presets.get(selected_preset, {"job": "product manager", "loc": "Paris"})

    col1, col2 = st.columns(2)
    with col1: job_title = st.text_input("Intitulé du poste", value=current_data["job"])
    with col2: location = st.text_input("Lieu", value=current_data["loc"])

    with col_btn_save:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Sauvegarder", use_container_width=True):
            new_name = f"{job_title.title()} - {location.title()}"
            st.session_state.presets[new_name] = {"job": job_title, "loc": location}
            save_presets_to_sheet(st.session_state.presets)
            st.success("Sauvegardé dans Feuille 2 !")
            time.sleep(1)
            st.rerun()

    with col_btn_del:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Supprimer", use_container_width=True):
            if len(st.session_state.presets) > 1:
                del st.session_state.presets[selected_preset]
                save_presets_to_sheet(st.session_state.presets)
                st.success("Supprimé !")
                time.sleep(1)
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
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    with btn_col1: st.link_button("LinkedIn", url_linkedin, use_container_width=True)
    with btn_col2: st.link_button("HelloWork", url_hellowork, use_container_width=True)
    with btn_col3: st.link_button("JobTeaser", url_jobteaser, use_container_width=True)

    st.markdown("---")
    st.markdown("<h2 style='text-align: center;'>Enregistrer une offre</h2>", unsafe_allow_html=True)
    
    offer_link = st.text_input("Lien de l'offre (optionnel) :")
    offer_text = st.text_area("Description de l'offre :", height=150)
    
    if st.button("Ajouter au Tracker", type="primary", use_container_width=True):
        if offer_text.strip() and gemini_configured:
            with st.spinner("Extraction via IA..."):
                prompt_json = f"""
                Extrais les infos sous forme JSON valide avec EXACTEMENT ces clés : 
                "Entreprise", "Poste", "Lieu", "Salaire". (Mets "Inconnu" si introuvable).
                Offre : {offer_text}
                """
                try:
                    response_text = call_gemini_with_fallback(prompt_json)
                    raw_json = response_text.replace('```json', '').replace('```', '').strip()
                    extracted_data = json.loads(raw_json)
                    
                    df_current = conn_gsheets.read(dtype=str).fillna("")
                    
                    new_row = {
                        "Entreprise": extracted_data.get("Entreprise", "Inconnu"),
                        "Poste": extracted_data.get("Poste", "Inconnu"),
                        "Lien": offer_link if offer_link else "",
                        "Lieu": extracted_data.get("Lieu", ""),
                        "Salaire": extracted_data.get("Salaire", ""),
                        "Date": pd.Timestamp.now().strftime("%d/%m/%Y"),
                        "Statut": "À postuler",
                        "Texte Offre": offer_text,
                        "Résumé IA": "",
                        "Entretien IA": "",
                        "Lettre de Motivation": ""
                    }
                    
                    new_df = pd.DataFrame([new_row])
                    updated_df = pd.concat([df_current, new_df], ignore_index=True).fillna("")
                    
                    conn_gsheets.update(data=updated_df)
                    st.cache_data.clear() 
                    st.success("Enregistrée !")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur : {e}")
        else:
            st.warning("Colle le texte de l'offre d'abord !")

# ==========================================
# ONGLET 2 : MES OFFRES & ANALYSES
# ==========================================
with tab2:
    st.markdown("<h2 style='text-align: center;'>Mes Offres Sauvegardées</h2>", unsafe_allow_html=True)
    
    try:
        df_tracker = conn_gsheets.read(dtype=str).fillna("")
        
        for col in ["Texte Offre", "Résumé IA", "Entretien IA", "Lettre de Motivation", "Entreprise", "Poste", "Statut"]:
            if col not in df_tracker.columns:
                df_tracker[col] = ""

        valid_indices = df_tracker[df_tracker['Entreprise'].str.strip() != ""].index
        
        if valid_indices.empty:
            st.info("Aucune offre sauvegardée pour le moment.")
        else:
            selected_idx = st.selectbox(
                "Sélectionne une offre :", 
                options=valid_indices,
                format_func=lambda x: f"{df_tracker.loc[x, 'Entreprise']} - {df_tracker.loc[x, 'Poste']}"
            )
            
            row_data = df_tracker.loc[selected_idx]
            
            # Titre centré de l'offre
            st.markdown(f"<h3 style='text-align: center;'>🏢 {row_data.get('Entreprise')} - {row_data.get('Poste')}</h3>", unsafe_allow_html=True)
            
            col_info, col_status = st.columns([2, 1])
            with col_info:
                st.write(f"📍 **Lieu:** {row_data.get('Lieu')} | 💰 **Salaire:** {row_data.get('Salaire')}")
                if row_data.get('Lien'):
                    st.markdown(f"[🔗 Voir l'annonce]({row_data.get('Lien')})")
            
            with col_status:
                # GESTION DU STATUT
                status_options = ["À postuler", "Envoyée", "Entretien", "Offre", "Refus"]
                current_status = row_data.get('Statut', 'À postuler')
                if current_status not in status_options and current_status.strip() != "":
                    status_options.insert(0, current_status)
                
                new_status = st.selectbox("🚥 Statut :", status_options, index=status_options.index(current_status) if current_status in status_options else 0, label_visibility="collapsed")
                
                if new_status != current_status:
                    df_tracker.at[selected_idx, 'Statut'] = new_status
                    conn_gsheets.update(data=df_tracker)
                    st.cache_data.clear()
                    st.rerun()

            st.button("🗑️ Supprimer l'offre", use_container_width=True, on_click=lambda: (conn_gsheets.update(data=df_tracker.drop(selected_idx)), st.cache_data.clear()))

            st.markdown("---")
            
            col_res, col_ent, col_lm = st.columns(3)
            
            with col_res:
                st.markdown("<h4 style='text-align: center;'>Résumé</h4>", unsafe_allow_html=True)
                current_resume = row_data.get('Résumé IA', '').strip()
                
                if current_resume:
                    st.info(current_resume)
                else:
                    if st.button("✨ Générer", key="btn_res", use_container_width=True):
                        with st.spinner("Analyse..."):
                            try:
                                prompt = f"Agis comme un Product Manager Senior. Résume les missions de cette offre en exactement 3 points à puces clairs et concis.\nOffre : {row_data.get('Texte Offre')}"
                                result = call_gemini_with_fallback(prompt)
                                df_tracker.at[selected_idx, 'Résumé IA'] = result
                                conn_gsheets.update(data=df_tracker)
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erreur Résumé : {e}")

            with col_ent:
                st.markdown("<h4 style='text-align: center;'>Entretien</h4>", unsafe_allow_html=True)
                current_entretien = row_data.get('Entretien IA', '').strip()
                
                if current_entretien:
                    st.success(current_entretien)
                else:
                    if st.button("🎯 Générer", key="btn_ent", use_container_width=True):
                        with st.spinner("Préparation..."):
                            try:
                                prompt = f"""
                                Agis comme le Lead Product. Divise ta réponse en 2 :
                                ### Fiche d'identité
                                (Création, PDG, Siège, Employés, Concurrents, CA)
                                ### Préparation (PM/PO)
                                1. 3 valeurs clés.
                                2. 3 questions techniques (Orientées Product).
                                3. Mini-étude de cas.
                                Offre : {row_data.get('Texte Offre')}
                                """
                                result = call_gemini_with_fallback(prompt)
                                df_tracker.at[selected_idx, 'Entretien IA'] = result
                                conn_gsheets.update(data=df_tracker)
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erreur Entretien : {e}")
                            
            with col_lm:
                st.markdown("<h4 style='text-align: center;'>Lettre</h4>", unsafe_allow_html=True)
                current_lm = row_data.get('Lettre de Motivation', '').strip()
                
                if current_lm:
                    with st.expander("Voir la lettre"):
                        st.markdown(current_lm)
                else:
                    if st.button("📝 Générer", key="btn_lm", use_container_width=True):
                        with st.spinner("Writing cover letter..."):
                            prompt_lm = f"""
Act as a Principal Product Manager and Hiring Director. Based on the job description below, generate 2 specific elements in English to complete the cover letter of an Engineering & Management dual-degree candidate:

1. "paragraphe_vous": 2 to 3 sentences in English. Analyze the company's product challenges (e.g., scaling, user adoption, tech debt, automation, or feature delivery). Explain why their mission and product challenges directly resonate with an engineer-product manager. Avoid generic flatteries; be precise on features, users, or business challenges mentioned in the ad.
2. "phrase_nous": 1 impactful closing sentence in English bridging the candidate's hands-on product building skills (discovery, writing specs, agile delivery, AI integration) with the role's primary need.

Return ONLY a valid JSON object with these two keys: "paragraphe_vous" and "phrase_nous".
Job description: {row_data.get('Texte Offre')}
"""
                            try:
                                resp_text = call_gemini_with_fallback(prompt_lm)
                                raw_json = resp_text.replace('```json', '').replace('```', '').strip()
                                dynamic_parts = json.loads(raw_json)
                                
                                lettre_finale = f"""**Subject:** Application for the {row_data.get('Poste')} Internship

Dear Hiring Team,

Currently pursuing an Engineering and Management dual degree at INSA Rennes and Audencia Business School, I am seeking a 6-month Product Management internship in Paris starting in January/February 2027. It is with great enthusiasm that I submit my application.

{dynamic_parts.get("paragraphe_vous", "")}

My hybrid background bridges product strategy with hands-on technical execution. My engineering curriculum at INSA Rennes and R&D internship at Ingeliance trained me to deconstruct complex technical constraints and translate client requirements into viable solutions. Simultaneously, my Master of Science at Audencia equipped me with solid frameworks in product discovery, agile delivery (Scrum, Kanban), and go-to-market execution.

Deeply hands-on, I don't just specify products—I build them:
• Supermarket Assistant App: Led the end-to-end product lifecycle of a mobile application (Flutter, Firestore) built to optimize in-store grocery shopping, validating problem-solution fit and iterating on UX.
• AI Internship Search Tracker: Engineered a full-stack web application (Python, Streamlit) integrating the Google Gemini API to automate job matching and analysis.

Furthermore, product success relies heavily on stakeholder alignment and leadership without authority. As President of the INSA Rennes Student Union, I steered campus operations for 1,300 members and managed a €1M operating budget across 40 student initiatives. {dynamic_parts.get("phrase_nous", "")}

I would welcome the opportunity to discuss your product vision and demonstrate how my dual technical and business skillset can contribute to your success.

Thank you for your time and consideration.

Sincerely,

**Pol CARTRON**
"""
                                df_tracker.at[selected_idx, 'Lettre de Motivation'] = lettre_finale
                                conn_gsheets.update(data=df_tracker)
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erreur LM : {e}")
                            
            with st.expander("Texte original"):
                st.write(row_data.get('Texte Offre', 'Aucun texte.'))

    except Exception as e:
        st.error(f"Erreur base de données : {e}")