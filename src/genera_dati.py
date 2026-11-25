"""Generatore dei dati inventati di CasaLampo (startup di fantasia).

CasaLampo raccoglie via app le richieste di intervento tecnico a domicilio
(idraulica, elettricità, caldaie, climatizzazione, serrature, elettrodomestici)
e le assegna a un'impresa partner (fornitore) e a un tecnico (operatore).

Lo script produce quattro file CSV, uno per tipologia, che imitano le
esportazioni dei diversi sistemi della startup:

    data/raw/clienti.csv     (app dei clienti)
    data/raw/fornitori.csv   (archivio dei fornitori partner)
    data/raw/operatori.csv   (sistema di gestione degli operatori)
    data/raw/richieste.csv   (piattaforma che gestisce le richieste)

I dati sono interamente sintetici e non contengono informazioni personali.
Per mettere alla prova la procedura ETL vengono introdotte di proposito, in
quantità nota, alcune imperfezioni tipiche delle esportazioni reali (formati
di data diversi, maiuscole incoerenti, decimali con la virgola, righe
duplicate, valori non ammissibili, operatori non ancora in anagrafica).
Il riepilogo delle anomalie introdotte è salvato in anomalie_introdotte.json.

Uso:  python src/genera_dati.py [--seed 42] [--n-richieste 594] ...
"""
import argparse
import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from comune import (
    ATTIVITA_PER_CATEGORIA,
    CATEGORIE,
    DIR_DATI_GREZZI,
    FILE_ANOMALIE,
    FILE_CSV,
    ZONE,
    e_festivo,
)

# ---------------------------------------------------------------------------
# Ipotesi del "mondo simulato" (modificabili per esperimenti)
# ---------------------------------------------------------------------------
PESO_ZONA = {"Centro": 0.26, "Nord": 0.18, "Est": 0.15, "Sud": 0.16, "Ovest": 0.13, "Provincia": 0.12}

QUOTA_CATEGORIA = {
    "Idraulica": 0.24,
    "Elettricità": 0.19,
    "Caldaie e riscaldamento": 0.15,
    "Climatizzazione": 0.14,
    "Serrature e infissi": 0.12,
    "Elettrodomestici": 0.16,
}

# Moltiplicatori stagionali per mese (1 = gennaio); i mesi assenti valgono 1.
STAGIONALITA = {
    "Caldaie e riscaldamento": {1: 2.1, 2: 1.7, 3: 1.1, 4: 0.6, 5: 0.4, 6: 0.3,
                                7: 0.3, 8: 0.3, 9: 0.6, 10: 1.3, 11: 1.8, 12: 2.0},
    "Climatizzazione": {1: 0.2, 2: 0.2, 3: 0.4, 4: 0.8, 5: 1.4, 6: 2.4,
                        7: 2.9, 8: 2.4, 9: 1.1, 10: 0.4, 11: 0.2, 12: 0.2},
    "Idraulica": {12: 1.2, 1: 1.25, 2: 1.15},
}

PESO_GIORNO_SETTIMANA = [1.15, 1.10, 1.10, 1.05, 1.05, 0.80, 0.55]  # lunedì ... domenica
PESO_FESTIVO = 0.60
# Distribuzione delle richieste nelle 24 ore: picco al mattino e dopo l'orario di lavoro.
PESO_ORA = [0.3, 0.2, 0.15, 0.1, 0.1, 0.2, 0.5, 1.6, 3.2, 3.6, 3.0, 2.4,
            1.8, 1.6, 2.0, 2.2, 2.3, 2.7, 3.3, 3.1, 2.0, 1.2, 0.7, 0.5]
CRESCITA_MENSILE = 0.09  # la startup è in crescita: circa +9% di richieste al mese

