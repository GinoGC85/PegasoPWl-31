"""Costanti e funzioni condivise da generatore, procedura ETL, query e dashboard.

Tenere qui i valori "di dominio" (zone, categorie, soglia di ritardo) evita che i
diversi script usino definizioni diverse della stessa cosa.
"""
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Percorsi del progetto
# ---------------------------------------------------------------------------
RADICE = Path(__file__).resolve().parent.parent
DIR_DATI_GREZZI = RADICE / "data" / "raw"
DIR_SCARTI = RADICE / "data" / "scarti"
DIR_DW = RADICE / "data" / "dw"
FILE_DW = DIR_DW / "casalampo_dw.sqlite"
FILE_SCHEMA = RADICE / "sql" / "schema.sql"
FILE_KPI = RADICE / "sql" / "kpi.sql"
FILE_REPORT_QUALITA = RADICE / "data" / "qualita_dati.json"
FILE_ANOMALIE = DIR_DATI_GREZZI / "anomalie_introdotte.json"


def uri_sola_lettura(percorso):
    """Indirizzo per aprire il database SQLite in sola lettura (funziona anche con spazi nel percorso)."""
    return Path(percorso).resolve().as_uri() + "?mode=ro"


FILE_CSV = {
    "clienti": DIR_DATI_GREZZI / "clienti.csv",
    "fornitori": DIR_DATI_GREZZI / "fornitori.csv",
    "operatori": DIR_DATI_GREZZI / "operatori.csv",
    "richieste": DIR_DATI_GREZZI / "richieste.csv",
}

# ---------------------------------------------------------------------------
# Valori di dominio (forma "canonica" usata nell'archivio dati)
# ---------------------------------------------------------------------------
ZONE = ["Centro", "Nord", "Est", "Sud", "Ovest", "Provincia"]

CATEGORIE = [
    "Idraulica",
    "Elettricità",
    "Caldaie e riscaldamento",
    "Climatizzazione",
    "Serrature e infissi",
    "Elettrodomestici",
]

# Ogni categoria di servizio richiede un operatore con l'attività corrispondente.
ATTIVITA_PER_CATEGORIA = {
    "Idraulica": "Idraulico",
    "Elettricità": "Elettricista",
    "Caldaie e riscaldamento": "Tecnico caldaie",
    "Climatizzazione": "Tecnico climatizzazione",
    "Serrature e infissi": "Fabbro",
    "Elettrodomestici": "Tecnico elettrodomestici",
}
ATTIVITA = list(ATTIVITA_PER_CATEGORIA.values())

STATI = ["Completata", "Annullata"]

# Promessa commerciale della startup: intervento concluso entro 4 ore dalla richiesta.
# Oltre questa soglia la richiesta completata è considerata "in ritardo".
SOGLIA_RITARDO_MIN = 240

# Membro "sconosciuto" delle dimensioni: accoglie i riferimenti non (ancora)
# presenti in anagrafica, così il fatto non va perso.
SK_SCONOSCIUTO = -1
ETICHETTA_SCONOSCIUTO = "Non in anagrafica"

MESI = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]
MESI_BREVI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


# ---------------------------------------------------------------------------
# Calendario
# ---------------------------------------------------------------------------
def pasqua(anno: int) -> date:
    """Data della Pasqua (calendario gregoriano, algoritmo di Meeus/Jones/Butcher)."""
    a = anno % 19
    b, c = divmod(anno, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mese = (h + l - 7 * m + 114) // 31
    giorno = ((h + l - 7 * m + 114) % 31) + 1
    return date(anno, mese, giorno)


def festivi_nazionali(anno: int) -> set:
    """Festività nazionali italiane dell'anno indicato (domeniche escluse)."""
    fissi = [(1, 1), (1, 6), (4, 25), (5, 1), (6, 2), (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)]
    giorni = {date(anno, m, g) for m, g in fissi}
    giorni.add(pasqua(anno) + timedelta(days=1))  # Lunedì dell'Angelo
    if anno >= 2026:
        giorni.add(date(anno, 10, 4))  # San Francesco d'Assisi, festa nazionale dal 2026
    return giorni


def e_festivo(giorno: date) -> bool:
    return giorno in festivi_nazionali(giorno.year)
