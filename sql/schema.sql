-- ===========================================================================
-- CasaLampo - archivio dati per l'analisi (data warehouse) organizzato "a stella"
--
-- Al centro c'è la tabella dei fatti fact_richieste: una riga per ogni
-- richiesta di intervento (grana = singola richiesta). Intorno ci sono le
-- quattro tabelle di supporto (dimensioni): tempo, cliente, fornitore, operatore.
-- Lo script viene rieseguito a ogni caricamento: le tabelle sono ricreate da zero.
-- ===========================================================================
PRAGMA foreign_keys = ON;

DROP VIEW  IF EXISTS v_richieste;
DROP TABLE IF EXISTS fact_richieste;
DROP TABLE IF EXISTS dim_tempo;
DROP TABLE IF EXISTS dim_cliente;
DROP TABLE IF EXISTS dim_fornitore;
DROP TABLE IF EXISTS dim_operatore;

-- Dimensione Tempo: un giorno per riga, calendario continuo (anche i giorni senza richieste).
CREATE TABLE dim_tempo (
    id_tempo         INTEGER PRIMARY KEY,           -- chiave nel formato AAAAMMGG
    data             TEXT    NOT NULL UNIQUE,       -- AAAA-MM-GG
    anno             INTEGER NOT NULL,
    trimestre        INTEGER NOT NULL CHECK (trimestre BETWEEN 1 AND 4),
    mese             INTEGER NOT NULL CHECK (mese BETWEEN 1 AND 12),
    nome_mese        TEXT    NOT NULL,
    anno_mese        TEXT    NOT NULL,              -- AAAA-MM, comodo per i raggruppamenti
    settimana_iso    INTEGER NOT NULL,
    giorno           INTEGER NOT NULL,
    giorno_settimana INTEGER NOT NULL CHECK (giorno_settimana BETWEEN 1 AND 7),  -- 1 = lunedì
    nome_giorno      TEXT    NOT NULL,
    is_weekend       INTEGER NOT NULL CHECK (is_weekend IN (0, 1)),
    is_festivo       INTEGER NOT NULL CHECK (is_festivo IN (0, 1))
);

-- Dimensione Cliente (chiave surrogata sk_*, chiave naturale id_* dei sistemi di origine).
CREATE TABLE dim_cliente (
    sk_cliente           INTEGER PRIMARY KEY,
    id_cliente           TEXT    NOT NULL UNIQUE,
    zona                 TEXT    NOT NULL,
    data_iscrizione      TEXT,
    anno_mese_iscrizione TEXT
);

-- Dimensione Fornitore (imprese partner).
CREATE TABLE dim_fornitore (
    sk_fornitore INTEGER PRIMARY KEY,
    id_fornitore TEXT    NOT NULL UNIQUE,
    nome         TEXT    NOT NULL,
    categoria    TEXT    NOT NULL,
    zona         TEXT    NOT NULL
);

-- Dimensione Operatore (tecnici che eseguono l'intervento).
CREATE TABLE dim_operatore (
    sk_operatore  INTEGER PRIMARY KEY,
    id_operatore  TEXT    NOT NULL UNIQUE,
    zona          TEXT    NOT NULL,
    tipo_attivita TEXT    NOT NULL
);

-- Tabella dei fatti: misure (importo, tempo) e indicatori 0/1 sommabili.
CREATE TABLE fact_richieste (
    id_richiesta         TEXT    PRIMARY KEY,       -- dimensione "degenere": identifica la richiesta
    id_tempo             INTEGER NOT NULL REFERENCES dim_tempo (id_tempo),
    sk_cliente           INTEGER NOT NULL REFERENCES dim_cliente (sk_cliente),
    sk_fornitore         INTEGER NOT NULL REFERENCES dim_fornitore (sk_fornitore),
    sk_operatore         INTEGER NOT NULL REFERENCES dim_operatore (sk_operatore),
    data_ora             TEXT    NOT NULL,          -- AAAA-MM-GG HH:MM:SS
    ora                  INTEGER NOT NULL CHECK (ora BETWEEN 0 AND 23),
    stato                TEXT    NOT NULL CHECK (stato IN ('Completata', 'Annullata')),
    importo              REAL    NOT NULL CHECK (importo >= 0),
    tempo_erogazione_min INTEGER CHECK (tempo_erogazione_min > 0),
    flag_completata      INTEGER NOT NULL CHECK (flag_completata IN (0, 1)),
    flag_annullata       INTEGER NOT NULL CHECK (flag_annullata IN (0, 1)),
    flag_ritardo         INTEGER NOT NULL CHECK (flag_ritardo IN (0, 1)),
    CHECK (flag_completata + flag_annullata = 1),
    -- una richiesta annullata non genera incasso e non ha tempo di erogazione
    CHECK ((stato = 'Completata' AND tempo_erogazione_min IS NOT NULL)
        OR (stato = 'Annullata' AND tempo_erogazione_min IS NULL AND importo = 0))
);

CREATE INDEX idx_fact_tempo     ON fact_richieste (id_tempo);
CREATE INDEX idx_fact_cliente   ON fact_richieste (sk_cliente);
CREATE INDEX idx_fact_fornitore ON fact_richieste (sk_fornitore);
CREATE INDEX idx_fact_operatore ON fact_richieste (sk_operatore);

-- Vista "piatta" usata da query e dashboard: il fatto con gli attributi delle dimensioni.
-- La zona di riferimento è quella del cliente, cioè il luogo in cui si svolge l'intervento.
CREATE VIEW v_richieste AS
SELECT f.id_richiesta,
       f.data_ora,
       t.data, t.anno, t.trimestre, t.mese, t.nome_mese, t.anno_mese, t.settimana_iso,
       t.giorno_settimana, t.nome_giorno, t.is_weekend, t.is_festivo,
       f.ora,
       c.sk_cliente, c.id_cliente, c.zona,
       fo.sk_fornitore, fo.id_fornitore, fo.nome AS fornitore, fo.categoria,
       o.sk_operatore, o.id_operatore, o.tipo_attivita, o.zona AS zona_operatore,
       f.stato, f.importo, f.tempo_erogazione_min,
       f.flag_completata, f.flag_annullata, f.flag_ritardo
FROM fact_richieste f
JOIN dim_tempo     t  ON t.id_tempo      = f.id_tempo
JOIN dim_cliente   c  ON c.sk_cliente    = f.sk_cliente
JOIN dim_fornitore fo ON fo.sk_fornitore = f.sk_fornitore
JOIN dim_operatore o  ON o.sk_operatore  = f.sk_operatore;
