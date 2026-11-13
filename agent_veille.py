import os
import json
import re
from datetime import datetime
import requests
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# ==========================================
# 1. INITIALISATION DES CONNEXIONS
# ==========================================
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
SPREADSHEET_URL = os.environ["SPREADSHEET_URL"].strip()
GSHEETS_CREDS = json.loads(os.environ["GSHEETS_CREDENTIALS_JSON"])

genai.configure(api_key=GEMINI_API_KEY)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(GSHEETS_CREDS, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_url(SPREADSHEET_URL)

sheet_offres = sh.worksheet("Feuille 1")
sheet_config = sh.worksheet("Feuille 2")
sheet_queue = sh.worksheet("Feuille 3")

MODELS_FALLBACK = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash"
]

FRENCH_DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

SITE_QUERY_TEMPLATES = {
    "LinkedIn": 'site:linkedin.com/jobs/view "stage" "{job}" "{loc}"',
    "Welcome to the Jungle": 'site:welcometothejungle.com/fr/companies "stage" "{job}" "{loc}"',
    "HelloWork": 'site:hellowork.com/fr-fr/emplois "stage" "{job}" "{loc}"',
    "Indeed": 'site:fr.indeed.com/viewjob "stage" "{job}" "{loc}"',
    "Web global": '"offre de stage" "{job}" "{loc}" 2027'
}


def ask_gemini_fast(prompt: str) -> str:
    last_err = None
    for model_name in MODELS_FALLBACK:
        try:
            model = genai.GenerativeModel(model_name)
            resp = model.generate_content(
                prompt,
                request_options={"retry": None, "timeout": 120}
            )
            if resp and resp.text:
                print(f"🤖 Réponse obtenue via {model_name}")
                return resp.text
        except Exception as e:
            last_err = e
            print(f"⚠️ {model_name} ignoré ({e})")
    raise RuntimeError(f"Tous les modèles Gemini ont échoué : {last_err}")


def parse_json_response(raw_text: str):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    return json.loads(cleaned)


# ==========================================
# 2. LECTURE CONFIGURATION (FEUILLE 2)
# ==========================================
def load_agent_config() -> dict:
    default_cfg = {
        "jobs": ["product manager", "product owner", "chef de projet digital", "product analyst"],
        "loc": "Paris",
        "days": 4,
        "weekdays": ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"],
        "sites": ["LinkedIn", "Welcome to the Jungle", "HelloWork", "Indeed"]
    }
    try:
        records = sheet_config.get_all_records()
        for r in records:
            if str(r.get("Nom", "")).strip() == "__AGENT_CONFIG__":
                raw_json = str(r.get("Poste", "")).strip()
                if raw_json:
                    default_cfg.update(json.loads(raw_json))
                    break
    except Exception as e:
        print(f"⚠️ Lecture config par défaut ({e})")
    return default_cfg


# ==========================================
# 3. DÉDOUBLONNAGE (FEUILLE 1 + FEUILLE 3)
# ==========================================
def get_all_known_offers_and_next_queue_row() -> tuple[set, set, int]:
    """Vérifie les offres dans Feuille 1 (Tracker) ET Feuille 3 (File d'attente + Refusées)."""
    existing_links = set()
    existing_pairs = set()

    for ws in (sheet_offres, sheet_queue):
        vals = ws.get_all_values()
        if not vals:
            continue
        headers = vals[0]
        idx_ent = headers.index("Entreprise") if "Entreprise" in headers else 0
        idx_poste = headers.index("Poste") if "Poste" in headers else 1
        idx_lien = headers.index("Lien") if "Lien" in headers else 2

        for row in vals[1:]:
            ent = row[idx_ent].strip().lower() if len(row) > idx_ent else ""
            poste = row[idx_poste].strip().lower() if len(row) > idx_poste else ""
            lien = row[idx_lien].strip().lower() if len(row) > idx_lien else ""
            if lien:
                existing_links.add(lien)
            if ent and poste:
                existing_pairs.add((ent, poste))

    # Calcul de la prochaine ligne vide exacte dans Feuille 3
    q_vals = sheet_queue.get_all_values()
    last_non_empty = 1
    for idx, row in enumerate(q_vals[1:], start=2):
        if any(cell.strip() for cell in row):
            last_non_empty = idx

    return existing_links, existing_pairs, last_non_empty + 1


# ==========================================
# 4. RECHERCHE WEB & EXTRACTION TEXTE COMPLET
# ==========================================
def search_jobs_tavily(job: str, loc: str, days: int, active_sites: list[str]) -> list[dict]:
    results = []
    seen_urls = set()

    for site_name in active_sites:
        template = SITE_QUERY_TEMPLATES.get(site_name)
        if not template:
            continue
        q = template.format(job=job, loc=loc)
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": q,
                    "search_depth": "basic",
                    "include_raw_content": True,
                    "max_results": 4,
                    "days": days
                },
                timeout=20
            )
            data = resp.json()
            for item in data.get("results", []):
                url = item.get("url", "").strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    full_txt = (item.get("raw_content") or item.get("content") or "").strip()
                    results.append({
                        "title": item.get("title", ""),
                        "url": url,
                        "snippet": (item.get("content") or "")[:600],
                        "full_text": full_txt[:8000]
                    })
        except Exception as e:
            print(f"Erreur Tavily ({q}) : {e}")
    return results


