import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np

# --- SEITEN KONFIGURATION ---
st.set_page_config(page_title="FIRE Simulator", layout="wide", page_icon="🔥")

# --- HILFSFUNKTIONEN (FINANZMATHEMATIK) ---

def format_de(val):
    if val == float('inf'): return "Unendlich"
    return f"{val:,.0f} €".replace(',', 'X').replace('.', ',').replace('X', '.')

def tax_logic(k_nom, s_nom, bedarf_nom, steuersatz, teilfrei):
    """Berechnet Bruttoentnahme aus Nominalwerten (Mischkursverfahren)."""
    if k_nom <= 0: return bedarf_nom, 0
    gewinnanteil = max(0.0, 1.0 - (s_nom / k_nom))
    eff_steuer = steuersatz * (0.7 if teilfrei else 1.0)
    faktor = 1.0 - (gewinnanteil * eff_steuer)
    brutto = bedarf_nom / faktor if faktor > 0 else bedarf_nom
    return brutto, brutto - bedarf_nom

def get_smile_factor(alter, use_smile):
    if not use_smile: return 1.0
    if alter < 65: return 1.0
    elif alter < 80: return 0.8
    else: return 1.2

# --- DIE CORE SIMULATION ENGINE ---

def run_simulation(params):
    # Parameter entpacken
    a_start, a_fire, a_ges, a_ende = params['a_start'], params['a_fire'], params['a_ges'], params['a_ende']
    cap_start, sparrate_m, dyn = params['cap_start'], params['sparrate_m'], params['dyn'] / 100
    entn_1_m, entn_2_m = params['entn_1_m'], params['entn_2_m']
    einmal, a_einmal = params['einmal'], params['a_einmal']
    r_anspar, r_entn, infl = params['r_anspar'] / 100, params['r_entn'] / 100, params['infl'] / 100
    tax_rate, teilfrei = params['tax_rate'] / 100, params['teilfrei']
    
    use_smile = params['use_smile']
    use_stresstest = params['use_stresstest']
    use_guardrails = params['use_guardrails']
    ziel_modus = params['ziel_modus']

    # Monatliche Raten
    r_ans_m = (1+r_anspar)**(1/12)-1; r_ent_m = (1+r_entn)**(1/12)-1; inf_m = (1+infl)**(1/12)-1; dyn_m = (1+dyn)**(1/12)-1

    # --- 1. ANSPARPHASE ---
    verlauf = []; k_nom = cap_start; s_nom = cap_start; spar_nom = sparrate_m
    t_anspar = (a_fire - a_start) * 12

    k_jahr_start = k_nom
    for m in range(t_anspar + 1):
        age = a_start + m/12
        inf_fac = (1+inf_m)**m

        # Vorabpauschale (Jährlich)
        if m > 0 and m % 12 == 0:
            basis = k_jahr_start * 0.02 * (0.7 if teilfrei else 1.0)
            vorab_steuer = max(0, basis * tax_rate)
            if k_nom > k_jahr_start: k_nom -= vorab_steuer
            k_jahr_start = k_nom

        if m % 12 == 0 or m == t_anspar: verlauf.append({'Alter': age, 'Kapital': k_nom / inf_fac})
        
        if m < t_anspar:
            if abs(age - a_einmal) < 0.01: k_nom += einmal * inf_fac; s_nom += einmal * inf_fac
            k_nom *= (1 + r_ans_m); k_nom += spar_nom; s_nom += spar_nom; spar_nom *= (1 + dyn_m)
    
    final_spar_nom = spar_nom / (1 + dyn_m)
    achieved_cap_nom, achieved_s_nom = k_nom, s_nom
    achieved_cap_real = k_nom / (1+inf_m)**t_anspar

    # --- 2. BISEKTION ZIELKAPITAL ---
    s_ratio = achieved_s_nom / achieved_cap_nom if achieved_cap_nom > 0 else 0.8

    def sim_ret(start_k_nom, start_s_nom):
        k, s = start_k_nom, start_s_nom
        t_ret = (a_ende - a_fire) * 12
        r_bear_m = [(1-0.20)**(1/12)-1, (1-0.10)**(1/12)-1, (1+0.00)**(1/12)-1]

        for m in range(t_ret + 1):
            age = a_fire + m/12
            inf_fac = (1+inf_m)**(t_anspar + m)
            bedarf = (entn_1_m if age < a_ges else entn_2_m) * get_smile_factor(age, use_smile)

            start_k_real = start_k_nom / (1+inf_m)**t_anspar
            if use_guardrails and k > 0:
                i_wr = (entn_1_m*12)/start_k_real if start_k_real > 1000 else 0.04
                if (bedarf*12)/(k/inf_fac) > i_wr * 1.2: bedarf *= 0.9

            if abs(age - a_einmal) < 0.01: k += einmal * inf_fac; s += einmal * inf_fac
            brutto, _ = tax_logic(k, s, bedarf*inf_fac, tax_rate, teilfrei)
            if k > 0: s *= max(0, (1.0 - (brutto/k)))
            k -= brutto
            if k <= 0: return k, m

            if m < t_ret:
                curr_r = r_ent_m
                if use_stresstest:
                    if m < 12: curr_r = r_bear_m[0]
                    elif m < 24: curr_r = r_bear_m[1]
                    elif m < 36: curr_r = r_bear_m[2]
                k *= (1 + curr_r)
        return k, None

    low_n, high_n = 0.0, 100_000_000.0
    for _ in range(50):
        mid = (low_n + high_n)/2
        end_k, _ = sim_ret(mid, mid * s_ratio)
        target = mid * (1+infl)**(a_ende-a_fire) if ziel_modus == "erhalt" else 0
        if end_k >= target: high_n = mid
        else: low_n = mid

    benoetigt_real = high_n / (1+inf_m)**t_anspar
    benoetigt_safe = benoetigt_real * 1.20

    # --- 3. FINALER DURCHLAUF (Chart & Logik) ---
    k_nom, s_nom, pleite, tax_real = achieved_cap_nom, achieved_s_nom, None, 0
    t_ret = (a_ende - a_fire) * 12
    r_bear_m = [(1-0.20)**(1/12)-1, (1-0.10)**(1/12)-1, (1+0.00)**(1/12)-1]

    for m in range(1, t_ret + 1):
        age, inf_fac = a_fire + m/12, (1+inf_m)**(t_anspar + m)
        bedarf = (entn_1_m if age < a_ges else entn_2_m) * get_smile_factor(age, use_smile)
        
        if use_guardrails and k_nom > 0:
            i_wr = (entn_1_m*12)/achieved_cap_real if achieved_cap_real > 1000 else 0.04
            if (bedarf*12)/(k_nom/inf_fac) > i_wr * 1.2: bedarf *= 0.9

        if abs(age - a_einmal) < 0.01: k_nom += einmal * inf_fac; s_nom += einmal * inf_fac
        brutto, t = tax_logic(k_nom, s_nom, bedarf*inf_fac, tax_rate, teilfrei)
        tax_real += t / inf_fac
        if k_nom > 0: s_nom *= max(0, (1.0 - (brutto/k_nom)))
        k_nom -= brutto
        if k_nom <= 0:
            if pleite is None: pleite = age
            k_nom = 0
        if m % 12 == 0 or m == t_ret: verlauf.append({'Alter': age, 'Kapital': k_nom / inf_fac})

        if k_nom > 0:
            curr_r = r_ent_m
            if use_stresstest:
                if (m-1) < 12: curr_r = r_bear_m[0]
                elif (m-1) < 24: curr_r = r_bear_m[1]
                elif (m-1) < 36: curr_r = r_bear_m[2]
            k_nom *= (1 + curr_r)

    return {
        'verlauf': pd.DataFrame(verlauf),
        'achieved_cap_real': achieved_cap_real,
        'benoetigt_safe': benoetigt_safe,
        'benoetigt_basis': benoetigt_real,
        'pleite_alter': pleite,
        'tax_real': tax_real,
        'final_spar_nom': final_spar_nom
    }

