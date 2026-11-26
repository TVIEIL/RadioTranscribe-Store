import os
import re
import readline  # Permet d'utiliser les flèches haut/bas pour l'historique dans le prompt
from datetime import datetime, timedelta
import chromadb

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "radio_news")

# Connexion au serveur ChromaDB local
chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
collection = chroma_client.get_or_create_collection(name=CHROMA_COLLECTION)

DATE_FR_PATTERN = re.compile(r"\b(\d{2})[-/](\d{2})[-/](\d{4})\b")
DATE_ISO_PATTERN = re.compile(r"\b(\d{4})[-/](\d{2})[-/](\d{2})\b")


def extract_date_filter(query: str):
    """Extrait une date (JJ-MM-AAAA, JJ/MM/AAAA ou AAAA-MM-JJ) et construit le filtre ChromaDB."""
    date_iso = None
    cleaned = query

    match_iso = DATE_ISO_PATTERN.search(query)
    match_fr = DATE_FR_PATTERN.search(query)

    if match_iso:
        annee, mois, jour = match_iso.groups()
        date_iso = f"{annee}-{mois}-{jour}"
        cleaned = DATE_ISO_PATTERN.sub("", query)
    elif match_fr:
        jour, mois, annee = match_fr.groups()
        date_iso = f"{annee}-{mois}-{jour}"
        cleaned = DATE_FR_PATTERN.sub("", query)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not date_iso:
        return None, None, cleaned

    try:
        day_start = datetime.strptime(date_iso, "%Y-%m-%d")
        day_end = day_start + timedelta(days=1)
    except ValueError:
        return None, None, query

    where_filter = {
        "$and": [
            {"timestamp": {"$gte": int(day_start.timestamp())}},
            {"timestamp": {"$lt": int(day_end.timestamp())}},
        ]
    }
    return date_iso, where_filter, cleaned


print("=== Moteur de recherche radio (ChromaDB) ===")
print("Tape ta recherche (ex: '01-11-2026 théâtre' ou '2026-11-26') ou 'exit' pour quitter.\n")

while True:
    try:
        raw_query = input("\n[Question / Thème] > ").strip()
        if not raw_query:
            continue
        if raw_query.lower() in ["exit", "quit", "q"]:
            break

        date_iso, where_filter, clean_query = extract_date_filter(raw_query)

        # Cas 1 : l'utilisateur a saisi uniquement une date sans mots-clés
        if date_iso and not clean_query:
            res = collection.get(
                where=where_filter,
                limit=10,
                include=["documents", "metadatas"],
            )
            docs = res["documents"]
            metas = res["metadatas"]

            if not docs:
                print(f"[-] Aucune archive trouvée pour le {date_iso}.")
                continue

            print(f"\n--- Archives du {date_iso} ({len(docs)} affichées) ---")
            for i, (doc, meta) in enumerate(zip(docs, metas), 1):
                date_str = meta.get("captured_at", date_iso)
                print(f"\n[{i}] Capture du {date_str}")
                print(f"    « {doc} »")
            continue

        # Cas 2 : recherche sémantique (avec ou sans filtre de date)
        results = collection.query(
            query_texts=[clean_query],
            n_results=3,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        if not docs:
            info_date = f" le {date_iso}" if date_iso else ""
            print(f"[-] Aucun résultat trouvé pour « {clean_query} »{info_date}.")
            continue

        info_date = f" (Filtre : {date_iso})" if date_iso else ""
        print(f"\n--- Résultats pour : « {clean_query} »{info_date} ---")
        for i, (doc, meta, dist) in enumerate(zip(docs, metas, distances), 1):
            date_str = meta.get("captured_at", "Date inconnue")
            score = 1.0 / (1.0 + dist)

            print(f"\n[{i}] Capture du {date_str} (Pertinence : {score:.0%})")
            print(f"    « {doc} »")

    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"[!] Erreur : {e}")

print("\nFermeture.")
