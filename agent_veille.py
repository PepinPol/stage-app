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
SPREADSHEET_URL = os.environ["SPREADSHEET_URL"]
GSHEETS_CREDS = json.loads(os.environ["GSHEETS_CREDENTIALS_JSON"])

genai.configure(api_key=GEMINI_API_KEY)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(GSHEETS_CREDS, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_url(SPREADSHEET_URL)

# Premier onglet tout à gauche (lu par défaut par Streamlit)
sheet_offres = sh.get_worksheet(0)

MODELS_FALLBACK = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash"
]

# Les 4 critères conservés (4 postes x 3 sites = 12 crédits Tavily max par jour)
TARGET_CRITERIA = [
    {"job": "product manager", "loc": "Paris"},
    {"job": "product owner", "loc": "Paris"},
    {"job": "chef de projet digital", "loc": "Paris"},
    {"job": "product analyst", "loc": "Paris"}
]


def ask_gemini_fast(prompt: str) -> str:
    last_err = None
    for model_name in MODELS_FALLBACK:
        try:
            model = genai.GenerativeModel(model_name)
            resp = model.generate_content(
                prompt,
                request_options={"retry": None, "timeout": 30}
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


def get_existing_offers() -> tuple[set, set]:
    records = sheet_offres.get_all_records()
    existing_links = set()
    existing_pairs = set()
    for r in records:
        lien = str(r.get("Lien", "")).strip().lower()
        if lien:
            existing_links.add(lien)
        ent = str(r.get("Entreprise", "")).strip().lower()
        poste = str(r.get("Poste", "")).strip().lower()
        if ent and poste:
            existing_pairs.add((ent, poste))
    return existing_links, existing_pairs


def search_jobs_tavily(job: str, loc: str) -> list[dict]:
    queries = [
        f'site:linkedin.com/jobs/view "stage" "{job}" "{loc}"',
        f'site:welcometothejungle.com/fr/companies "stage" "{job}" "{loc}"',
        f'site:hellowork.com/fr-fr/emplois "stage" "{job}" "{loc}"'
    ]
    results = []
    seen_urls = set()

    for q in queries:
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": q,
                    "search_depth": "basic",
                    "max_results": 4,
                    "days": 4
                },
                timeout=15
            )
            data = resp.json()
            for item in data.get("results", []):
                url = item.get("url", "").strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append({
                        "title": item.get("title", ""),
                        "url": url,
                        "content": item.get("content", "")[:600]
                    })
        except Exception as e:
            print(f"Erreur Tavily ({q}) : {e}")
    return results


def filter_batch_with_gemini(offers: list[dict]) -> list[dict]:
    if not offers:
        return []

    prompt = f"""
    Tu es un assistant de recrutement pour Pol Cartron (étudiant double diplôme Ingénieur INSA Rennes / Management Audencia).
    Il cherche un STAGE de fin d'études de 6 mois en Product Management (PM), Product Ownership (PO), Gestion de Projet Digital ou Product Analysis, à Paris / Île-de-France, pour janvier/février 2027.

    Analyse la liste de résultats web ci-dessous et ne garde QUE les vraies offres de stage individuelles pertinentes :
    - Exclure les pages de listes/annuaires, les CDI, les alternances, les postes senior/confirmé (ex: "First Product Manager" ou "Platform PM" sans mention de stage).
    - Ne garde qu'une seule offre par entreprise si plusieurs annonces sont quasi-identiques.

    Liste des résultats :
    {json.dumps(offers, ensure_ascii=False)}

    Retourne UNIQUEMENT un tableau JSON valide contenant les offres retenues (score >= 7/10) avec exactement ces clés :
    - "url": l'URL exacte de l'offre
    - "Entreprise": nom de l'entreprise
    - "Poste": intitulé exact du poste
    - "Lieu": ville (ex: "Paris")
    - "Salaire": gratification si mentionnée (sinon "Inconnu")
    - "Resume_Offre": résumé clair de l'offre en 3-4 lignes
    Si aucune offre n'est pertinente, retourne [].
    """
    try:
        res = parse_json_response(ask_gemini_fast(prompt))
        return res if isinstance(res, list) else []
    except Exception as e:
        print(f"Erreur batch Gemini : {e}")
        return []


def run_agent():
    print(f"🚀 Démarrage de l'agent sur la feuille : '{sheet_offres.title}'...")
    existing_links, existing_pairs = get_existing_offers()
    today_str = datetime.now().strftime("%d/%m/%Y")
    headers = sheet_offres.row_values(1)

    all_candidates = []
    seen_batch_urls = set()

    for crit in TARGET_CRITERIA:
        print(f"🔍 Recherche : {crit['job']} à {crit['loc']}")
        for item in search_jobs_tavily(crit["job"], crit["loc"]):
            u = item["url"].lower()
            if u not in existing_links and u not in seen_batch_urls:
                seen_batch_urls.add(u)
                all_candidates.append(item)

    print(f"📦 {len(all_candidates)} nouvelles URLs à analyser par Gemini...")
    selected_offers = filter_batch_with_gemini(all_candidates)
    print(f"🎯 {len(selected_offers)} offre(s) retenue(s) par Gemini.")

    added_count = 0
    for offer in selected_offers:
        ent = str(offer.get("Entreprise", "Inconnu")).strip()
        poste = str(offer.get("Poste", "Inconnu")).strip()
        url = str(offer.get("url", "")).strip()

        if not ent or ent.lower() == "inconnu" or (ent.lower(), poste.lower()) in existing_pairs:
            continue

        row_dict = {
            "Entreprise": ent,
            "Poste": poste,
            "Lien": url,
            "Lieu": offer.get("Lieu", "Paris"),
            "Salaire": offer.get("Salaire", "Inconnu"),
            "Date": today_str,
            "Statut": "À postuler",
            "Historique": json.dumps([{"date": today_str, "etape": "Trouvée par l'Agent IA"}], ensure_ascii=False),
            "Texte Offre": offer.get("Resume_Offre", ""),
            "Résumé IA": "",
            "Entretien IA": "",
            "Lettre de Motivation": ""
        }

        ordered_row = [row_dict.get(col, "") for col in headers]
        sheet_offres.append_row(ordered_row, value_input_option="USER_ENTERED")

        existing_links.add(url.lower())
        existing_pairs.add((ent.lower(), poste.lower()))
        added_count += 1
        print(f"✅ Ajoutée : {ent} - {poste}")

    print(f"🏁 Terminé ! {added_count} nouvelle(s) offre(s) ajoutée(s) dans '{sheet_offres.title}'.")


if __name__ == "__main__":
    run_agent()