# --- STREAMLIT UI ---

st.title("🔥 FIRE Simulator")
st.markdown("### Präzisions-Engine für nominale Finanzplanung & Stress-Testing")

# SIDEBAR EINGABEN
with st.sidebar:
    st.header("1. Zeiten & Sparen")
    a_start = st.number_input("Aktuelles Alter", value=36, step=1)
    a_fire = st.number_input("FIRE-Alter", value=50, step=1)
    a_ges = st.number_input("Gesetzl. Rentenalter", value=67, step=1)
    a_ende = st.number_input("Geplantes Endalter", value=90, step=1)
    st.divider()
    cap_start = st.number_input("Startkapital (€)", value=100000.0, step=5000.0)
    sparrate_m = st.number_input("Monatl. Sparrate (€)", value=1000.0, step=100.0)
    dyn = st.number_input("Sparraten-Dynamik (% p.a.)", value=1.0, step=0.1)

    st.header("2. Ausgaben & Erbe")
    entn_1_m = st.number_input("Entnahme VOR Rente (€/Monat)", value=2500.0, step=100.0)
    entn_2_m = st.number_input("Entnahme AB Rente (€/Monat)", value=1600.0, step=100.0)
    einmal = st.number_input("Einmalzahlung / Erbe (€)", value=0.0, step=10000.0)
    a_einmal = st.number_input("Alter bei Einmalzahlung", value=55, step=1)

    st.header("3. Markt & Risiko")
    r_anspar = st.number_input("Rendite Ansparphase (%)", value=7.0, step=0.1)
    r_entn = st.number_input("Rendite Entnahme (%)", value=5.0, step=0.1)
    infl = st.number_input("Inflation (%)", value=2.0, step=0.1)
    tax_rate = st.number_input("Abgeltungsteuer (%)", value=26.375, step=0.1, format="%.3f")
    teilfrei = st.checkbox("30% Teilfreistellung", value=True)
    st.divider()
    ziel_modus = st.radio("Ziel-Strategie", options=["zero", "erhalt"], format_func=lambda x: "Kapitalverzehr" if x=="zero" else "Kapitalerhalt")
    use_smile = st.checkbox("Spending Smile (U-Kurve)", value=True)
    use_stresstest = st.checkbox("3-jähriger Bärenmarkt (SoRR)", value=True)
    use_guardrails = st.checkbox("Guardrails (Sparmodus)", value=True)

