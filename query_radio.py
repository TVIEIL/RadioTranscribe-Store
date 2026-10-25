import readline  # Permet d'utiliser les flèches haut/bas pour l'historique dans le prompt
import chromadb

# Connexion au serveur ChromaDB local
chroma_client = chromadb.HttpClient(host="localhost", port=8000)
collection = chroma_client.get_or_create_collection(name="radio_news")

print("=== Moteur de recherche radio (ChromaDB) ===")
print("Tape ta recherche ou 'exit' pour quitter.\n")

while True:
    try:
        query = input("\n[Question / Thème] > ").strip()
        if not query:
            continue
        if query.lower() in ["exit", "quit", "q"]:
            break

        # Recherche des 3 blocs les plus proches sémantiquement
        results = collection.query(
            query_texts=[query],
            n_results=3,
            include=["documents", "metadatas", "distances"],
        )

        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        if not docs:
            print("[-] Aucun résultat trouvé.")
            continue

        print(f"\n--- Résultats pour : « {query} » ---")
        for i, (doc, meta, dist) in enumerate(zip(docs, metas, distances), 1):
            date_str = meta.get("captured_at", "Date inconnue")
            # Normalisation de la distance L2 en score lisible
            score = 1.0 / (1.0 + dist)

            print(f"\n[{i}] Capture du {date_str} (Pertinence : {score:.0%})")
            print(f"    « {doc} »")

    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"[!] Erreur : {e}")

print("\nFermeture.")
