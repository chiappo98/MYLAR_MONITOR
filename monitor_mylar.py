#!/usr/bin/env python3
"""
Ogni 10 minuti:
  - scatta una foto da una camera, ritaglia la parte centrale e la salva in PNG
    con nome dd_mm_yyyy_hh_mm.png (es. 30_09_2026_14_10.png)
  - legge la temperatura da un sensore BME280 collegato via USB (seriale)
    e la appende a un file di testo con data/ora nel formato dd-mm-yyyy hh-mm
  - legge il comparatore centesimale dalla foto appena scattata
    e appende la misura (mm) a un secondo file di testo

Requisiti:
    pip install opencv-python pyserial numpy

Configurazione (variabili d'ambiente, oppure modifica i valori di default):
    CARTELLA_IMMAGINI   cartella dove salvare le foto
    CAMERA              indice della webcam (0, 1, ...) oppure URL di una camera IP
    INTERVALLO_MIN      intervallo in minuti (default 10)
    PORTA_SENSORE       porta seriale del sensore (es. COM5 su Windows, /dev/ttyACM0 su Linux)
    FILE_TEMPERATURE    file txt dove appendere le temperature
    FILE_MISURE         file txt dove appendere le misure del comparatore
    CROP_LARGHEZZA_PCT  % di larghezza da tenere al centro
    CROP_ALTEZZA_PCT    % di altezza da tenere al centro
    ZERO_ANGLE          angolo dello 0 della scala grande (gradi, orario da ore 12)
    SALVA_DEBUG         "1" per salvare anche l'immagine annotata della lettura

Fermare lo script con Ctrl+C.
"""

import math
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import serial  # pyserial

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

# Sensore BME280 (Arduino MKR via USB)
PORTA_SENSORE = os.environ.get("PORTA_SENSORE", "COM4")
BAUDRATE = 9600  # deve coincidere con Serial.begin() del firmware
TIMEOUT_LETTURA_S = 15  # il firmware invia una riga ogni 5 s
FILE_TEMPERATURE = Path(os.environ.get("FILE_TEMPERATURE", "temperature.txt"))
FILE_MISURE = Path(os.environ.get("FILE_MISURE", "misure.txt"))

# Crop centrale (percentuale della dimensione originale da mantenere)
CROP_LARGHEZZA_PCT = float(os.environ.get("CROP_LARGHEZZA_PCT", "50"))
CROP_ALTEZZA_PCT = float(os.environ.get("CROP_ALTEZZA_PCT", "100"))

# Riga inviata dal firmware: "T:23.45C|RH:45.10%|AH:..."
RE_TEMPERATURA = re.compile(r"T:\s*(-?\d+(?:\.\d+)?)\s*C")

# Comparatore
DIVISIONS = 100          # divisioni per giro
MM_PER_DIV = 0.01        # risoluzione
ANGLE_STEP = 0.5         # gradi
ZERO_ANGLE = float(os.environ.get("ZERO_ANGLE", "56"))  # 0 della scala grande
BIG_CCW = False          # scala grande numerata in senso orario
SMALL_ZERO_ANGLE = 90.0  # 0 del quadrantino piccolo (ore 3)
SMALL_CCW = True         # quadrantino piccolo numerato in senso antiorario
SALVA_DEBUG = os.environ.get("SALVA_DEBUG", "0") == "1"

_sensore: serial.Serial | None = None


# --------------------------------------------------------------------------
# SENSORE
# --------------------------------------------------------------------------
def apri_sensore() -> serial.Serial | None:
    """Apre la porta seriale del sensore (la tiene aperta tra una lettura e l'altra)."""
    global _sensore
    try:
        _sensore = serial.Serial(PORTA_SENSORE, BAUDRATE, timeout=1)
        time.sleep(2)  # la scheda può resettarsi all'apertura della porta
    except serial.SerialException as e:
        print(f"[{datetime.now():%H:%M:%S}] Impossibile aprire {PORTA_SENSORE}: {e}", file=sys.stderr)
        _sensore = None
    return _sensore


