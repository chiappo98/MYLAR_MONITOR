# Monitoraggio comparatore + temperatura

Catena completa: **foto → lettura comparatore (OpenCV) → file di testo → Telegraf → InfluxDB → Grafana**.

```
 camera ──► monitor_mylar.py ──► misure.txt ────┐
 sensore BME280 ─────────► temperature.txt ─────┤
                                                ▼
                                            Telegraf ──► InfluxDB ──► Grafana
                                        (legge i file)  (bucket)     (dashboard)
```

---

## Parametri da tenere a portata di mano

> | Parametro | Valore |
> |---|---|
> | **Organization** | **`MylarSensors`** |
> | **Bucket** | **`sensoriMylar`** |
> | Measurement (misura) | `misura`, field `valore` |
> | Measurement (temperatura) | `temperatura`, field `valore` |
> | InfluxDB | `http://localhost:8086` (versione **2.x**, non la 3) |
> | Grafana | `http://localhost:3000` |
> | Query language in Grafana | **Flux** |

Il nome dell'**organization** e del **bucket** devono essere **identici** (maiuscole comprese) in tre posti:
InfluxDB, `telegraf.conf` e la data source di Grafana.

Account InfluxDB: username *mylarsensor*, password *mylarsensor*
---

## 1. Requisiti

- Windows con **InfluxDB OSS v2**, **Telegraf** e **Grafana OSS** installati.
- Python 3.10+ con:

```powershell
pip install opencv-python pyserial numpy
```

### Percorsi usati in questa guida

| Cosa | Percorso |
|---|---|
| Foto del comparatore | `F:/MYLAR_MONITOR/foto` |
| File di testo letti da Telegraf | `F:/MYLAR_MONITOR/misure.txt` e `F:/MYLAR_MONITOR/temperature.txt` |
| **Configurazione di Telegraf** | **`C:\Program Files\Telegraf\telegraf.conf`** |
| File di stato di Telegraf | `C:/dati/telegraf.state` |
| Script di acquisizione | `monitor_mylar.py` (adatta al nome reale) |
| Script di riprocessamento | `reprocess.py` |

> I nomi dei file di testo devono coincidere con quelli elencati in `files = [...]` di `telegraf.conf`.
> Lo script di acquisizione usa per default `misure.txt` e `temperature.txt` nella cartella corrente:
> impostali con le variabili d'ambiente come indicato al punto 4, altrimenti Telegraf non troverà i dati.

---

## 2. Avvio dei servizi (ordine consigliato)

### 2.1 InfluxDB
Se non è registrato come servizio, avvialo da terminale (da lasciare aperto):

```powershell
cd C:\influxdb
.\influxd.exe
```

Interfaccia web: <http://localhost:8086> oppure <http://131.154.102.143:8086>
Al primo avvio il setup chiede utente, password, **organization `MylarSensors`** e **bucket `sensoriMylar`**.

### 2.2 Token di InfluxDB
In *Load Data → API Tokens* servono:

- un token con permesso di **scrittura** sul bucket `sensoriMylar` → per **Telegraf**
- un token con permesso di **lettura** sul bucket e sui *Buckets* (oppure All Access per le prove) → per **Grafana**

Noi abbiamo un token All access:
```
KL5qvRBdwlNlWTmRgxHPlFc9WVORxZYgDzvdAwNgZ9cR130UG2zH6uMGPwuLjIQHV8EuceJf7D7pFxMEENrvzQ==
```

### 2.3 Telegraf

**File di configurazione:** `C:\Program Files\Telegraf\telegraf.conf`

Contenuto di riferimento (il file deve avere **una sola** sezione `[agent]`; inserisci il tuo token in `IL_TUO_TOKEN`):

```toml
[agent]
  interval = "600s"
  statefile = "F:/MYLAR_MONITOR/telegraf.state"

[[inputs.tail]]
  files = ["F:/MYLAR_MONITOR/misure.txt"]
  initial_read_offset = "saved-or-beginning"
  watch_method = "poll"
  name_override = "misura"
  data_format = "csv"
  csv_delimiter = ";"
  csv_header_row_count = 0
  csv_column_names = ["ora", "valore"]
  csv_column_types = ["string", "float"]
  csv_timestamp_column = "ora"
  csv_timestamp_format = "02-01-2006 15-04"
  csv_timezone = "Europe/Rome"

[[inputs.tail]]
  files = ["F:/MYLAR_MONITOR/temperature.txt"]
  initial_read_offset = "saved-or-beginning"
  watch_method = "poll"
  name_override = "temperatura"
  data_format = "csv"
  csv_delimiter = ";"
  csv_header_row_count = 0
  csv_column_names = ["ora", "valore"]
  csv_column_types = ["string", "float"]
  csv_timestamp_column = "ora"
  csv_timestamp_format = "02-01-2006 15-04"
  csv_timezone = "Europe/Rome"

[[outputs.influxdb_v2]]
  urls = ["http://localhost:8086"]
  token = "KL5qvRBdwlNlWTmRgxHPlFc9WVORxZYgDzvdAwNgZ9cR130UG2zH6uMGPwuLjIQHV8EuceJf7D7pFxMEENrvzQ=="
  organization = "MylarSensors"
  bucket = "sensoriMylar"
```

Note:
- Nei percorsi Windows usa la barra normale `/`.
- Salva il file in **UTF-8 senza BOM**.
- Da Telegraf 1.38 l'opzione `from_beginning` non esiste più: si usa `initial_read_offset`.
- `saved-or-beginning` + `statefile`: al primo avvio legge i file dall'inizio, ai riavvii riprende da dove era arrivato.

**Prova in primo piano** (PowerShell come amministratore). Cerca righe come `Wrote batch of N metrics`; interrompi con `Ctrl+C`:

