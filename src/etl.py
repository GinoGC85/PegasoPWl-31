"""Procedura ETL (Estrazione, Trasformazione, Caricamento) di CasaLampo.

1. Estrazione: legge i quattro CSV grezzi come testo, senza conversioni automatiche,
   così ogni trasformazione è esplicita e controllabile.
2. Trasformazione: pulisce e uniforma i valori, elimina i duplicati, scarta le righe
   non ammissibili (salvandole con il motivo), collega ogni richiesta alle
   anagrafiche tramite chiavi surrogate e costruisce il calendario.
3. Caricamento: ricrea lo schema a stella in SQLite (sql/schema.sql) e inserisce i
   dati in un'unica transazione; poi esegue i controlli di integrità.

Al termine scrive il rapporto sulla qualità dei dati (data/qualita_dati.json) e il
file delle righe scartate (data/scarti/richieste_scartate.csv).

Uso:  python src/etl.py
"""
import json
import sqlite3
from datetime import datetime

import pandas as pd

from comune import (
    ATTIVITA,
    ATTIVITA_PER_CATEGORIA,
    CATEGORIE,
    DIR_DW,
    DIR_SCARTI,
    ETICHETTA_SCONOSCIUTO,
    FILE_CSV,
    FILE_DW,
    FILE_REPORT_QUALITA,
    FILE_SCHEMA,
    GIORNI,
    MESI,
    SK_SCONOSCIUTO,
    SOGLIA_RITARDO_MIN,
    STATI,
    ZONE,
    e_festivo,
)

# Varianti note delle categorie usate dall'archivio fornitori.
SINONIMI_CATEGORIA = {
    "Impianti elettrici": "Elettricità",
    "Riscaldamento": "Caldaie e riscaldamento",
    "Aria condizionata": "Climatizzazione",
    "Fabbro": "Serrature e infissi",
}
FORMATI_DATA = ["%Y-%m-%d", "%d/%m/%Y"]
FORMATI_DATA_ORA = ["%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"]


# ---------------------------------------------------------------------------
# Funzioni di pulizia riutilizzabili
# ---------------------------------------------------------------------------
def leggi_csv(percorso):
    return pd.read_csv(percorso, dtype=str, keep_default_na=False, encoding="utf-8")


def pulisci_testo(serie):
    """Toglie gli spazi iniziali e finali e riduce a uno quelli multipli."""
    return serie.str.strip().str.replace(r"\s+", " ", regex=True)


def uniforma(serie, canonici, sinonimi=None):
    """Riporta i valori alla forma canonica ignorando maiuscole, minuscole e spazi.
    Restituisce la serie uniformata (NaN se il valore non è riconosciuto),
    il numero di valori corretti e l'elenco dei valori non riconosciuti."""
    mappa = {v.casefold(): v for v in canonici}
    for variante, canonico in (sinonimi or {}).items():
        mappa[variante.casefold()] = canonico
    risultato = pulisci_testo(serie).str.casefold().map(mappa)
    corretti = int((risultato.notna() & (risultato != serie)).sum())
    non_riconosciuti = sorted(set(serie[risultato.isna()]))
    return risultato, corretti, non_riconosciuti


def converti_date(serie, formati):
    """Interpreta date scritte in formati diversi; il primo formato è quello standard.
    Restituisce le date e il numero di valori convertiti da un formato alternativo."""
    testo = serie.str.strip()
    risultato = pd.to_datetime(testo, format=formati[0], errors="coerce")
    da_formato_alternativo = 0
    for formato in formati[1:]:
        mancanti = risultato.isna()
        alternativa = pd.to_datetime(testo[mancanti], format=formato, errors="coerce")
        da_formato_alternativo += int(alternativa.notna().sum())
        risultato = risultato.fillna(alternativa)
    return risultato, da_formato_alternativo


def converti_importi(serie):
    """Converte gli importi in numero accettando anche la virgola come separatore decimale."""
    testo = serie.str.strip()
    con_virgola = int(testo.str.contains(",", regex=False).sum())
    numeri = pd.to_numeric(testo.str.replace(",", ".", regex=False), errors="coerce")
    return numeri, con_virgola


