import re
import readline  # Permet d'utiliser les flèches haut/bas pour l'historique dans le prompt
import chromadb

# Connexion au serveur ChromaDB local
chroma_client = chromadb.HttpClient(host="localhost", port=8000)
collection = chroma_client.get_or_create_collection(name="radio_news")

DATE_PATTERN = re.compile(r"\b(\d{2})[-/](\d{2})[-/](\d{4})\b")

print("=== Moteur de recherche radio (ChromaDB) ===")
print("Tape ta recherche (ex: '21-11-2026 météo') ou 'exit' pour quitter.\n")

while True:
    try:
        raw_query = input("\n[Question / Thème] > ").strip()
        if not raw_query:
            continue
        if raw_query.lower() in ["exit", "quit", "q"]:
            break

        # Détection automatique d'une date JJ-MM-AAAA ou JJ/MM/AAAA
        date_iso = None
        match = DATE_PATTERN.search(raw_query)
        if match:
            jour, mois, annee = match.groups()
            date_iso = f"{annee}-{mois}-{jour}"
            # Retire la date du texte pour garder uniquement les mots-clés sémantiques
            clean_query = re.sub(r"\s+", " ", DATE_PATTERN.sub("", raw_query)).strip()
        else:
            clean_query = raw_query

        where_filter = {"date": date_iso} if date_iso else None

        # Cas 1 : l'utilisateur a saisi uniquement une date sans mots-clés
        if date_iso and not clean_query:
            res = collection.get(
                where=where_filter,
                limit=5,
                include=["documents", "metadatas"],
            )
            docs = res["documents"]
            metas = res["metadatas"]

            if not docs:
                print(f"[-] Aucune archive trouvée pour le {date_iso}.")
                continue

            print(f"\n--- Archives du {date_iso} (5 premières) ---")
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
