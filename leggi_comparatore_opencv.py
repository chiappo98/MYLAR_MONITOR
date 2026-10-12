#!/usr/bin/env python3
"""
Lettura della lancetta grande di un comparatore centesimale (es. Mitutoyo 0.01 mm).

Principio:
  1. trova il quadrante (cerchio) -> centro e raggio
  2. per ogni angolo (passo 0.5 deg) misura quanto e' "scuro" il raggio
     tra r_min e r_max: la lancetta e' l'angolo con il punteggio massimo
  3. converte l'angolo in divisioni usando l'angolo dello ZERO (calibrazione)

Uso:
  python leggi_comparatore.py immagine.png
  python leggi_comparatore.py immagine.png --calibra          # clicca sullo 0 del quadrante
  python leggi_comparatore.py immagine.png --zero-angle 56    # zero noto (gradi, orario da ore 12)
  python leggi_comparatore.py immagine.png --turns 1          # forza i mm interi (default: automatico)
  python leggi_comparatore.py immagine.png --center 281,238 --radius 190
"""
import argparse
import math
import sys

import cv2
import numpy as np
from datetime import datetime

DIVISIONS = 100          # divisioni per giro
MM_PER_DIV = 0.01        # risoluzione
ANGLE_STEP = 0.5         # gradi


# --------------------------------------------------------------------------
# Rilevamento quadrante
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
# Rilevamento lancetta
# --------------------------------------------------------------------------
def needle_scores(gray, cx, cy, r_min, r_max):
    """Per ogni angolo (orario da ore 12) ritorna il punteggio di 'scuro' lungo il raggio."""
    angles = np.arange(0, 360, ANGLE_STEP, dtype=np.float32)
    radii = np.arange(r_min, r_max, 1.0, dtype=np.float32)
    A, R = np.meshgrid(np.deg2rad(angles), radii, indexing="ij")
    map_x = (cx + R * np.sin(A)).astype(np.float32)
    map_y = (cy - R * np.cos(A)).astype(np.float32)

    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    polar = cv2.remap(blur, map_x, map_y, cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_REPLICATE).astype(np.float32)

    bg = float(np.median(polar))                       # colore del fondo quadrante
    dark = np.clip((bg - polar) / max(bg, 1.0), 0, 1)  # 0 = fondo, 1 = nero
    dark = np.where(dark > 0.15, dark, 0)              # sopprime rumore
    return angles, dark.mean(axis=1)


def refine_peak(angles, scores):
    """Baricentro circolare attorno al massimo -> angolo sub-passo."""
    n = len(scores)
    i = int(np.argmax(scores))
    half = int(round(4.0 / ANGLE_STEP))
    idx = np.arange(i - half, i + half + 1)
    w = scores[idx % n]
    w = np.clip(w - w.min(), 0, None)
    if w.sum() == 0:
        return float(angles[i])
    offs = (idx - i) * ANGLE_STEP
    return float((angles[i] + (w * offs).sum() / w.sum()) % 360)


# --------------------------------------------------------------------------
# Quadrantino piccolo (contagiri: 1 cifra = 1 mm)
# --------------------------------------------------------------------------
# Posizione del quadrantino rispetto al centro, in frazioni del raggio della ghiera
SMALL_OFFSET = (-0.37, -0.17)
SMALL_RADIUS = 0.20


def find_small_dial(gray, cx, cy, r_ghiera):
    """Centro e raggio del quadrantino piccolo.

    Parte da una posizione attesa (frazioni del raggio della ghiera) e cerca
    il cerchio stampato che massimizza lo scuro medio lungo la circonferenza.
    """
    ex = cx + SMALL_OFFSET[0] * r_ghiera
    ey = cy + SMALL_OFFSET[1] * r_ghiera
    er = SMALL_RADIUS * r_ghiera

    blur = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)
    bg = float(np.median(blur[int(ey - 2 * er):int(ey + 2 * er),
                              int(ex - 2 * er):int(ex + 2 * er)]))
    dark = np.clip((bg - blur) / max(bg, 1.0), 0, 1)
    dark = np.where(dark > 0.08, dark, 0)
    dark = cv2.dilate(dark, np.ones((3, 3), np.uint8))   # tolleranza di 1 px sul bordo

    t = np.deg2rad(np.arange(0, 360, 4, dtype=np.float32))
    st, ct = np.sin(t), np.cos(t)
    span = int(round(0.18 * r_ghiera))
    best = (-1.0, ex, ey, er)
    for rr in np.arange(er * 0.85, er * 1.15, 1.0):
        for dy in range(-span, span + 1):
            for dx in range(-span, span + 1):
                px = np.rint(ex + dx + rr * ct).astype(int)
                py = np.rint(ey + dy + rr * st).astype(int)
                # percentile basso: serve un anello scuro su TUTTA la circonferenza
                # (un'ombra o la lancetta grande coprono solo un arco)
                sc = np.percentile(dark[py, px], 30)
                if sc > best[0]:
                    best = (sc, ex + dx, ey + dy, rr)
    return float(best[1]), float(best[2]), float(best[3])


def read_small_dial(gray, scx, scy, sr, zero_deg, ccw=True):
    """Ritorna (valore_in_mm_continuo 0..10, angolo_lancetta)."""
    angles, scores = needle_scores(gray, scx, scy, 0.32 * sr, 0.55 * sr)
    ang = refine_peak(angles, scores)
    delta = (zero_deg - ang) % 360 if ccw else (ang - zero_deg) % 360
    return delta / 360.0 * 10.0, ang


