import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import urllib.parse
import google.generativeai as genai
import json
import time
import re
from datetime import datetime
from fpdf import FPDF

st.set_page_config(page_title="Tracker de Stage PM", layout="centered")

st.markdown("<h1 style='text-align: center;'>Espace de Recherche PM/PO</h1>", unsafe_allow_html=True)

# --- INITIALISATION DE L'IA ---
gemini_configured = False
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    gemini_configured = True
except KeyError:
    st.error("La clé GEMINI_API_KEY n'est pas trouvée.")

MODELS_FALLBACK_LIST = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.7-flash",
    "gemini-2.5-flash-lite"
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
            if "429" in error_str or "quota" in error_str.lower():
                continue
            else:
                continue
                
    raise Exception(f"Tous les modèles ont échoué. Dernière erreur : {last_error}")

# --- UTILITAIRES HISTORIQUE ---
def parse_history(raw_val: str) -> list:
    if not raw_val or not str(raw_val).strip():
        return []
    try:
        data = json.loads(str(raw_val))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def infer_main_status(history_list: list, current_status: str) -> str:
    if not history_list:
        return current_status if current_status else "À postuler"
    last_step = history_list[-1].get("etape", "").lower()
    if "refus" in last_step:
        return "Refus"
    elif "offre" in last_step:
        return "Offre"
    elif "entretien" in last_step or "étude de cas" in last_step or "case" in last_step:
        return "Entretien"
    elif "envoy" in last_step or "postul" in last_step:
        return "Envoyée"
    elif "ajout" in last_step:
        return "À postuler"
    return current_status

