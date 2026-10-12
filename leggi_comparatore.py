#!/usr/bin/env python3
"""
Legge la misura di un comparatore centesimale (es. Mitutoyo 2046A, Mahr MarCator)
dall'ultima immagine salvata in una cartella, usando l'API di Claude.

Ad ogni esecuzione:
  1. cerca l'ultima immagine nella cartella;
  2. la ritaglia sul quadrante del comparatore (ritaglio configurabile);
  3. la invia a Claude e aggiunge una riga al file CSV dei risultati.

Requisiti:
    pip install anthropic opencv-python
    variabile d'ambiente ANTHROPIC_API_KEY impostata

Configurazione (variabili d'ambiente, oppure modifica i valori di default):
    CARTELLA_IMMAGINI   cartella dove viene salvata l'immagine
    FILE_CSV            file CSV con lo storico delle misure
    MODELLO_CLAUDE      modello da usare
    RITAGLIO            "x1,y1,x2,y2" come frazioni (0-1) della larghezza/altezza
                        dell'immagine; "no" per disattivare il ritaglio
    SALVA_RITAGLIO      "1" (default) salva il ritaglio inviato in <cartella>/ritagli,
                        utile per controllare che il quadrante sia inquadrato bene
"""

import base64
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import cv2
import numpy as np

# --------------------------------------------------------------------------
# CONFIGURAZIONE
# --------------------------------------------------------------------------
CARTELLA_IMMAGINI = Path(os.environ.get("CARTELLA_IMMAGINI", r"F:\MYLAR_MONITOR\foto"))
FILE_CSV = Path(os.environ.get("FILE_CSV", CARTELLA_IMMAGINI / "misure.csv"))
FILE_STATO = Path(os.environ.get("FILE_STATO", CARTELLA_IMMAGINI / ".ultima_immagine.json"))
MODELLO = os.environ.get("MODELLO_CLAUDE", "claude-sonnet-5-5")

# Ritaglio sul quadrante, in frazioni dell'immagine (sinistra, alto, destra, basso).
# Il valore di default è calibrato sulla foto di prova 1920x1080 (quadrante circa
# tra x 690-1210 e y 290-800, con un piccolo margine). Va rifatto se la camera si sposta.
RITAGLIO_DEFAULT = "0.35,0.26,0.64,0.75"
SALVA_RITAGLIO = os.environ.get("SALVA_RITAGLIO", "1") == "1"

# Se True, analizza l'ultima immagine anche se è la stessa dell'ora precedente
RIANALIZZA_SE_INVARIATA = False

ESTENSIONI = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

ISTRUZIONI = """Sei un assistente che legge la misura di un comparatore centesimale \
analogico (dial indicator) a partire da una fotografia. Lo strumento può essere un \
Mitutoyo, un Mahr MarCator o simile, con risoluzione 0,01 mm.

Come si legge lo strumento:
- Il quadrante grande ha 100 divisioni: ogni divisione vale 0,01 mm e un giro completo \
della lancetta grande corrisponde a 1 mm.
- ATTENZIONE: il quadrante può essere ruotato, quindi lo 0 non è necessariamente in alto. \
Individua sempre lo 0 sulla scala stampata e conta le divisioni a partire da lì. \
Sulla scala principale le cifre (0, 10, 20 ... 90) crescono in senso orario.
- Possono esserci due serie di numeri: una principale, con cifre più grandi, e una più piccola \
per la lettura inversa. Usa la serie principale (quella che cresce in senso orario) e ignora l'altra.
- Se la lancetta è a metà tra due tacche, stima al mezzo centesimo (es. 47,5) e segnalalo nelle note.
- Il piccolo quadrante ausiliario (contagiri), con cifre da 0 a 9, indica i millimetri interi: \
ogni cifra vale 1 mm. Il suo verso di numerazione può essere antiorario: leggi la cifra indicata \
seguendo la numerazione stampata.
- Quando la lancetta del contagiri sta tra due cifre, prendi quella già superata nel verso di \
conteggio, e controlla la coerenza con la lancetta grande: se la lancetta grande è vicino a 0 \
(es. 97, 98, 99 oppure 01, 02) il contagiri è rispettivamente appena prima o appena dopo una cifra.
- Misura totale (mm) = millimetri interi del contagiri + (lettura quadrante grande / 100).
- Le eventuali tacche rosse di tolleranza non sono la misura.
- Nell'immagine possono essere visibili altri oggetti (supporti, cavi, ecc.): ignorali.

Rispondi SOLO con un oggetto JSON, senza testo prima o dopo e senza backtick, con questi campi:
{
  "mm_interi": <intero letto sul contagiri>,
  "centesimi": <numero letto sul quadrante grande, 0-99.5>,
  "misura_mm": <misura totale in mm, numero con al massimo 3 decimali>,
  "confidenza": "alta" | "media" | "bassa",
  "note": "<breve commento, es. lancetta tra due tacche, immagine sfocata, strumento non visibile>"
}
Se nell'immagine non è visibile un comparatore leggibile, usa null per i valori numerici \
e confidenza "bassa", spiegando il motivo in "note"."""


