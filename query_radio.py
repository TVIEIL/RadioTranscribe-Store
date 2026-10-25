# Copyright (c) 2026 Thierry VIEIL
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

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