# --- GÉNÉRATEUR PDF LETTRE DE MOTIVATION (FORMAT A4 GOOGLE DOCS) ---
def sanitize_pdf_text(text: str) -> str:
    """Remplace les caractères Unicode spéciaux pour compatibilité Helvetica standard."""
    replacements = {
        "€": "EUR ",
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "…": "...",
        "\u00a0": " "
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.encode("latin-1", "replace").decode("latin-1")

def build_cover_letter_pdf(company: str, location: str, body_text: str) -> bytes:
    """Génère un PDF A4 standard (210x297 mm) avec marges Google Docs (25 mm) sur 1 page."""
    pdf = FPDF(orientation="P", format="A4", unit="mm")
    pdf.set_margins(left=25, top=22, right=25)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # 1. En-tête Candidat (aligné à gauche)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 6, sanitize_pdf_text("Pol CARTRON"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 4.8, sanitize_pdf_text("Dual degree Engineering/Management student"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 4.8, sanitize_pdf_text("INSA Rennes / Audencia"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 4.8, sanitize_pdf_text("pol.cartron@gmail.com | (+33) 7 83 86 71 64"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # 2. Bloc Destinataire & Date (aligné tout à droite)
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 10.5)
    clean_company = sanitize_pdf_text(company.strip() if company and company.strip().lower() != "inconnu" else "")
    if clean_company:
        pdf.cell(0, 5, clean_company, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    clean_loc = sanitize_pdf_text(location.strip() if location and location.strip().lower() != "inconnu" else "Paris, France")
    if "france" not in clean_loc.lower() and "paris" in clean_loc.lower():
        clean_loc = f"{clean_loc}, France"
    pdf.cell(0, 5, clean_loc, align="R", new_x="LMARGIN", new_y="NEXT")

    today_str = datetime.now().strftime("%d/%m/%Y")
    pdf.cell(0, 5, today_str, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # 3. Corps de la lettre
    clean_body = body_text.replace("**", "").strip()
    paragraphs = clean_body.split("\n\n")

    for p in paragraphs:
        p_clean = p.strip()
        if not p_clean:
            continue
        if p_clean.startswith("Subject:"):
            pdf.set_font("Helvetica", "B", 10.5)
            pdf.multi_cell(0, 5, sanitize_pdf_text(p_clean))
            pdf.ln(3)
        elif p_clean in ["Pol CARTRON", "Sincerely,"]:
            pdf.set_font("Helvetica", "B" if p_clean == "Pol CARTRON" else "", 10.5)
            pdf.multi_cell(0, 5, sanitize_pdf_text(p_clean))
            if p_clean == "Sincerely,":
                pdf.ln(2.5)
        else:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 4.8, sanitize_pdf_text(p_clean))
            pdf.ln(3)

    return bytes(pdf.output())

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

if "selected_offer_idx" not in st.session_state:
    st.session_state.selected_offer_idx = None

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

    # --- ENCODAGE & GÉNÉRATION DE LIENS ---
    job_encoded = urllib.parse.quote(job_title)
    loc_encoded = urllib.parse.quote(location)
    is_paris = location.strip().lower() == "paris"
    
    # 1. LinkedIn (Stage + Dernier mois f_TPR=r2592000)
    if is_paris:
        url_linkedin = f"https://www.linkedin.com/jobs/search-results/?keywords={job_encoded}&origin=JOB_SEARCH_PAGE_JOB_FILTER&geoId=106383538&distance=15&f_TPR=r2592000&f_SAL=f_SA_id_226001%3A275001"
    else:
        url_linkedin = f"https://www.linkedin.com/jobs/search-results/?keywords={job_encoded}&location={loc_encoded}&origin=JOB_SEARCH_PAGE_JOB_FILTER&f_TPR=r2592000&f_SAL=f_SA_id_226001%3A275001"
    
    # 2. HelloWork
    url_hellowork = f"https://www.hellowork.com/fr-fr/emploi/recherche.html?k={job_encoded}&l={loc_encoded}&c=Stage&st=date"
    if is_paris: url_hellowork += "&ray=20"

    # 3. JobTeaser
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
                    today_str = pd.Timestamp.now().strftime("%d/%m/%Y")
                    
                    initial_history = json.dumps(
                        [{"date": today_str, "etape": "Ajoutée au tracker"}],
                        ensure_ascii=False
                    )
                    
                    new_row = {
                        "Entreprise": extracted_data.get("Entreprise", "Inconnu"),
                        "Poste": extracted_data.get("Poste", "Inconnu"),
                        "Lien": offer_link if offer_link else "",
                        "Lieu": extracted_data.get("Lieu", ""),
                        "Salaire": extracted_data.get("Salaire", ""),
                        "Date": today_str,
                        "Statut": "À postuler",
                        "Historique": initial_history,
                        "Texte Offre": offer_text,
                        "Résumé IA": "",
                        "Entretien IA": "",
                        "Lettre de Motivation": ""
                    }
                    
                    new_df = pd.DataFrame([new_row])
                    updated_df = pd.concat([df_current, new_df], ignore_index=True).fillna("")
                    
                    conn_gsheets.update(data=updated_df)
                    st.cache_data.clear() 
                    st.success("Enregistrée avec date du jour !")
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
    try:
        df_tracker = conn_gsheets.read(dtype=str).fillna("")
        
        for col in ["Texte Offre", "Résumé IA", "Entretien IA", "Lettre de Motivation", "Entreprise", "Poste", "Statut", "Lieu", "Salaire", "Lien", "Historique"]:
            if col not in df_tracker.columns:
                df_tracker[col] = ""

        valid_indices = df_tracker[df_tracker['Entreprise'].str.strip() != ""].index

        if st.session_state.selected_offer_idx not in valid_indices:
            st.session_state.selected_offer_idx = None

        # --- VUE 1 : VUE GLOBALE / LISTE DES OFFRES ---
        if st.session_state.selected_offer_idx is None:
            st.markdown("<h2 style='text-align: center;'>Mes Offres Sauvegardées</h2>", unsafe_allow_html=True)
            
            if valid_indices.empty:
                st.info("Aucune offre sauvegardée pour le moment.")
            else:
                st.caption(f"{len(valid_indices)} offre(s) enregistrée(s)")
                
                status_colors = {
                    "À postuler": "#3b82f6",
                    "Envoyée": "#eab308",
                    "Entretien": "#8b5cf6",
                    "Offre": "#22c55e",
                    "Refus": "#ef4444"
                }

                for idx in valid_indices:
                    row = df_tracker.loc[idx]
                    status = row.get("Statut", "À postuler")
                    badge_color = status_colors.get(status, "#64748b")
                    offer_url = row.get("Lien", "").strip()
                    
                    with st.container(border=True):
                        col_card_txt, col_card_btn = st.columns([3, 1.4], vertical_alignment="center")
                        with col_card_txt:
                            st.markdown(f"**🏢 {row.get('Entreprise')}** — {row.get('Poste')}")
                            details = []
                            if row.get("Lieu"): details.append(f"📍 {row.get('Lieu')}")
                            if row.get("Salaire"): details.append(f"💰 {row.get('Salaire')}")
                            meta_str = " | ".join(details)
                            
                            badge_html = f"<span style='background-color:{badge_color}; color:white; padding:2px 8px; border-radius:12px; font-size:12px; font-weight:bold;'>{status}</span>"
                            if meta_str:
                                st.markdown(f"<small style='color: gray;'>{meta_str}</small> &nbsp; {badge_html}", unsafe_allow_html=True)
                            else:
                                st.markdown(badge_html, unsafe_allow_html=True)
                                
                        with col_card_btn:
                            if offer_url:
                                sub_col1, sub_col2 = st.columns([3, 1], vertical_alignment="center")
                                with sub_col1:
                                    if st.button("🔍 Analyser", key=f"btn_open_{idx}", use_container_width=True):
                                        st.session_state.selected_offer_idx = idx
                                        st.rerun()
                                with sub_col2:
                                    st.link_button("🔗", offer_url, use_container_width=True, help="Ouvrir le lien de l'offre")
                            else:
                                if st.button("🔍 Analyser", key=f"btn_open_{idx}", use_container_width=True):
                                    st.session_state.selected_offer_idx = idx
                                    st.rerun()

        # --- VUE 2 : DÉTAIL ET ANALYSE D'UNE OFFRE ---
        else:
            selected_idx = st.session_state.selected_offer_idx
            row_data = df_tracker.loc[selected_idx]

            col_back, _ = st.columns([1.5, 3])
            with col_back:
                if st.button("⬅️ Retour aux offres", use_container_width=True):
                    st.session_state.selected_offer_idx = None
                    st.rerun()

            st.markdown(f"<h3 style='text-align: center;'>🏢 {row_data.get('Entreprise')} - {row_data.get('Poste')}</h3>", unsafe_allow_html=True)
            
            col_info, col_status = st.columns([2, 1])
            with col_info:
                st.write(f"📍 **Lieu:** {row_data.get('Lieu')} | 💰 **Salaire:** {row_data.get('Salaire')}")
                if row_data.get('Lien'):
                    st.markdown(f"[🔗 Voir l'annonce]({row_data.get('Lien')})")
            
            with col_status:
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

            # --- BLOC HISTORIQUE CHRONOLOGIQUE ---
            with st.container(border=True):
                st.markdown("#### 📅 Historique des étapes")
                history_list = parse_history(row_data.get("Historique", ""))

                if not history_list:
                    st.caption("Aucun historique enregistré pour cette offre.")
                else:
                    for h_idx, item in enumerate(history_list):
                        h_col1, h_col2, h_col3 = st.columns([1.2, 3, 0.5], vertical_alignment="center")
                        with h_col1:
                            st.markdown(f"**`{item.get('date', '')}`**")
                        with h_col2:
                            st.write(item.get("etape", ""))
                        with h_col3:
                            if st.button("🗑️", key=f"del_hist_{selected_idx}_{h_idx}", help="Supprimer cette étape"):
                                history_list.pop(h_idx)
                                df_tracker.at[selected_idx, 'Historique'] = json.dumps(history_list, ensure_ascii=False)
                                df_tracker.at[selected_idx, 'Statut'] = infer_main_status(history_list, row_data.get('Statut', 'À postuler'))
                                conn_gsheets.update(data=df_tracker)
                                st.cache_data.clear()
                                st.rerun()

                st.markdown("---")
                preset_steps = [
                    "Candidature envoyée",
                    "Entretien RH 1",
                    "Entretien Manager 2",
                    "Étude de cas",
                    "Entretien Final 3",
                    "Offre reçue",
                    "Refus",
                    "Autre (personnalisé)"
                ]
                add_col1, add_col2, add_col3 = st.columns([2.2, 1.3, 1], vertical_alignment="bottom")
                with add_col1:
                    chosen_step = st.selectbox("Nouvelle étape :", preset_steps, key=f"step_sel_{selected_idx}")
                with add_col2:
                    step_date = st.date_input("Date :", value=datetime.now(), format="DD/MM/YYYY", key=f"step_date_{selected_idx}")
                with add_col3:
                    add_clicked = st.button("➕ Ajouter", key=f"add_hist_{selected_idx}", use_container_width=True)

                custom_step_text = ""
                if chosen_step == "Autre (personnalisé)":
                    custom_step_text = st.text_input("Intitulé personnalisé (ex: Entretien CPO 3) :", key=f"custom_step_{selected_idx}")

                if add_clicked:
                    final_step_label = custom_step_text.strip() if chosen_step == "Autre (personnalisé)" else chosen_step
                    if final_step_label:
                        new_entry = {
                            "date": step_date.strftime("%d/%m/%Y"),
                            "etape": final_step_label
                        }
                        history_list.append(new_entry)
                        df_tracker.at[selected_idx, 'Historique'] = json.dumps(history_list, ensure_ascii=False)
                        df_tracker.at[selected_idx, 'Statut'] = infer_main_status(history_list, row_data.get('Statut', 'À postuler'))
                        conn_gsheets.update(data=df_tracker)
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning("Indique un intitulé pour l'étape personnalisée.")

            if st.button("🗑️ Supprimer l'offre", use_container_width=True):
                conn_gsheets.update(data=df_tracker.drop(selected_idx))
                st.cache_data.clear()
                st.session_state.selected_offer_idx = None
                st.rerun()

            st.markdown("---")

            # ========================================================
            # 1. LETTRE DE MOTIVATION & EXPORT PDF (PLEINE LARGEUR)
            # ========================================================
            def generer_lettre():
                with st.spinner("Writing cover letter..."):
                    prompt_lm = f"""
                    Act as a Product Hiring Expert. Based on the job description below, write two tailored elements in English to complete the cover letter of an Engineering & Management dual-degree candidate:
                    
                    1. "paragraphe_vous": 2 to 3 sentences maximum in English explaining why the company's mission, product, or technological challenges are particularly appealing. Be specific and include keywords from the job description.
                    2. "phrase_nous": 1 impactful sentence in English bridging the candidate's cross-functional project management and delivery skills with the primary need described in the role.
                    
                    Return the result ONLY as a valid JSON object with exactly these two keys ("paragraphe_vous" and "phrase_nous"). Do not include any extra text before or after.
                    
                    Job description: {row_data.get('Texte Offre')}
                    """
                    try:
                        resp_text = call_gemini_with_fallback(prompt_lm)
                        raw_json = resp_text.replace('```json', '').replace('```', '').strip()
                        dynamic_parts = json.loads(raw_json)
                        
                        lettre_finale = f"""Subject: Application for the {row_data.get('Poste')} Internship

Dear Hiring Team,

Currently pursuing an Engineering and Management dual degree at INSA Rennes and Audencia, I am seeking a 6-month {row_data.get('Poste')} internship in Paris starting in January/February 2027. It is with great enthusiasm that I submit my application.

{dynamic_parts.get("paragraphe_vous", "")}

My hybrid background allows me to naturally bridge the gap between business strategy and technical execution. My engineering curriculum at INSA Rennes provided me with strong analytical rigor, while my Master's program at Audencia honed my skills in product strategy, agile methodologies (Scrum, Kanban), and Go-to-Market execution. Hands-on by nature, I actively design and build technical products. Specifically, I led the end-to-end lifecycle of a mobile application built with Flutter, and engineered an AI-powered web application in Python integrating the Google Gemini API.

Furthermore, product success relies heavily on stakeholder alignment and leadership without authority. As President of the INSA Rennes Student Union, I steered campus operations for 1,300 members and managed a €1M operating budget across 40 student initiatives. {dynamic_parts.get("phrase_nous", "")}

I would welcome the opportunity to discuss your product vision and demonstrate how my dual technical and business skillset can contribute to your success.

Thank you for your time and consideration.

Sincerely,

Pol CARTRON"""
                        df_tracker.at[selected_idx, 'Lettre de Motivation'] = lettre_finale
                        conn_gsheets.update(data=df_tracker)
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur LM : {e}")

            current_lm = row_data.get('Lettre de Motivation', '').strip()
            with st.expander("📝 Lettre de Motivation & Export PDF", expanded=bool(current_lm)):
                if current_lm:
                    col_lm_info, col_lm_regen = st.columns([4, 1], vertical_alignment="center")
                    with col_lm_info:
                        st.caption("Modifie le texte ci-dessous si besoin puis télécharge directement ton PDF A4.")
                    with col_lm_regen:
                        if st.button("🔄 Regénérer", key="btn_regen_lm", use_container_width=True, help="Générer une nouvelle version via IA"):
                            generer_lettre()

                    edited_lm = st.text_area(
                        "Contenu de la lettre :",
                        value=current_lm,
                        height=300,
                        key=f"lm_editor_{selected_idx}",
                        label_visibility="collapsed"
                    )

                    pdf_bytes = build_cover_letter_pdf(
                        company=row_data.get("Entreprise", ""),
                        location=row_data.get("Lieu", "Paris, France"),
                        body_text=edited_lm
                    )
                    safe_company = re.sub(r'[^a-zA-Z0-9_-]', '_', row_data.get('Entreprise', 'Entreprise').strip()).strip('_')
                    pdf_filename = f"LM_Pol_Cartron_{safe_company}.pdf"

                    btn_save_lm, btn_dl_pdf = st.columns(2)
                    with btn_save_lm:
                        if st.button("💾 Sauvegarder les modifs", key=f"save_lm_{selected_idx}", use_container_width=True):
                            df_tracker.at[selected_idx, 'Lettre de Motivation'] = edited_lm
                            conn_gsheets.update(data=df_tracker)
                            st.cache_data.clear()
                            st.success("Modifications sauvegardées !")
                            time.sleep(0.8)
                            st.rerun()
                    with btn_dl_pdf:
                        st.download_button(
                            label="📥 Télécharger en PDF",
                            data=pdf_bytes,
                            file_name=pdf_filename,
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True
                        )
                else:
                    if st.button("📝 Générer la lettre & le PDF", key="btn_lm", use_container_width=True):
                        generer_lettre()

            # ========================================================
            # 2. RÉSUMÉ DE L'OFFRE (PLEINE LARGEUR, REPLIABLE)
            # ========================================================
            current_resume = row_data.get('Résumé IA', '').strip()
            with st.expander("✨ Résumé de l'offre", expanded=False):
                if current_resume:
                    st.info(current_resume)
                else:
                    if st.button("✨ Générer le résumé", key="btn_res", use_container_width=True):
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

            # ========================================================
            # 3. PRÉPARATION ENTRETIEN (CONTRÔLE PAR ÉTAT + BOUTON REFERMER)
            # ========================================================
            current_entretien = row_data.get('Entretien IA', '').strip()
            ent_open_key = f"ent_is_open_{selected_idx}"
            if ent_open_key not in st.session_state:
                st.session_state[ent_open_key] = False

            with st.container(border=True):
                if current_entretien:
                    if not st.session_state[ent_open_key]:
                        if st.button("🎯 Afficher la préparation entretien", key=f"open_ent_{selected_idx}", use_container_width=True):
                            st.session_state[ent_open_key] = True
                            st.rerun()
                    else:
                        st.success(current_entretien)
                        _, col_close_ent = st.columns([4, 1])
                        with col_close_ent:
                            if st.button("⬆️ Refermer", key=f"close_ent_{selected_idx}", use_container_width=True):
                                st.session_state[ent_open_key] = False
                                st.rerun()
                else:
                    if st.button("🎯 Générer la préparation entretien", key="btn_ent", use_container_width=True):
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
                                st.session_state[ent_open_key] = True
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erreur Entretien : {e}")

            # ========================================================
            # 4. TEXTE ORIGINAL DE L'OFFRE
            # ========================================================
            with st.expander("📄 Texte original de l'offre", expanded=False):
                st.write(row_data.get('Texte Offre', 'Aucun texte.'))

    except Exception as e:
        st.error(f"Erreur base de données : {e}")