# Comportamento dei clienti: chi si iscrive di solito fa presto la prima richiesta;
# una parte dei clienti serviti torna a richiedere il servizio.
PROB_NUOVO_CLIENTE = 0.68   # quota attesa di richieste fatte da clienti al primo utilizzo
GIORNI_DECADIMENTO = 40     # più è recente l'iscrizione, più è probabile la prima richiesta
PESO_RITORNO = 0.5          # chi è già tornato più volte tende a tornare ancora

# Tempo di erogazione: minuti tra l'invio della richiesta e la chiusura dell'intervento.
TEMPO_MEDIANO_MIN = {
    "Idraulica": 125, "Elettricità": 115, "Caldaie e riscaldamento": 150,
    "Climatizzazione": 155, "Serrature e infissi": 85, "Elettrodomestici": 135,
}
FATTORE_TEMPO_ZONA = {"Centro": 0.95, "Nord": 1.0, "Est": 1.0, "Sud": 1.05, "Ovest": 1.0, "Provincia": 1.28}
FATTORE_TEMPO_FUORI_ZONA = 1.22   # il tecnico arriva da un'altra zona
PROB_IMPREVISTO = 0.04            # guasto complesso, pezzo di ricambio mancante, traffico...

PREZZO_MEDIANO = {
    "Idraulica": 110, "Elettricità": 95, "Caldaie e riscaldamento": 140,
    "Climatizzazione": 165, "Serrature e infissi": 120, "Elettrodomestici": 105,
}
FATTORE_PREZZO_ZONA = {"Centro": 1.06, "Nord": 1.0, "Est": 1.0, "Sud": 1.0, "Ovest": 1.0, "Provincia": 0.96}

PREFISSI_FORNITORE = {
    "Idraulica": ["Idro", "Idrotecnica", "Tubi"],
    "Elettricità": ["Elettro", "Impianti", "Volt"],
    "Caldaie e riscaldamento": ["Termo", "Calor", "Termotecnica"],
    "Climatizzazione": ["Clima", "Aria", "Frigo"],
    "Serrature e infissi": ["Serrature", "Chiavi", "Infissi"],
    "Elettrodomestici": ["Tecno", "Ripara", "Elettrodomestici"],
}
SUFFISSI_FORNITORE = ["Service", "Pronto", "Express", "Point", "Lab", "Team", "Pro", "Più", "Casa", "Rapido"]

# Varianti "sporche" di alcuni valori, come comparirebbero in sistemi diversi.
SINONIMI_CATEGORIA = {
    "Elettricità": "Impianti elettrici",
    "Caldaie e riscaldamento": "Riscaldamento",
    "Climatizzazione": "Aria condizionata",
    "Serrature e infissi": "Fabbro",
}


# ---------------------------------------------------------------------------
# Funzioni di supporto
# ---------------------------------------------------------------------------
def normalizza(pesi):
    v = np.asarray(pesi, dtype=float)
    return v / v.sum()


def ripartisci(totale, pesi):
    """Ripartisce un intero secondo i pesi (metodo dei resti maggiori)."""
    quote = normalizza(pesi) * totale
    base = np.floor(quote).astype(int)
    resto = totale - base.sum()
    ordine = np.argsort(-(quote - base))
    base[ordine[:resto]] += 1
    return base


def mesi_dall_inizio(giorno, inizio):
    return (giorno - inizio).days / 30.44


def variante_testo(rng, testo):
    """Restituisce il testo con una imperfezione di formattazione casuale."""
    scelta = rng.integers(0, 4)
    if scelta == 0:
        return testo.lower()
    if scelta == 1:
        return testo.upper()
    if scelta == 2:
        return "  " + testo
    return testo + "  "


