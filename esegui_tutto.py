"""Esegue in sequenza generazione dei dati, procedura ETL, calcolo dei KPI e test,
poi indica il comando per aprire la dashboard.

Uso:  python esegui_tutto.py            (stessi dati della relazione, seme 42)
      python esegui_tutto.py --seed 7   (gli argomenti passano al generatore)
"""
import subprocess
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent
PASSI = [
    ("Generazione dei dati inventati", [sys.executable, "src/genera_dati.py", *sys.argv[1:]]),
    ("Procedura ETL e caricamento", [sys.executable, "src/etl.py"]),
    ("Indicatori principali", [sys.executable, "src/kpi.py"]),
    ("Test automatici", [sys.executable, "-m", "unittest", "discover", "-s", "tests"]),
]

for titolo, comando in PASSI:
    print(f"\n=== {titolo} ===")
    esito = subprocess.run(comando, cwd=RADICE)
    if esito.returncode != 0:
        sys.exit(f"Interrotto: il passo '{titolo}' non è andato a buon fine.")

print("\nTutto pronto. Per aprire la dashboard:\n    streamlit run dashboard/app.py")
