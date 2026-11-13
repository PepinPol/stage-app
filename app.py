import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import urllib.parse
import google.generativeai as genai
import json
import time
import re
import hashlib
from datetime import datetime
from fpdf import FPDF

# ==========================================
# CONFIGURATION & CONSTANTES
# ==========================================
st.set_page_config(page_title="Tracker de Stage PM", layout="centered")

MODELS_FALLBACK_LIST = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
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

REQUIRED_COLUMNS_F1 = [
    "Entreprise", "Poste", "Lien", "Lieu", "Salaire", "Date",
    "Statut", "Historique", "Texte Offre", "Résumé IA",
    "Entretien IA", "Lettre de Motivation"
]

REQUIRED_COLUMNS_F3 = [
    "Date_Ajout", "Entreprise", "Poste", "Lieu", "Salaire",
    "Lien", "Score", "Texte_Complet", "Statut_File"
]

DEFAULT_AGENT_CONFIG = {
    "jobs": ["product manager", "product owner", "chef de projet digital", "product analyst"],
    "loc": "Paris",
    "days": 4,
    "weekdays": ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"],
    "sites": ["LinkedIn", "Welcome to the Jungle", "HelloWork", "Indeed"]
}

ALL_WEEKDAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
ALL_SITES = ["LinkedIn", "Welcome to the Jungle", "HelloWork", "Indeed", "Web global"]

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
    """Appelle Gemini sans retry bloquant : bascule en <0.1s si un modèle est saturé (429/503)."""
    if not gemini_configured:
        raise RuntimeError("Gemini n'est pas configuré.")
    last_error = None
    for model_name in MODELS_FALLBACK_LIST:
        try:
            response = genai.GenerativeModel(model_name).generate_content(
                prompt,
                request_options={"retry": None, "timeout": 25}
            )
            if response and response.text:
                return response.text
        except Exception as e:
            last_error = e
    raise RuntimeError(f"Tous les modèles ont échoué. Dernière erreur : {last_error}")


