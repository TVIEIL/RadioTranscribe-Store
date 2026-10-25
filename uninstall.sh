#!/usr/bin/env bash
# =============================================================================
# uninstall.sh - Désinstallation propre de RadioTranscribe-Store
# =============================================================================
set -e

# Vérification des privilèges root
if [ "$EUID" -ne 0 ]; then
    echo "[!] Ce script doit être exécuté avec les privilèges root (sudo ./uninstall.sh)"
    exit 1
fi

TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME=$(eval echo "~$TARGET_USER")
CONDA_DIR="$TARGET_HOME/miniconda3"
ENV_NAME="sdr-stt"

echo "=== Désinstallation de RadioTranscribe-Store ==="

# --- 1. Arrêt et suppression du service de veille radio ---
echo "[1/4] Arrêt et suppression du service systemd..."
if systemctl is-active --quiet radio-pipeline.service 2>/dev/null; then
    systemctl stop radio-pipeline.service
    echo "  -> Service radio-pipeline arrêté."
fi

if [ -f /etc/systemd/system/radio-pipeline.service ]; then
    systemctl disable radio-pipeline.service 2>/dev/null || true
    rm -f /etc/systemd/system/radio-pipeline.service
    systemctl daemon-reload
    echo "  -> Fichier de service radio-pipeline supprimé."
fi

# --- 2. Nettoyage de la mémoire temporaire (RAM disk) ---
echo "[2/4] Nettoyage du RAM disk (/dev/shm/radio_chunks)..."
if [ -d /dev/shm/radio_chunks ]; then
    rm -rf /dev/shm/radio_chunks
    echo "  -> Répertoire temporaire purgé."
fi

# --- 3. Suppression ciblée de la base et du compte MariaDB (optionnel) ---
echo "[3/4] Base de données MariaDB"
echo "  Note : Le serveur MariaDB reste intact."
read -p "  Voulez-vous supprimer la base 'radio_monitoring' et l'utilisateur 'radio_user' ? (o/N) : " drop_db

if [[ "$drop_db" =~ ^[oOyY]$ ]]; then
    mariadb -u root << 'EOF' 2>/dev/null || true
DROP DATABASE IF EXISTS radio_monitoring;
DROP USER IF EXISTS 'radio_user'@'localhost';
FLUSH PRIVILEGES;
EOF
    echo "  -> Base 'radio_monitoring' et utilisateur 'radio_user' supprimés."
else
    echo "  -> Base et données MariaDB conservées."
fi

# --- 4. Environnement virtuel Conda sdr-stt (optionnel) ---
echo "[4/4] Environnement Python Conda"
echo "  Note : L'installation de Miniconda et les autres environnements restent intacts."
if [ -d "$CONDA_DIR/envs/$ENV_NAME" ]; then
    read -p "  Voulez-vous supprimer l'environnement Conda '$ENV_NAME' ? (o/N) : " drop_env
    if [[ "$drop_env" =~ ^[oOyY]$ ]]; then
        sudo -u "$TARGET_USER" "$CONDA_DIR/bin/conda" env remove -n "$ENV_NAME" -y 2>/dev/null || rm -rf "$CONDA_DIR/envs/$ENV_NAME"
        echo "  -> Environnement '$ENV_NAME' supprimé."
    else
        echo "  -> Environnement Conda conservé."
    fi
else
    echo "  -> Aucun environnement '$ENV_NAME' détecté."
fi

echo ""
echo "=== Désinstallation terminée ==="
echo "Le serveur ChromaDB, MariaDB, les packages système (RTL-SDR/ffmpeg) et vos archives audio ont été conservés."
