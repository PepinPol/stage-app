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

sheet_offres = sh.sheet1
sheet_presets = sh.worksheet("Feuille 2")

MODELS_FALLBACK = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite"
]

def ask_gemini(prompt: str) -> str:
    for model_name in MODELS_FALLBACK:
        try:
            resp = genai.GenerativeModel(model_name).generate_content(prompt)
            if resp and resp.text:
                return resp.text
        except Exception as e:
            print(f"Modèle {model_name} indisponible : {e}")
    raise RuntimeError("Aucun modèle Gemini n'a répondu.")

def parse_json_response(raw_text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    return json.loads(cleaned)

# ==========================================
# 2. LECTURE DES CRITÈRES & DOUBLONS
# ==========================================
def get_existing_offers() -> tuple[set, set]:
    """Récupère les liens et couples (entreprise, poste) déjà dans le tracker pour éviter les doublons."""
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

def get_search_criteria() -> list[dict]:
    """Lit les recherches sauvegardées dans Feuille 2."""
    records = sheet_presets.get_all_records()
    criteria = []
    for r in records:
        poste = str(r.get("Poste", "")).strip()
        lieu = str(r.get("Lieu", "")).strip()
        if poste:
            criteria.append({"job": poste, "loc": lieu or "Paris"})
    return criteria or [{"job": "product manager", "loc": "Paris"}, {"job": "product owner", "loc": "Paris"}]

# ==========================================
# 3. RECHERCHE WEB MULTI-SOURCES (TAVILY)
# ==========================================
def search_jobs_tavily(job: str, loc: str) -> list[dict]:
    """Interroge Tavily sur LinkedIn, WTTJ, HelloWork et sites carrières (offres des 3 derniers jours)."""
    queries = [
        f'site:linkedin.com/jobs/view "stage" "{job}" "{loc}"',
        f'site:welcometothejungle.com/fr/companies "stage" "{job}" "{loc}"',
        f'site:hellowork.com/fr-fr/emplois "stage" "{job}" "{loc}"',
        f'"offre de stage" "{job}" "{loc}" 2027'
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
                    "search_depth": "advanced",
                    "max_results": 5,
                    "days": 3  # Uniquement les pages indexées sur les 3 derniers jours
                },
                timeout=20
            )
            data = resp.json()
            for item in data.get("results", []):
                url = item.get("url", "").strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append({
                        "title": item.get("title", ""),
                        "url": url,
                        "content": item.get("content", "")
                    })
        except Exception as e:
            print(f"Erreur recherche Tavily ({q}) : {e}")
    return results

# ==========================================
# 4. FILTRAGE & EXTRACTION PAR GEMINI
# ==========================================
def evaluate_and_extract(offer: dict) -> dict | None:
    """Demande à Gemini si l'offre correspond au profil de Pol et extrait les colonnes du Sheet."""
    prompt = f"""
    Tu es un assistant de recrutement pour Pol Cartron (étudiant en double diplôme Ingénieur INSA Rennes / Management Audencia).
    Il cherche un STAGE DE FIN D'ÉTUDES de 6 mois en Product Management (PM) ou Product Ownership (PO), à Paris / Île-de-France, idéalement pour janvier/février 2027 (ou fin 2026 / début 2027).

    Analyse ce résultat web et détermine s'il s'agit d'une VRAIE offre de stage précise (pas une page de liste générale, pas un CDI, pas une alternance).
    
    Titre : {offer['title']}
    URL : {offer['url']}
    Contenu : {offer['content']}

    Réponds UNIQUEMENT avec un JSON valide contenant exactement ces clés :
    - "pertinent": true ou false
    - "score": note de 1 à 10 sur l'adéquation avec le profil
    - "Entreprise": nom de l'entreprise (ou "Inconnu")
    - "Poste": intitulé exact du poste
    - "Lieu": ville (ex: "Paris")
    - "Salaire": gratification si mentionnée (sinon "Inconnu")
    - "Resume_Offre": résumé propre de l'offre en 4-5 lignes basé sur le contenu disponible
    """
    try:
        parsed = parse_json_response(ask_gemini(prompt))
        if parsed.get("pertinent") is True and int(parsed.get("score", 0)) >= 7:
            return parsed
    except Exception as e:
        print(f"Erreur parsing Gemini pour {offer['url']} : {e}")
    return None

# ==========================================
# 5. EXÉCUTION PRINCIPALE
# ==========================================
def run_agent():
    print("🚀 Démarrage de l'agent de veille...")
    existing_links, existing_pairs = get_existing_offers()
    criteria_list = get_search_criteria()
    today_str = datetime.now().strftime("%d/%m/%Y")

    # Vérifie l'ordre exact des colonnes de Feuille 1
    headers = sheet_offres.row_values(1)
    added_count = 0

    for crit in criteria_list:
        print(f"🔍 Recherche : {crit['job']} à {crit['loc']}")
        raw_offers = search_jobs_tavily(crit["job"], crit["loc"])

        for raw in raw_offers:
            if raw["url"].lower() in existing_links:
                continue

            extracted = evaluate_and_extract(raw)
            if not extracted:
                continue

            ent = extracted.get("Entreprise", "Inconnu").strip()
            poste = extracted.get("Poste", "Inconnu").strip()
            if ent.lower() == "inconnu" or (ent.lower(), poste.lower()) in existing_pairs:
                continue

            # Prépare la nouvelle ligne alignée avec les en-têtes du Sheet
            row_dict = {
                "Entreprise": ent,
                "Poste": poste,
                "Lien": raw["url"],
                "Lieu": extracted.get("Lieu", crit["loc"]),
                "Salaire": extracted.get("Salaire", "Inconnu"),
                "Date": today_str,
                "Statut": "À postuler",
                "Historique": json.dumps([{"date": today_str, "etape": "Trouvée par l'Agent IA"}], ensure_ascii=False),
                "Texte Offre": f"{extracted.get('Resume_Offre', '')}\n\nSource : {raw['content']}",
                "Résumé IA": "",
                "Entretien IA": "",
                "Lettre de Motivation": ""
            }

            ordered_row = [row_dict.get(col, "") for col in headers]
            sheet_offres.append_row(ordered_row, value_input_option="USER_ENTERED")

            existing_links.add(raw["url"].lower())
            existing_pairs.add((ent.lower(), poste.lower()))
            added_count += 1
            print(f"✅ Ajoutée : {ent} - {poste}")

    print(f"🏁 Terminé ! {added_count} nouvelle(s) offre(s) ajoutée(s).")

if __name__ == "__main__":
    run_agent()