# ---------------------------------------------------------------------------
# Trasformazione delle anagrafiche (dimensioni)
# ---------------------------------------------------------------------------
def riga_sconosciuta(colonne, chiave):
    """Membro 'sconosciuto' della dimensione, con chiave surrogata -1."""
    riga = {c: ETICHETTA_SCONOSCIUTO for c in colonne}
    riga[chiave] = SK_SCONOSCIUTO
    return riga


def togli_duplicati_su_chiave(df, chiave, esito):
    doppi = df.duplicated(subset=chiave, keep="first")
    esito["chiavi_duplicate_rimosse"] = int(doppi.sum())
    return df[~doppi].reset_index(drop=True)


def trasforma_clienti(grezzo):
    esito = {"righe_lette": len(grezzo)}
    df = togli_duplicati_su_chiave(grezzo.assign(id_cliente=grezzo["id_cliente"].str.strip()), "id_cliente", esito)
    zona, esito["zone_uniformate"], esito["zone_non_riconosciute"] = uniforma(df["zona"], ZONE)
    data, esito["date_convertite_da_gg_mm_aaaa"] = converti_date(df["data_iscrizione"], FORMATI_DATA)
    esito["date_non_valide"] = int(data.isna().sum())
    dim = pd.DataFrame({
        "sk_cliente": range(1, len(df) + 1),
        "id_cliente": df["id_cliente"],
        "zona": zona.fillna("Non indicata"),
        "data_iscrizione": data.dt.strftime("%Y-%m-%d"),
        "anno_mese_iscrizione": data.dt.strftime("%Y-%m"),
    })
    dim = pd.concat([pd.DataFrame([riga_sconosciuta(dim.columns, "sk_cliente")]), dim], ignore_index=True)
    dim.loc[0, ["data_iscrizione", "anno_mese_iscrizione"]] = None
    esito["righe_caricate"] = len(dim) - 1
    return dim, esito


def trasforma_fornitori(grezzo):
    esito = {"righe_lette": len(grezzo)}
    df = togli_duplicati_su_chiave(grezzo.assign(id_fornitore=grezzo["id_fornitore"].str.strip()), "id_fornitore", esito)
    nome = pulisci_testo(df["nome"])
    esito["nomi_ripuliti"] = int((nome != df["nome"]).sum())
    categoria, esito["categorie_uniformate"], esito["categorie_non_riconosciute"] = uniforma(
        df["categoria"], CATEGORIE, SINONIMI_CATEGORIA)
    zona, esito["zone_uniformate"], esito["zone_non_riconosciute"] = uniforma(df["zona"], ZONE)
    dim = pd.DataFrame({
        "sk_fornitore": range(1, len(df) + 1),
        "id_fornitore": df["id_fornitore"],
        "nome": nome,
        "categoria": categoria.fillna("Non indicata"),
        "zona": zona.fillna("Non indicata"),
    })
    dim = pd.concat([pd.DataFrame([riga_sconosciuta(dim.columns, "sk_fornitore")]), dim], ignore_index=True)
    esito["righe_caricate"] = len(dim) - 1
    return dim, esito


def trasforma_operatori(grezzo):
    esito = {"righe_lette": len(grezzo)}
    df = togli_duplicati_su_chiave(grezzo.assign(id_operatore=grezzo["id_operatore"].str.strip()), "id_operatore", esito)
    attivita, esito["attivita_uniformate"], esito["attivita_non_riconosciute"] = uniforma(df["tipo_attivita"], ATTIVITA)
    zona, esito["zone_uniformate"], esito["zone_non_riconosciute"] = uniforma(df["zona"], ZONE)
    dim = pd.DataFrame({
        "sk_operatore": range(1, len(df) + 1),
        "id_operatore": df["id_operatore"],
        "zona": zona.fillna("Non indicata"),
        "tipo_attivita": attivita.fillna("Non indicata"),
    })
    dim = pd.concat([pd.DataFrame([riga_sconosciuta(dim.columns, "sk_operatore")]), dim], ignore_index=True)
    esito["righe_caricate"] = len(dim) - 1
    return dim, esito


