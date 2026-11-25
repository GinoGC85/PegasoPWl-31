-- ===========================================================================
-- CasaLampo - interrogazioni SQL per gli indicatori principali (KPI)
--
-- Ogni interrogazione ha un nome (riga "-- name: ...") ed è usata sia dallo
-- script src/kpi.py sia dalla dashboard, così gli indicatori hanno una sola
-- definizione. Tutte partono dallo stesso perimetro "base", filtrato con:
--   :data_da, :data_a   periodo, nel formato AAAA-MM-GG (estremi inclusi)
--   :zone, :categorie   elenchi separati da '|', ad esempio '|Centro|Nord|'
-- La condizione instr(:zone, '|' || zona || '|') > 0 è vera se la zona della
-- richiesta compare nell'elenco: un solo parametro per un numero qualsiasi di valori.
-- ===========================================================================

-- name: sintesi
WITH base AS (
    SELECT * FROM v_richieste
    WHERE data BETWEEN :data_da AND :data_a
      AND instr(:zone, '|' || zona || '|') > 0
      AND instr(:categorie, '|' || categoria || '|') > 0
)
SELECT COUNT(*)                                             AS richieste_totali,
       COALESCE(SUM(flag_completata), 0)                    AS richieste_completate,
       COALESCE(SUM(flag_annullata), 0)                     AS richieste_annullate,
       ROUND(100.0 * SUM(flag_annullata) / NULLIF(COUNT(*), 0), 1)           AS perc_annullate,
       ROUND(COALESCE(SUM(importo), 0), 2)                  AS incassi_totali,
       ROUND(SUM(importo) / NULLIF(SUM(flag_completata), 0), 2)              AS importo_medio,
       ROUND(AVG(tempo_erogazione_min), 1)                  AS tempo_medio_min,  -- solo completate (NULL escluso)
       ROUND(100.0 * SUM(flag_ritardo) / NULLIF(SUM(flag_completata), 0), 1) AS perc_in_ritardo,
       COUNT(DISTINCT sk_cliente)                           AS clienti_attivi
FROM base;

-- name: clienti_ricorrenti
-- Un cliente "torna" se nel periodo ha ricevuto almeno due interventi completati.
WITH base AS (
    SELECT * FROM v_richieste
    WHERE data BETWEEN :data_da AND :data_a
      AND instr(:zone, '|' || zona || '|') > 0
      AND instr(:categorie, '|' || categoria || '|') > 0
),
per_cliente AS (
    SELECT sk_cliente, SUM(flag_completata) AS interventi
    FROM base
    GROUP BY sk_cliente
)
SELECT SUM(interventi >= 1)                                         AS clienti_serviti,
       SUM(interventi >= 2)                                         AS clienti_ricorrenti,
       ROUND(100.0 * SUM(interventi >= 2) / NULLIF(SUM(interventi >= 1), 0), 1) AS perc_clienti_ricorrenti
FROM per_cliente;

-- name: andamento_mensile
WITH base AS (
    SELECT * FROM v_richieste
    WHERE data BETWEEN :data_da AND :data_a
      AND instr(:zone, '|' || zona || '|') > 0
      AND instr(:categorie, '|' || categoria || '|') > 0
)
SELECT anno_mese,
       COUNT(*)                 AS richieste,
       SUM(flag_completata)     AS completate,
       SUM(flag_annullata)      AS annullate,
       ROUND(SUM(importo), 2)   AS incassi,
       ROUND(AVG(tempo_erogazione_min), 1) AS tempo_medio_min
FROM base
GROUP BY anno_mese
ORDER BY anno_mese;

-- name: top_fornitori
WITH base AS (
    SELECT * FROM v_richieste
    WHERE data BETWEEN :data_da AND :data_a
      AND instr(:zone, '|' || zona || '|') > 0
      AND instr(:categorie, '|' || categoria || '|') > 0
)
SELECT fornitore,
       categoria,
       COUNT(*)               AS richieste,
       SUM(flag_completata)   AS completate,
       ROUND(SUM(importo), 2) AS incassi
FROM base
GROUP BY sk_fornitore, fornitore, categoria
ORDER BY incassi DESC, richieste DESC
LIMIT 5;