# --------------------------------------------------------------------------
# FUNZIONI
# --------------------------------------------------------------------------
def leggi_parametri_ritaglio():
    """Legge RITAGLIO ('x1,y1,x2,y2' in frazioni) o None se disattivato."""
    valore = os.environ.get("RITAGLIO", RITAGLIO_DEFAULT).strip()
    if valore.lower() in ("", "no", "off", "nessuno"):
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in valore.split(","))
    except ValueError:
        sys.exit(f"Valore RITAGLIO non valido: {valore!r} (atteso 'x1,y1,x2,y2', es. 0.35,0.26,0.64,0.75)")
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        sys.exit("RITAGLIO: i valori devono essere tra 0 e 1, con sinistra<destra e alto<basso.")
    return x1, y1, x2, y2


def ultima_immagine(cartella: Path):
    """Restituisce il file immagine modificato più di recente, o None."""
    candidati = [p for p in cartella.iterdir() if p.is_file() and p.suffix.lower() in ESTENSIONI]
    if not candidati:
        return None
    return max(candidati, key=lambda p: p.stat().st_mtime)


def gia_analizzata(immagine: Path) -> bool:
    if RIANALIZZA_SE_INVARIATA or not FILE_STATO.exists():
        return False
    try:
        stato = json.loads(FILE_STATO.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return stato.get("percorso") == str(immagine) and stato.get("mtime") == immagine.stat().st_mtime


def salva_stato(immagine: Path):
    FILE_STATO.write_text(json.dumps({"percorso": str(immagine), "mtime": immagine.stat().st_mtime}))


def prepara_immagine(immagine: Path, ritaglio):
    """Restituisce (byte, media_type) dell'immagine da inviare, ritagliata se richiesto."""
    dati = immagine.read_bytes()
    if ritaglio is None:
        return dati, ESTENSIONI[immagine.suffix.lower()]

    img = cv2.imdecode(np.frombuffer(dati, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("immagine non leggibile (file incompleto o danneggiato?)")
    h, w = img.shape[:2]
    x1, y1, x2, y2 = ritaglio
    img = img[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)]
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("errore nella codifica del ritaglio")
    dati = buf.tobytes()

    if SALVA_RITAGLIO:
        cartella_ritagli = CARTELLA_IMMAGINI / "ritagli"
        cartella_ritagli.mkdir(exist_ok=True)
        (cartella_ritagli / (immagine.stem + "_ritaglio.png")).write_bytes(dati)

    return dati, "image/png"


def leggi_misura(immagine: Path, ritaglio) -> dict:
    client = anthropic.Anthropic()  # usa ANTHROPIC_API_KEY
    dati, media_type = prepara_immagine(immagine, ritaglio)

    risposta = client.messages.create(
        model=MODELLO,
        max_tokens=600,
        system=ISTRUZIONI,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.standard_b64encode(dati).decode("utf-8"),
                        },
                    },
                    {"type": "text", "text": "Leggi la misura indicata dal comparatore."},
                ],
            }
        ],
    )

    testo = "".join(b.text for b in risposta.content if b.type == "text").strip()
    testo = re.sub(r"^```(?:json)?|```$", "", testo, flags=re.MULTILINE).strip()
    return json.loads(testo)


def scrivi_csv(immagine: Path, r: dict):
    nuovo = not FILE_CSV.exists()
    with FILE_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        if nuovo:
            w.writerow(["data_ora", "file", "misura_mm", "mm_interi", "centesimi", "confidenza", "note"])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            immagine.name,
            r.get("misura_mm"),
            r.get("mm_interi"),
            r.get("centesimi"),
            r.get("confidenza"),
            r.get("note"),
        ])


def main():
    if not CARTELLA_IMMAGINI.is_dir():
        sys.exit(f"Cartella non trovata: {CARTELLA_IMMAGINI}")

    ritaglio = leggi_parametri_ritaglio()

    immagine = ultima_immagine(CARTELLA_IMMAGINI)
    if immagine is None:
        print("Nessuna immagine nella cartella.")
        return

    if gia_analizzata(immagine):
        print(f"{immagine.name}: già analizzata, nessuna immagine nuova.")
        return

    try:
        risultato = leggi_misura(immagine, ritaglio)
    except Exception as e:  # errore di rete, JSON non valido, ecc.
        print(f"Errore nell'analisi di {immagine.name}: {e}", file=sys.stderr)
        sys.exit(1)

    scrivi_csv(immagine, risultato)
    salva_stato(immagine)
    print(f"{immagine.name}: {risultato.get('misura_mm')} mm (confidenza {risultato.get('confidenza')})")


if __name__ == "__main__":
    main()
