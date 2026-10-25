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

"""
radio_pipeline.py - Pipeline continu de veille et transcription radio.
RTL-SDR -> RAM Disk (/dev/shm) -> Faster-Whisper -> MariaDB & ChromaDB.
"""
from datetime import datetime
import glob
import os
from pathlib import Path
import shutil
import subprocess
import time
import chromadb
from faster_whisper import WhisperModel
import pymysql

# --- Paramètres de capture et chemins ---
FREQ = os.getenv("RADIO_FREQ", "87.8M")
CHUNK_DURATION = int(os.getenv("RADIO_CHUNK_DURATION", "30"))
TEMP_DIR = os.getenv("RADIO_TEMP_DIR", "/dev/shm/radio_chunks")
ARCHIVE_DIR = os.path.expanduser(os.getenv("RADIO_ARCHIVE_DIR", "~/radio_archive"))

# --- Configuration MariaDB ---
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "radio_user"),
    "password": os.getenv("DB_PASS", "votre_mot_de_passe"),
    "database": os.getenv("DB_NAME", "radio_monitoring"),
    "autocommit": True,
}

# --- Configuration ChromaDB ---
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "radio_news")

# --- Mots-clés d'artefacts et hallucinations à filtrer ---
BANNED_WORDS = [
    "sous-titr",
    "jérémy diaz",
    "jeremy diaz",
    "radio-canada",
    "société radio-canada",
    "st' 501",
    "st 501",
    "regardé cette vidéo",
    "visionné cette vidéo",
    "merci d'avoir regardé",
    "abonnez-vous",
    "pouce bleu",
    "tipeurs",
    "souscripteurs",
]

# Initialisation des dossiers
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# Purge des segments audio résiduels en RAM
for f in glob.glob(f"{TEMP_DIR}/*.wav"):
    try:
        os.remove(f)
    except OSError:
        pass

print("[*] Chargement du modèle Faster-Whisper large-v3...")
model = WhisperModel(
    "large-v3", device="cpu", compute_type="int8", cpu_threads=16
)

print("[*] Connexion aux bases de données...")
db = pymysql.connect(**DB_CONFIG)
cursor = db.cursor()

chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
collection = chroma_client.get_or_create_collection(name=CHROMA_COLLECTION)

print(f"[*] Démarrage de la capture continue sur {FREQ}...")
capture_cmd = (
    f"rtl_fm -f {FREQ} -M wfm -s 200k -r 16k - 2>/dev/null | "
    f"ffmpeg -y -f s16le -ar 16000 -ac 1 -i - "
    f"-f segment -segment_time {CHUNK_DURATION} -strftime 1 "
    f'"{TEMP_DIR}/chunk_%Y%m%d_%H%M%S.wav" -loglevel quiet'
)
capture_process = subprocess.Popen(capture_cmd, shell=True)
print("[*] Ingestion continue active.\n")

try:
    while True:
        wav_files = sorted(glob.glob(f"{TEMP_DIR}/chunk_*.wav"))

        # On ne traite que lorsqu'un nouveau segment a commencé à s'écrire
        if len(wav_files) > 1:
            file_to_process = wav_files[0]

            # 1. Élimination des fichiers tronqués (< 100 Ko)
            try:
                if os.path.getsize(file_to_process) < 100000:
                    os.remove(file_to_process)
                    continue
            except OSError:
                continue

            filename = Path(file_to_process).stem
            time_part = filename.replace("chunk_", "")
            try:
                dt = datetime.strptime(time_part, "%Y%m%d_%H%M%S")
                timestamp_unix = int(dt.timestamp())
                timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                timestamp_unix = int(time.time())
                timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

            t0 = time.time()

            try:
                # 2. Transcription Whisper avec VAD et répétition contrôlée
                segments, _ = model.transcribe(
                    file_to_process,
                    language="fr",
                    beam_size=5,
                    condition_on_previous_text=False,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500),
                    repetition_penalty=1.2,
                )

                captured_segments = []
                first_id = None
                last_id = None

                for s in segments:
                    text = s.text.strip()
                    clean_lower = text.lower()

                    # Rejet musique / silence
                    if s.no_speech_prob > 0.6:
                        continue

                    # Rejet ponctuation vide
                    if not text or text in ["...", "…", ".", "-"]:
                        continue

                    # Rejet des hallucinations connues
                    if any(w in clean_lower for w in BANNED_WORDS):
                        continue

                    # Insertion MariaDB
                    sql = """
                        INSERT INTO transcriptions (frequency, start_time, end_time, text)
                        VALUES (%s, %s, %s, %s)
                    """
                    cursor.execute(sql, (FREQ, s.start, s.end, text))
                    inserted_id = cursor.lastrowid
                    if first_id is None:
                        first_id = inserted_id
                    last_id = inserted_id
                    captured_segments.append(text)
                    print(f"  [SQL #{inserted_id}] {text}")

                # 3. Indexation ChromaDB du bloc agrégé
                duree = time.time() - t0
                if captured_segments:
                    aggregated_text = " ".join(captured_segments)
                    chunk_uid = f"chunk_{timestamp_unix}"

                    collection.add(
                        documents=[aggregated_text],
                        metadatas=[
                            {
                                "frequency": FREQ,
                                "captured_at": timestamp_str,
                                "timestamp": timestamp_unix,
                                "db_start_id": first_id,
                                "db_end_id": last_id,
                            }
                        ],
                        ids=[chunk_uid],
                    )
                    print(
                        f"[ChromaDB] Bloc {timestamp_str} indexé en {duree:.2f}s "
                        f"({len(captured_segments)} segments) -> ID: {chunk_uid}\n"
                    )
                else:
                    print(
                        f"[-] Bloc {timestamp_str} ignoré (musique/silence) en {duree:.2f}s\n"
                    )

            except Exception as e:
                print(f"[!] Erreur de traitement sur {file_to_process} : {e}")

            finally:
                # 4. Archivage ou suppression du fichier audio traité
                dest_archive = os.path.join(ARCHIVE_DIR, Path(file_to_process).name)
                try:
                    shutil.move(file_to_process, dest_archive)
                except OSError:
                    try:
                        os.remove(file_to_process)
                    except OSError:
                        pass

        time.sleep(1)

except KeyboardInterrupt:
    print("\nArrêt du pipeline...")
    capture_process.terminate()
    capture_process.wait()
finally:
    cursor.close()
    db.close()