def costruisci_dim_tempo(prima_data, ultima_data):
    """Calendario continuo per mesi interi, dal primo all'ultimo mese con richieste."""
    inizio = prima_data.replace(day=1)
    fine = (pd.Timestamp(ultima_data) + pd.offsets.MonthEnd(0)).date()
    giorni = pd.date_range(inizio, fine, freq="D")
    return pd.DataFrame({
        "id_tempo": giorni.strftime("%Y%m%d").astype(int),
        "data": giorni.strftime("%Y-%m-%d"),
        "anno": giorni.year,
        "trimestre": giorni.quarter,
        "mese": giorni.month,
        "nome_mese": [MESI[m - 1] for m in giorni.month],
        "anno_mese": giorni.strftime("%Y-%m"),
        "settimana_iso": giorni.isocalendar().week.astype(int).to_numpy(),
        "giorno": giorni.day,
        "giorno_settimana": giorni.dayofweek + 1,
        "nome_giorno": [GIORNI[d] for d in giorni.dayofweek],
        "is_weekend": (giorni.dayofweek >= 5).astype(int),
        "is_festivo": [int(e_festivo(g.date())) for g in giorni],
    })


# ---------------------------------------------------------------------------
# Trasformazione delle richieste (fatti)
# ---------------------------------------------------------------------------
def trasforma_richieste(grezzo, dim_clienti, dim_fornitori, dim_operatori):
    esito = {"righe_lette": len(grezzo)}

    # 1) Duplicati: righe identiche (doppia esportazione) e identificativi ripetuti.
    #    Il confronto ignora gli spazi superflui; i valori originali restano in df.
    confronto = grezzo.apply(lambda colonna: colonna.str.strip())
    identiche = confronto.duplicated(keep="first")
    esito["duplicati_rimossi"] = int(identiche.sum())
    stesso_id = confronto[~identiche].duplicated(subset="id_richiesta", keep="first")
    esito["id_ripetuti_con_dati_diversi"] = int(stesso_id.sum())
    df = grezzo[~identiche][~stesso_id.to_numpy()].reset_index(drop=True)
    for colonna in ["id_richiesta", "id_cliente", "id_fornitore", "id_operatore"]:
        df[colonna] = df[colonna].str.strip()

    # 2) Uniformazione dei formati.
    stato, esito["stati_uniformati"], esito["stati_non_riconosciuti"] = uniforma(df["stato"], STATI)
    data_ora, esito["date_ora_convertite_da_gg_mm_aaaa"] = converti_date(df["data_ora"], FORMATI_DATA_ORA)
    importo, esito["importi_con_virgola_convertiti"] = converti_importi(df["importo"])
    tempo = pd.to_numeric(df["tempo_erogazione_min"], errors="coerce")

    # 3) Regole di validità: la prima regola violata dà il motivo dello scarto.
    completata = stato == "Completata"
    regole = [
        (stato.isna(), "stato non riconosciuto"),
        (data_ora.isna(), "data e ora non interpretabili"),
        (completata & importo.isna(), "importo mancante su richiesta completata"),
        (completata & (importo < 0), "importo negativo"),
        (completata & (importo == 0), "importo nullo su richiesta completata"),
        (completata & (tempo.isna() | (tempo <= 0)), "tempo di erogazione mancante o non valido"),
    ]
    motivo = pd.Series("", index=df.index, dtype=object)
    for condizione, descrizione in regole:
        motivo[condizione.fillna(False) & (motivo == "")] = descrizione
    da_scartare = motivo != ""
    scarti = df[da_scartare].assign(motivo=motivo[da_scartare])
    esito["scartate"] = scarti["motivo"].value_counts().to_dict()
    esito["scartate_totale"] = int(da_scartare.sum())

    validi = ~da_scartare
    df, stato, data_ora, importo, tempo = (x[validi] for x in (df, stato, data_ora, importo, tempo))
    completata = stato == "Completata"
    # Una richiesta annullata non produce incasso né tempo di erogazione.
    esito["annullate_con_importo_azzerato"] = int(((~completata) & importo.fillna(0).ne(0)).sum())

    # 4) Collegamento alle dimensioni tramite chiavi surrogate.
    def collega(colonna, dim, chiave_naturale, chiave_surrogata, nome):
        mappa = dict(zip(dim[chiave_naturale], dim[chiave_surrogata]))
        sk = df[colonna].map(mappa)
        esito[f"{nome}_non_in_anagrafica"] = int(sk.isna().sum())
        return sk.fillna(SK_SCONOSCIUTO).astype(int)

    sk_cliente = collega("id_cliente", dim_clienti, "id_cliente", "sk_cliente", "clienti")
    sk_fornitore = collega("id_fornitore", dim_fornitori, "id_fornitore", "sk_fornitore", "fornitori")
    sk_operatore = collega("id_operatore", dim_operatori, "id_operatore", "sk_operatore", "operatori")

    # 5) Controlli di coerenza tra sistemi (segnalati, non bloccanti).
    iscrizione = pd.to_datetime(sk_cliente.map(dict(zip(dim_clienti["sk_cliente"], dim_clienti["data_iscrizione"]))))
    esito["richieste_precedenti_all_iscrizione"] = int((data_ora.dt.normalize() < iscrizione).sum())
    categoria = sk_fornitore.map(dict(zip(dim_fornitori["sk_fornitore"], dim_fornitori["categoria"])))
    attivita = sk_operatore.map(dict(zip(dim_operatori["sk_operatore"], dim_operatori["tipo_attivita"])))
    noto = sk_operatore != SK_SCONOSCIUTO
    esito["operatori_con_attivita_incoerente"] = int((noto & (attivita != categoria.map(ATTIVITA_PER_CATEGORIA))).sum())

    # 6) Campi derivati e misure.
    fatti = pd.DataFrame({
        "id_richiesta": df["id_richiesta"],
        "id_tempo": data_ora.dt.strftime("%Y%m%d").astype(int),
        "sk_cliente": sk_cliente,
        "sk_fornitore": sk_fornitore,
        "sk_operatore": sk_operatore,
        "data_ora": data_ora.dt.strftime("%Y-%m-%d %H:%M:%S"),
        "ora": data_ora.dt.hour.astype(int),
        "stato": stato,
        "importo": importo.where(completata, 0.0).round(2),
        "tempo_erogazione_min": tempo.where(completata).astype("Int64"),
        "flag_completata": completata.astype(int),
        "flag_annullata": (~completata).astype(int),
        "flag_ritardo": (completata & (tempo > SOGLIA_RITARDO_MIN)).astype(int),
    }).sort_values(["data_ora", "id_richiesta"]).reset_index(drop=True)
    esito["righe_caricate"] = len(fatti)
    return fatti, scarti, esito, (data_ora.min().date(), data_ora.max().date())


