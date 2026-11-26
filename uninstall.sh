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

# --- 1. Suppression de la planification Cron ---
echo "[1/6] Suppression des tâches planifiées Cron..."
if [ -f /etc/cron.d/radio-pipeline ]; then
    rm -f /etc/cron.d/radio-pipeline
    echo "  -> Fichier /etc/cron.d/radio-pipeline supprimé."
fi

# --- 2. Arrêt et suppression des services systemd ---
echo "[2/6] Arrêt des services et suppression des unités systemd..."

# Arrêt de toutes les instances template actives (inter, culture, info...)
systemctl stop "radio-pipeline@*.service" 2>/dev/null || true
systemctl stop radio-pipeline.service 2>/dev/null || true

# Nettoyage des processus zombies éventuels (script, capture SDR, retour audio)
pkill -9 -f "radio_pipeline" 2>/dev/null || true
pkill -9 -f "rtl_fm" 2>/dev/null || true

# Suppression des fichiers d'unité
RELOAD_NEEDED=0
if [ -f /etc/systemd/system/radio-pipeline@.service ]; then
    systemctl disable "radio-pipeline@*.service" 2>/dev/null || true
    rm -f /etc/systemd/system/radio-pipeline@.service
    RELOAD_NEEDED=1
    echo "  -> Modèle systemd radio-pipeline@.service supprimé."
fi

if [ -f /etc/systemd/system/radio-pipeline.service ]; then
    systemctl disable radio-pipeline.service 2>/dev/null || true
    rm -f /etc/systemd/system/radio-pipeline.service
    RELOAD_NEEDED=1
    echo "  -> Service legacy radio-pipeline.service supprimé."
fi

# Gestion du service ChromaDB
if [ -f /etc/systemd/system/chromadb.service ]; then
    read -p "  Voulez-vous également arrêter et désinstaller le service systemd ChromaDB ? (o/N) : " drop_chroma_srv
    if [[ "$drop_chroma_srv" =~ ^[oOyY]$ ]]; then
        systemctl stop chromadb.service 2>/dev/null || true
        systemctl disable chromadb.service 2>/dev/null || true
        rm -f /etc/systemd/system/chromadb.service
        RELOAD_NEEDED=1
        echo "  -> Service chromadb.service supprimé."
    else
        echo "  -> Service chromadb.service conservé actif."
    fi
fi

if [ "$RELOAD_NEEDED" -eq 1 ]; then
    systemctl daemon-reload
    systemctl reset-failed 2>/dev/null || true
fi

# --- 3. Nettoyage de la mémoire temporaire (RAM disk) ---
echo "[3/6] Nettoyage du RAM disk (/dev/shm)..."
rm -rf /dev/shm/radio_chunks*
echo "  -> Tous les répertoires temporaires /dev/shm/radio_chunks* ont été purgés."

# --- 4. Suppression de la base et du compte MariaDB (optionnel) ---
echo "[4/6] Base de données MariaDB"
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

# --- 5. Données ChromaDB locales (optionnel) ---
echo "[5/6] Données vectorielles ChromaDB"
if [ -d "$TARGET_HOME/chroma_data" ]; then
    read -p "  Voulez-vous supprimer l'index vectoriel ($TARGET_HOME/chroma_data) ? (o/N) : " drop_chroma_data
    if [[ "$drop_chroma_data" =~ ^[oOyY]$ ]]; then
        rm -rf "$TARGET_HOME/chroma_data"
        echo "  -> Dossier chroma_data supprimé."
    else
        echo "  -> Données vectorielles conservées."
    fi
fi

# --- 6. Environnement virtuel Conda & cache modèle Whisper (optionnel) ---
echo "[6/6] Environnement Python Conda ($ENV_NAME)"
echo "  Note : Miniconda, les autres environnements et les pilotes NVIDIA système restent intacts."
if [ -d "$CONDA_DIR/envs/$ENV_NAME" ]; then
    read -p "  Voulez-vous supprimer l'environnement Conda '$ENV_NAME' (inclut Faster-Whisper et CUDA/cuDNN) ? (o/N) : " drop_env
    if [[ "$drop_env" =~ ^[oOyY]$ ]]; then
        sudo -u "$TARGET_USER" "$CONDA_DIR/bin/conda" env remove -n "$ENV_NAME" -y 2>/dev/null || rm -rf "$CONDA_DIR/envs/$ENV_NAME"
        echo "  -> Environnement '$ENV_NAME' supprimé."
    else
        echo "  -> Environnement Conda conservé."
    fi
else
    echo "  -> Aucun environnement '$ENV_NAME' détecté."
fi

HF_MODEL_CACHE="$TARGET_HOME/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3"
if [ -d "$HF_MODEL_CACHE" ]; then
    read -p "  Voulez-vous supprimer le cache du modèle Whisper large-v3 (~3 Go) ? (o/N) : " drop_hf
    if [[ "$drop_hf" =~ ^[oOyY]$ ]]; then
        rm -rf "$HF_MODEL_CACHE"
        echo "  -> Cache du modèle large-v3 supprimé."
    fi
fi

echo ""
echo "=== Désinstallation terminée ==="
echo "Les paquets système de base (RTL-SDR, FFmpeg, ALSA, MariaDB), le blacklist noyau DVB et les dossiers d'archive restent en place."
