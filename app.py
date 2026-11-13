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

# ==========================================
# CONFIGURATION & CONSTANTES
# ==========================================
st.set_page_config(page_title="Tracker de Stage PM", layout="centered")

MODELS_FALLBACK_LIST = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.7-flash",
    "gemini-2.5-flash-lite"
]

STATUS_COLORS = {
    "À postuler": "#3b82f6",
    "Envoyée": "#eab308",
    "Entretien": "#8b5cf6",
    "Offre": "#22c55e",
    "Refus": "#ef4444"
}
STATUS_OPTIONS = list(STATUS_COLORS.keys())

PRESET_STEPS = [
    "Candidature envoyée",
    "Entretien RH 1",
    "Entretien Manager 2",
    "Étude de cas",
    "Entretien Final 3",
    "Offre reçue",
    "Refus",
    "Autre (personnalisé)"
]

REQUIRED_COLUMNS = [
    "Entreprise", "Poste", "Lien", "Lieu", "Salaire", "Date",
    "Statut", "Historique", "Texte Offre", "Résumé IA",
    "Entretien IA", "Lettre de Motivation"
]

# ==========================================
# INITIALISATION IA & CONNEXION SHEETS
# ==========================================
gemini_configured = False
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    gemini_configured = True
else:
    st.error("La clé GEMINI_API_KEY n'est pas trouvée.")

conn_gsheets = st.connection("gsheets", type=GSheetsConnection)


def call_gemini_with_fallback(prompt: str) -> str:
    """Appelle l'API Gemini avec bascule automatique sur les modèles de secours."""
    if not gemini_configured:
        raise RuntimeError("Gemini n'est pas configuré.")
    last_error = None
    for model_name in MODELS_FALLBACK_LIST:
        try:
            response = genai.GenerativeModel(model_name).generate_content(prompt)
            if response and response.text:
                return response.text
        except Exception as e:
            last_error = e
    raise RuntimeError(f"Tous les modèles ont échoué. Dernière erreur : {last_error}")