def parse_llm_json(raw_text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    return json.loads(cleaned)


def make_offer_uid(row: pd.Series) -> str:
    """Génère un identifiant unique et stable propre à l'annonce, indépendant de son numéro de ligne."""
    raw = f"{row.get('Lien', '').strip()}_{row.get('Entreprise', '').strip()}_{row.get('Poste', '').strip()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


# ==========================================
# GÉNÉRATION IA DES 3 LIVRABLES (RÉSUMÉ / ENTRETIEN / LM)
# ==========================================
def generate_resume_text(offer_text: str) -> str:
    prompt = (
        "Agis comme un Product Manager Senior. Résume les missions de cette offre "
        f"en exactement 3 points à puces clairs et concis.\nOffre : {offer_text}"
    )
    return call_gemini_with_fallback(prompt)


def generate_entretien_text(offer_text: str) -> str:
    prompt = f"""
    Agis comme le Lead Product. Divise ta réponse en 2 :
    ### Fiche d'identité
    (Création, PDG, Siège, Employés, Concurrents, CA)
    ### Préparation (PM/PO)
    1. 3 valeurs clés.
    2. 3 questions techniques (Orientées Product).
    3. Mini-étude de cas.
    Offre : {offer_text}
    """
    return call_gemini_with_fallback(prompt)


def generate_cover_letter_text(poste_label: str, offer_text: str) -> str:
    prompt_lm = f"""
    Act as a Product Hiring Expert. Based on the job description below, write two tailored elements in English to complete the cover letter of an Engineering & Management dual-degree candidate:
    
    1. "paragraphe_vous": 2 to 3 sentences maximum in English explaining why the company's mission, product, or technological challenges are particularly appealing. Be specific and include keywords from the job description.
    2. "phrase_nous": 1 impactful sentence in English bridging the candidate's cross-functional project management and delivery skills with the primary need described in the role.
    
    Return the result ONLY as a valid JSON object with exactly these two keys ("paragraphe_vous" and "phrase_nous"). Do not include any extra text before or after.
    
    Job description: {offer_text}
    """
    dynamic_parts = parse_llm_json(call_gemini_with_fallback(prompt_lm))
    return f"""Subject: Application for the {poste_label} Internship

Dear Hiring Team,

Currently pursuing an Engineering and Management dual degree at INSA Rennes and Audencia, I am seeking a 6-month {poste_label} internship in Paris starting in January/February 2027. It is with great enthusiasm that I submit my application.

{dynamic_parts.get("paragraphe_vous", "")}

My hybrid background allows me to naturally bridge the gap between business strategy and technical execution. My engineering curriculum at INSA Rennes provided me with strong analytical rigor, while my Master's program at Audencia honed my skills in product strategy, agile methodologies (Scrum, Kanban), and Go-to-Market execution. Hands-on by nature, I actively design and build technical products. Specifically, I led the end-to-end lifecycle of a mobile application built with Flutter, and engineered an AI-powered web application in Python integrating the Google Gemini API.

Furthermore, product success relies heavily on stakeholder alignment and leadership without authority. As President of the INSA Rennes Student Union, I steered campus operations for 1,300 members and managed a €1M operating budget across 40 student initiatives. {dynamic_parts.get("phrase_nous", "")}

I would welcome the opportunity to discuss your product vision and demonstrate how my dual technical and business skillset can contribute to your success.

Thank you for your time and consideration.

Sincerely,

Pol CARTRON"""


def process_and_save_offer_with_progress(
    offer_text: str,
    offer_link: str,
    preset_meta: dict | None = None,
    step_label: str = "Ajoutée au tracker"
) -> None:
    """Exécute tout le pipeline d'ajout avec suivi en temps réel et barre de pourcentage."""
    progress_bar = st.progress(0, text="0% — Initialisation...")
    with st.status("Traitement de l'offre en cours...", expanded=True) as status_box:
        if preset_meta is None:
            progress_bar.progress(10, text="10% — Extraction de l'entreprise, du poste et du salaire...")
            st.write("🔍 Lecture et structuration des informations de l'annonce...")
            prompt_json = (
                'Extrais les infos sous forme JSON valide avec EXACTEMENT ces clés : '
                '"Entreprise", "Poste", "Lieu", "Salaire". (Mets "Inconnu" si introuvable).\n'
                f"Offre : {offer_text}"
            )
            meta = parse_llm_json(call_gemini_with_fallback(prompt_json))
        else:
            meta = preset_meta

        entreprise = meta.get("Entreprise", "Inconnu")
        poste = meta.get("Poste", "Inconnu")
        lieu = meta.get("Lieu", "Paris")
        salaire = meta.get("Salaire", "Inconnu")

        progress_bar.progress(35, text="35% — Génération du résumé des missions...")
        st.write("✨ Rédaction du résumé en 3 points clés...")
        try:
            resume_ia = generate_resume_text(offer_text)
        except Exception as e:
            resume_ia = f"Erreur génération résumé : {e}"

        progress_bar.progress(60, text="60% — Préparation de la fiche entreprise & entretien...")
        st.write("🎯 Analyse de l'entreprise et questions d'entretien...")
        try:
            entretien_ia = generate_entretien_text(offer_text)
        except Exception as e:
            entretien_ia = f"Erreur génération entretien : {e}"

        progress_bar.progress(82, text="82% — Rédaction de la lettre de motivation (EN)...")
        st.write("📝 Rédaction de la lettre de motivation sur-mesure...")
        try:
            lm_ia = generate_cover_letter_text(poste, offer_text)
        except Exception as e:
            lm_ia = f"Erreur génération LM : {e}"

        progress_bar.progress(95, text="95% — Enregistrement dans Google Sheets...")
        st.write("💾 Sauvegarde dans la base de données...")
        df_current = fetch_sheet_df()
        today_str = datetime.now().strftime("%d/%m/%Y")

        new_row = {
            "Entreprise": entreprise,
            "Poste": poste,
            "Lien": offer_link.strip(),
            "Lieu": lieu,
            "Salaire": salaire,
            "Date": today_str,
            "Statut": "À postuler",
            "Historique": json.dumps([{"date": today_str, "etape": step_label}], ensure_ascii=False),
            "Texte Offre": offer_text,
            "Résumé IA": resume_ia,
            "Entretien IA": entretien_ia,
            "Lettre de Motivation": lm_ia
        }

        updated_df = pd.concat([df_current, pd.DataFrame([new_row])], ignore_index=True).fillna("")
        update_sheet_df(updated_df)

        progress_bar.progress(100, text="100% — Offre enregistrée et analysée !")
        status_box.update(label=f"✅ {entreprise} — {poste} prête !", state="complete", expanded=False)


# ==========================================
# COUCHE DONNÉES (GOOGLE SHEETS AVEC CACHE)
# ==========================================
@st.cache_data(ttl=120, show_spinner=False)
def fetch_sheet_df(worksheet: str | None = None) -> pd.DataFrame:
    if worksheet:
        return conn_gsheets.read(worksheet=worksheet, dtype=str).fillna("")
    return conn_gsheets.read(dtype=str).fillna("")


def update_sheet_df(df: pd.DataFrame, worksheet: str | None = None) -> None:
    if worksheet:
        conn_gsheets.update(worksheet=worksheet, data=df)
    else:
        conn_gsheets.update(data=df)
    st.cache_data.clear()


def load_feuille2_data() -> tuple[dict, dict]:
    default_presets = {
        "Product Manager - Paris": {"job": "product manager", "loc": "Paris"},
        "Product Owner - Paris": {"job": "product owner", "loc": "Paris"}
    }
    agent_cfg = dict(DEFAULT_AGENT_CONFIG)
    presets = {}

    try:
        df_p = fetch_sheet_df(worksheet="Feuille 2")
        if not df_p.empty:
            for _, r in df_p.iterrows():
                nom = str(r.get("Nom", "")).strip()
                poste = str(r.get("Poste", "")).strip()
                lieu = str(r.get("Lieu", "")).strip()
                if nom == "__AGENT_CONFIG__" and poste:
                    try:
                        loaded_cfg = json.loads(poste)
                        agent_cfg.update(loaded_cfg)
                    except Exception:
                        pass
                elif nom and nom != "__AGENT_CONFIG__":
                    presets[nom] = {"job": poste, "loc": lieu}
    except Exception:
        pass

    return (presets if presets else default_presets), agent_cfg


def save_feuille2_data(presets_dict: dict, agent_cfg: dict) -> None:
    rows = [
        {"Nom": name, "Poste": data.get("job", ""), "Lieu": data.get("loc", "")}
        for name, data in presets_dict.items()
    ]
    rows.append({
        "Nom": "__AGENT_CONFIG__",
        "Poste": json.dumps(agent_cfg, ensure_ascii=False),
        "Lieu": ""
    })
    update_sheet_df(pd.DataFrame(rows), worksheet="Feuille 2")


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
    if "ajout" in last_step or "valid" in last_step:
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
    pdf = FPDF(orientation="P", format="A4", unit="mm")
    pdf.set_margins(left=25, top=22, right=25)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

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
col_title, col_refresh = st.columns([5, 1], vertical_alignment="center")
with col_title:
    st.markdown("<h1 style='text-align: center;'>Espace de Recherche PM/PO</h1>", unsafe_allow_html=True)
with col_refresh:
    if st.button("🔄", help="Actualiser les données depuis Google Sheets", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if "presets" not in st.session_state or "agent_cfg" not in st.session_state:
    p_loaded, cfg_loaded = load_feuille2_data()
    st.session_state.presets = p_loaded
    st.session_state.agent_cfg = cfg_loaded

if "selected_offer_idx" not in st.session_state:
    st.session_state.selected_offer_idx = None

tab1, tab2, tab3 = st.tabs([
    "1. Recherche & Ajout",
    "2. Mes Offres & Analyses",
    "3. 🤖 Agent & File d'attente"
])

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
            save_feuille2_data(st.session_state.presets, st.session_state.agent_cfg)
            st.success("Sauvegardé dans Feuille 2 !")
            time.sleep(0.8)
            st.rerun()

    with col_btn_del:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Supprimer", use_container_width=True) and len(st.session_state.presets) > 1:
            del st.session_state.presets[selected_preset]
            save_feuille2_data(st.session_state.presets, st.session_state.agent_cfg)
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

    if st.button("Ajouter au Tracker (avec Résumé, Entretien & LM)", type="primary", use_container_width=True):
        if offer_text.strip() and gemini_configured:
            try:
                process_and_save_offer_with_progress(
                    offer_text=offer_text,
                    offer_link=offer_link,
                    preset_meta=None,
                    step_label="Ajoutée au tracker"
                )
                time.sleep(0.8)
                st.rerun()
            except Exception as e:
                st.error(f"Erreur lors de l'ajout : {e}")
        else:
            st.warning("Colle le texte de l'offre d'abord !")

# ==========================================
# ONGLET 2 : MES OFFRES & ANALYSES
# ==========================================
with tab2:
    try:
        df_tracker = fetch_sheet_df()
        for col in REQUIRED_COLUMNS_F1:
            if col not in df_tracker.columns:
                df_tracker[col] = ""

        valid_indices = df_tracker[df_tracker["Entreprise"].str.strip() != ""].index
        if st.session_state.selected_offer_idx not in valid_indices:
            st.session_state.selected_offer_idx = None

        # --- VUE 1 : LISTE DES OFFRES AVEC TRI ---
        if st.session_state.selected_offer_idx is None:
            st.markdown("<h2 style='text-align: center;'>Mes Offres Sauvegardées</h2>", unsafe_allow_html=True)

            if valid_indices.empty:
                st.info("Aucune offre sauvegardée pour le moment.")
            else:
                col_sort_by, col_sort_dir = st.columns([2, 1.5])
                with col_sort_by:
                    sort_criterion = st.selectbox(
                        "Trier par :",
                        options=["Ordre d'ajout (Excel)", "Nom (Entreprise)", "État (Statut)"],
                        key="tracker_sort_by"
                    )
                with col_sort_dir:
                    sort_order = st.selectbox(
                        "Ordre :",
                        options=["Décroissant (Récent / Z→A)", "Croissant (Ancien / A→Z)"],
                        key="tracker_sort_dir"
                    )

                is_ascending = sort_order.startswith("Croissant")
                df_valid = df_tracker.loc[valid_indices].copy()

                if sort_criterion == "Ordre d'ajout (Excel)":
                    sorted_indices = df_valid.index.sort_values(ascending=is_ascending)
                elif sort_criterion == "Nom (Entreprise)":
                    df_valid["_sort_key"] = df_valid["Entreprise"].str.strip().str.lower()
                    sorted_indices = df_valid.sort_values(
                        by=["_sort_key", "Poste"],
                        ascending=is_ascending,
                        kind="mergesort"
                    ).index
                else:
                    status_rank = {
                        "À postuler": 1,
                        "Envoyée": 2,
                        "Entretien": 3,
                        "Offre": 4,
                        "Refus": 5
                    }
                    df_valid["_sort_key"] = df_valid["Statut"].map(status_rank).fillna(99)
                    sorted_indices = df_valid.sort_values(
                        by="_sort_key",
                        ascending=is_ascending,
                        kind="mergesort"
                    ).index

                st.caption(f"{len(sorted_indices)} offre(s) enregistrée(s)")

                for idx in sorted_indices:
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
            # 1. LETTRE DE MOTIVATION & EXPORT PDF (Regénération conservée)
            # ========================================================
            def regenerer_lettre():
                with st.spinner("Regénération de la lettre de motivation..."):
                    try:
                        nouvelle_lm = generate_cover_letter_text(
                            poste_label=row_data.get("Poste", ""),
                            offer_text=row_data.get("Texte Offre", "")
                        )
                        df_tracker.at[selected_idx, "Lettre de Motivation"] = nouvelle_lm
                        update_sheet_df(df_tracker)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur regénération LM : {e}")

            current_lm = row_data.get("Lettre de Motivation", "").strip()
            with st.expander("📝 Lettre de Motivation & Export PDF", expanded=bool(current_lm)):
                col_lm_info, col_lm_regen = st.columns([4, 1], vertical_alignment="center")
                with col_lm_info:
                    st.caption("Modifie le texte ci-dessous si besoin puis télécharge directement ton PDF A4.")
                with col_lm_regen:
                    if st.button("🔄 Regénérer", key="btn_regen_lm", use_container_width=True, help="Générer une nouvelle version via IA"):
                        regenerer_lettre()

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
                    body_text=edited_lm or " "
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

            # ========================================================
            # 2. RÉSUMÉ DE L'OFFRE (Affichage direct, sans bouton)
            # ========================================================
            current_resume = row_data.get("Résumé IA", "").strip()
            with st.expander("✨ Résumé de l'offre", expanded=True):
                if current_resume:
                    st.info(current_resume)
                else:
                    st.caption("Aucun résumé enregistré pour cette offre.")

            # ========================================================
            # 3. PRÉPARATION ENTRETIEN (Affichage direct, sans bouton)
            # ========================================================
            current_entretien = row_data.get("Entretien IA", "").strip()
            with st.expander("🎯 Préparation entretien", expanded=False):
                if current_entretien:
                    st.success(current_entretien)
                else:
                    st.caption("Aucune préparation d'entretien enregistrée pour cette offre.")

            # ========================================================
            # 4. TEXTE ORIGINAL DE L'OFFRE
            # ========================================================
            with st.expander("📄 Texte original de l'offre", expanded=False):
                st.write(row_data.get("Texte Offre", "Aucun texte."))

    except Exception as e:
        st.error(f"Erreur base de données : {e}")

# ==========================================
# ONGLET 3 : AGENT IA & FILE D'ATTENTE
# ==========================================
with tab3:
    st.markdown("<h2 style='text-align: center;'>Agent de Veille Automatique</h2>", unsafe_allow_html=True)

    # --- 1. PARAMÈTRES MODULABLES DE L'AGENT ---
    with st.expander("⚙️ Paramètres de recherche de l'Agent", expanded=False):
        cfg = st.session_state.agent_cfg

        st.markdown("**1. Intitulés de postes recherchés**")
        base_jobs = ["product manager", "product owner", "chef de projet digital", "product analyst", "business analyst", "data analyst"]
        all_known_jobs = list(dict.fromkeys(base_jobs + cfg.get("jobs", [])))
        selected_jobs = st.multiselect(
            "Postes actifs :",
            options=all_known_jobs,
            default=[j for j in cfg.get("jobs", []) if j in all_known_jobs]
        )
        new_custom_job = st.text_input("Ajouter un intitulé personnalisé (optionnel) :", placeholder="ex: associate product manager")

        col_cfg1, col_cfg2 = st.columns(2)
        with col_cfg1:
            cfg_loc = st.text_input("Ville cible :", value=cfg.get("loc", "Paris"))
        with col_cfg2:
            cfg_days = st.slider("Ancienneté max des offres (jours) :", min_value=1, max_value=14, value=int(cfg.get("days", 4)))

        st.markdown("**2. Jours actifs de la semaine**")
        selected_weekdays = st.multiselect(
            "L'agent tourne uniquement ces jours-là :",
            options=ALL_WEEKDAYS,
            default=cfg.get("weekdays", ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"])
        )

        st.markdown("**3. Sites explorés par l'agent**")
        selected_sites = st.multiselect(
            "Sources web actives :",
            options=ALL_SITES,
            default=cfg.get("sites", ["LinkedIn", "Welcome to the Jungle", "HelloWork", "Indeed"])
        )

        nb_credits_est = len(selected_jobs) * len(selected_sites)
        st.caption(f"⚡ Coût estimé par exécution : **{nb_credits_est} crédits Tavily** (Limite conseillée : ≤ 32/jour)")

        if st.button("💾 Enregistrer la configuration de l'Agent", type="primary", use_container_width=True):
            final_jobs = list(selected_jobs)
            if new_custom_job.strip() and new_custom_job.strip().lower() not in [j.lower() for j in final_jobs]:
                final_jobs.append(new_custom_job.strip().lower())

            updated_cfg = {
                "jobs": final_jobs or ["product manager", "product owner"],
                "loc": cfg_loc.strip() or "Paris",
                "days": cfg_days,
                "weekdays": selected_weekdays or ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"],
                "sites": selected_sites or ["LinkedIn", "Welcome to the Jungle", "HelloWork", "Indeed"]
            }
            st.session_state.agent_cfg = updated_cfg
            save_feuille2_data(st.session_state.presets, updated_cfg)
            st.success("Configuration enregistrée ! L'agent l'appliquera dès son prochain passage.")
            time.sleep(0.8)
            st.rerun()

    st.markdown("---")

    # --- 2. FILE D'ATTENTE & HISTORIQUE DES REFUS (FEUILLE 3) ---
    try:
        df_queue = fetch_sheet_df(worksheet="Feuille 3")
        for col in REQUIRED_COLUMNS_F3:
            if col not in df_queue.columns:
                df_queue[col] = ""

        valid_q = df_queue[df_queue["Entreprise"].str.strip() != ""]
        pending_df = valid_q[valid_q["Statut_File"].str.strip().isin(["", "En attente"])]
        rejected_df = valid_q[valid_q["Statut_File"].str.strip() == "Refusée"]

        st.markdown(f"### 📥 File d'attente ({len(pending_df)} offre(s) à trier)")

        if pending_df.empty:
            st.info("Aucune nouvelle offre en attente pour le moment. Reviens après le prochain passage de l'agent !")
        else:
            for q_idx, q_row in pending_df.iterrows():
                offer_uid = make_offer_uid(q_row)
                text_widget_key = f"q_text_{offer_uid}"
                score_txt = q_row.get("Score", "").strip()
                score_badge = f" | ⭐ **{score_txt}/10**" if score_txt else ""
                date_ajout = q_row.get("Date_Ajout", "")
                lien_offre = q_row.get("Lien", "").strip()

                with st.container(border=True):
                    top_c1, top_c2 = st.columns([4, 1], vertical_alignment="center")
                    with top_c1:
                        st.markdown(f"**🏢 {q_row.get('Entreprise')}** — {q_row.get('Poste')}")
                        st.caption(f"📅 Trouvée le {date_ajout} • 📍 {q_row.get('Lieu', 'Paris')} • 💰 {q_row.get('Salaire', 'Inconnu')}{score_badge}")
                    with top_c2:
                        if lien_offre:
                            st.link_button("🔗 Voir", lien_offre, use_container_width=True)

                    with st.expander("📄 Voir / Modifier le texte de l'offre avant ajout", expanded=False):
                        st.text_area(
                            "Texte qui sera enregistré dans le tracker :",
                            value=q_row.get("Texte_Complet", ""),
                            height=180,
                            key=text_widget_key,
                            label_visibility="collapsed"
                        )

                    btn_accept, btn_reject = st.columns(2)
                    with btn_accept:
                        if st.button("✅ Ajouter au Tracker", key=f"acc_{offer_uid}", type="primary", use_container_width=True):
                            try:
                                final_text = st.session_state.get(text_widget_key, q_row.get("Texte_Complet", ""))
                                preset_meta = {
                                    "Entreprise": q_row.get("Entreprise", "Inconnu"),
                                    "Poste": q_row.get("Poste", "Inconnu"),
                                    "Lieu": q_row.get("Lieu", "Paris"),
                                    "Salaire": q_row.get("Salaire", "Inconnu")
                                }
                                process_and_save_offer_with_progress(
                                    offer_text=final_text,
                                    offer_link=lien_offre,
                                    preset_meta=preset_meta,
                                    step_label="Validée depuis la file Agent"
                                )

                                updated_f3 = df_queue.drop(q_idx)
                                update_sheet_df(updated_f3, worksheet="Feuille 3")
                                st.session_state.pop(text_widget_key, None)
                                time.sleep(0.8)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erreur lors de l'ajout : {e}")

                    with btn_reject:
                        if st.button("❌ Refuser", key=f"rej_{offer_uid}", use_container_width=True):
                            df_queue.at[q_idx, "Statut_File"] = "Refusée"
                            update_sheet_df(df_queue, worksheet="Feuille 3")
                            st.session_state.pop(text_widget_key, None)
                            st.rerun()

        st.markdown("---")

        # --- 3. HISTORIQUE DES OFFRES REFUSÉES ---
        with st.expander(f"🗄️ Historique des offres refusées ({len(rejected_df)})", expanded=False):
            if rejected_df.empty:
                st.caption("Aucune offre refusée pour le moment.")
            else:
                for r_idx, r_row in rejected_df.iterrows():
                    rej_uid = make_offer_uid(r_row)
                    rc1, rc2, rc3 = st.columns([3, 1, 1.3], vertical_alignment="center")
                    with rc1:
                        st.markdown(f"**{r_row.get('Entreprise')}** — {r_row.get('Poste')}")
                        st.caption(f"Refusée (trouvée le {r_row.get('Date_Ajout', '')})")
                    with rc2:
                        if r_row.get("Lien", "").strip():
                            st.link_button("🔗", r_row.get("Lien", "").strip(), use_container_width=True)
                    with rc3:
                        if st.button("↩️ Repêcher", key=f"restore_{rej_uid}", use_container_width=True):
                            df_queue.at[r_idx, "Statut_File"] = "En attente"
                            update_sheet_df(df_queue, worksheet="Feuille 3")
                            st.rerun()

    except Exception as e:
        st.error(f"Erreur lecture Feuille 3 (vérifie que l'onglet 'Feuille 3' existe bien) : {e}")