#!/usr/bin/env python3
"""
Scatta una foto ogni 10 minuti da una camera e la salva in formato PNG
con nome dd_mm_yyyy_hh_mm.png (es. 30_09_2026_14_10.png).

Requisiti:
    pip install opencv-python

Configurazione (variabili d'ambiente, oppure modifica i valori di default):
    CARTELLA_IMMAGINI   cartella dove salvare le foto (la stessa usata da leggi_comparatore.py)
    CAMERA              indice della webcam (0, 1, ...) oppure URL di una camera IP
                        (es. rtsp://utente:password@192.168.1.50:554/stream1)
    INTERVALLO_MIN      intervallo in minuti (default 10)

Fermare lo script con Ctrl+C.
"""

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2

# --------------------------------------------------------------------------
# CONFIGURAZIONE
# --------------------------------------------------------------------------
CARTELLA_IMMAGINI = Path(os.environ.get("CARTELLA_IMMAGINI", "F:/MYLAR_MONITOR/foto"))
INTERVALLO_MIN = int(os.environ.get("INTERVALLO_MIN", "10"))

_camera = os.environ.get("CAMERA", "0")
CAMERA = int(_camera) if _camera.isdigit() else _camera

# Frame scartati prima dello scatto, per lasciare che la camera regoli
# esposizione e messa a fuoco automatiche
FRAME_DI_ASSESTAMENTO = 10


# --------------------------------------------------------------------------
# FUNZIONI
# --------------------------------------------------------------------------
def scatta_foto() -> Path | None:
    """Apre la camera, scatta una foto e la salva. Restituisce il percorso o None se fallisce."""
    cap = cv2.VideoCapture(CAMERA)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    if not cap.isOpened():
        print(f"[{datetime.now():%H:%M:%S}] Impossibile aprire la camera: {CAMERA}", file=sys.stderr)
        return None

    try:
        frame = None
        for _ in range(FRAME_DI_ASSESTAMENTO):
            ok, f = cap.read()
            if ok:
                frame = f
        if frame is None:
            print(f"[{datetime.now():%H:%M:%S}] Nessun frame ricevuto dalla camera.", file=sys.stderr)
            return None
    finally:
        cap.release()

    nome = datetime.now().strftime("%d_%m_%Y_%H_%M") + ".png"
    percorso = CARTELLA_IMMAGINI / nome
    if not cv2.imwrite(str(percorso), frame):
        print(f"[{datetime.now():%H:%M:%S}] Errore nel salvataggio di {percorso}", file=sys.stderr)
        return None
    return percorso


def secondi_al_prossimo_scatto() -> float:
    """Secondi da attendere fino al prossimo multiplo dell'intervallo (es. :00, :10, :20 ...)."""
    ora = datetime.now()
    minuti_trascorsi = ora.minute % INTERVALLO_MIN
    prossimo = (ora - timedelta(minutes=minuti_trascorsi, seconds=ora.second,
                                microseconds=ora.microsecond)) + timedelta(minutes=INTERVALLO_MIN)
    return (prossimo - ora).total_seconds()


def main():
    CARTELLA_IMMAGINI.mkdir(parents=True, exist_ok=True)
    print(f"Salvataggio ogni {INTERVALLO_MIN} minuti in: {CARTELLA_IMMAGINI}  (Ctrl+C per fermare)")

    try:
        # Prima foto subito, poi allineata agli intervalli
        while True:
            percorso = scatta_foto()
            if percorso:
                print(f"[{datetime.now():%H:%M:%S}] Salvata {percorso.name}")
            time.sleep(secondi_al_prossimo_scatto())
    except KeyboardInterrupt:
        print("\nInterrotto.")


if __name__ == "__main__":
    main()
