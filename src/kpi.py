"""Calcola gli indicatori principali eseguendo le interrogazioni di sql/kpi.sql.

Le stesse funzioni sono usate dalla dashboard: gli indicatori hanno così
un'unica definizione, scritta in SQL.

Uso:
    python src/kpi.py                                   # tutto il periodo, tutte le zone e categorie
    python src/kpi.py --da 2026-06-01 --a 2026-08-31 --zona Centro --zona Nord
"""
import argparse
import re
import sqlite3
from pathlib import Path

import pandas as pd

from comune import FILE_DW, FILE_KPI, uri_sola_lettura


def carica_query(percorso=FILE_KPI):
    """Legge il file SQL e restituisce un dizionario {nome: testo della query}."""
    testo = Path(percorso).read_text(encoding="utf-8")
    parti = re.split(r"^-- name:\s*(\w+)\s*$", testo, flags=re.MULTILINE)
    return {nome: sql.strip() for nome, sql in zip(parti[1::2], parti[2::2])}


def connetti(percorso=FILE_DW):
    if not Path(percorso).exists():
        raise FileNotFoundError(f"Archivio non trovato: {percorso}. Eseguire prima src/etl.py")
    return sqlite3.connect(uri_sola_lettura(percorso), uri=True)  # sola lettura


def valori_disponibili(con):
    """Periodo coperto e valori presenti, utili come filtri predefiniti."""
    data_min, data_max = con.execute("SELECT MIN(data), MAX(data) FROM v_richieste").fetchone()
    zone = [r[0] for r in con.execute("SELECT DISTINCT zona FROM v_richieste ORDER BY zona")]
    categorie = [r[0] for r in con.execute("SELECT DISTINCT categoria FROM v_richieste ORDER BY categoria")]
    return data_min, data_max, zone, categorie


def parametri_filtro(con, data_da=None, data_a=None, zone=None, categorie=None):
    """Costruisce i parametri delle query; i filtri non indicati comprendono tutto."""
    data_min, data_max, tutte_zone, tutte_categorie = valori_disponibili(con)
    return {
        "data_da": str(data_da or data_min),
        "data_a": str(data_a or data_max),
        "zone": "|" + "|".join(zone if zone else tutte_zone) + "|",
        "categorie": "|" + "|".join(categorie if categorie else tutte_categorie) + "|",
    }


def esegui(con, query, nome, parametri):
    return pd.read_sql_query(query[nome], con, params=parametri)


def euro(valore):
    """Formato italiano: 72.042,50 €"""
    testo = f"{valore:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{testo} €"


def main():
    parser = argparse.ArgumentParser(description="Indicatori principali di CasaLampo.")
    parser.add_argument("--da", help="data iniziale AAAA-MM-GG")
    parser.add_argument("--a", help="data finale AAAA-MM-GG")
    parser.add_argument("--zona", action="append", help="zona da includere (ripetibile)")
    parser.add_argument("--categoria", action="append", help="categoria da includere (ripetibile)")
    args = parser.parse_args()

    query = carica_query()
    with connetti() as con:
        p = parametri_filtro(con, args.da, args.a, args.zona, args.categoria)
        s = esegui(con, query, "sintesi", p).iloc[0]
        r = esegui(con, query, "clienti_ricorrenti", p).iloc[0]
        print(f"Periodo: {p['data_da']} - {p['data_a']}")
        print(f"Zone: {p['zone'].strip('|').replace('|', ', ')}")
        print(f"Categorie: {p['categorie'].strip('|').replace('|', ', ')}\n")
        print(f"Richieste totali ........... {int(s.richieste_totali)}")
        print(f"  di cui completate ........ {int(s.richieste_completate)}")
        print(f"  di cui annullate ......... {int(s.richieste_annullate)} ({s.perc_annullate} %)")
        print(f"Incassi totali ............. {euro(s.incassi_totali)}")
        print(f"Importo medio per intervento {euro(s.importo_medio)}")
        print(f"Tempo medio di erogazione .. {s.tempo_medio_min} min")
        print(f"Interventi in ritardo ...... {s.perc_in_ritardo} % (oltre 240 minuti)")
        print(f"Clienti attivi ............. {int(s.clienti_attivi)}")
        print(f"Clienti che tornano ........ {r.perc_clienti_ricorrenti} % "
              f"({int(r.clienti_ricorrenti)} su {int(r.clienti_serviti)} clienti serviti)\n")

        pd.set_option("display.width", 120)
        for nome, titolo in [
            ("andamento_mensile", "Andamento mensile"),
            ("top_fornitori", "Primi cinque fornitori per incassi"),
            ("per_zona", "Richieste per zona"),
            ("per_categoria", "Richieste per categoria di servizio"),
            ("ritardi_per_fascia", "Tempi e ritardi per fascia oraria"),
            ("concentrazione_fornitori", "Concentrazione degli incassi"),
        ]:
            print(titolo)
            print(esegui(con, query, nome, p).to_string(index=False), "\n")


if __name__ == "__main__":
    main()
