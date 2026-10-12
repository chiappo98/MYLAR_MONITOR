#!/usr/bin/env python3
"""Rilegge le foto già acquisite e scrive le misure in un file di backup.

Esempi:
    python riprocessa.py                              # tutte le foto
    python riprocessa.py --da "02-10-2026 15-00"      # dal 2 ottobre ore 15:00 in poi
    python riprocessa.py --da "02-10-2026 15-00" --fino "04-10-2026 12-00"
    python riprocessa.py --da "02-10-2026 15-00" --append

La data si scrive nello stesso formato dei file di testo: dd-mm-yyyy hh-mm
"""
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import cv2

# <-- metti qui il nome del tuo script di acquisizione (senza .py)
from monitor_mylar import leggi_comparatore, disegna_debug

CARTELLA = Path(os.environ.get("CARTELLA_IMMAGINI", "F:/MYLAR_MONITOR/foto"))
FILE_BACKUP = Path(os.environ.get("FILE_BACKUP", "misure_backup.txt"))
FORMATO_DATA = "%d-%m-%Y %H-%M"

# dd_mm_yyyy_hh_mm.png (esclude i file *_debug.png)
RE_NOME = re.compile(r"^(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})\.png$", re.IGNORECASE)


def ora_da_nome(nome: str) -> datetime | None:
    m = RE_NOME.match(nome)
    if not m:
        return None
    g, mese, a, h, mi = map(int, m.groups())
    try:
        return datetime(a, mese, g, h, mi)
    except ValueError:
        return None


def parse_data(testo: str) -> datetime:
    try:
        return datetime.strptime(testo, FORMATO_DATA)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"data non valida: '{testo}' (formato richiesto: dd-mm-yyyy hh-mm)")


def main():
    ap = argparse.ArgumentParser(description="Riprocessa le foto del comparatore.")
    ap.add_argument("--da", type=parse_data, default=None,
                    help="elabora solo le foto da questa data/ora in poi (inclusa)")
    ap.add_argument("--fino", type=parse_data, default=None,
                    help="elabora solo le foto fino a questa data/ora (inclusa)")
    ap.add_argument("--append", action="store_true",
                    help="aggiunge al file di backup invece di sovrascriverlo")
    ap.add_argument("--debug", action="store_true",
                    help="salva anche le immagini annotate (*_debug.png)")
    args = ap.parse_args()

    foto = []
    for p in CARTELLA.glob("*.png"):
        ora = ora_da_nome(p.name)
        if ora is None:
            continue
        if args.da and ora < args.da:
            continue
        if args.fino and ora > args.fino:
            continue
        foto.append((ora, p))
    foto.sort()

    if not foto:
        print("Nessuna foto trovata nell'intervallo richiesto.", file=sys.stderr)
        return

    intervallo = f"{foto[0][0]:{FORMATO_DATA}} -> {foto[-1][0]:{FORMATO_DATA}}"
    print(f"{len(foto)} foto da elaborare ({intervallo}) -> {FILE_BACKUP}")

    ok = errori = 0
    with open(FILE_BACKUP, "a" if args.append else "w", encoding="utf-8") as out:
        for i, (ora, percorso) in enumerate(foto, 1):
            img = cv2.imread(str(percorso))
            if img is None:
                print(f"[{i}/{len(foto)}] {percorso.name}: immagine illeggibile", file=sys.stderr)
                errori += 1
                continue
            try:
                d = leggi_comparatore(img)
            except Exception as e:
                print(f"[{i}/{len(foto)}] {percorso.name}: lettura fallita ({e})", file=sys.stderr)
                errori += 1
                continue

            out.write(f"{ora:{FORMATO_DATA}};{d['mm']:.3f}\n")
            out.flush()
            ok += 1
            if args.debug:
                cv2.imwrite(str(percorso.with_name(percorso.stem + "_debug.png")),
                            disegna_debug(img, d))
            if i % 50 == 0 or i == len(foto):
                print(f"[{i}/{len(foto)}] ultima: {percorso.name} -> {d['mm']:.3f} mm")

    print(f"Fatto: {ok} misure scritte, {errori} errori.")


if __name__ == "__main__":
    main()