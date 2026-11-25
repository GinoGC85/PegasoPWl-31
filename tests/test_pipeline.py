"""Controlli automatici sul prototipo (si eseguono dopo generatore ed ETL).

    python -m unittest discover -s tests -v

Verificano che i volumi rispettino la traccia, che la procedura ETL intercetti
tutte le anomalie introdotte di proposito, che l'archivio sia integro e che
gli indicatori calcolati in SQL quadrino con un ricalcolo indipendente.
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from comune import (FILE_ANOMALIE, FILE_CSV, FILE_DW, FILE_REPORT_QUALITA,  # noqa: E402
                    SOGLIA_RITARDO_MIN)
from kpi import carica_query, esegui, parametri_filtro  # noqa: E402


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = sqlite3.connect(FILE_DW)
        cls.anomalie = json.loads(FILE_ANOMALIE.read_text(encoding="utf-8"))["anomalie_introdotte"]
        cls.qualita = json.loads(FILE_REPORT_QUALITA.read_text(encoding="utf-8"))
        cls.query = carica_query()
        cls.vista = pd.read_sql_query("SELECT * FROM v_richieste", cls.con)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def valore(self, sql):
        return self.con.execute(sql).fetchone()[0]

    # --- Requisiti della traccia ------------------------------------------
    def test_volumi_tra_300_e_600_record_per_tipologia(self):
        for nome, percorso in FILE_CSV.items():
            n = len(pd.read_csv(percorso, dtype=str))
            self.assertTrue(300 <= n <= 600, f"{nome}: {n} righe")

    def test_nessuna_informazione_personale(self):
        colonne = pd.read_csv(FILE_CSV["clienti"], nrows=1).columns.tolist()
        self.assertEqual(colonne, ["id_cliente", "zona", "data_iscrizione"])

    # --- Procedura ETL -----------------------------------------------------
    def test_anomalie_introdotte_tutte_intercettate(self):
        a, q = self.anomalie, self.qualita
        self.assertEqual(q["clienti"]["zone_uniformate"], a["clienti"]["zone_da_uniformare"])
        self.assertEqual(q["clienti"]["date_convertite_da_gg_mm_aaaa"], a["clienti"]["date_formato_gg_mm_aaaa"])
        self.assertEqual(q["fornitori"]["categorie_uniformate"],
                         a["fornitori"]["categorie_con_sinonimo"] + a["fornitori"]["categorie_da_uniformare"])
        self.assertEqual(q["fornitori"]["zone_uniformate"], a["fornitori"]["zone_da_uniformare"])
        self.assertEqual(q["fornitori"]["nomi_ripuliti"], a["fornitori"]["nomi_con_spazi_superflui"])
        self.assertEqual(q["operatori"]["attivita_uniformate"], a["operatori"]["attivita_da_uniformare"])
        self.assertEqual(q["operatori"]["zone_uniformate"], a["operatori"]["zone_da_uniformare"])
        r, ar = q["richieste"], a["richieste"]
        self.assertEqual(r["duplicati_rimossi"], ar["righe_duplicate"])
        self.assertEqual(r["scartate"].get("importo negativo", 0), ar["importi_negativi"])
        self.assertEqual(r["scartate"].get("tempo di erogazione mancante o non valido", 0),
                         ar["completate_senza_tempo"])
        self.assertEqual(r["date_ora_convertite_da_gg_mm_aaaa"], ar["date_formato_gg_mm_aaaa"])
        self.assertEqual(r["importi_con_virgola_convertiti"], ar["importi_con_virgola"])
        self.assertEqual(r["stati_uniformati"], ar["stati_da_uniformare"])
        self.assertEqual(r["operatori_non_in_anagrafica"], ar["operatori_non_in_anagrafica"])

    def test_nessun_valore_non_riconosciuto(self):
        for nome in ["clienti", "fornitori", "operatori", "richieste"]:
            for chiave, valore in self.qualita[nome].items():
                if chiave.endswith("non_riconosciute") or chiave.endswith("non_riconosciuti"):
                    self.assertEqual(valore, [], f"{nome}.{chiave}")

    # --- Integrità dell'archivio ------------------------------------------
    def test_integrita_referenziale_e_unicita(self):
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.valore("SELECT COUNT(*) - COUNT(DISTINCT id_richiesta) FROM fact_richieste"), 0)
        self.assertEqual(self.valore("SELECT COUNT(*) FROM fact_richieste"), len(self.vista))

    def test_regole_di_business(self):
        v = self.vista
        annullate, completate = v[v["stato"] == "Annullata"], v[v["stato"] == "Completata"]
        self.assertTrue((annullate["importo"] == 0).all())
        self.assertTrue(annullate["tempo_erogazione_min"].isna().all())
        self.assertTrue((completate["importo"] > 0).all())
        self.assertTrue((completate["tempo_erogazione_min"] > 0).all())
        attesi = (completate["tempo_erogazione_min"] > SOGLIA_RITARDO_MIN).astype(int)
        self.assertTrue((completate["flag_ritardo"] == attesi).all())
        self.assertEqual(int(annullate["flag_ritardo"].sum()), 0)

    def test_calendario_continuo(self):
        self.assertEqual(self.valore(
            "SELECT (julianday(MAX(data)) - julianday(MIN(data)) + 1) - COUNT(*) FROM dim_tempo"), 0)
        self.assertEqual(self.valore(
            "SELECT COUNT(*) FROM fact_richieste f LEFT JOIN dim_tempo t USING (id_tempo) WHERE t.data IS NULL"), 0)

    # --- Quadrature ----------------------------------------------------------
    def test_quadratura_con_i_file_di_origine(self):
        """Ricalcolo indipendente dai CSV grezzi, con regole volutamente semplici."""
        grezzo = pd.read_csv(FILE_CSV["richieste"], dtype=str, keep_default_na=False)
        grezzo = grezzo.apply(lambda c: c.str.strip()).drop_duplicates()
        importo = pd.to_numeric(grezzo["importo"].str.replace(",", "."), errors="coerce")
        completata = grezzo["stato"].str.lower() == "completata"
        tempo_ok = pd.to_numeric(grezzo["tempo_erogazione_min"], errors="coerce") > 0
        valide = completata & (importo > 0) & tempo_ok
        self.assertAlmostEqual(round(importo[valide].sum(), 2),
                               self.valore("SELECT ROUND(SUM(importo), 2) FROM fact_richieste"), places=2)
        self.assertEqual(int(valide.sum()), self.valore("SELECT SUM(flag_completata) FROM fact_richieste"))

    def test_kpi_sql_uguali_al_ricalcolo_in_pandas(self):
        p = parametri_filtro(self.con)
        s = esegui(self.con, self.query, "sintesi", p).iloc[0]
        r = esegui(self.con, self.query, "clienti_ricorrenti", p).iloc[0]
        v = self.vista
        completate = v[v["flag_completata"] == 1]
        self.assertEqual(s["richieste_totali"], len(v))
        self.assertAlmostEqual(s["incassi_totali"], round(v["importo"].sum(), 2), places=2)
        self.assertAlmostEqual(s["tempo_medio_min"], round(completate["tempo_erogazione_min"].mean(), 1), places=1)
        self.assertEqual(s["clienti_attivi"], v["sk_cliente"].nunique())
        per_cliente = completate.groupby("sk_cliente").size()
        self.assertAlmostEqual(r["perc_clienti_ricorrenti"],
                               round(100 * (per_cliente >= 2).sum() / len(per_cliente), 1), places=1)

    def test_filtri_coerenti(self):
        """La somma per zona coincide con il totale, anche con altri filtri attivi."""
        p = parametri_filtro(self.con, data_da="2026-06-01", data_a="2026-08-31",
                             categorie=["Climatizzazione", "Idraulica"])
        totale = esegui(self.con, self.query, "sintesi", p).iloc[0]["richieste_totali"]
        per_zona = esegui(self.con, self.query, "per_zona", p)["richieste"].sum()
        self.assertEqual(totale, per_zona)
        atteso = self.vista.query(
            "data >= '2026-06-01' and data <= '2026-08-31' and categoria in ['Climatizzazione', 'Idraulica']")
        self.assertEqual(totale, len(atteso))


if __name__ == "__main__":
    unittest.main()