# ---------------------------------------------------------------------------
# Generazione delle anagrafiche
# ---------------------------------------------------------------------------
def genera_clienti(rng, n, inizio, fine):
    """Clienti registrati nell'app: una parte si iscrive alla lista d'attesa prima del lancio."""
    n_attesa = round(n * 0.15)
    giorni_attesa = pd.date_range(inizio - timedelta(days=92), inizio - timedelta(days=1)).date
    giorni_periodo = pd.date_range(inizio, fine).date
    pesi = [np.exp(CRESCITA_MENSILE * mesi_dall_inizio(g, inizio)) for g in giorni_periodo]
    iscrizioni = list(rng.choice(giorni_attesa, size=n_attesa))
    iscrizioni += list(rng.choice(giorni_periodo, size=n - n_attesa, p=normalizza(pesi)))
    iscrizioni.sort()  # gli identificativi crescono con la data di iscrizione
    zone = rng.choice(ZONE, size=n, p=normalizza([PESO_ZONA[z] for z in ZONE]))
    return pd.DataFrame({
        "id_cliente": [f"CL{i:04d}" for i in range(1, n + 1)],
        "zona": zone,
        "data_iscrizione": iscrizioni,
    })


def genera_fornitori(rng, n):
    celle = [(c, z) for c in CATEGORIE for z in ZONE]
    conteggi = ripartisci(n, [QUOTA_CATEGORIA[c] * PESO_ZONA[z] for c, z in celle])
    righe = []
    for (categoria, zona), k in zip(celle, conteggi):
        for _ in range(k):
            righe.append((categoria, zona))
    ordine = rng.permutation(len(righe))  # l'archivio non è ordinato per categoria
    righe = [righe[i] for i in ordine]
    fornitori = []
    for i, (categoria, zona) in enumerate(righe, start=1):
        nome = f"{rng.choice(PREFISSI_FORNITORE[categoria])} {rng.choice(SUFFISSI_FORNITORE)} {i:03d}"
        fornitori.append({
            "id_fornitore": f"FO{i:04d}",
            "nome": nome,
            "categoria": categoria,
            "zona": zona,
            "popolarita": rng.lognormal(0.0, 1.0),  # pochi partner ricevono molte richieste
        })
    return pd.DataFrame(fornitori)


def genera_operatori(rng, n):
    celle = [(c, z) for c in CATEGORIE for z in ZONE]
    conteggi = ripartisci(n, [QUOTA_CATEGORIA[c] * PESO_ZONA[z] for c, z in celle])
    righe = []
    for (categoria, zona), k in zip(celle, conteggi):
        righe += [(categoria, zona)] * int(k)
    ordine = rng.permutation(len(righe))
    righe = [righe[i] for i in ordine]
    return pd.DataFrame([{
        "id_operatore": f"OP{i:04d}",
        "zona": zona,
        "tipo_attivita": ATTIVITA_PER_CATEGORIA[categoria],
        "popolarita": rng.lognormal(0.0, 0.8),
    } for i, (categoria, zona) in enumerate(righe, start=1)])


