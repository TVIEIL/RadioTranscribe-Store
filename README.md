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