def leggi_temperatura() -> float | None:
    """Restituisce una temperatura fresca in °C, oppure None se la lettura fallisce."""
    global _sensore
    if _sensore is None or not _sensore.is_open:
        if apri_sensore() is None:
            return None

    try:
        _sensore.reset_input_buffer()  # scarta le righe vecchie accumulate
        scadenza = time.monotonic() + TIMEOUT_LETTURA_S
        while time.monotonic() < scadenza:
            riga = _sensore.readline().decode("ascii", errors="ignore").strip()
            m = RE_TEMPERATURA.search(riga)
            if m:
                return float(m.group(1))
        print(f"[{datetime.now():%H:%M:%S}] Nessuna lettura valida dal sensore.", file=sys.stderr)
    except (serial.SerialException, OSError) as e:
        print(f"[{datetime.now():%H:%M:%S}] Errore seriale: {e}", file=sys.stderr)
        try:
            _sensore.close()
        except Exception:
            pass
        _sensore = None  # verrà riaperta al prossimo ciclo
    return None


def salva_temperatura(ora: datetime, temperatura: float) -> None:
    """Appende una riga 'dd-mm-yyyy hh-mm;temperatura' al file di testo."""
    with open(FILE_TEMPERATURE, "a", encoding="utf-8") as f:
        f.write(f"{ora:%d-%m-%Y %H-%M};{temperatura:.2f}\n")


# --------------------------------------------------------------------------
# CAMERA
# --------------------------------------------------------------------------
def ritaglia_centro(frame):
    """Mantiene solo la parte centrale dell'immagine."""
    h, w = frame.shape[:2]
    cw = max(1, int(w * CROP_LARGHEZZA_PCT / 100))
    ch = max(1, int(h * CROP_ALTEZZA_PCT / 100))
    x0 = (w - cw) // 2
    y0 = (h - ch) // 2
    return frame[y0:y0 + ch, x0:x0 + cw]


def scatta_foto(ora: datetime) -> Path | None:
    """Apre la camera, scatta una foto, la ritaglia e la salva. Restituisce il percorso o None."""
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

    frame = ritaglia_centro(frame)

    nome = ora.strftime("%d_%m_%Y_%H_%M") + ".png"
    percorso = CARTELLA_IMMAGINI / nome
    if not cv2.imwrite(str(percorso), frame):
        print(f"[{datetime.now():%H:%M:%S}] Errore nel salvataggio di {percorso}", file=sys.stderr)
        return None
    return percorso


# --------------------------------------------------------------------------
# COMPARATORE: rilevamento quadrante
# --------------------------------------------------------------------------
def find_dial(gray):
    """Ritorna (cx, cy, r) del cerchio piu' grande/evidente (ghiera)."""
    h, w = gray.shape
    blur = cv2.medianBlur(gray, 5)
    min_r = int(min(h, w) * 0.25)
    max_r = int(min(h, w) * 0.60)
    circles = cv2.HoughCircles(
        blur, cv2.HOUGH_GRADIENT, dp=1.5, minDist=min(h, w),
        param1=120, param2=60, minRadius=min_r, maxRadius=max_r)
    if circles is None:
        return w / 2.0, h / 2.0, min(h, w) * 0.45  # ripiego: centro immagine
    cx, cy, r = circles[0][0]
    return float(cx), float(cy), float(r)


# --------------------------------------------------------------------------
# COMPARATORE: rilevamento lancetta
# --------------------------------------------------------------------------
# def needle_scores(gray, cx, cy, r_min, r_max):
#     """Per ogni angolo (orario da ore 12) ritorna il punteggio di 'scuro' lungo il raggio."""
#     angles = np.arange(0, 360, ANGLE_STEP, dtype=np.float32)
#     radii = np.arange(r_min, r_max, 1.0, dtype=np.float32)
#     A, R = np.meshgrid(np.deg2rad(angles), radii, indexing="ij")
#     map_x = (cx + R * np.sin(A)).astype(np.float32)
#     map_y = (cy - R * np.cos(A)).astype(np.float32)

#     blur = cv2.GaussianBlur(gray, (5, 5), 0)
#     polar = cv2.remap(blur, map_x, map_y, cv2.INTER_LINEAR,
#                       borderMode=cv2.BORDER_REPLICATE).astype(np.float32)