# ---------------------------------------------------------------------------
# Generazione delle richieste
# ---------------------------------------------------------------------------
def campiona_giorni_e_categorie(rng, n, inizio, fine):
    """Estrae n coppie (giorno, categoria) da una distribuzione con crescita,
    stagionalità per categoria e minore attività nel fine settimana e nei festivi."""
    giorni = pd.date_range(inizio, fine).date
    pesi = np.zeros((len(giorni), len(CATEGORIE)))
    for i, g in enumerate(giorni):
        peso_giorno = np.exp(CRESCITA_MENSILE * mesi_dall_inizio(g, inizio))
        peso_giorno *= PESO_GIORNO_SETTIMANA[g.weekday()]
        if e_festivo(g):
            peso_giorno *= PESO_FESTIVO
        for j, c in enumerate(CATEGORIE):
            pesi[i, j] = peso_giorno * QUOTA_CATEGORIA[c] * STAGIONALITA.get(c, {}).get(g.month, 1.0)
    indici = rng.choice(pesi.size, size=n, p=normalizza(pesi.ravel()))
    coppie = sorted((giorni[k // len(CATEGORIE)], CATEGORIE[k % len(CATEGORIE)]) for k in indici)
    return coppie


def scegli_pesato(rng, candidati, pesi):
    return candidati[rng.choice(len(candidati), p=normalizza(pesi))]


def genera_richieste(rng, n, clienti, fornitori, operatori, inizio, fine):
    coppie = campiona_giorni_e_categorie(rng, n, inizio, fine)
    prob_zona = normalizza([PESO_ZONA[z] for z in ZONE])
    n_precedenti = {c: 0 for c in clienti["id_cliente"]}
    clienti_lista = clienti.to_dict("records")
    fornitori_per_cat = {c: fornitori[fornitori["categoria"] == c].to_dict("records") for c in CATEGORIE}
    operatori_per_att = {a: operatori[operatori["tipo_attivita"] == a].to_dict("records")
                         for a in ATTIVITA_PER_CATEGORIA.values()}

    righe = []
    for giorno, categoria in coppie:
        ora = int(rng.choice(24, p=normalizza(PESO_ORA)))
        istante = datetime(giorno.year, giorno.month, giorno.day, ora,
                           int(rng.integers(0, 60)), int(rng.integers(0, 60)))

        # Cliente: la zona segue il peso delle zone. Con probabilità PROB_NUOVO_CLIENTE
        # la richiesta arriva da un iscritto che non ha mai usato il servizio (di solito
        # iscritto da poco), altrimenti da un cliente che torna (chi è tornato più volte
        # tende a farlo ancora).
        zona = rng.choice(ZONE, p=prob_zona)
        iscritti = [c for c in clienti_lista if c["zona"] == zona and c["data_iscrizione"] <= giorno]
        if not iscritti:
            iscritti = [c for c in clienti_lista if c["data_iscrizione"] <= giorno]
        nuovi = [c for c in iscritti if n_precedenti[c["id_cliente"]] == 0]
        abituali = [c for c in iscritti if n_precedenti[c["id_cliente"]] > 0]
        if nuovi and (not abituali or rng.random() < PROB_NUOVO_CLIENTE):
            pesi = [np.exp(-(giorno - c["data_iscrizione"]).days / GIORNI_DECADIMENTO) for c in nuovi]
            cliente = scegli_pesato(rng, nuovi, pesi)
        else:
            pesi = [1.0 + PESO_RITORNO * (n_precedenti[c["id_cliente"]] - 1) for c in abituali]
            cliente = scegli_pesato(rng, abituali, pesi)
        zona = cliente["zona"]
        n_precedenti[cliente["id_cliente"]] += 1

        # Fornitore e operatore della categoria richiesta, di norma nella stessa zona.
        tutti_f = fornitori_per_cat[categoria]
        stessi_f = [f for f in tutti_f if f["zona"] == zona]
        pool_f = stessi_f if stessi_f and rng.random() < 0.90 else tutti_f
        fornitore = scegli_pesato(rng, pool_f, [f["popolarita"] for f in pool_f])

        tutti_o = operatori_per_att[ATTIVITA_PER_CATEGORIA[categoria]]
        stessi_o = [o for o in tutti_o if o["zona"] == zona]
        pool_o = stessi_o if stessi_o and rng.random() < 0.86 else tutti_o
        operatore = scegli_pesato(rng, pool_o, [o["popolarita"] for o in pool_o])
        fuori_zona = operatore["zona"] != zona

        # Tempo di erogazione.
        festivo = giorno.weekday() == 6 or e_festivo(giorno)
        tempo = rng.lognormal(np.log(TEMPO_MEDIANO_MIN[categoria]), 0.30)
        tempo *= FATTORE_TEMPO_ZONA[zona]
        tempo *= 1.18 if 17 <= ora <= 20 else (1.10 if ora >= 22 or ora <= 6 else 1.0)
        tempo *= 1.12 if festivo else (1.05 if giorno.weekday() == 5 else 1.0)
        tempo *= FATTORE_TEMPO_FUORI_ZONA if fuori_zona else 1.0
        if (categoria == "Climatizzazione" and giorno.month in (6, 7, 8)) or \
           (categoria == "Caldaie e riscaldamento" and giorno.month in (12, 1)):
            tempo *= 1.15  # picco stagionale: tecnici molto richiesti
        if rng.random() < PROB_IMPREVISTO:
            tempo += rng.uniform(90, 300)
        tempo = int(max(25, round(tempo)))

        # Probabilità di annullamento: cresce quando l'attesa si annuncia lunga.
        p_annullo = 0.045
        p_annullo += 0.07 if zona == "Provincia" else 0.0
        p_annullo += 0.03 if 17 <= ora <= 20 else 0.0
        p_annullo += 0.04 if fuori_zona else 0.0
        p_annullo += 0.03 if festivo else 0.0
        p_annullo += 0.03 if categoria == "Climatizzazione" and giorno.month in (6, 7, 8) else 0.0
        annullata = rng.random() < p_annullo

        # Importo: tariffa tipica della categoria, maggiorazioni notturne e festive.
        importo = rng.lognormal(np.log(PREZZO_MEDIANO[categoria]), 0.35) * FATTORE_PREZZO_ZONA[zona]
        importo *= 1.30 if ora >= 22 or ora <= 6 else 1.0
        importo *= 1.20 if festivo else 1.0
        importo = round(importo * 2) / 2  # importi arrotondati a 50 centesimi

        righe.append({
            "id_cliente": cliente["id_cliente"],
            "id_fornitore": fornitore["id_fornitore"],
            "id_operatore": operatore["id_operatore"],
            "categoria": categoria,
            "zona": zona,
            "data_ora": istante,
            "importo": 0.0 if annullata else importo,
            "tempo_erogazione_min": None if annullata else tempo,
            "stato": "Annullata" if annullata else "Completata",
        })

    richieste = pd.DataFrame(righe).sort_values("data_ora", kind="stable").reset_index(drop=True)
    richieste.insert(0, "id_richiesta", [f"RQ{i:05d}" for i in range(1, len(richieste) + 1)])
    return richieste


def aggiungi_operatori_recenti(rng, richieste, operatori, n_recenti, fine):
    """Simula tecnici registrati dopo l'ultima esportazione dell'anagrafica operatori:
    compaiono in alcune richieste recenti ma non nel file operatori.csv."""
    recenti = richieste[richieste["data_ora"].dt.date >= fine - timedelta(days=30)]
    scelte = rng.choice(recenti.index.to_numpy(), size=n_recenti, replace=False)
    nuovi = []
    for k, idx in enumerate(sorted(scelte), start=1):
        id_op = f"OP{len(operatori) + k:04d}"
        nuovi.append({
            "id_operatore": id_op,
            "zona": richieste.at[idx, "zona"],
            "tipo_attivita": ATTIVITA_PER_CATEGORIA[richieste.at[idx, "categoria"]],
        })
        richieste.at[idx, "id_operatore"] = id_op
    return nuovi


# ---------------------------------------------------------------------------
# Esportazione con imperfezioni controllate
# ---------------------------------------------------------------------------
def esporta(rng, clienti, fornitori, operatori, richieste, n_non_valide, n_duplicati):
    anomalie = {}

    # clienti.csv ---------------------------------------------------------
    cl = clienti.copy()
    mask_zona = rng.random(len(cl)) < 0.07
    cl.loc[mask_zona, "zona"] = [variante_testo(rng, z) for z in cl.loc[mask_zona, "zona"]]
    mask_data = rng.random(len(cl)) < 0.12
    cl["data_iscrizione"] = [d.strftime("%d/%m/%Y") if m else d.isoformat()
                             for d, m in zip(clienti["data_iscrizione"], mask_data)]
    anomalie["clienti"] = {"zone_da_uniformare": int(mask_zona.sum()),
                           "date_formato_gg_mm_aaaa": int(mask_data.sum())}

    # fornitori.csv -------------------------------------------------------
    fo = fornitori.drop(columns="popolarita").copy()
    mask_sin = rng.random(len(fo)) < 0.04
    mask_sin &= fo["categoria"].isin(list(SINONIMI_CATEGORIA)).to_numpy()
    mask_cat = (rng.random(len(fo)) < 0.08) & ~mask_sin
    fo.loc[mask_sin, "categoria"] = [SINONIMI_CATEGORIA[c] for c in fo.loc[mask_sin, "categoria"]]
    fo.loc[mask_cat, "categoria"] = [variante_testo(rng, c) for c in fo.loc[mask_cat, "categoria"]]
    mask_zona = rng.random(len(fo)) < 0.06
    fo.loc[mask_zona, "zona"] = [variante_testo(rng, z) for z in fo.loc[mask_zona, "zona"]]
    mask_nome = rng.random(len(fo)) < 0.04
    fo.loc[mask_nome, "nome"] = [n.replace(" ", "  ", 1) + " " for n in fo.loc[mask_nome, "nome"]]
    anomalie["fornitori"] = {"categorie_con_sinonimo": int(mask_sin.sum()),
                             "categorie_da_uniformare": int(mask_cat.sum()),
                             "zone_da_uniformare": int(mask_zona.sum()),
                             "nomi_con_spazi_superflui": int(mask_nome.sum())}

    # operatori.csv -------------------------------------------------------
    op = operatori.drop(columns="popolarita").copy()
    mask_att = rng.random(len(op)) < 0.08
    op.loc[mask_att, "tipo_attivita"] = [variante_testo(rng, a) for a in op.loc[mask_att, "tipo_attivita"]]
    mask_zona = rng.random(len(op)) < 0.06
    op.loc[mask_zona, "zona"] = [variante_testo(rng, z) for z in op.loc[mask_zona, "zona"]]
    anomalie["operatori"] = {"attivita_da_uniformare": int(mask_att.sum()),
                             "zone_da_uniformare": int(mask_zona.sum())}

    # richieste.csv -------------------------------------------------------
    rq = richieste.copy()
    completate = rq.index[(rq["stato"] == "Completata") & rq["id_operatore"].isin(operatori["id_operatore"])]
    non_valide = rng.choice(completate.to_numpy(), size=n_non_valide, replace=False)
    meta = n_non_valide // 2
    idx_negativi, idx_senza_tempo = non_valide[:meta], non_valide[meta:]

    data_alt = rng.random(len(rq)) < 0.15     # vecchia versione della piattaforma
    virgola = rng.random(len(rq)) < 0.18      # esportazione con impostazioni italiane
    stato_alt = rng.random(len(rq)) < 0.12
    righe = []
    n_virgola = 0
    for i, r in rq.iterrows():
        if data_alt[i]:
            data_ora = r["data_ora"].strftime("%d/%m/%Y %H:%M")
        else:
            data_ora = r["data_ora"].strftime("%Y-%m-%d %H:%M:%S")
        if r["stato"] == "Annullata":
            importo = "" if rng.random() < 0.6 else "0"
            tempo = ""
        else:
            valore = -r["importo"] if i in idx_negativi else r["importo"]
            importo = f"{valore:.2f}"
            if virgola[i]:
                importo = importo.replace(".", ",")
                n_virgola += 1
            tempo = "" if i in idx_senza_tempo else str(int(r["tempo_erogazione_min"]))
        stato = variante_testo(rng, r["stato"]) if stato_alt[i] else r["stato"]
        righe.append({
            "id_richiesta": r["id_richiesta"],
            "id_cliente": r["id_cliente"],
            "id_fornitore": r["id_fornitore"],
            "id_operatore": r["id_operatore"],
            "data_ora": data_ora,
            "importo": importo,
            "tempo_erogazione_min": tempo,
            "stato": stato,
        })
    grezzo = pd.DataFrame(righe)
    duplicati = grezzo.sample(n=n_duplicati, random_state=int(rng.integers(0, 10**6)))
    grezzo = pd.concat([grezzo, duplicati]).sort_values("id_richiesta", kind="stable")
    anomalie["richieste"] = {
        "righe_duplicate": int(n_duplicati),
        "importi_negativi": int(len(idx_negativi)),
        "completate_senza_tempo": int(len(idx_senza_tempo)),
        "date_formato_gg_mm_aaaa": int(data_alt.sum()),
        "importi_con_virgola": int(n_virgola),
        "stati_da_uniformare": int(stato_alt.sum()),
        "operatori_non_in_anagrafica": int((~rq["id_operatore"].isin(operatori["id_operatore"])).sum()),
    }

    DIR_DATI_GREZZI.mkdir(parents=True, exist_ok=True)
    cl.to_csv(FILE_CSV["clienti"], index=False, encoding="utf-8")
    fo.to_csv(FILE_CSV["fornitori"], index=False, encoding="utf-8")
    op.to_csv(FILE_CSV["operatori"], index=False, encoding="utf-8")
    grezzo.to_csv(FILE_CSV["richieste"], index=False, encoding="utf-8")
    return anomalie, {"clienti": len(cl), "fornitori": len(fo), "operatori": len(op), "richieste": len(grezzo)}


def main():
    parser = argparse.ArgumentParser(description="Genera i dati inventati di CasaLampo.")
    parser.add_argument("--seed", type=int, default=42, help="seme casuale (stesso seme = stessi dati)")
    parser.add_argument("--n-clienti", type=int, default=450)
    parser.add_argument("--n-fornitori", type=int, default=300)
    parser.add_argument("--n-operatori", type=int, default=320,
                        help="operatori presenti nel file esportato")
    parser.add_argument("--n-richieste", type=int, default=594, help="richieste distinte generate")
    parser.add_argument("--n-non-valide", type=int, default=4,
                        help="richieste con valori non ammissibili (da scartare)")
    parser.add_argument("--n-duplicati", type=int, default=6, help="righe duplicate nel file richieste")
    parser.add_argument("--n-operatori-recenti", type=int, default=3,
                        help="operatori registrati dopo l'esportazione dell'anagrafica")
    parser.add_argument("--inizio", default="2025-10-01", help="primo giorno del periodo (AAAA-MM-GG)")
    parser.add_argument("--fine", default="2026-09-30", help="ultimo giorno del periodo (AAAA-MM-GG)")
    args = parser.parse_args()

    inizio, fine = date.fromisoformat(args.inizio), date.fromisoformat(args.fine)
    rng = np.random.default_rng(args.seed)

    clienti = genera_clienti(rng, args.n_clienti, inizio, fine)
    fornitori = genera_fornitori(rng, args.n_fornitori)
    operatori = genera_operatori(rng, args.n_operatori)
    richieste = genera_richieste(rng, args.n_richieste, clienti, fornitori, operatori, inizio, fine)
    aggiungi_operatori_recenti(rng, richieste, operatori, args.n_operatori_recenti, fine)
    anomalie, righe = esporta(rng, clienti, fornitori, operatori, richieste,
                              args.n_non_valide, args.n_duplicati)

    riepilogo = {"parametri": vars(args), "righe_esportate": righe, "anomalie_introdotte": anomalie}
    FILE_ANOMALIE.write_text(json.dumps(riepilogo, indent=2, ensure_ascii=False), encoding="utf-8")

    print("Dati generati in", DIR_DATI_GREZZI)
    for nome, n in righe.items():
        print(f"  {nome:<10} {n:>4} righe")
    print("Anomalie introdotte di proposito: vedi", FILE_ANOMALIE.name)


if __name__ == "__main__":
    main()