# --------------------------------------------------------------------------
# Calibrazione interattiva dello zero
# --------------------------------------------------------------------------
def calibrate_zero(img, cx, cy):
    clicked = {}

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked["pt"] = (x, y)

    win = "Clicca sulla tacca dello 0 (ESC per annullare)"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    while "pt" not in clicked:
        cv2.imshow(win, img)
        if cv2.waitKey(30) == 27:
            break
    cv2.destroyAllWindows()
    if "pt" not in clicked:
        return None
    x, y = clicked["pt"]
    return math.degrees(math.atan2(x - cx, -(y - cy))) % 360


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("outfile")
    ap.add_argument("--zero-angle", type=float, default=56.0,
                    help="angolo dello 0 in gradi, orario da ore 12 (default 56, "
                         "valido per l'immagine di esempio)")
    ap.add_argument("--calibra", action="store_true",
                    help="scegli lo zero cliccando sull'immagine")
    ap.add_argument("--ccw", action="store_true",
                    help="scala numerata in senso antiorario (default: orario)")
    ap.add_argument("--center", help="cx,cy (altrimenti rilevato automaticamente)")
    ap.add_argument("--radius", type=float, help="raggio utile del quadrante in px")
    ap.add_argument("--turns", type=int, default=None,
                    help="mm interi (default: letti dal quadrantino piccolo)")
    ap.add_argument("--small-zero-angle", type=float, default=90.0,
                    help="angolo dello 0 del quadrantino piccolo (default 90 = ore 3)")
    ap.add_argument("--small-cw", action="store_true",
                    help="quadrantino piccolo numerato in senso orario (default: antiorario)")
    ap.add_argument("--out", default="risultato.png", help="immagine di debug")
    args = ap.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        sys.exit(f"Impossibile aprire {args.image}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    outfile = cv2.imread(args.outfile)
    if outfile is None:
        sys.exit(f"Impossibile aprire {args.outfile}")

    cx, cy, r = find_dial(gray)
    if args.center:
        cx, cy = map(float, args.center.split(","))
    if args.radius:
        r = args.radius
    else:
        r *= 0.90        # raggio della sola faccia bianca (esclude la ghiera)

    zero = args.zero_angle
    if args.calibra:
        z = calibrate_zero(img, cx, cy)
        if z is not None:
            zero = z
            print(f"Zero calibrato: {zero:.1f} deg")

    # La lancetta si cerca tra 30% e 85% del raggio (evita perno e tacche)
    angles, scores = needle_scores(gray, cx, cy, 0.30 * r, 0.85 * r)
    needle = refine_peak(angles, scores)

    delta = (zero - needle) % 360 if args.ccw else (needle - zero) % 360
    divisions = delta / 360.0 * DIVISIONS
    frac_mm = divisions * MM_PER_DIV            # parte sotto il mm (0..1)

    # Quadrantino piccolo: stima grezza dei mm, poi si "aggancia" ai giri interi
    # usando la lancetta grande (che e' molto piu' precisa).
    scx, scy, sr = find_small_dial(gray, cx, cy, r / 0.90)
    small_val, small_ang = read_small_dial(
        gray, scx, scy, sr, args.small_zero_angle, ccw=not args.small_cw)
    if args.turns is not None:
        turns = args.turns
    else:
        turns = int(round(small_val - frac_mm)) % 10
    mm = turns + frac_mm

    # print(f"Centro: ({cx:.1f}, {cy:.1f})  raggio: {r:.1f}px")
    # print(f"Angolo lancetta: {needle:.1f} deg  (zero a {zero:.1f} deg)")
    # print(f"Quadrantino piccolo: lancetta a {small_ang:.1f} deg -> {small_val:.2f} mm (grezzo)")
    print(f"Lettura: {turns} mm + {divisions:.1f} divisioni  ->  {mm:.3f} mm")

    with open(outfile, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now() :%d-%m-%Y %H-%M};{mm:.2f}\n")
    
    # # Immagine di debug
    # dbg = img.copy()
    # cv2.circle(dbg, (int(cx), int(cy)), int(r), (0, 255, 0), 1)
    # cv2.circle(dbg, (int(cx), int(cy)), 3, (0, 0, 255), -1)
    # tip = (int(cx + r * math.sin(math.radians(needle))),
    #        int(cy - r * math.cos(math.radians(needle))))
    # zp = (int(cx + r * math.sin(math.radians(zero))),
    #       int(cy - r * math.cos(math.radians(zero))))
    # cv2.line(dbg, (int(cx), int(cy)), tip, (0, 0, 255), 2)
    # cv2.line(dbg, (int(cx), int(cy)), zp, (255, 0, 0), 1)
    # cv2.circle(dbg, (int(scx), int(scy)), int(sr), (0, 200, 255), 1)
    # stip = (int(scx + sr * math.sin(math.radians(small_ang))),
    #         int(scy - sr * math.cos(math.radians(small_ang))))
    # cv2.line(dbg, (int(scx), int(scy)), stip, (0, 140, 255), 2)
    # cv2.putText(dbg, f"{mm:.3f} mm", (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
    #             1.0, (0, 0, 255), 2)
    # cv2.imwrite(args.out, dbg)
    # print(f"Immagine di debug: {args.out}")


if __name__ == "__main__":
    main()