def filter_batch_with_gemini(offers: list[dict], loc: str) -> list[dict]:
    if not offers:
        return []

    all_selected = []
    batch_size = 15  # Découpe par paquets de 15 pour éviter tout timeout

    for i in range(0, len(offers), batch_size):
        chunk = offers[i:i + batch_size]
        light_offers = [
            {"title": o["title"], "url": o["url"], "snippet": o["snippet"][:350]}
            for o in chunk
        ]

        prompt = f"""
        Tu es un assistant de recrutement pour Pol Cartron (étudiant double diplôme Ingénieur INSA Rennes / Management Audencia).
        Il cherche un STAGE de fin d'études de 6 mois en Product Management (PM), Product Ownership (PO), Gestion de Projet Digital ou Product Analysis, autour de {loc}, pour janvier/février 2027.

        Analyse ces résultats et ne garde QUE les vraies offres de stage individuelles pertinentes :
        - Exclure les pages de listes/annuaires, les CDI, les alternances, et les postes senior sans mention de stage.
        - Une seule offre par entreprise si doublon.

        Résultats :
        {json.dumps(light_offers, ensure_ascii=False)}

        Retourne UNIQUEMENT un tableau JSON valide contenant les offres retenues (score >= 7/10) avec exactement ces clés :
        - "url": l'URL exacte
        - "Entreprise": nom de l'entreprise
        - "Poste": intitulé exact du poste
        - "Lieu": ville
        - "Salaire": gratification si mentionnée (sinon "Inconnu")
        - "Score": note entière sur 10
        - "Resume_Propre": résumé structuré de l'offre en 4-5 lignes
        Si aucune offre n'est pertinente, retourne [].
        """
        try:
            res = parse_json_response(ask_gemini_fast(prompt))
            if isinstance(res, list):
                all_selected.extend(res)
        except Exception as e:
            print(f"Erreur sur le lot {i // batch_size + 1} : {e}")

    return all_selected


# ==========================================
# 5. EXÉCUTION PRINCIPALE
# ==========================================
def run_agent():
    cfg = load_agent_config()
    today_weekday = FRENCH_DAYS[datetime.now().weekday()]
    print(f"📅 Jour actuel : {today_weekday} | Jours configurés : {cfg['weekdays']}")

    if today_weekday not in cfg["weekdays"]:
        print(f"😴 {today_weekday} n'est pas coché dans les paramètres de l'app. Arrêt propre (0 crédit utilisé).")
        return

    existing_links, existing_pairs, next_row = get_all_known_offers_and_next_queue_row()
    today_str = datetime.now().strftime("%d/%m/%Y")
    headers_f3 = sheet_queue.row_values(1)

    all_candidates = []
    seen_batch_urls = set()

    for job in cfg["jobs"]:
        print(f"🔍 Recherche : {job} à {cfg['loc']} (≤ {cfg['days']}j)")
        for item in search_jobs_tavily(job, cfg["loc"], int(cfg["days"]), cfg["sites"]):
            u = item["url"].lower()
            if u not in existing_links and u not in seen_batch_urls:
                seen_batch_urls.add(u)
                all_candidates.append(item)

    print(f"📦 {len(all_candidates)} nouvelles URLs à analyser par Gemini...")
    selected_offers = filter_batch_with_gemini(all_candidates, cfg["loc"])
    print(f"🎯 {len(selected_offers)} offre(s) retenue(s) pour la file d'attente.")

    raw_text_by_url = {c["url"].lower(): c["full_text"] for c in all_candidates}

    rows_to_insert = []
    for offer in selected_offers:
        ent = str(offer.get("Entreprise", "Inconnu")).strip()
        poste = str(offer.get("Poste", "Inconnu")).strip()
        url = str(offer.get("url", "")).strip()

        if not ent or ent.lower() == "inconnu" or (ent.lower(), poste.lower()) in existing_pairs:
            continue

        raw_full = raw_text_by_url.get(url.lower(), "")
        best_text = raw_full if len(raw_full) > 250 else offer.get("Resume_Propre", "")

        row_dict = {
            "Date_Ajout": today_str,
            "Entreprise": ent,
            "Poste": poste,
            "Lieu": offer.get("Lieu", cfg["loc"]),
            "Salaire": offer.get("Salaire", "Inconnu"),
            "Lien": url,
            "Score": str(offer.get("Score", "8")),
            "Texte_Complet": best_text,
            "Statut_File": "En attente"
        }

        ordered_row = [row_dict.get(col, "") for col in headers_f3]
        rows_to_insert.append(ordered_row)

        existing_links.add(url.lower())
        existing_pairs.add((ent.lower(), poste.lower()))
        print(f"✅ Mise en file d'attente : {ent} - {poste} ({row_dict['Score']}/10)")

    if rows_to_insert:
        end_row = next_row + len(rows_to_insert) - 1
        range_label = f"A{next_row}:I{end_row}"
        sheet_queue.update(values=rows_to_insert, range_name=range_label, value_input_option="USER_ENTERED")
        print(f"🏁 Terminé ! {len(rows_to_insert)} offre(s) ajoutée(s) dans 'Feuille 3' ({range_label}).")
    else:
        print("🏁 Terminé ! Aucune nouvelle offre à mettre en file d'attente.")


if __name__ == "__main__":
    run_agent()