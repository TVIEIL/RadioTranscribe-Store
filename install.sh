#!/usr/bin/env bash
# =============================================================================
# install.sh - Déploiement automatisé pour RadioTranscribe-Store (CPU / GPU CUDA)
# =============================================================================
set -e

if [ "$EUID" -ne 0 ]; then
    echo "[!] Ce script doit être exécuté avec les privilèges root (sudo ./install.sh)"
    exit 1
fi

TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME=$(eval echo "~$TARGET_USER")
PROJECT_DIR=$(pwd)

echo "=== Déploiement de RadioTranscribe-Store pour : $TARGET_USER ==="

# --- 1. Dépendances système ---
echo "[1/8] Installation des paquets requis..."
apt update
apt install -y rtl-sdr librtlsdr-dev ffmpeg alsa-utils mariadb-server curl git build-essential cron

# --- 2. Neutralisation du pilote TV DVB & droits matériel ---
echo "[2/8] Configuration de la clé RTL-SDR et de l'audio..."
cat << 'EOF' > /etc/modprobe.d/blacklist-rtl.conf
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
blacklist dvb_usb_v2
blacklist dvb_core
EOF

modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
usermod -aG plugdev,audio "$TARGET_USER"

# --- 3. MariaDB ---
echo "[3/8] Initialisation de la base MariaDB..."
systemctl enable --now mariadb

DB_USER="radio_user"
DB_PASS="votre_mot_de_passe"
DB_NAME="radio_monitoring"

mariadb -u root << EOF
CREATE DATABASE IF NOT EXISTS ${DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON ${DB_NAME}.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;

USE ${DB_NAME};
CREATE TABLE IF NOT EXISTS transcriptions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    frequency VARCHAR(32) DEFAULT '87.8M',
    start_time FLOAT DEFAULT 0.0,
    end_time FLOAT DEFAULT 0.0,
    text TEXT NOT NULL,
    INDEX idx_captured (captured_at)
);
EOF

# --- 4. Répertoires de travail & RAM disk ---
echo "[4/8] Création des dossiers..."
sudo -u "$TARGET_USER" mkdir -p "$TARGET_HOME/radio_archive"
sudo -u "$TARGET_USER" mkdir -p "$TARGET_HOME/chroma_data"
mkdir -p /dev/shm/radio_chunks
chmod 777 /dev/shm/radio_chunks

# --- 5. Miniconda, Détection GPU & Dépendances Python ---
CONDA_DIR="$TARGET_HOME/miniconda3"
CONDA_BIN="$CONDA_DIR/bin/conda"

if [ ! -f "$CONDA_BIN" ]; then
    echo "[5/8] Installation de Miniconda3..."
    INSTALLER="/tmp/miniconda.sh"
    curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o "$INSTALLER"
    sudo -u "$TARGET_USER" bash "$INSTALLER" -b -p "$CONDA_DIR"
    rm -f "$INSTALLER"
    sudo -u "$TARGET_USER" "$CONDA_BIN" init bash
else
    echo "[5/8] Miniconda3 déjà en place."
fi

ENV_NAME="sdr-stt"
ENV_PYTHON="$CONDA_DIR/envs/$ENV_NAME/bin/python"
ENV_PIP="$CONDA_DIR/envs/$ENV_NAME/bin/pip"

if [ ! -d "$CONDA_DIR/envs/$ENV_NAME" ]; then
    echo "[+] Création de l'environnement Conda '$ENV_NAME'..."
    sudo -u "$TARGET_USER" "$CONDA_BIN" create -n "$ENV_NAME" python=3.10 -y
fi

if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    echo "[+] Installation des paquets Python..."
    sudo -u "$TARGET_USER" "$ENV_PIP" install --upgrade pip
    sudo -u "$TARGET_USER" "$ENV_PIP" install -r "$PROJECT_DIR/requirements.txt"
fi

# Détection automatique d'un GPU NVIDIA pour activer CUDA / float16
if command -v nvidia-smi &> /dev/null && nvidia-smi &> /dev/null; then
    echo "[+] GPU NVIDIA détecté : installation des bibliothèques CUDA/cuDNN..."
    sudo -u "$TARGET_USER" "$ENV_PIP" install nvidia-cublas-cu12 nvidia-cudnn-cu12
    SITE_PACKAGES=$("$ENV_PYTHON" -c "import site; print(site.getsitepackages()[0])")
    CUDA_ENV_LINE="Environment=\"LD_LIBRARY_PATH=$SITE_PACKAGES/nvidia/cublas/lib:$SITE_PACKAGES/nvidia/cudnn/lib\""
    WHISPER_DEVICE="cuda"
    WHISPER_COMPUTE_TYPE="float16"
