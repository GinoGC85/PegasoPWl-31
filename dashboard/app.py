"""Dashboard interattiva di CasaLampo (Streamlit).

Avvio, dalla cartella principale del progetto:
    streamlit run dashboard/app.py

Gli indicatori sono calcolati con le stesse query SQL di sql/kpi.sql, alle quali
vengono passati come parametri i filtri scelti (periodo, zona, categoria).
"""
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from comune import FILE_DW, GIORNI, MESI_BREVI, SOGLIA_RITARDO_MIN, uri_sola_lettura  # noqa: E402
from kpi import carica_query  # noqa: E402

# Colori: una sola tinta per le serie singole, grigio per le richieste annullate,
# scala sequenziale di blu per la mappa di calore.
BLU = "#2a78d6"
GRIGIO = "#b9b7ae"
SCALA_BLU = ["#f3f6fb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
TESTO, TESTO_SECONDARIO, GRIGLIA = "#2b2b2b", "#6b6a65", "#ecebe7"

st.set_page_config(page_title="CasaLampo · Analisi delle richieste", layout="wide")
st.markdown("<style>.block-container{padding-top:2.2rem;padding-bottom:2rem}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Accesso ai dati
# ---------------------------------------------------------------------------
@st.cache_data
def valori_filtri():
    with sqlite3.connect(uri_sola_lettura(FILE_DW), uri=True) as con:
        data_min, data_max = con.execute("SELECT MIN(data), MAX(data) FROM v_richieste").fetchone()
        zone = [r[0] for r in con.execute(
            "SELECT zona FROM v_richieste GROUP BY zona ORDER BY COUNT(*) DESC")]
        categorie = [r[0] for r in con.execute(
            "SELECT categoria FROM v_richieste GROUP BY categoria ORDER BY COUNT(*) DESC")]
    return date.fromisoformat(data_min), date.fromisoformat(data_max), zone, categorie


@st.cache_data
def interroga(nome, data_da, data_a, zone, categorie):
    """Esegue una delle query di sql/kpi.sql con i filtri come parametri."""
    parametri = {"data_da": data_da, "data_a": data_a,
                 "zone": "|" + "|".join(zone) + "|", "categorie": "|" + "|".join(categorie) + "|"}
    with sqlite3.connect(uri_sola_lettura(FILE_DW), uri=True) as con:
        return pd.read_sql_query(carica_query()[nome], con, params=parametri)


# ---------------------------------------------------------------------------
# Formattazione all'italiana
# ---------------------------------------------------------------------------
def numero(valore, decimali=0):
    testo = f"{valore:,.{decimali}f}"
    return testo.replace(",", "X").replace(".", ",").replace("X", ".")


def euro(valore):
    return f"{numero(valore)} €"


def durata(minuti):
    if pd.isna(minuti):
        return "n.d."
    ore, resto = divmod(int(round(minuti)), 60)
    return f"{ore} h {resto:02d} min" if ore else f"{resto} min"


def percentuale(valore):
    return "n.d." if pd.isna(valore) else f"{numero(valore, 1)} %"


def etichetta_mese(anno_mese):
    anno, mese = anno_mese.split("-")
    return f"{MESI_BREVI[int(mese) - 1]} {anno}"


def stile(fig, altezza=300, legenda=False):
    fig.update_layout(
        height=altezza,
        margin=dict(l=4, r=12, t=30 if legenda else 8, b=4),
        font=dict(family="Source Sans Pro, Segoe UI, Arial, sans-serif", size=13, color=TESTO),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        separators=",.",
        showlegend=legenda,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title_text=""),
        hoverlabel=dict(bgcolor="white", font_size=13),
        bargap=0.35,
        barcornerradius=4,
    )
    fig.update_xaxes(showgrid=False, linecolor="#d9d8d3", ticks="", tickfont_color=TESTO_SECONDARIO)
    fig.update_yaxes(gridcolor=GRIGLIA, zeroline=False, tickfont_color=TESTO_SECONDARIO)
    return fig


# ---------------------------------------------------------------------------
# Pagina
# ---------------------------------------------------------------------------
data_min, data_max, tutte_zone, tutte_categorie = valori_filtri()

st.title("CasaLampo · Analisi delle richieste")
st.caption(f"Prototipo per il project work · dati inventati, generati per l'occasione · "
           f"periodo disponibile: {data_min:%d/%m/%Y} - {data_max:%d/%m/%Y}")

# Filtri: una sola riga sopra tutti i grafici, che li applicano tutti allo stesso modo.
f1, f2, f3 = st.columns([1.5, 1.6, 2.0])
periodo = f1.date_input("Periodo", value=(data_min, data_max), min_value=data_min,
                        max_value=data_max, format="DD/MM/YYYY")
# I filtri iniziali si possono indicare anche nell'indirizzo, es. ?zona=Provincia&categoria=Idraulica
zone_iniziali = [z for z in st.query_params.get_all("zona") if z in tutte_zone]
categorie_iniziali = [c for c in st.query_params.get_all("categoria") if c in tutte_categorie]
zone_scelte = f2.multiselect("Zona", tutte_zone, default=zone_iniziali, placeholder="Tutte le zone")
categorie_scelte = f3.multiselect("Categoria di servizio", tutte_categorie, default=categorie_iniziali,
                                  placeholder="Tutte le categorie")

if isinstance(periodo, (tuple, list)) and len(periodo) == 2:
    data_da, data_a = periodo
else:  # l'utente ha scelto solo la data iniziale
    data_da, data_a = (periodo[0] if isinstance(periodo, (tuple, list)) else periodo), data_max
filtri = (data_da.isoformat(), data_a.isoformat(),
          tuple(zone_scelte or tutte_zone), tuple(categorie_scelte or tutte_categorie))

sintesi = interroga("sintesi", *filtri).iloc[0]
if sintesi["richieste_totali"] == 0:
    st.warning("Nessuna richiesta corrisponde ai filtri scelti.")
    st.stop()
ricorrenti = interroga("clienti_ricorrenti", *filtri).iloc[0]

# Indicatori principali.
k1, k2, k3, k4 = st.columns(4)
k1.metric("Richieste totali", numero(sintesi["richieste_totali"]), border=True,
          help="Tutte le richieste del periodo, completate e annullate.")
k2.metric("Incassi", euro(sintesi["incassi_totali"]), border=True,
          help="Somma degli importi degli interventi completati.")
k3.metric("Tempo medio erogazione", durata(sintesi["tempo_medio_min"]), border=True)
k4.metric("Clienti attivi", numero(sintesi["clienti_attivi"]), border=True,
          help="Clienti con almeno una richiesta nel periodo.")

k5, k6, k7, k8 = st.columns(4)
k5.metric("Clienti che tornano", percentuale(ricorrenti["perc_clienti_ricorrenti"]),
          help="Quota dei clienti serviti che hanno ricevuto almeno due interventi nel periodo.")
k6.metric("Richieste annullate", percentuale(sintesi["perc_annullate"]))
k7.metric(f"Interventi oltre {SOGLIA_RITARDO_MIN // 60} ore", percentuale(sintesi["perc_in_ritardo"]),
          help=f"Interventi completati oltre {SOGLIA_RITARDO_MIN} minuti dalla richiesta.")
k8.metric("Importo medio per intervento", euro(sintesi["importo_medio"]))

# Andamento nel tempo.
mensile = interroga("andamento_mensile", *filtri)
mensile["mese"] = mensile["anno_mese"].map(etichetta_mese)
with st.container(border=True):
    st.markdown("**Andamento delle richieste nel tempo** · richieste per mese")
    fig = go.Figure()
    fig.add_bar(x=mensile["mese"], y=mensile["completate"], name="Completate", marker_color=BLU,
                hovertemplate="%{x}<br>%{y} completate<extra></extra>")
    fig.add_bar(x=mensile["mese"], y=mensile["annullate"], name="Annullate", marker_color=GRIGIO,
                hovertemplate="%{x}<br>%{y} annullate<extra></extra>")
    fig.update_traces(marker_line_color="#fcfcfb", marker_line_width=1.5)
    fig.update_layout(barmode="stack", legend_traceorder="normal")
    st.plotly_chart(stile(fig, 290, legenda=True), config={"displayModeBar": False})

# Fornitori e zone.
c1, c2 = st.columns(2)
with c1, st.container(border=True):
    st.markdown("**Primi cinque fornitori per incassi**")
    top = interroga("top_fornitori", *filtri).iloc[::-1]
    fig = go.Figure(go.Bar(
        x=top["incassi"], y=top["fornitore"], orientation="h", marker_color=BLU,
        text=[euro(v) for v in top["incassi"]], textposition="outside", cliponaxis=False,
        customdata=top[["categoria", "richieste"]],
        hovertemplate="%{y}<br>%{customdata[0]}<br>%{x:,.0f} € · %{customdata[1]} richieste<extra></extra>"))
    fig.update_xaxes(showticklabels=False, showgrid=False, range=[0, top["incassi"].max() * 1.28])
    st.plotly_chart(stile(fig, 280), config={"displayModeBar": False})

with c2, st.container(border=True):
    st.markdown("**Richieste per zona**")
    zone_df = interroga("per_zona", *filtri)
    fig = go.Figure(go.Bar(
        x=zone_df["zona"], y=zone_df["richieste"], marker_color=BLU,
        text=zone_df["richieste"], textposition="outside", cliponaxis=False,
        customdata=zone_df[["incassi", "tempo_medio_min", "perc_in_ritardo"]],
        hovertemplate=("%{x}<br>%{y} richieste · %{customdata[0]:,.0f} €<br>"
                       "tempo medio %{customdata[1]:.0f} min · oltre 4 ore %{customdata[2]:.1f} %<extra></extra>")))
    fig.update_yaxes(range=[0, zone_df["richieste"].max() * 1.15])
    st.plotly_chart(stile(fig, 280), config={"displayModeBar": False})

# Categorie e fasce orarie.
c3, c4 = st.columns(2)
with c3, st.container(border=True):
    st.markdown("**Richieste per categoria di servizio**")
    cat = interroga("per_categoria", *filtri).iloc[::-1]
    fig = go.Figure(go.Bar(
        x=cat["richieste"], y=cat["categoria"], orientation="h", marker_color=BLU,
        text=cat["richieste"], textposition="outside", cliponaxis=False,
        customdata=cat[["incassi", "tempo_medio_min"]],
        hovertemplate="%{y}<br>%{x} richieste · %{customdata[0]:,.0f} €<br>tempo medio %{customdata[1]:.0f} min<extra></extra>"))
    fig.update_xaxes(range=[0, cat["richieste"].max() * 1.15])
    st.plotly_chart(stile(fig, 280), config={"displayModeBar": False})

with c4, st.container(border=True):
    st.markdown("**Quando arrivano le richieste** · giorno e ora")
    calore = interroga("per_giorno_e_ora", *filtri)
    griglia = (calore.pivot_table(index="giorno_settimana", columns="ora", values="richieste", aggfunc="sum")
               .reindex(index=range(1, 8), columns=range(24)).fillna(0))
    fig = go.Figure(go.Heatmap(
        z=griglia.to_numpy(), x=[f"{h}" for h in range(24)], y=[g[:3] for g in GIORNI],
        colorscale=[[i / (len(SCALA_BLU) - 1), c] for i, c in enumerate(SCALA_BLU)],
        xgap=2, ygap=2, colorbar=dict(title=dict(text="richieste", font=dict(size=12, color=TESTO_SECONDARIO)),
                      thickness=10, tickfont=dict(size=12, color=TESTO_SECONDARIO)),
        hovertemplate="%{y} ore %{x}<br>%{z:.0f} richieste<extra></extra>"))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(title_text="ora della richiesta", title_font_color=TESTO_SECONDARIO, dtick=2)
    st.plotly_chart(stile(fig, 280), config={"displayModeBar": False})

# Tempi di servizio per zona e vista tabellare dei dati.
def tabella(df, colonne):
    """Tabella con intestazioni leggibili e numeri nel formato italiano."""
    out = pd.DataFrame()
    for campo, (titolo, decimali) in colonne.items():
        valori = df[campo]
        out[titolo] = valori if decimali is None else [
            "n.d." if pd.isna(v) else numero(v, decimali) for v in valori]
    return out


with st.expander("Tempi, ritardi e annullamenti per zona e per fascia oraria"):
    st.dataframe(tabella(zone_df, {
        "zona": ("Zona", None), "richieste": ("Richieste", 0), "incassi": ("Incassi (€)", 0),
        "tempo_medio_min": ("Minuti medi", 0), "perc_in_ritardo": ("Oltre 4 h (%)", 1),
        "perc_annullate": ("Annullate (%)", 1)}), hide_index=True)
    fasce = interroga("ritardi_per_fascia", *filtri)
    fasce["fascia"] = fasce["fascia"].str[3:]
    st.dataframe(tabella(fasce, {
        "fascia": ("Fascia oraria", None), "richieste": ("Richieste", 0),
        "tempo_medio_min": ("Minuti medi", 0), "perc_in_ritardo": ("Oltre 4 h (%)", 1),
        "perc_annullate": ("Annullate (%)", 1)}), hide_index=True)

with st.expander("Dati in tabella (andamento mensile e fornitori)"):
    st.dataframe(tabella(mensile, {
        "mese": ("Mese", None), "richieste": ("Richieste", 0), "completate": ("Completate", 0),
        "annullate": ("Annullate", 0), "incassi": ("Incassi (€)", 2),
        "tempo_medio_min": ("Tempo medio (min)", 0)}), hide_index=True)
    st.dataframe(tabella(top.iloc[::-1], {
        "fornitore": ("Fornitore", None), "categoria": ("Categoria", None), "richieste": ("Richieste", 0),
        "completate": ("Completate", 0), "incassi": ("Incassi (€)", 2)}), hide_index=True)

st.caption("Fonte: data/dw/casalampo_dw.sqlite · indicatori calcolati con le query di sql/kpi.sql · "
           "tempo di erogazione: minuti tra l'invio della richiesta e la chiusura dell'intervento "
           "(solo interventi completati) · la zona è quella del cliente, cioè il luogo dell'intervento.")