#     bg = float(np.median(polar))                       # colore del fondo quadrante
#     dark = np.clip((bg - polar) / max(bg, 1.0), 0, 1)  # 0 = fondo, 1 = nero
#     dark = np.where(dark > 0.15, dark, 0)              # sopprime rumore
#     return angles, dark.mean(axis=1)


# def refine_peak(angles, scores):
#     """Baricentro circolare attorno al massimo -> angolo sub-passo."""
#     n = len(scores)
#     i = int(np.argmax(scores))
#     half = int(round(4.0 / ANGLE_STEP))
#     idx = np.arange(i - half, i + half + 1)
#     w = scores[idx % n]
#     w = np.clip(w - w.min(), 0, None)
#     if w.sum() == 0:
#         return float(angles[i])
#     offs = (idx - i) * ANGLE_STEP
#     return float((angles[i] + (w * offs).sum() / w.sum()) % 360)


# # --------------------------------------------------------------------------
# # COMPARATORE: quadrantino piccolo (contagiri: 1 cifra = 1 mm)
# # --------------------------------------------------------------------------
# # Posizione del quadrantino rispetto al centro, in frazioni del raggio della ghiera
SMALL_OFFSET = (-0.37, -0.17)
SMALL_RADIUS = 0.20

def _blur_wrap(v, sigma):
    """Gaussiana 1D circolare (359.75° confina con 0°)."""
    k = int(math.ceil(3 * sigma))
    x = np.arange(-k, k + 1, dtype=np.float32)
    ker = np.exp(-0.5 * (x / sigma) ** 2)
    ker /= ker.sum()
    ext = np.concatenate([v[-k:], v, v[:k]])
    return np.convolve(ext, ker, mode="valid")


def needle_scores(gray, cx, cy, r_min, r_max, step=0.25):
    """Punteggio per angolo (orario da ore 12) di 'linea radiale continua'.

    Indipendente dal colore: la lancetta può essere più chiara O più scura
    del fondo. Serve contrasto su (quasi) tutta la lunghezza della lancetta,
    non solo in un tratto (scritte, tacche, ombre).
    """
    angles = np.arange(0, 360, step, dtype=np.float32)
    radii = np.arange(r_min, r_max, 2.0, dtype=np.float32)
    A, R = np.meshgrid(np.deg2rad(angles), radii, indexing="ij")
    map_x = (cx + R * np.sin(A)).astype(np.float32)
    map_y = (cy - R * np.cos(A)).astype(np.float32)
    g = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)
    polar = cv2.remap(g, map_x, map_y, cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_REPLICATE)

    contrast = np.empty_like(polar)
    for j, r in enumerate(radii):
        px_per_sample = r * math.radians(step)
        s_c = max(1.0 / px_per_sample, 0.5)   # centro: ~1 px
        s_s = max(5.0 / px_per_sample, 2.0)   # intorno: ~5 px
        col = polar[:, j]
        contrast[:, j] = np.abs(_blur_wrap(col, s_c) - _blur_wrap(col, s_s))

    return angles, np.percentile(contrast, 25, axis=1).astype(np.float32)


def refine_peak(angles, scores, step=0.25, half_deg=2.0):
    """Baricentro circolare attorno al massimo -> angolo sub-passo."""
    n = len(scores)
    i = int(np.argmax(scores))
    half = int(round(half_deg / step))
    idx = np.arange(i - half, i + half + 1)
    w = np.clip(scores[idx % n] - scores[idx % n].min(), 0, None)
    if w.sum() == 0:
        return float(angles[i])
    return float((angles[i] + (w * (idx - i) * step).sum() / w.sum()) % 360)

