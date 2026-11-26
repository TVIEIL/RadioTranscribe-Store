<img width="2752" height="1536" alt="Gemini_Generated_Image_hki91qhki91qhki9" src="https://github.com/user-attachments/assets/04092e7b-e4ea-4618-9086-daf1615719ca" />

# RadioTranscribe-Store

> Pipeline autonome d'écoute radio continue (RTL-SDR), transcription automatique en temps réel (Faster-Whisper) et double indexation persistance SQL & vectorielle (MariaDB + ChromaDB).

**RadioTranscribe-Store** transforme une simple clé logicielle SDR (*Software Defined Radio*) en sentinelle d'écoute hertzienne continue. Le système intercepte passivement les flux FM en direct, convertit la voix en texte au fil de l'eau avec le modèle `large-v3`, filtre les hallucinations de transcription et archive les données dans deux moteurs complémentaires :
* **MariaDB** : registre relationnel horodaté à la seconde (archivage légal, métriques, chronologie brute).
* **ChromaDB** : base vectorielle sémantique servant de mémoire vive (*RAG*) pour interroger les actualités en langage naturel ou alimenter un LLM local sans connexion Internet.

---

## Architecture du Pipeline

```text
[ Antenne FM ]
      │
      ▼
[ Clé RTL-SDR ] ──(refroidissement actif recommandé)
      │
      ▼  (rtl_fm | ffmpeg)
[ RAM Disk : /dev/shm ] ──(découpage en segments audio temporaires sans usure disque)
      │
      ▼
[ Faster-Whisper (large-v3) ] ──(CTranslate2, ex: 16 threads CPU)
      │
      ├──> [ Filtre anti-hallucinations ] (purge des artefacts télétexte, génériques...)
      │
      ├──> [ MariaDB ]   ──> Archivage chronologique persistant
      └──> [ ChromaDB ]  ──> Indexation vectorielle sémantique (RAG)
```

## Installation rapide

Un script d'automatisation clé en main configure l'ensemble des prérequis (dépendances système, blacklist du pilote DVB, base MariaDB, environnement Conda et services systemd) :

```bash
git clone   https://github.com/TVIEIL/RadioTranscribe-Store.git
cd RadioTranscribe-Store
chmod +x install.sh uninstall.sh
sudo ./install.sh
```

## Désinstallation

Le projet fournit un script de désinstallation interactive (`uninstall.sh`) conçu pour supprimer la chaîne d'écoute sans altérer les services partagés de votre machine :

```bash
chmod +x uninstall.sh
sudo ./uninstall.sh
```

## Vérification et supervision

Une fois l'installation terminée, vous pouvez contrôler le bon fonctionnement de chaque maillon du pipeline :

### 1. Tâche Cron, processus actifs et logs
```bash
# Vérifier la planification cron (utilisateur courant et root)
crontab -l
sudo crontab -l

# Vérifier les processus en cours d'exécution (SDR, découpage audio, transcription)
pgrep -af "radio|rtl_fm|ffmpeg"

# Contrôler les segments audio temporaires en RAM
ls -lh /dev/shm/

# Suivre les exécutions cron dans les journaux système
journalctl -u cron -n 20 --no-pager


# Vérifier que le modèle est bien chargé en VRAM lors d'une exécution
nvidia-smi

# Vérifier que le démon MariaDB est actif
systemctl status mariadb --no-pager

# Lister les bases et consulter les dernières entrées archivées
sudo mariadb -e "SHOW DATABASES;"
sudo mariadb -D radio_monitoring -e "SELECT * FROM transcriptions ORDER BY id DESC LIMIT 5;"
```

## Configuration matérielle (GPU CUDA / CPU)

Par défaut, le déploiement configure Faster-Whisper pour exploiter l'accélération matérielle d'un GPU NVIDIA (`WHISPER_DEVICE=cuda` et `WHISPER_COMPUTE_TYPE=float16`)[span_0](start_span)[span_0](end_span).

Si vous souhaitez exécuter la transcription uniquement sur processeur (CPU), ouvrez le service `systemd` (`/etc/systemd/system/radio-pipeline.service` ou via `sudo systemctl edit --full radio-pipeline`) et remplacez les variables d'environnement par les lignes suivantes :

```ini
Environment="WHISPER_DEVICE=cpu"
Environment="WHISPER_COMPUTE_TYPE=int8"
Environment="WHISPER_THREADS=16"
```

* **`WHISPER_DEVICE`** : doit impérativement être renseigné en minuscules (`cpu`).
* **`WHISPER_COMPUTE_TYPE`** : sur processeur x86, utilisez `int8` (recommandé pour les performances) ou `float32` (`float16` n'étant supporté que sur GPU).
* **`WHISPER_THREADS`** : à adapter selon le nombre de cœurs physiques alloués au pipeline.

Rechargez ensuite la configuration et redémarrez le service :

```bash
sudo systemctl daemon-reload
sudo systemctl restart radio-pipeline
```