```powershell
cd "C:\Program Files\Telegraf"
.\telegraf.exe --config telegraf.conf --debug
```

**Installazione come servizio Windows** (una sola volta):

```powershell
cd "C:\Program Files\Telegraf"
.\telegraf.exe --service install --config "C:\Program Files\Telegraf\telegraf.conf"
```

**Gestione del servizio:**

```powershell
net start telegraf      # avvia
net stop telegraf       # ferma
net stop telegraf; net start telegraf   # riavvia (dopo ogni modifica a telegraf.conf)
.\telegraf.exe --service uninstall      # rimuove il servizio
```

I log del servizio sono nel *Visualizzatore eventi* (Registri di Windows → Applicazione).

### 2.4 Grafana
Parte da sola come servizio su <http://localhost:3000> oppure <http://131.154.102.74:3000> (login `admin` / `dune`).

Data source: *Connections → Data sources → Add → InfluxDB*

| Campo | Valore |
|---|---|
| Query language | **Flux** |
| URL | `http://131.154.102.143:8086` |
| **Organization** | **`MylarSensors`** |
| Token | token di **lettura** |
| **Default Bucket** | **`sensoriMylar`** |

Poi *Save & test*. Basic auth e With Credentials restano disattivati.

---

## 3. Acquisizione (foto, misura, temperatura)

Lo script `monitor_mylar.py` ogni 10 minuti:
1. scatta e ritaglia la foto (`dd_mm_yyyy_hh_mm.png`);
2. legge il comparatore dalla foto e appende `dd-mm-yyyy hh-mm;valore` al file delle misure;
3. legge la temperatura dal sensore e la appende al file delle temperature.

## 4. Avvio dello script di acquisizione

```powershell
$env:CARTELLA_IMMAGINI = "F:/MYLAR_MONITOR/foto"
$env:FILE_MISURE       = "F:/MYLAR_MONITOR/misure.txt"
$env:FILE_TEMPERATURE  = "F:/MYLAR_MONITOR/temperature.txt"
$env:PORTA_SENSORE     = "COM4"
$env:CAMERA            = "0"
$env:SALVA_DEBUG       = "1"     # opzionale: salva anche le immagini annotate
python monitor_mylar.py
```

Si ferma con `Ctrl+C`. I file di testo devono essere **solo in aggiunta** (append): Telegraf non funziona correttamente se vengono riscritti da zero.

| Variabile | Significato | Default |
|---|---|---|
| `CARTELLA_IMMAGINI` | cartella delle foto | `F:/MYLAR_MONITOR/foto` |
| `CAMERA` | indice webcam o URL camera IP | `0` |
| `INTERVALLO_MIN` | intervallo in minuti | `10` |
| `PORTA_SENSORE` | porta seriale del BME280 | `COM4` |
| `FILE_TEMPERATURE` | file delle temperature | `temperature.txt` |
| `FILE_MISURE` | file delle misure | `misure.txt` |
| `CROP_LARGHEZZA_PCT` / `CROP_ALTEZZA_PCT` | ritaglio centrale | `50` / `100` |
| `ZERO_ANGLE` | angolo dello 0 della scala grande | `56` |
| `SALVA_DEBUG` | `1` = salva `*_debug.png` | `0` |

---

## 5. Visualizzare i dati in Grafana

Nel pannello usa questa query (per la temperatura sostituisci `misura` con `temperatura`):

```flux
from(bucket: "sensoriMylar")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "misura" and r._field == "valore")
  |> map(fn: (r) => ({ r with _field: "lunghezza" }))
```

- Imposta l'intervallo di tempo in alto a destra in modo che comprenda i dati (es. *Last 7 days*).
- Imposta l'auto-refresh della dashboard (es. 1m).

---

## 6. Riprocessare le foto già acquisite

`reprocess.py` rilegge le foto con l'algoritmo corrente e scrive un file di backup, **senza toccare** i file originali. Importa le funzioni da `monitor_mylar.py`, quindi i due file devono stare nella stessa cartella.

```powershell
$env:CARTELLA_IMMAGINI = "F:/MYLAR_MONITOR/foto"
$env:FILE_BACKUP       = "F:/MYLAR_MONITOR/misure_backup.txt"

python reprocess.py                                              # tutte le foto
python reprocess.py --da "02-10-2026 15-00"                      # da data/ora in poi (inclusa)
python reprocess.py --da "02-10-2026 15-00" --fino "04-10-2026 12-00"
python reprocess.py --da "02-10-2026 15-00" --append             # aggiunge al backup invece di sovrascrivere
python reprocess.py --debug                                      # salva anche le immagini annotate
```

Formato di data: `dd-mm-yyyy hh-mm`. Le foto che falliscono vengono saltate e segnalate.

### Sostituire in Grafana i dati vecchi con quelli riprocessati

1. Controlla a campione alcuni valori del backup (con `--debug` vedi la lancetta rilevata).
2. Ferma lo script di acquisizione e Telegraf (`net stop telegraf`).
3. Sostituisci in `misure.txt` le righe interessate con quelle del backup.
4. Cancella `F:/MYLAR_MONITOR/telegraf.state`.
5. Riavvia Telegraf (`net start telegraf`).

A parità di timestamp InfluxDB **sovrascrive** il valore, quindi non si creano duplicati.
Per ripartire da zero puoi prima cancellare la misurazione `misura` dal bucket `sensoriMylar`.

---

## 7. Checklist di avvio quotidiano

1. InfluxDB attivo (<http://localhost:8086> risponde)
2. `net start telegraf`
3. Grafana attivo (<http://localhost:3000>)
4. `python monitor_mylar.py` con le variabili d'ambiente del punto 4