# ---------------------------------------------------------------------------
# Caricamento e controlli finali
# ---------------------------------------------------------------------------
def righe_per_sqlite(df):
    """Converte un DataFrame in tuple di tipi Python nativi (None al posto dei mancanti)."""
    colonne = [[None if pd.isna(v) else v for v in df[c].tolist()] for c in df.columns]
    return list(zip(*colonne))


def carica(tabelle):
    DIR_DW.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(FILE_DW)
    try:
        con.executescript(FILE_SCHEMA.read_text(encoding="utf-8"))
        con.execute("PRAGMA foreign_keys = ON")
        with con:  # transazione unica: o si carica tutto o niente
            for nome, df in tabelle.items():
                colonne = ", ".join(df.columns)
                segnaposto = ", ".join("?" * len(df.columns))
                con.executemany(f"INSERT INTO {nome} ({colonne}) VALUES ({segnaposto})", righe_per_sqlite(df))
        con.execute("VACUUM")
    finally:
        con.close()


def verifica():
    """Controlli di integrità sull'archivio appena caricato."""
    con = sqlite3.connect(FILE_DW)
    try:
        q = lambda sql: con.execute(sql).fetchone()[0]
        controlli = {
            "righe_fact_richieste": q("SELECT COUNT(*) FROM fact_richieste"),
            "id_richiesta_duplicati": q("SELECT COUNT(*) - COUNT(DISTINCT id_richiesta) FROM fact_richieste"),
            "violazioni_chiavi_esterne": len(con.execute("PRAGMA foreign_key_check").fetchall()),
            "annullate_con_incasso": q("SELECT COUNT(*) FROM fact_richieste WHERE stato = 'Annullata' AND importo <> 0"),
            "giorni_mancanti_nel_calendario": q(
                "SELECT (julianday(MAX(data)) - julianday(MIN(data)) + 1) - COUNT(*) FROM dim_tempo"),
            "incassi_totali": q("SELECT ROUND(SUM(importo), 2) FROM fact_richieste"),
        }
    finally:
        con.close()
    return controlli


