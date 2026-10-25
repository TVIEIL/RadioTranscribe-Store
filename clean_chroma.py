# Copyright (c) 2026 Thierry VIEIL
# SPDX-License-Identifier: MIT
"""
clean_chroma.py - Nettoyage et purge des hallucinations dans la base ChromaDB.
"""
import os
import chromadb

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "radio_news")

# Motifs d'artefacts et hallucinations récurrentes de Whisper à supprimer
BANNED_PATTERNS = [
    "jérémy diaz",
    "jeremy diaz",
    "société radio-canada",
    "radio-canada",
    "st' 501",
    "st 501",
    "sous-titr",
    "regardé cette vidéo",
    "visionné cette vidéo",
    "merci d'avoir regardé",
    "abonnez-vous",
    "pouce bleu",
    "tipeurs",
    "souscripteurs",
]

def main():
    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    collection = client.get_collection(name=CHROMA_COLLECTION)

    data = collection.get(include=["documents"])
    ids_to_delete = []

    print("=== Recherche des artefacts dans ChromaDB ===")
    for doc_id, doc_text in zip(data["ids"], data["documents"]):
        text_lower = doc_text.lower()
        if any(pattern in text_lower for pattern in BANNED_PATTERNS):
            print(f"[-] À supprimer ({doc_id}) : « {doc_text} »")
            ids_to_delete.append(doc_id)

    if ids_to_delete:
        collection.delete(ids=ids_to_delete)
        print(f"\n[+] {len(ids_to_delete)} bloc(s) supprimé(s) avec succès de ChromaDB.")
    else:
        print("\n[+] Aucun artefact résiduel trouvé dans ChromaDB.")

if __name__ == "__main__":
    main()