def parse_llm_json(raw_text: str) -> dict:
    """Nettoie les balises Markdown éventuelles et parse le JSON retourné par le LLM."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    return json.loads(cleaned)


# ==========================================
# COUCHE DONNÉES (GOOGLE SHEETS AVEC CACHE)
# ==========================================
@st.cache_data(ttl=120, show_spinner=False)
def fetch_sheet_df(worksheet: str | None = None) -> pd.DataFrame:
    """Lit une feuille Google Sheets avec mise en cache courte (2 min) pour accélérer la navigation."""
    if worksheet:
        return conn_gsheets.read(worksheet=worksheet, dtype=str).fillna("")
    return conn_gsheets.read(dtype=str).fillna("")


def update_sheet_df(df: pd.DataFrame, worksheet: str | None = None) -> None:
    """Met à jour Google Sheets et invalide immédiatement le cache local."""
    if worksheet:
        conn_gsheets.update(worksheet=worksheet, data=df)
    else:
        conn_gsheets.update(data=df)
    st.cache_data.clear()


def load_presets_from_sheet() -> dict:
    default_presets = {
        "Product Manager - Paris": {"job": "product manager", "loc": "Paris"},
        "Product Owner - Paris": {"job": "product owner", "loc": "Paris"}
    }
    try:
        df_p = fetch_sheet_df(worksheet="Feuille 2")
        if not df_p.empty and {"Nom", "Poste", "Lieu"}.issubset(df_p.columns):
            presets = {
                str(r["Nom"]).strip(): {"job": str(r["Poste"]).strip(), "loc": str(r["Lieu"]).strip()}
                for _, r in df_p.iterrows()
                if str(r["Nom"]).strip()
            }
            if presets:
                return presets
    except Exception:
        pass
    return default_presets


def save_presets_to_sheet(presets_dict: dict) -> None:
    df_new = pd.DataFrame([
        {"Nom": name, "Poste": data.get("job", ""), "Lieu": data.get("loc", "")}
        for name, data in presets_dict.items()
    ])
    update_sheet_df(df_new, worksheet="Feuille 2")


# ==========================================
# UTILITAIRES MÉTIER (HISTORIQUE & PDF)
# ==========================================
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
        return current_status or "À postuler"
    last_step = history_list[-1].get("etape", "").lower()
    if "refus" in last_step:
        return "Refus"
    if "offre" in last_step:
        return "Offre"
    if any(k in last_step for k in ("entretien", "étude de cas", "case")):
        return "Entretien"
    if any(k in last_step for k in ("envoy", "postul")):
        return "Envoyée"
    if "ajout" in last_step:
        return "À postuler"
    return current_status


_PDF_CHAR_MAP = str.maketrans({
    "’": "'", "‘": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "\u00a0": " "
})


def sanitize_pdf_text(text: str) -> str:
    cleaned = text.replace("€", "EUR ").replace("…", "...").translate(_PDF_CHAR_MAP)
    return cleaned.encode("latin-1", "replace").decode("latin-1")


def build_cover_letter_pdf(company: str, location: str, body_text: str) -> bytes:
    """Génère un PDF A4 standard (210x297 mm) avec marges Google Docs (25 mm)."""
    pdf = FPDF(orientation="P", format="A4", unit="mm")
    pdf.set_margins(left=25, top=22, right=25)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # 1. En-tête Candidat (gauche)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 6, "Pol CARTRON", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(60, 60, 60)
    for line in (
        "Dual degree Engineering/Management student",
        "INSA Rennes / Audencia",
        "pol.cartron@gmail.com | (+33) 7 83 86 71 64"
    ):
        pdf.cell(0, 4.8, sanitize_pdf_text(line), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # 2. Bloc Destinataire & Date (droite)
    pdf.set_text_color(20, 20, 20)
    clean_company = sanitize_pdf_text(company.strip()) if company and company.strip().lower() != "inconnu" else ""
    if clean_company:
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.cell(0, 5, clean_company, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    clean_loc = sanitize_pdf_text(location.strip()) if location and location.strip().lower() != "inconnu" else "Paris, France"
    if "paris" in clean_loc.lower() and "france" not in clean_loc.lower():
        clean_loc = f"{clean_loc}, France"
    pdf.cell(0, 5, clean_loc, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, datetime.now().strftime("%d/%m/%Y"), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # 3. Corps de la lettre
    for p in body_text.replace("**", "").strip().split("\n\n"):
        p_clean = p.strip()
        if not p_clean:
            continue
        if p_clean.startswith("Subject:"):
            pdf.set_font("Helvetica", "B", 10.5)
            pdf.multi_cell(0, 5, sanitize_pdf_text(p_clean))
            pdf.ln(3)
        elif p_clean in ("Pol CARTRON", "Sincerely,"):
            pdf.set_font("Helvetica", "B" if p_clean == "Pol CARTRON" else "", 10.5)
            pdf.multi_cell(0, 5, sanitize_pdf_text(p_clean))
            if p_clean == "Sincerely,":
                pdf.ln(2.5)
        else:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 4.8, sanitize_pdf_text(p_clean))
            pdf.ln(3)

    return bytes(pdf.output())


def build_job_search_urls(job_title: str, location: str) -> tuple[str, str, str]:
    """Construit les URLs filtrées pour LinkedIn, HelloWork et JobTeaser."""
    job_enc = urllib.parse.quote(job_title)
    loc_enc = urllib.parse.quote(location)
    is_paris = location.strip().lower() == "paris"

    if is_paris:
        url_linkedin = (
            f"https://www.linkedin.com/jobs/search-results/?keywords={job_enc}"
            "&origin=JOB_SEARCH_PAGE_JOB_FILTER&geoId=106383538&distance=15"
            "&f_TPR=r2592000&f_SAL=f_SA_id_226001%3A275001"
        )
        url_hellowork = f"https://www.hellowork.com/fr-fr/emploi/recherche.html?k={job_enc}&l={loc_enc}&c=Stage&st=date&ray=20"
        url_jobteaser = (
            f"https://audencia.jobteaser.com/fr/job-offers?contract=internship&q={job_enc}"
            "&contract_duration=6&start_date=2027_01&start_date=2027_02&study_levels=4"
            "&work_experience_code=young_graduate&sort=recency&radius=20&lat=48.853495&lng=2.348391"
            "&location=France%3A%3A%C3%8Ele-de-France%3A%3AParis%3A%3AParis%3A%3AbG9jYWxpdHk6ZnI6Y2l0eTpmemVIZnJnZDJQekhETTNCZXE0NlUyL3pFMG89"
        )
    else:
        url_linkedin = (
            f"https://www.linkedin.com/jobs/search-results/?keywords={job_enc}&location={loc_enc}"
            "&origin=JOB_SEARCH_PAGE_JOB_FILTER&f_TPR=r2592000&f_SAL=f_SA_id_226001%3A275001"
        )
        url_hellowork = f"https://www.hellowork.com/fr-fr/emploi/recherche.html?k={job_enc}&l={loc_enc}&c=Stage&st=date"
        url_jobteaser = (
            f"https://audencia.jobteaser.com/fr/job-offers?contract=internship&q={job_enc}"
            "&contract_duration=6&start_date=2027_01&start_date=2027_02&study_levels=4"
            f"&work_experience_code=young_graduate&sort=recency&location={loc_enc}"
        )
    return url_linkedin, url_hellowork, url_jobteaser


# ==========================================
# INTERFACE UTILISATEUR
# ==========================================
st.markdown("<h1 style='text-align: center;'>Espace de Recherche PM/PO</h1>", unsafe_allow_html=True)

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
        if not st.session_state.presets:
            st.session_state.presets["Défaut"] = {"job": "product manager", "loc": "Paris"}
        preset_options = list(st.session_state.presets.keys())
        selected_preset = st.selectbox("Recherche sauvegardée :", preset_options)
        current_data = st.session_state.presets.get(selected_preset, {"job": "product manager", "loc": "Paris"})

    col1, col2 = st.columns(2)
    with col1:
        job_title = st.text_input("Intitulé du poste", value=current_data["job"])
    with col2:
        location = st.text_input("Lieu", value=current_data["loc"])

    with col_btn_save:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Sauvegarder", use_container_width=True):
            new_name = f"{job_title.title()} - {location.title()}"
            st.session_state.presets[new_name] = {"job": job_title, "loc": location}
            save_presets_to_sheet(st.session_state.presets)
            st.success("Sauvegardé dans Feuille 2 !")
            time.sleep(0.8)
            st.rerun()

    with col_btn_del:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Supprimer", use_container_width=True) and len(st.session_state.presets) > 1:
            del st.session_state.presets[selected_preset]
            save_presets_to_sheet(st.session_state.presets)
            st.success("Supprimé !")
            time.sleep(0.8)
            st.rerun()

    url_linkedin, url_hellowork, url_jobteaser = build_job_search_urls(job_title, location)

    st.markdown("---")
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    with btn_col1:
        st.link_button("LinkedIn", url_linkedin, use_container_width=True)
    with btn_col2:
        st.link_button("HelloWork", url_hellowork, use_container_width=True)
    with btn_col3:
        st.link_button("JobTeaser", url_jobteaser, use_container_width=True)

    st.markdown("---")
    st.markdown("<h2 style='text-align: center;'>Enregistrer une offre</h2>", unsafe_allow_html=True)

    offer_link = st.text_input("Lien de l'offre (optionnel) :")
    offer_text = st.text_area("Description de l'offre :", height=150)

    if st.button("Ajouter au Tracker", type="primary", use_container_width=True):
        if offer_text.strip() and gemini_configured:
            with st.spinner("Extraction via IA..."):
                prompt_json = (
                    'Extrais les infos sous forme JSON valide avec EXACTEMENT ces clés : '
                    '"Entreprise", "Poste", "Lieu", "Salaire". (Mets "Inconnu" si introuvable).\n'
                    f"Offre : {offer_text}"
                )
                try:
                    extracted_data = parse_llm_json(call_gemini_with_fallback(prompt_json))
                    df_current = fetch_sheet_df()
                    today_str = datetime.now().strftime("%d/%m/%Y")

                    new_row = {
                        "Entreprise": extracted_data.get("Entreprise", "Inconnu"),
                        "Poste": extracted_data.get("Poste", "Inconnu"),
                        "Lien": offer_link.strip(),
                        "Lieu": extracted_data.get("Lieu", ""),
                        "Salaire": extracted_data.get("Salaire", ""),
                        "Date": today_str,
                        "Statut": "À postuler",
                        "Historique": json.dumps([{"date": today_str, "etape": "Ajoutée au tracker"}], ensure_ascii=False),
                        "Texte Offre": offer_text,
                        "Résumé IA": "",
                        "Entretien IA": "",
                        "Lettre de Motivation": ""
                    }

                    updated_df = pd.concat([df_current, pd.DataFrame([new_row])], ignore_index=True).fillna("")
                    update_sheet_df(updated_df)
                    st.success("Enregistrée avec date du jour !")
                    time.sleep(0.8)
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
        df_tracker = fetch_sheet_df()
        for col in REQUIRED_COLUMNS:
            if col not in df_tracker.columns:
                df_tracker[col] = ""

        valid_indices = df_tracker[df_tracker["Entreprise"].str.strip() != ""].index
        if st.session_state.selected_offer_idx not in valid_indices:
            st.session_state.selected_offer_idx = None

        # --- VUE 1 : LISTE DES OFFRES ---
        if st.session_state.selected_offer_idx is None:
            st.markdown("<h2 style='text-align: center;'>Mes Offres Sauvegardées</h2>", unsafe_allow_html=True)

            if valid_indices.empty:
                st.info("Aucune offre sauvegardée pour le moment.")
            else:
                st.caption(f"{len(valid_indices)} offre(s) enregistrée(s)")

                for idx in valid_indices:
                    row = df_tracker.loc[idx]
                    status = row.get("Statut", "À postuler")
                    badge_color = STATUS_COLORS.get(status, "#64748b")
                    offer_url = row.get("Lien", "").strip()

                    with st.container(border=True):
                        col_card_txt, col_card_btn = st.columns([3, 1.4], vertical_alignment="center")
                        with col_card_txt:
                            st.markdown(f"**🏢 {row.get('Entreprise')}** — {row.get('Poste')}")
                            details = [
                                item for item in (
                                    f"📍 {row.get('Lieu')}" if row.get("Lieu") else "",
                                    f"💰 {row.get('Salaire')}" if row.get("Salaire") else ""
                                ) if item
                            ]
                            meta_str = " | ".join(details)
                            badge_html = (
                                f"<span style='background-color:{badge_color}; color:white; padding:2px 8px; "
                                f"border-radius:12px; font-size:12px; font-weight:bold;'>{status}</span>"
                            )
                            st.markdown(
                                f"<small style='color: gray;'>{meta_str}</small> &nbsp; {badge_html}" if meta_str else badge_html,
                                unsafe_allow_html=True
                            )

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
                if row_data.get("Lien"):
                    st.markdown(f"[🔗 Voir l'annonce]({row_data.get('Lien')})")

            with col_status:
                options = list(STATUS_OPTIONS)
                current_status = row_data.get("Statut", "À postuler")
                if current_status and current_status not in options:
                    options.insert(0, current_status)

                new_status = st.selectbox(
                    "🚥 Statut :",
                    options,
                    index=options.index(current_status) if current_status in options else 0,
                    label_visibility="collapsed"
                )
                if new_status != current_status:
                    df_tracker.at[selected_idx, "Statut"] = new_status
                    update_sheet_df(df_tracker)
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
                                df_tracker.at[selected_idx, "Historique"] = json.dumps(history_list, ensure_ascii=False)
                                df_tracker.at[selected_idx, "Statut"] = infer_main_status(history_list, current_status)
                                update_sheet_df(df_tracker)
                                st.rerun()

                st.markdown("---")
                add_col1, add_col2, add_col3 = st.columns([2.2, 1.3, 1], vertical_alignment="bottom")
                with add_col1:
                    chosen_step = st.selectbox("Nouvelle étape :", PRESET_STEPS, key=f"step_sel_{selected_idx}")
                with add_col2:
                    step_date = st.date_input("Date :", value=datetime.now(), format="DD/MM/YYYY", key=f"step_date_{selected_idx}")
                with add_col3:
                    add_clicked = st.button("➕ Ajouter", key=f"add_hist_{selected_idx}", use_container_width=True)

                custom_step_text = (
                    st.text_input("Intitulé personnalisé (ex: Entretien CPO 3) :", key=f"custom_step_{selected_idx}")
                    if chosen_step == "Autre (personnalisé)" else ""
                )

                if add_clicked:
                    final_step_label = custom_step_text.strip() if chosen_step == "Autre (personnalisé)" else chosen_step
                    if final_step_label:
                        history_list.append({"date": step_date.strftime("%d/%m/%Y"), "etape": final_step_label})
                        df_tracker.at[selected_idx, "Historique"] = json.dumps(history_list, ensure_ascii=False)
                        df_tracker.at[selected_idx, "Statut"] = infer_main_status(history_list, current_status)
                        update_sheet_df(df_tracker)
                        st.rerun()
                    else:
                        st.warning("Indique un intitulé pour l'étape personnalisée.")

            if st.button("🗑️ Supprimer l'offre", use_container_width=True):
                update_sheet_df(df_tracker.drop(selected_idx))
                st.session_state.selected_offer_idx = None
                st.rerun()

            st.markdown("---")

            # ========================================================
            # 1. LETTRE DE MOTIVATION & EXPORT PDF
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
                        dynamic_parts = parse_llm_json(call_gemini_with_fallback(prompt_lm))
                        poste_label = row_data.get("Poste", "")
                        lettre_finale = f"""Subject: Application for the {poste_label} Internship