-- name: per_zona
WITH base AS (
    SELECT * FROM v_richieste
    WHERE data BETWEEN :data_da AND :data_a
      AND instr(:zone, '|' || zona || '|') > 0
      AND instr(:categorie, '|' || categoria || '|') > 0
)
SELECT zona,
       COUNT(*)                            AS richieste,
       ROUND(SUM(importo), 2)              AS incassi,
       ROUND(AVG(tempo_erogazione_min), 1) AS tempo_medio_min,
       ROUND(100.0 * SUM(flag_ritardo) / NULLIF(SUM(flag_completata), 0), 1) AS perc_in_ritardo,
       ROUND(100.0 * SUM(flag_annullata) / COUNT(*), 1)                      AS perc_annullate
FROM base
GROUP BY zona
ORDER BY richieste DESC;

-- name: per_categoria
WITH base AS (
    SELECT * FROM v_richieste
    WHERE data BETWEEN :data_da AND :data_a
      AND instr(:zone, '|' || zona || '|') > 0
      AND instr(:categorie, '|' || categoria || '|') > 0
)
SELECT categoria,
       COUNT(*)                            AS richieste,
       ROUND(SUM(importo), 2)              AS incassi,
       ROUND(AVG(tempo_erogazione_min), 1) AS tempo_medio_min
FROM base
GROUP BY categoria
ORDER BY richieste DESC;

-- name: per_giorno_e_ora
WITH base AS (
    SELECT * FROM v_richieste
    WHERE data BETWEEN :data_da AND :data_a
      AND instr(:zone, '|' || zona || '|') > 0
      AND instr(:categorie, '|' || categoria || '|') > 0
)
SELECT giorno_settimana, nome_giorno, ora, COUNT(*) AS richieste
FROM base
GROUP BY giorno_settimana, nome_giorno, ora
ORDER BY giorno_settimana, ora;

-- name: ritardi_per_fascia
-- Quota di interventi completati oltre la soglia di 240 minuti, per fascia oraria della richiesta.
WITH base AS (
    SELECT * FROM v_richieste
    WHERE data BETWEEN :data_da AND :data_a
      AND instr(:zone, '|' || zona || '|') > 0
      AND instr(:categorie, '|' || categoria || '|') > 0
)
SELECT CASE
           WHEN ora BETWEEN 7 AND 12  THEN '1. Mattina (7-12)'
           WHEN ora BETWEEN 13 AND 16 THEN '2. Pomeriggio (13-16)'
           WHEN ora BETWEEN 17 AND 20 THEN '3. Sera (17-20)'
           ELSE '4. Notte (21-6)'
       END                                    AS fascia,
       COUNT(*)                               AS richieste,
       ROUND(AVG(tempo_erogazione_min), 1)    AS tempo_medio_min,
       ROUND(100.0 * SUM(flag_ritardo) / NULLIF(SUM(flag_completata), 0), 1) AS perc_in_ritardo,
       ROUND(100.0 * SUM(flag_annullata) / COUNT(*), 1)                      AS perc_annullate
FROM base
GROUP BY fascia
ORDER BY fascia;

-- name: concentrazione_fornitori
-- Quanto pesano i fornitori più attivi sul totale degli incassi.
WITH base AS (
    SELECT * FROM v_richieste
    WHERE data BETWEEN :data_da AND :data_a
      AND instr(:zone, '|' || zona || '|') > 0
      AND instr(:categorie, '|' || categoria || '|') > 0
),
per_fornitore AS (
    SELECT sk_fornitore, SUM(importo) AS incassi FROM base GROUP BY sk_fornitore
),
classifica AS (
    SELECT incassi, ROW_NUMBER() OVER (ORDER BY incassi DESC) AS posizione FROM per_fornitore
)
SELECT (SELECT COUNT(*) FROM dim_fornitore WHERE sk_fornitore > 0)          AS fornitori_in_anagrafica,
       COUNT(*)                                                             AS fornitori_con_richieste,
       ROUND(100.0 * SUM(CASE WHEN posizione <= 5 THEN incassi END) / SUM(incassi), 1)  AS perc_incassi_top5,
       ROUND(100.0 * SUM(CASE WHEN posizione <= 30 THEN incassi END) / SUM(incassi), 1) AS perc_incassi_top30
FROM classifica;
