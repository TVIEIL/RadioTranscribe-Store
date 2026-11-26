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
RTL-SDR -> RAM Disk (/dev/shm) -> Faster-Whisper -> MariaDB & ChromaDB (HTTP).
"""
import os
import sys
import time
import glob
import signal
import socket
import argparse
import subprocess
from datetime import datetime

import numpy as np
import pymysql
import chromadb
from faster_whisper import WhisperModel

# --- Configuration des stations et seuils ---
STATIONS_CONFIG = {
    "inter": {
        "freq": 87.8e6,
        "alt_freq": None,
        "http_fallback": "http://icecast.radiofrance.fr/franceinter-midfi.mp3",
    },
    "culture": {
        "freq": 93.5e6,       # Tour Eiffel
        "alt_freq": 90.7e6,   # Creil / Nogent (Nord-IDF / Oise)
        "http_fallback": "http://icecast.radiofrance.fr/franceculture-midfi.mp3",
    },
    "info": {
        "freq": 105.5e6,
        "alt_freq": None,
        "http_fallback": "http://icecast.radiofrance.fr/franceinfo-midfi.mp3",
    },
}

SNR_THRESHOLD_DB = 15.0
CRITICAL_FLOOR_DB = 5.0
CHUNK_DURATION_SEC = 30

# Configuration Faster-Whisper (surchargeable via variables d'environnement)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
WHISPER_THREADS = int(os.getenv("WHISPER_THREADS", "16"))

# Configuration MariaDB et ChromaDB (alignée avec install.sh et compute1)
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "radio_user")
DB_PASS = os.getenv("DB_PASS", "votre_mot_de_passe")
DB_NAME = os.getenv("DB_NAME", "radio_monitoring")

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "radio_news")

# Mots/expressions parasites à filtrer
BANNED_WORDS = [
    "sous-titrage", "sous-titres", "transcription",
    "musique", "applaudissements", "rires"
]

# Variables globales de processus pour arrêt propre
capture_proc = None
db_conn = None


def signal_handler(sig, frame):
    """Interception des signaux SIGINT/SIGTERM pour un arrêt sans zombies."""
    print("\n[*] Signal SIGTERM reçu : arrêt ordonné du pipeline...")
    if capture_proc and capture_proc.poll() is None:
        print("[*] Arrêt du sous-processus de capture...")
        try:
            os.killpg(os.getpgid(capture_proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    if db_conn:
        try:
            db_conn.close()
        except Exception:
            pass
    print("[*] Connexions fermées. Arrêt propre terminé.")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def check_internet(host="1.1.1.1", port=53, timeout=2.0) -> bool:
    """Vérifie la connectivité sortante en testant une socket TCP rapide."""
    try:
        socket.setdefaulttimeout(timeout)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
        return True
    except (socket.timeout, OSError):
        return False


def measure_rf_snr(freq_hz: float, sample_rate: int = 250000, duration: float = 0.5) -> float:
    """Démodule une courte capture I/Q et mesure le SNR FM (pilote 19 kHz + audio MPX)."""
    num_samples = int(sample_rate * duration)
    cmd = [
        "rtl_sdr",
        "-f", str(int(freq_hz)),
        "-s", str(sample_rate),
        "-n", str(num_samples * 2),
        "-g", "42",
        "-"
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        raw_bytes, _ = proc.communicate(timeout=3.0)
        if len(raw_bytes) < num_samples * 2:
            return 0.0

        iq = np.frombuffer(raw_bytes, dtype=np.uint8).astype(np.float32) - 127.5
        iq_complex = iq[0::2] + 1j * iq[1::2]

        # Discriminateur FM (insensible aux raies parasites CW non modulées)
        demod = np.angle(iq_complex[1:] * np.conj(iq_complex[:-1]))
        demod -= np.mean(demod)

        mpx_power = np.abs(np.fft.rfft(demod)) ** 2
        freqs = np.fft.rfftfreq(len(demod), d=1.0 / sample_rate)

        # 1. Pilote stéréo 19 kHz vs bande de garde vide
        pilot_mask = (freqs >= 18980) & (freqs <= 19020)
        guard_mask = ((freqs >= 16000) & (freqs <= 18500)) | ((freqs >= 19500) & (freqs <= 22000))
        guard_noise = np.median(mpx_power[guard_mask])
        pilot_snr = 10.0 * np.log10(np.max(mpx_power[pilot_mask]) / guard_noise) if guard_noise > 0 else 0.0

        # 2. Modulation audio L+R (300 Hz - 15 kHz) vs bruit ultrasonore (65 - 95 kHz)
        audio_mask = (freqs >= 300) & (freqs <= 15000)
        ultra_mask = (freqs >= 65000) & (freqs <= 95000)
        ultra_noise = np.mean(mpx_power[ultra_mask])
        audio_snr = 10.0 * np.log10(np.mean(mpx_power[audio_mask]) / ultra_noise) if ultra_noise > 0 else 0.0

        return float(max(0.0, pilot_snr, audio_snr))
    except Exception:
        return 0.0

def resolve_audio_source(station: str):
    """Détermine la meilleure source entre tuner SDR local et flux Icecast HTTP."""
    cfg = STATIONS_CONFIG[station]
    target_freq = cfg["freq"]
    alt_freq = cfg.get("alt_freq")

    print(f"[*] Analyse RF préliminaire pour {station.upper()}...")
    snr_main = measure_rf_snr(target_freq)
    print(f"[*] {target_freq / 1e6:.1f} MHz : SNR = {snr_main:.1f} dB (seuil: {SNR_THRESHOLD_DB:.1f} dB)")

    snr_alt = 0.0
    if snr_main < SNR_THRESHOLD_DB and alt_freq:
        snr_alt = measure_rf_snr(alt_freq)
        print(f"[*] Fréquence alternative {alt_freq / 1e6:.1f} MHz : SNR = {snr_alt:.1f} dB")

    best_freq = target_freq if snr_main >= snr_alt else alt_freq
    best_snr = max(snr_main, snr_alt)

    # Réception RF suffisante
    if best_snr >= SNR_THRESHOLD_DB:
        return "rf", best_freq

    # Signal insuffisant : basculement conditionnel
    print("[*] Signal RF faible. Contrôle de la connexion Internet...")
    if check_internet():
        print("[*] Connexion active : bascule sur le flux HTTP officiel.")
        return "http", cfg["http_fallback"]

    # Mode 100% hors-ligne sans connexion
    print("[!] Absence de connexion Internet : maintien sur la meilleure fréquence RF disponible.")
    if best_snr < CRITICAL_FLOOR_DB:
        print(f"[!] ERREUR CRITIQUE : SNR trop bas ({best_snr:.1f} dB < {CRITICAL_FLOOR_DB} dB) et pas d'Internet.")
        print("[!] Annulation de l'écoute pour éviter de saturer le RAG de transcriptions fantômes.")
        sys.exit(1)

    print(f"[*] Capture hors-ligne maintenue sur {best_freq / 1e6:.1f} MHz (SNR: {best_snr:.1f} dB).")
    return "rf", best_freq


# --- Parser d'arguments ---
parser = argparse.ArgumentParser(description="Pipeline de transcription Radio France")
parser.add_argument("--station", choices=list(STATIONS_CONFIG.keys()), default="inter", help="Station cible (défaut : inter)")
parser.add_argument("--force-http", action="store_true", help="Forcer le flux HTTP sans tester le SDR")
parser.add_argument("--mute", action="store_true", help="Désactiver l'écoute haut-parleur (silence total)")
args = parser.parse_args()

# Cloisonnement étanche de l'espace temporaire en RAM par station
TEMP_DIR = f"/dev/shm/radio_chunks_{args.station}"
ARCHIVE_DIR = os.path.expanduser(f"~/radio_archive/{args.station}")
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# Purge des chunks orphelins résiduels en RAM
for f in glob.glob(f"{TEMP_DIR}/*.wav"):
    try:
        os.remove(f)
    except OSError:
        pass

# Résolution de la source audio
if args.force_http:
    source_type, source_target = "http", STATIONS_CONFIG[args.station]["http_fallback"]
else:
    source_type, source_target = resolve_audio_source(args.station)

# Construction de la commande de capture
if source_type == "rf":
    freq_int = int(source_target)
    freq_label = f"{freq_int / 1e6:.1f}M"  # ex: 87.8M (format historique compute1)
    source_pipe = f"rtl_fm -f {freq_int} -M wfm -s 200k -r 16k -g 42 -E deemp - 2>/dev/null"
else:
    freq_label = f"{args.station.upper()[:3]}-WEB"  # ex: INT-WEB
    source_pipe = f"ffmpeg -y -i '{source_target}' -ac 1 -ar 16000 -f s16le -loglevel quiet -"

# Branche d'écoute permanente tolérante aux pannes ALSA (active par défaut)
if not args.mute:
    monitor_pipe = "tee >( aplay -q -r 16000 -f S16_LE -c 1 || cat >/dev/null ) 2>/dev/null | "
    print("[*] Écoute haut-parleur activée (tolérante aux erreurs ALSA/carte son).")
else:
    monitor_pipe = ""

print(f"[*] Source active : {freq_label}")

# Commande pipeline globale : capture | écoute locale | découpe par blocs de 30 s
segment_pipe = f"ffmpeg -f s16le -ar 16000 -ac 1 -i - -f segment -segment_time {CHUNK_DURATION_SEC} -strftime 1 '{TEMP_DIR}/chunk_%Y%m%d_%H%M%S.wav' -loglevel quiet"
full_capture_cmd = f"{source_pipe} | {monitor_pipe} {segment_pipe}"

# Initialisation du modèle Faster-Whisper
print(f"[*] Chargement du modèle Faster-Whisper {WHISPER_MODEL} ({WHISPER_DEVICE} / {WHISPER_COMPUTE_TYPE})...")
model = WhisperModel(
    WHISPER_MODEL,
    device=WHISPER_DEVICE,
    compute_type=WHISPER_COMPUTE_TYPE,
    cpu_threads=WHISPER_THREADS,
)

# Connexions aux bases de données
print(f"[*] Connexion MariaDB ({DB_NAME}) et ChromaDB ({CHROMA_HOST}:{CHROMA_PORT}/{CHROMA_COLLECTION})...")
db_conn = pymysql.connect(
    host=DB_HOST,
    port=DB_PORT,
    user=DB_USER,
    password=DB_PASS,
    database=DB_NAME,
    autocommit=True,
    cursorclass=pymysql.cursors.DictCursor
)
cursor = db_conn.cursor()

chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
chroma_collection = chroma_client.get_or_create_collection(
    name=CHROMA_COLLECTION,
    metadata={"hnsw:space": "cosine"}
)

# Démarrage du sous-processus de capture en arrière-plan
capture_proc = subprocess.Popen(
    full_capture_cmd,
    shell=True,
    executable="/bin/bash",
    preexec_fn=os.setsid
)

print("[*] Ingestion continue active.")

# Boucle principale de transcription
try:
    while True:
        wav_files = sorted(glob.glob(f"{TEMP_DIR}/chunk_*.wav"))

        # On attend d'avoir au moins deux fichiers pour ne pas lire le bloc en cours d'écriture
        if len(wav_files) <= 1:
            time.sleep(1.0)
            continue

        chunk_path = wav_files[0]
        base_name = os.path.basename(chunk_path)
        timestamp_str = base_name.replace("chunk_", "").replace(".wav", "")

        try:
            timestamp_dt = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            timestamp_unix = int(timestamp_dt.timestamp())
        except ValueError:
            timestamp_dt = datetime.now()
            timestamp_unix = int(timestamp_dt.timestamp())

        t0 = time.time()
        segments_gen, _ = model.transcribe(
            chunk_path,
            language="fr",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            beam_size=5,
            condition_on_previous_text=False,
            repetition_penalty=1.2,
        )

        segments = list(segments_gen)
        captured_segments = []
        db_start_id = None
        db_end_id = None

        if not segments:
            duree = time.time() - t0
            print(f"[-] Bloc {timestamp_dt.strftime('%Y-%m-%d %H:%M:%S')} ignoré (musique/silence) en {duree:.2f}s")
            try:
                os.remove(chunk_path)
            except OSError:
                pass
            continue

        for s in segments:
            text = s.text.strip()
            clean_lower = text.lower()

            # Filtrage VAD / silence / résidus
            if s.no_speech_prob > 0.6:
                continue
            if not text or text in ["...", "…", ".", "-"]:
                continue
            if any(w in clean_lower for w in BANNED_WORDS):
                continue

            # Insertion stricte dans MariaDB
            sql = """
            INSERT INTO transcriptions (frequency, start_time, end_time, text)
            VALUES (%s, %s, %s, %s)
            """
            cursor.execute(sql, (freq_label, s.start, s.end, text))
            inserted_id = cursor.lastrowid
            if db_start_id is None:
                db_start_id = inserted_id
            db_end_id = inserted_id

            captured_segments.append(text)
            print(f" [SQL #{inserted_id}] {text}")

        duree = time.time() - t0

        # Indexation vectorielle dans ChromaDB
        if captured_segments:
            aggregated_text = " ".join(captured_segments)
            chunk_uid = f"{args.station}_{timestamp_unix}"
            chroma_collection.add(
                documents=[aggregated_text],
                metadatas=[{
                    "frequency": freq_label,
                    "timestamp": timestamp_unix,
                    "captured_at": timestamp_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "db_start_id": db_start_id,
                    "db_end_id": db_end_id,
                }],
                ids=[chunk_uid]
            )
            print(f"[ChromaDB] Bloc {timestamp_dt.strftime('%Y-%m-%d %H:%M:%S')} indexé en {duree:.2f}s ({len(captured_segments)} segments) -> ID: {chunk_uid}")

        # Nettoyage du fichier analysé
        try:
            os.remove(chunk_path)
        except OSError:
            pass

except Exception as e:
    print(f"\n[!] Erreur inattendue dans la boucle d'ingestion : {e}")
finally:
    signal_handler(signal.SIGTERM, None)