def main():
    print("Estrazione dei file CSV...")
    grezzi = {nome: leggi_csv(percorso) for nome, percorso in FILE_CSV.items()}

    print("Trasformazione...")
    dim_clienti, esito_clienti = trasforma_clienti(grezzi["clienti"])
    dim_fornitori, esito_fornitori = trasforma_fornitori(grezzi["fornitori"])
    dim_operatori, esito_operatori = trasforma_operatori(grezzi["operatori"])
    fatti, scarti, esito_richieste, (prima, ultima) = trasforma_richieste(
        grezzi["richieste"], dim_clienti, dim_fornitori, dim_operatori)
    dim_tempo = costruisci_dim_tempo(prima, ultima)

    print("Caricamento nel data warehouse SQLite...")
    carica({
        "dim_tempo": dim_tempo,
        "dim_cliente": dim_clienti,
        "dim_fornitore": dim_fornitori,
        "dim_operatore": dim_operatori,
        "fact_richieste": fatti,
    })
    controlli = verifica()

    DIR_SCARTI.mkdir(parents=True, exist_ok=True)
    scarti.to_csv(DIR_SCARTI / "richieste_scartate.csv", index=False, encoding="utf-8")
    rapporto = {
        "eseguito_il": datetime.now().isoformat(timespec="seconds"),
        "clienti": esito_clienti,
        "fornitori": esito_fornitori,
        "operatori": esito_operatori,
        "richieste": esito_richieste,
        "calendario": {"dal": str(dim_tempo["data"].iloc[0]), "al": str(dim_tempo["data"].iloc[-1]),
                       "giorni": len(dim_tempo)},
        "controlli_dopo_il_caricamento": controlli,
    }
    FILE_REPORT_QUALITA.write_text(json.dumps(rapporto, indent=2, ensure_ascii=False, default=int),
                                   encoding="utf-8")

    print(f"\nArchivio creato: {FILE_DW}")
    print(f"  dim_cliente    {len(dim_clienti) - 1:>4} righe (+1 membro 'sconosciuto')")
    print(f"  dim_fornitore  {len(dim_fornitori) - 1:>4} righe (+1 membro 'sconosciuto')")
    print(f"  dim_operatore  {len(dim_operatori) - 1:>4} righe (+1 membro 'sconosciuto')")
    print(f"  dim_tempo      {len(dim_tempo):>4} giorni ({rapporto['calendario']['dal']} - {rapporto['calendario']['al']})")
    print(f"  fact_richieste {len(fatti):>4} righe")
    print(f"Righe duplicate rimosse: {esito_richieste['duplicati_rimossi']}; "
          f"righe scartate: {esito_richieste['scartate_totale']} (dettaglio in data/scarti)")
    print(f"Operatori non ancora in anagrafica: {esito_richieste['operatori_non_in_anagrafica']} "
          f"(collegati al membro '{ETICHETTA_SCONOSCIUTO}')")
    esito_ok = controlli["id_richiesta_duplicati"] == 0 and controlli["violazioni_chiavi_esterne"] == 0
    print("Controlli di integrità:", "superati" if esito_ok else "NON superati, vedi qualita_dati.json")


if __name__ == "__main__":
    main()