else
    echo "[*] Aucun GPU NVIDIA détecté : configuration en mode CPU (int8)..."
    CUDA_ENV_LINE="# Pas de bibliothèque CUDA requise en mode CPU"
    WHISPER_DEVICE="cpu"
    WHISPER_COMPUTE_TYPE="int8"
fi

# Pré-téléchargement du modèle dans le cache utilisateur avant activation du mode hors-ligne
echo "[+] Pré-téléchargement du modèle Whisper (large-v3) dans le cache local..."
sudo -u "$TARGET_USER" "$ENV_PYTHON" -c "from faster_whisper import download_model; download_model('large-v3')"

# --- 6. Services Systemd (Template @) ---
echo "[6/8] Génération des unités systemd ($WHISPER_DEVICE / $WHISPER_COMPUTE_TYPE)..."

# Serveur vectoriel ChromaDB
cat << EOF > /etc/systemd/system/chromadb.service
[Unit]
Description=ChromaDB Vector Store Server
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$TARGET_USER
WorkingDirectory=$TARGET_HOME
ExecStart=$CONDA_DIR/envs/$ENV_NAME/bin/chroma run --path $TARGET_HOME/chroma_data --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Template de service radio paramétrable par station (%i = inter, culture, info)
cat << EOF > /etc/systemd/system/radio-pipeline@.service
[Unit]
Description=Pipeline Transcription Radio - %i
Wants=network-online.target
After=network-online.target sound.target mariadb.service chromadb.service
Requires=mariadb.service chromadb.service

[Service]
Type=simple
User=$TARGET_USER
WorkingDirectory=$PROJECT_DIR
Environment="PYTHONUNBUFFERED=1"
Environment="DB_USER=$DB_USER"
Environment="DB_PASS=$DB_PASS"
Environment="DB_NAME=$DB_NAME"
Environment="WHISPER_MODEL=large-v3"
Environment="HF_HUB_OFFLINE=1"
Environment="WHISPER_DEVICE=$WHISPER_DEVICE"
Environment="WHISPER_COMPUTE_TYPE=$WHISPER_COMPUTE_TYPE"
Environment="WHISPER_THREADS=16"
$CUDA_ENV_LINE
ExecStart=$ENV_PYTHON $PROJECT_DIR/radio_pipeline.py --station %i
KillSignal=SIGTERM
TimeoutStopSec=35s
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable chromadb.service

# --- 7. Planification Cron ciblée ---
echo "[7/8] Configuration des créneaux Cron..."
CRON_FILE="/etc/cron.d/radio-pipeline"

cat << 'EOF' > "$CRON_FILE"
# =============================================================================
# Grille d'enregistrement ciblée Radio France
# Syntaxe : m h dom mon dow user commande
# =============================================================================
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin

# --- FRANCE INTER : Matinale / Journal de 8h (07h00 -> 09h00 du lundi au vendredi) ---
0 7 * * 1-5 root /bin/systemctl stop radio-pipeline@culture.service; /bin/systemctl start radio-pipeline@inter.service
0 9 * * 1-5 root /bin/systemctl stop radio-pipeline@inter.service

# --- FRANCE INTER : Journal de 13h (12h58 -> 13h35 tous les jours) ---
58 12 * * * root /bin/systemctl stop radio-pipeline@culture.service; /bin/systemctl start radio-pipeline@inter.service
35 13 * * * root /bin/systemctl stop radio-pipeline@inter.service

# --- FRANCE CULTURE : Créneau documentaire / débat (16h00 -> 18h00 du lundi au vendredi) ---
0 16 * * 1-5 root /bin/systemctl stop radio-pipeline@inter.service; /bin/systemctl start radio-pipeline@culture.service
0 18 * * 1-5 root /bin/systemctl stop radio-pipeline@culture.service
EOF

chmod 644 "$CRON_FILE"
systemctl enable --now cron

# --- 8. Démarrage ChromaDB ---
echo "[8/8] Démarrage du serveur vectoriel ChromaDB..."
systemctl restart chromadb.service

echo ""
echo "=== Déploiement terminé (Mode Whisper : $WHISPER_DEVICE / $WHISPER_COMPUTE_TYPE) ! ==="
echo "Les rendez-vous d'enregistrement sont inscrits dans : $CRON_FILE"
echo ""
echo "Pour tester immédiatement une station hors créneau :"
echo "  sudo systemctl start radio-pipeline@inter.service"
echo "  sudo systemctl start radio-pipeline@culture.service"
echo ""
echo "Pour inspecter le flux :"
echo "  journalctl -u radio-pipeline@inter.service -f -o cat"