def find_small_dial(gray, cx, cy, r_ghiera):
    """Centro e raggio del quadrantino piccolo.

    Parte da una posizione attesa (frazioni del raggio della ghiera) e cerca
    il cerchio stampato che massimizza lo scuro lungo tutta la circonferenza.
    """
    ex = cx + SMALL_OFFSET[0] * r_ghiera
    ey = cy + SMALL_OFFSET[1] * r_ghiera
    er = SMALL_RADIUS * r_ghiera

    blur = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)
    y0, y1 = max(int(ey - 2 * er), 0), int(ey + 2 * er)
    x0, x1 = max(int(ex - 2 * er), 0), int(ex + 2 * er)
    bg = float(np.median(blur[y0:y1, x0:x1]))
    dark = np.clip((bg - blur) / max(bg, 1.0), 0, 1)
    dark = np.where(dark > 0.08, dark, 0)
    dark = cv2.dilate(dark, np.ones((3, 3), np.uint8))   # tolleranza di 1 px sul bordo

    h, w = dark.shape
    t = np.deg2rad(np.arange(0, 360, 4, dtype=np.float32))
    st, ct = np.sin(t), np.cos(t)
    span = int(round(0.18 * r_ghiera))
    best = (-1.0, ex, ey, er)
    for rr in np.arange(er * 0.85, er * 1.15, 1.0):
        for dy in range(-span, span + 1):
            for dx in range(-span, span + 1):
                # clip: se il quadrantino e' vicino al bordo non si esce dall'immagine
                px = np.clip(np.rint(ex + dx + rr * ct).astype(int), 0, w - 1)
                py = np.clip(np.rint(ey + dy + rr * st).astype(int), 0, h - 1)
                # percentile basso: serve un anello scuro su TUTTA la circonferenza
                # (un'ombra o la lancetta grande coprono solo un arco)
                sc = np.percentile(dark[py, px], 30)
                if sc > best[0]:
                    best = (sc, ex + dx, ey + dy, rr)
    return float(best[1]), float(best[2]), float(best[3])


def read_small_dial(gray, scx, scy, sr, zero_deg, ccw=True):
    """Ritorna (valore_in_mm_continuo 0..10, angolo_lancetta)."""
    # angles, scores = needle_scores(gray, scx, scy, 0.32 * sr, 0.55 * sr)
    # ang = refine_peak(angles, scores)
    angles, scores = needle_scores(gray, scx, scy, 0.20 * sr, 0.60 * sr)
    ang = refine_peak(angles, scores, half_deg=3.0)
    delta = (zero_deg - ang) % 360 if ccw else (ang - zero_deg) % 360
    return delta / 360.0 * 10.0, ang