# SIMULATION AUSFÜHREN
sim_params = {
    'a_start': a_start, 'a_fire': a_fire, 'a_ges': a_ges, 'a_ende': a_ende,
    'cap_start': cap_start, 'sparrate_m': sparrate_m, 'dyn': dyn,
    'entn_1_m': entn_1_m, 'entn_2_m': entn_2_m, 'einmal': einmal, 'a_einmal': a_einmal,
    'r_anspar': r_anspar, 'r_entn': r_entn, 'infl': infl, 'tax_rate': tax_rate, 'teilfrei': teilfrei,
    'ziel_modus': ziel_modus, 'use_smile': use_smile, 'use_stresstest': use_stresstest, 'use_guardrails': use_guardrails
}

results = run_simulation(sim_params)

# METRIKEN ANZEIGEN
col1, col2, col3 = st.columns(3)
col1.metric("Erreichtes Kapital (FIRE)", format_de(results['achieved_cap_real']))
col2.metric("Safe Zielkapital (+20%)", format_de(results['benoetigt_safe']))
col3.metric("Basis-Bedarf (Bisektion)", format_de(results['benoetigt_basis']))

# STATUS MELDUNG
if results['achieved_cap_real'] >= results['benoetigt_safe']:
    st.success("✅ DEIN PLAN IST ROBUST! Du erreichst das Reddit-Safe Zielkapital inklusive 20% Puffer.")
elif results['achieved_cap_real'] >= results['benoetigt_basis']:
    st.warning("⚠️ ZIEL ERREICHT, ABER KNAPP. Dein Kapital reicht mathematisch aus, aber du hast keinen nennenswerten Puffer für Abweichungen.")
else:
    if results['pleite_alter']:
        st.error(f"❌ KAPITALLÜCKE ERKANNT. Das Depot ist voraussichtlich bei Alter {results['pleite_alter']:.1f} aufgebraucht.")
    else:
        st.error("❌ ZIEL VERFEHLT. Das Kapital reicht nicht aus, um die gewünschten Entnahmen zu decken.")

# CHART
st.subheader("Kapitalverlauf (Heutige Kaufkraft)")
df = results['verlauf']
fig = px.line(df, x='Alter', y='Kapital', labels={'Kapital': 'Realwert (€)'})
fig.update_traces(line_color='#2ecc71', line_width=4)

# Meilensteine & Linien
fig.add_hline(y=results['benoetigt_safe'], line_dash="dash", line_color="#f39c12", annotation_text="Safe Zielkapital")
fig.add_vline(x=a_fire, line_dash="dot", line_color="#3498db", annotation_text="FIRE Start")
fig.add_vline(x=a_ges, line_dash="dot", line_color="#9b59b6", annotation_text="Rente")

fig.update_layout(yaxis_tickformat=',.0f', hovermode="x unified", height=500)
st.plotly_chart(fig, use_container_width=True)

# LOGIK-INSPEKTOR
with st.expander("🔬 Logik-Inspektor & Detaillierter Finanzbericht", expanded=True):
    col_inf1, col_inf2 = st.columns(2)
    
    with col_inf1:
        st.markdown(f"""
        ### 🚀 Ansparphase
        - **Dauer:** {a_fire - a_start} Jahre (von {a_start} bis {a_fire})
        - **End-Sparrate:** Nominal {format_de(results['final_spar_nom'])} / Monat (durch Dynamik)
        - **Vorabpauschale:** Jährlich berücksichtigt (Sicherheits-Abschlag auf Zinseszins)
        
        ### 🎭 Konsumphasen (Spending Smile)
        - **Go-Go Phase:** 100% Bedarf ({format_de(entn_1_m)}) bis 65.
        - **Slow-Go Phase:** 80% Bedarf ab 65.
        - **No-Go Phase:** 120% Bedarf ab 80 (Pflege/Gesundheit).
        """)
        
    with col_inf2:
        st.markdown(f"""
        ### 🛡️ Risikomanagement
        - **Stresstest:** {'AKTIV (3 Jahre Bärenmarkt)' if use_stresstest else 'Inaktiv'}
        - **SWR (Start):** {(entn_1_m * 12 / results['achieved_cap_real'] * 100):.2f}% p.a.
        - **Guardrails:** {'AKTIV (10% Kürzung bei hohem SWR)' if use_guardrails else 'Inaktiv'}
        
        ### 🏛️ Steuer & Inflation
        - **Realsteuer-Effekt:** Ca. {format_de(results['tax_real'])} reale Steuerlast über Gesamtlaufzeit.
        - **Zielkapital-Puffer:** 20% Sicherheitsmarge auf das Basis-Bedarfskapital.
        """)

st.caption("Hinweis: Dies ist eine Simulation basierend auf historischen Wahrscheinlichkeiten und steuerlichen Annahmen. Keine Anlageberatung.")
