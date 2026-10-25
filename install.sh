#!/usr/bin/env bash
# =============================================================================
# install.sh - Déploiement automatisé pour RadioTranscribe-Store
# =============================================================================
set -e

# Vérification des privilèges root
if [ "$EUID" -ne 0 ]; then
    echo "[!] Ce script doit être exécuté avec les privilèges root (sudo ./install.sh)"
    exit 1
fi

TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME=$(eval echo "~$TARGET_USER")
PROJECT_DIR=$(pwd)

echo "=== Déploiement de RadioTranscribe-Store pour l'utilisateur : $TARGET_USER ==="

# --- 1. Paquets système ---
echo "[1/7] Installation des dépendances système..."
apt update
apt install -y rtl-sdr librtlsdr-dev ffmpeg mariadb-server curl git build-essential

# --- 2. Configuration du pilote RTL-SDR & Conflit DVB ---
echo "[2/7] Configuration du tuner RTL-SDR et neutralisation du pilote TV..."
cat << 'EOF' > /etc/modprobe.d/blacklist-rtl.conf
# Désactivation des pilotes TV DVB pour libérer la clé pour le mode SDR
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
blacklist dvb_usb_v2
blacklist dvb_core
EOF

# Déchargement à chaud si le pilote noyau est déjà actif
modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true

# Droits d'accès USB pour l'utilisateur
usermod -aG plugdev "$TARGET_USER"

# --- 3. Configuration de MariaDB ---
echo "[3/7] Configuration de la base de données MariaDB..."
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
    frequency VARCHAR(16) DEFAULT '87.8M',
    start_time FLOAT DEFAULT 0.0,
    end_time FLOAT DEFAULT 0.0,
    text TEXT NOT NULL,
    INDEX idx_captured (captured_at)
);
EOF

# --- 4. Dossiers de travail & RAM disk ---
echo "[4/7] Préparation des répertoires d'archivage et de cache..."
sudo -u "$TARGET_USER" mkdir -p "$TARGET_HOME/radio_archive"
sudo -u "$TARGET_USER" mkdir -p "$TARGET_HOME/chroma_data"
mkdir -p /dev/shm/radio_chunks
chmod 777 /dev/shm/radio_chunks

# --- 5. Installation de Miniconda & Environnement Python ---
CONDA_DIR="$TARGET_HOME/miniconda3"
CONDA_BIN="$CONDA_DIR/bin/conda"

if [ ! -f "$CONDA_BIN" ]; then
    echo "[5/7] Téléchargement et installation de Miniconda3..."
    INSTALLER="/tmp/miniconda.sh"
    curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o "$INSTALLER"
    sudo -u "$TARGET_USER" bash "$INSTALLER" -b -p "$CONDA_DIR"
    rm -f "$INSTALLER"
    sudo -u "$TARGET_USER" "$CONDA_BIN" init bash
else
    echo "[5/7] Miniconda est déjà installé."
fi

ENV_NAME="sdr-stt"
ENV_PYTHON="$CONDA_DIR/envs/$ENV_NAME/bin/python"

if [ ! -d "$CONDA_DIR/envs/$ENV_NAME" ]; then
    echo "[+] Création de l'environnement Conda '$ENV_NAME' (Python 3.10)..."
    sudo -u "$TARGET_USER" "$CONDA_BIN" create -n "$ENV_NAME" python=3.10 -y
fi

echo "[+] Installation des paquets Python (requirements.txt)..."
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    sudo -u "$TARGET_USER" "$CONDA_DIR/envs/$ENV_NAME/bin/pip" install --upgrade pip
    sudo -u "$TARGET_USER" "$CONDA_DIR/envs/$ENV_NAME/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
else
    echo "[!] Attention : requirements.txt introuvable dans $PROJECT_DIR"
fi

# --- 6. Création des services systemd ---
echo "[6/7] Création des services systemd..."

# Service ChromaDB (port 8000)
cat << EOF > /etc/systemd/system/chromadb.service
[Unit]
Description=ChromaDB Vector Store Server
After=network.target

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

# Service Pipeline Radio
cat << EOF > /etc/systemd/system/radio-pipeline.service
[Unit]
Description=RadioTranscribe-Store Monitoring Pipeline
After=network.target mariadb.service chromadb.service
Requires=mariadb.service chromadb.service

[Service]
Type=simple
User=$TARGET_USER
WorkingDirectory=$PROJECT_DIR
Environment="DB_USER=$DB_USER"
Environment="DB_PASS=$DB_PASS"
Environment="DB_NAME=$DB_NAME"
ExecStart=$ENV_PYTHON $PROJECT_DIR/radio_pipeline.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable chromadb.service
systemctl enable radio-pipeline.service

# --- 7. Démarrage des briques ---
echo "[7/7] Démarrage du serveur ChromaDB..."
systemctl restart chromadb.service

echo ""
echo "=== Déploiement terminé avec succès ! ==="
echo "Pour démarrer la surveillance radio continue :"
echo "  sudo systemctl start radio-pipeline.service"
echo ""
echo "Pour vérifier les logs en direct :"
echo "  journalctl -u radio-pipeline.service -f"
echo ""
echo "Note : Déconnecte et reconnecte ta session utilisateur (ou redémarre) pour valider l'accès au groupe plugdev (USB SDR)."