# --------------------------------------------------------------------------
# COMPARATORE: lettura completa
# --------------------------------------------------------------------------
def leggi_comparatore(img) -> dict:
    """Legge il comparatore da un'immagine BGR (gia' in memoria).

    Ritorna un dizionario con: mm, turns, divisions e i dati per il debug.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Quadrante grande
    cx, cy, r_ghiera = find_dial(gray)
    r = r_ghiera * 0.90      # raggio della sola faccia bianca (esclude la ghiera)

    # Lancetta grande: tra 30% e 85% del raggio (evita perno e tacche)
    angles, scores = needle_scores(gray, cx, cy, 0.30 * r, 0.85 * r)
    needle = refine_peak(angles, scores)
    delta = (ZERO_ANGLE - needle) % 360 if BIG_CCW else (needle - ZERO_ANGLE) % 360
    divisions = delta / 360.0 * DIVISIONS
    frac_mm = divisions * MM_PER_DIV            # parte sotto il mm (0..1)

    # Quadrantino piccolo: stima grezza dei mm, poi si "aggancia" ai giri interi
    # usando la lancetta grande (che e' molto piu' precisa).
    scx, scy, sr = find_small_dial(gray, cx, cy, r_ghiera)
    small_val, small_ang = read_small_dial(
        gray, scx, scy, sr, SMALL_ZERO_ANGLE, ccw=SMALL_CCW)
    turns = int(round(small_val - frac_mm)) % 10

    return {
        "mm": turns + frac_mm, "turns": turns, "divisions": divisions,
        "cx": cx, "cy": cy, "r": r, "needle": needle,
        "scx": scx, "scy": scy, "sr": sr, "small_ang": small_ang,
        "small_val": small_val,
    }


def disegna_debug(img, d: dict):
    """Immagine annotata: lancetta grande (rossa), piccola (arancione), zero (blu)."""
    dbg = img.copy()
    cx, cy, r = d["cx"], d["cy"], d["r"]
    cv2.circle(dbg, (int(cx), int(cy)), int(r), (0, 255, 0), 1)
    tip = (int(cx + r * math.sin(math.radians(d["needle"]))),
           int(cy - r * math.cos(math.radians(d["needle"]))))
    zp = (int(cx + r * math.sin(math.radians(ZERO_ANGLE))),
          int(cy - r * math.cos(math.radians(ZERO_ANGLE))))
    cv2.line(dbg, (int(cx), int(cy)), tip, (0, 0, 255), 2)
    cv2.line(dbg, (int(cx), int(cy)), zp, (255, 0, 0), 1)

    scx, scy, sr = d["scx"], d["scy"], d["sr"]
    cv2.circle(dbg, (int(scx), int(scy)), int(sr), (0, 200, 255), 1)
    stip = (int(scx + sr * math.sin(math.radians(d["small_ang"]))),
            int(scy - sr * math.cos(math.radians(d["small_ang"]))))
    cv2.line(dbg, (int(scx), int(scy)), stip, (0, 140, 255), 2)
    cv2.putText(dbg, f"{d['mm']:.3f} mm", (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (0, 0, 255), 2)
    return dbg


def read_dial(percorso: Path, ora: datetime) -> float | None:
    """Legge il comparatore dalla foto salvata in `percorso` e appende la misura a FILE_MISURE.

    Ritorna i mm letti, oppure None se la lettura fallisce (senza mai
    interrompere il ciclo di acquisizione).
    """
    img = cv2.imread(str(percorso))
    if img is None:
        print(f"[{datetime.now():%H:%M:%S}] Impossibile leggere {percorso} per la misura.", file=sys.stderr)
        return None

    try:
        d = leggi_comparatore(img)
    except Exception as e:  # una foto "storta" non deve fermare il monitoraggio
        print(f"[{datetime.now():%H:%M:%S}] Lettura comparatore fallita: {e}", file=sys.stderr)
        return None

    mm = d["mm"]
    print(f"[{datetime.now():%H:%M:%S}] Misura {d['turns']} mm + {d['divisions']:.1f} div -> {mm:.3f} mm")

    # Stesso timestamp (e formato) di foto e temperatura, con 3 decimali (0.001 mm)
    with open(FILE_MISURE, "a", encoding="utf-8") as f:
        f.write(f"{ora:%d-%m-%Y %H-%M};{mm:.3f}\n")

    if SALVA_DEBUG:
        cv2.imwrite(str(percorso.with_name(percorso.stem + "_debug.png")), disegna_debug(img, d))
    return mm


# --------------------------------------------------------------------------
# CICLO PRINCIPALE
# --------------------------------------------------------------------------
def secondi_al_prossimo_scatto() -> float:
    """Secondi da attendere fino al prossimo multiplo dell'intervallo (es. :00, :10, :20 ...)."""
    ora = datetime.now()
    minuti_trascorsi = ora.minute % INTERVALLO_MIN
    prossimo = (ora - timedelta(minutes=minuti_trascorsi, seconds=ora.second,
                                microseconds=ora.microsecond)) + timedelta(minutes=INTERVALLO_MIN)
    return (prossimo - ora).total_seconds()


def main():
    CARTELLA_IMMAGINI.mkdir(parents=True, exist_ok=True)
    FILE_TEMPERATURE.parent.mkdir(parents=True, exist_ok=True)
    FILE_MISURE.parent.mkdir(parents=True, exist_ok=True)
    print(f"Salvataggio ogni {INTERVALLO_MIN} minuti in: {CARTELLA_IMMAGINI}")
    print(f"Temperature in: {FILE_TEMPERATURE}  (sensore su {PORTA_SENSORE})  (Ctrl+C per fermare)")
    print(f"Misure in: {FILE_MISURE}")

    apri_sensore()

    try:
        # Prima foto subito, poi allineata agli intervalli
        while True:
            ora = datetime.now()  # stesso timestamp per foto, misura e temperatura
            percorso = scatta_foto(ora)
            if percorso:
                print(f"[{datetime.now():%H:%M:%S}] Salvata {percorso.name}")
                read_dial(percorso, ora)
                temperatura = leggi_temperatura()
                if temperatura is not None:
                    salva_temperatura(ora, temperatura)
                    print(f"[{datetime.now():%H:%M:%S}] Temperatura {temperatura:.2f} °C salvata")
            time.sleep(secondi_al_prossimo_scatto())
    except KeyboardInterrupt:
        print("\nInterrotto.")
    finally:
        if _sensore is not None and _sensore.is_open:
            _sensore.close()


if __name__ == "__main__":
    main()