Dear Hiring Team,

Currently pursuing an Engineering and Management dual degree at INSA Rennes and Audencia, I am seeking a 6-month {poste_label} internship in Paris starting in January/February 2027. It is with great enthusiasm that I submit my application.

{dynamic_parts.get("paragraphe_vous", "")}

My hybrid background allows me to naturally bridge the gap between business strategy and technical execution. My engineering curriculum at INSA Rennes provided me with strong analytical rigor, while my Master's program at Audencia honed my skills in product strategy, agile methodologies (Scrum, Kanban), and Go-to-Market execution. Hands-on by nature, I actively design and build technical products. Specifically, I led the end-to-end lifecycle of a mobile application built with Flutter, and engineered an AI-powered web application in Python integrating the Google Gemini API.

Furthermore, product success relies heavily on stakeholder alignment and leadership without authority. As President of the INSA Rennes Student Union, I steered campus operations for 1,300 members and managed a €1M operating budget across 40 student initiatives. {dynamic_parts.get("phrase_nous", "")}

I would welcome the opportunity to discuss your product vision and demonstrate how my dual technical and business skillset can contribute to your success.

Thank you for your time and consideration.

Sincerely,

Pol CARTRON"""
                        df_tracker.at[selected_idx, "Lettre de Motivation"] = lettre_finale
                        update_sheet_df(df_tracker)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur LM : {e}")

            current_lm = row_data.get("Lettre de Motivation", "").strip()
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

                    safe_company = re.sub(r"[^a-zA-Z0-9_-]", "_", row_data.get("Entreprise", "Entreprise").strip()).strip("_")
                    pdf_filename = f"LM_Pol_Cartron_{safe_company}.pdf"
                    pdf_bytes = build_cover_letter_pdf(
                        company=row_data.get("Entreprise", ""),
                        location=row_data.get("Lieu", "Paris, France"),
                        body_text=edited_lm
                    )

                    btn_save_lm, btn_dl_pdf = st.columns(2)
                    with btn_save_lm:
                        if st.button("💾 Sauvegarder les modifs", key=f"save_lm_{selected_idx}", use_container_width=True):
                            df_tracker.at[selected_idx, "Lettre de Motivation"] = edited_lm
                            update_sheet_df(df_tracker)
                            st.success("Modifications sauvegardées !")
                            time.sleep(0.6)
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
            # 2. RÉSUMÉ DE L'OFFRE
            # ========================================================
            current_resume = row_data.get("Résumé IA", "").strip()
            with st.expander("✨ Résumé de l'offre", expanded=False):
                if current_resume:
                    st.info(current_resume)
                else:
                    if st.button("✨ Générer le résumé", key="btn_res", use_container_width=True):
                        with st.spinner("Analyse..."):
                            try:
                                prompt = (
                                    "Agis comme un Product Manager Senior. Résume les missions de cette offre "
                                    f"en exactement 3 points à puces clairs et concis.\nOffre : {row_data.get('Texte Offre')}"
                                )
                                df_tracker.at[selected_idx, "Résumé IA"] = call_gemini_with_fallback(prompt)
                                update_sheet_df(df_tracker)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erreur Résumé : {e}")

            # ========================================================
            # 3. PRÉPARATION ENTRETIEN
            # ========================================================
            current_entretien = row_data.get("Entretien IA", "").strip()
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
                                df_tracker.at[selected_idx, "Entretien IA"] = call_gemini_with_fallback(prompt)
                                update_sheet_df(df_tracker)
                                st.session_state[ent_open_key] = True
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erreur Entretien : {e}")

            # ========================================================
            # 4. TEXTE ORIGINAL DE L'OFFRE
            # ========================================================
            with st.expander("📄 Texte original de l'offre", expanded=False):
                st.write(row_data.get("Texte Offre", "Aucun texte."))

    except Exception as e:
        st.error(f"Erreur base de données : {e}")