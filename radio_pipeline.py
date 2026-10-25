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

# --- Configuration ---
FREQ = "87.8M"
CHUNK_DURATION = 30
TEMP_DIR = "/dev/shm/radio_chunks"
ARCHIVE_DIR = "/home/vieil/radio_archive"

DB_CONFIG = {
    "host": "localhost",
    "user": "compute1",
    "password": "G2LAMaria!",
    "database": "radio_monitoring",
    "autocommit": True,
}

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# Purge initiale de la RAM
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

chroma_client = chromadb.HttpClient(host="localhost", port=8000)
collection = chroma_client.get_or_create_collection(name="radio_news")

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

        # On ne traite que lorsqu'un second fichier est en cours d'écriture
        if len(wav_files) > 1:
            file_to_process = wav_files[0]

            # 1. Élimination immédiate des fichiers tronqués
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
                # 2. Transcription sans boucle infinie et avec VAD
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

                    # 1. Rejet des silences / musiques selon Whisper
                    if s.no_speech_prob > 0.6:
                        continue

                    # 2. Rejet des ponctuations seules
                    if not text or text in ["...", "…", ".", "-"]:
                        continue

                    # 3. Rejet des hallucinations de sous-titrage connues
                    banned_words = [
                        "sous-titr",
                        "jérémy diaz",
                        "jeremy diaz",
                        "radio-canada",
                        "regardé cette vidéo",
                        "merci d'avoir regardé",
                        "visionné cette vidéo",
                        "abonnez-vous",
                        "pouce bleu",
                    ]

                    if any(w in clean_lower for w in banned_words):
                        continue


            
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

                # 3. Indexation ChromaDB ou affichage du rejet
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
                # 4. Déplacement inconditionnel : la file avance quoiqu'il arrive
                dest_archive = os.path.join(
                    ARCHIVE_DIR, Path(file_to_process).name
                )
                try:
                    shutil.move(file_to_process, dest_archive)
                except OSError:
                    try:
                        os.remove(file_to_process)
                    except OSError:
                        pass

        time.sleep(1)

except KeyboardInterrupt:
    print("\nArrêt demandé...")
    capture_process.terminate()
    capture_process.wait()
finally:
    cursor.close()
    db.close()
