import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

# --- SEITEN KONFIGURATION ---
st.set_page_config(page_title="FIRE Simulator", layout="wide", page_icon="🔥")

# --- HILFSFUNKTIONEN (FINANZMATHEMATIK) ---

def format_de(val):
    if val == float('inf'): return "Unendlich"
    return f"{val:,.0f} €".replace(',', 'X').replace('.', ',').replace('X', '.')

def tax_logic(k_nom, s_nom, bedarf_nom, steuersatz, aktien_quote):
    """Berechnet Bruttoentnahme aus Nominalwerten (Mischkursverfahren)."""
    if k_nom <= 0: return bedarf_nom, 0
    gewinnanteil = max(0.0, 1.0 - (s_nom / k_nom))
    tf_faktor = 1.0 - ((aktien_quote / 100) * 0.30)
    eff_steuer = steuersatz * tf_faktor
    faktor = 1.0 - (gewinnanteil * eff_steuer)
    faktor = max(0.01, faktor)
    brutto = bedarf_nom / faktor
    return brutto, brutto - bedarf_nom

def get_smile_factor(alter, use_smile):
    if not use_smile: return 1.0
    if alter < 70: return 1.0
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
    tax_rate_anspar = params['tax_rate_anspar'] / 100
    tax_rate_entn = params['tax_rate_entn'] / 100
    aktien_quote = params['aktien_quote']
    m_einmal = int(round((a_einmal - a_start) * 12))
    
    use_smile = params['use_smile']
    use_stresstest = params['use_stresstest']
    use_guardrails = params['use_guardrails']

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
            basis = k_jahr_start * 0.02 * (1.0 - ((aktien_quote / 100) * 0.30))
            vorab_steuer = max(0, basis * tax_rate_anspar)
            if k_nom > k_jahr_start:
                k_nom -= vorab_steuer
                s_nom += basis
            k_jahr_start = k_nom

        if m % 12 == 0 or m == t_anspar: verlauf.append({'Alter': age, 'Kapital': k_nom / inf_fac})
        
        if m < t_anspar:
            if m == m_einmal: k_nom += einmal * inf_fac; s_nom += einmal * inf_fac
            k_nom *= (1 + r_ans_m); k_nom += spar_nom; s_nom += spar_nom; spar_nom *= (1 + dyn_m)
    
    final_spar_nom = spar_nom / (1 + dyn_m)
    achieved_cap_nom, achieved_s_nom = k_nom, s_nom
    achieved_cap_real = k_nom / (1+inf_m)**t_anspar

    # --- 2. BISEKTION ZIELKAPITAL ---
    s_ratio = achieved_s_nom / achieved_cap_nom if achieved_cap_nom > 0 else 0.8

    def sim_ret(start_k_nom, start_s_nom):
        k, s = start_k_nom, start_s_nom
        t_ret = (a_ende - a_fire) * 12
        
        # Bärenmarkt-Setup (SoRR)
        r_bear_y = [-0.20, -0.10, 0.00]
        
        # Start-Werte für Guardrails
        start_k_real = start_k_nom / (1+inf_m)**t_anspar
        start_bedarf_real = entn_1_m
        start_swr = (start_bedarf_real * 12 / start_k_real) if start_k_real > 1000 else 0.04
        current_bedarf_real = start_bedarf_real

        for m in range(t_ret):
            age = a_fire + m/12
            inf_fac = (1+inf_m)**(t_anspar + m)
            
            # Bedarf ermitteln (Smile + Guardrails)
            base_bedarf = (entn_1_m if age < a_ges else entn_2_m) * get_smile_factor(age, use_smile)
            
            if use_guardrails and k > 0:
                current_k_real = k / inf_fac
                current_swr = (current_bedarf_real * 12 / current_k_real) if current_k_real > 1000 else start_swr
                if current_swr > start_swr * 1.2:
                    current_bedarf_real *= 0.99 # Sanfte Reduktion
                elif current_swr < start_swr * 0.8:
                    current_bedarf_real *= 1.01 # Sanfte Erholung
                bedarf = current_bedarf_real * get_smile_factor(age, use_smile)
            else:
                bedarf = base_bedarf

            if (t_anspar + m) == m_einmal: k += einmal * inf_fac; s += einmal * inf_fac
            brutto, _ = tax_logic(k, s, bedarf*inf_fac, tax_rate_entn, aktien_quote)
            if k > 0: s *= max(0, (1.0 - (brutto/k)))
            k -= brutto
            if k <= 0: return 0, m

            # Rendite anwenden
            curr_r = r_ent_m
            if use_stresstest:
                year = m // 12
                if year < 3:
                    y_rate = (1 + r_bear_y[year])**(1/12) - 1
                    curr_r = y_rate
            k *= (1 + curr_r)
        return k, None

    low_n, high_n = 0.0, 100_000_000.0
    for _ in range(50):
        mid = (low_n + high_n)/2
        end_k, _ = sim_ret(mid, mid * s_ratio)
        if end_k >= 0: high_n = mid
        else: low_n = mid

    benoetigt_real = high_n / (1+inf_m)**t_anspar
    benoetigt_safe = benoetigt_real * 1.20

    # --- 3. FINALER DURCHLAUF (Chart & Logik) ---
    k_nom, s_nom, pleite, tax_real = achieved_cap_nom, achieved_s_nom, None, 0
    t_ret = (a_ende - a_fire) * 12
    
    # Gleiche Logik wie sim_ret für Konsistenz
    r_bear_y = [-0.20, -0.10, 0.00]
    current_bedarf_real = entn_1_m
    start_swr = (entn_1_m * 12 / achieved_cap_real) if achieved_cap_real > 1000 else 0.04

    for m in range(t_ret):
        age = a_fire + m/12
        inf_fac = (1+inf_m)**(t_anspar + m)
        
        base_bedarf = (entn_1_m if age < a_ges else entn_2_m) * get_smile_factor(age, use_smile)
        if use_guardrails and k_nom > 0:
            current_k_real = k_nom / inf_fac
            current_swr = (current_bedarf_real * 12 / current_k_real) if current_k_real > 1000 else start_swr
            if current_swr > start_swr * 1.2:
                current_bedarf_real *= 0.99
            elif current_swr < start_swr * 0.8:
                current_bedarf_real *= 1.01
            bedarf = current_bedarf_real * get_smile_factor(age, use_smile)
        else:
            bedarf = base_bedarf

        if (t_anspar + m) == m_einmal: k_nom += einmal * inf_fac; s_nom += einmal * inf_fac
        brutto, t = tax_logic(k_nom, s_nom, bedarf*inf_fac, tax_rate_entn, aktien_quote)
        tax_real += t / inf_fac
        if k_nom > 0: s_nom *= max(0, (1.0 - (brutto/k_nom)))
        k_nom -= brutto
        
        if k_nom <= 0:
            if pleite is None: pleite = age
            k_nom = 0
        
        if m % 12 == 0: verlauf.append({'Alter': age, 'Kapital': k_nom / inf_fac})

        # Rendite anwenden
        curr_r = r_ent_m
        if use_stresstest:
            year = m // 12
            if year < 3:
                y_rate = (1 + r_bear_y[year])**(1/12) - 1
                curr_r = y_rate
        k_nom *= (1 + curr_r)

    # Letzten Punkt hinzufügen
    verlauf.append({'Alter': a_ende, 'Kapital': k_nom / (1+inf_m)**(t_anspar + t_ret)})


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
    a_ende = st.number_input("Geplantes Endalter", value=87, step=1)
    st.divider()
    cap_start = st.number_input("Startkapital (€)", value=100000.0, step=5000.0)
    sparrate_m = st.number_input("Monatl. Sparrate (€)", value=1000.0, step=100.0)
    dyn = st.number_input("Sparraten-Dynamik (% p.a.)", value=1.0, step=0.1)

    st.header("2. Ausgaben & Erbe")
    entn_1_m = st.number_input("Entnahme VOR Rente (€/Monat)", value=2500.0, step=100.0)
    entn_2_m = st.number_input("Entnahme AB Rente (€/Monat)", value=1600.0, step=100.0)
    einmal = st.number_input("Einmalzahlung / Erbe (€)", value=100000.0, step=10000.0)
    a_einmal = st.number_input("Alter bei Einmalzahlung", value=55, step=1)

    st.header("3. Markt & Risiko")
    r_anspar = st.number_input("Rendite Ansparphase (%)", value=7.5, step=0.1)
    r_entn = st.number_input("Rendite Entnahme (%)", value=5.5, step=0.1)
    infl = st.number_input("Inflation (%)", value=3.0, step=0.1)
    tax_rate_anspar = st.number_input("Steuer Ansparphase (%)", value=26.375, step=0.1, format="%.3f")
    tax_rate_entn = st.number_input("Steuer Entnahmephase (%)", value=26.375, step=0.1, format="%.3f", help="Tipp: Für die Günstigerprüfung im Alter hier z.B. 15% eintragen")
    aktien_quote = st.slider("Aktien-ETF Quote im Depot (%)", min_value=0, max_value=100, value=100, step=5)
    st.divider()
    use_smile = st.checkbox("Spending Smile (U-Kurve)", value=True)
    use_stresstest = st.checkbox("3-jähriger Bärenmarkt (SoRR)", value=True)
    use_guardrails = st.checkbox("Guardrails (Sparmodus)", value=True)

# SIMULATION AUSFÜHREN
sim_params = {
    'a_start': a_start, 'a_fire': a_fire, 'a_ges': a_ges, 'a_ende': a_ende,
    'cap_start': cap_start, 'sparrate_m': sparrate_m, 'dyn': dyn,
    'entn_1_m': entn_1_m, 'entn_2_m': entn_2_m, 'einmal': einmal, 'a_einmal': a_einmal,
    'r_anspar': r_anspar, 'r_entn': r_entn, 'infl': infl,
    'tax_rate_anspar': tax_rate_anspar, 'tax_rate_entn': tax_rate_entn, 'aktien_quote': aktien_quote,
    'use_smile': use_smile, 'use_stresstest': use_stresstest, 'use_guardrails': use_guardrails
}

results = run_simulation(sim_params)

# METRIKEN ANZEIGEN
col1, col2, col3 = st.columns(3)
col1.metric("Erreichtes Kapital (FIRE)", format_de(results['achieved_cap_real']))
col2.metric("Safe Zielkapital (+20%)", format_de(results['benoetigt_safe']))
col3.metric("Basis-Bedarf (Bisektion)", format_de(results['benoetigt_basis']))

# STATUS MELDUNG
if results['achieved_cap_real'] >= results['benoetigt_safe']:
    st.success("✅ DEIN PLAN IST ROBUST! Du erreichst das Safe-Community Zielkapital inklusive 20% Puffer.")
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
st.plotly_chart(fig, width='stretch')

# LOGIK-INSPEKTOR
with st.expander("🔬 Logik-Inspektor & Detaillierter Finanzbericht", expanded=True):
    tf = 1.0 - ((aktien_quote / 100) * 0.30)
    entn_eff_tax_pct = tax_rate_entn * tf
    start_swr = (entn_1_m * 12 / results['achieved_cap_real'] * 100) if results['achieved_cap_real'] > 0 else 0.0

    tab1, tab2, tab3, tab4 = st.tabs([
        "🔥 FIRE-Basics & SWR",
        "⚖️ Steuern & Mathematik",
        "🛡️ Risiko & Bärenmarkt",
        "📈 Inflation & Lebensphasen"
    ])

    with tab1:
        st.markdown(f"""
        ### 🔥 Was ist FIRE?
        **FIRE** steht für *Financial Independence, Retire Early*. Das bedeutet: Du erreichst ein Kapital, das deine jährlichen Lebenshaltungskosten dauerhaft deckt, sodass du finanziell unabhängig wirst und theoretisch früher aufhören kannst zu arbeiten.

        ### 💡 Deine persönliche Start-Entnahme & SWR
        - **Start-Entnahme:** {format_de(entn_1_m)} pro Monat → {format_de(entn_1_m*12)} pro Jahr
        - **Erreichtes FIRE-Kapital:** {format_de(results['achieved_cap_real'])}
        - **Start-SWR:** {start_swr:.2f}% p.a.

        Die **Safe Withdrawal Rate (SWR)** beschreibt, wie viel Prozent deines Kapitalstocks du im ersten Jahr entnehmen kannst, ohne das Depot zu schnell zu ruinieren.
        - Die historische Trinity-Studie zeigte, dass eine anfängliche Entnahmerate von etwa 4% in vielen historischen Marktphasen über 30 Jahre funktioniert hätte.
        - Eine **SWR unter 3.5%** gilt im FIRE-Kontext als **besonders konservativ und „kugelsicher“**, weil sie mehr Puffer gegen schlechte Börsenjahre schafft.

        ### 🛡️ Safe Zielkapital
        Dein **Basis-Zielkapital** ist die Summe, die die App als rechnerisch notwendig erachtet.
        Die App schlägt zusätzlich **20% Puffer** auf dieses Kapital auf, weil:
        - Märkte schwanken,
        - Renditen in der Zukunft unsicher sind,
        - Steuern und Inflation weiter wirken.

        > Der Puffer ist kein Luxus, sondern ein Sicherheitsnetz. Er hilft zu verhindern, dass ein einzelner schlechter Marktzyklus deinen FIRE-Plan zerstört.
        """)

    with tab2:
        st.markdown(f"""
        ### ⚖️ Mischkursverfahren & Steuerlogik
        Im Ruhestand werden Entnahmen nicht vollständig versteuert. Die App berechnet mit dem **Mischkursverfahren**, dass nur der **Gewinnanteil** steuerpflichtig ist.

        - **k_nom** ist das nominale Depotvolumen.
        - **s_nom** ist der Einstandswert, also der Teil des Depots, der bereits versteuert oder aus eigener Einlage stammt.
        - Je größer der Anteil von `s_nom` im Vergleich zu `k_nom`, desto geringer ist der steuerpflichtige Gewinnanteil.

        Beispiel für deinen Plan:
        - Depotwert: {format_de(results['achieved_cap_real'])}
        - Wenn der Einstandswert hoch ist, fällt in der Rente weniger Steuer an.

        ### 🧾 Vorabpauschale in der Ansparphase
        In der Ansparphase approximiert die App die steuerliche Wirkung der **Vorabpauschale** durch einen pauschalen jährlichen Steuerabschlag.
        Sie ist keine echte Auszahlung, sondern ein steuerliches Abgeltungssteuer-Aggregat auf die fiktiven Erträge.

        In dieser App nutzt die Vorabpauschale stur die volle **Abgeltungsteuer** auf die Ansparphase, weil:
        - während des Berufslebens in der Regel kein großer **Freibetrag** mehr verfügbar ist,
        - die Abgeltungsteuer auf Kapitalerträge standardmäßig 26,375% beträgt.

        Jedes Jahr wird die Vorabpauschale auf eine Basis von **2% des Depotwerts**, reduziert um den Teilfreistellungsfaktor.

        ### 🧮 Günstigerprüfung & Teilfreistellung
        In der Entnahmephase kann dein Steuersatz deutlich niedriger sein, weil dein sonstiges Einkommen meist sinkt.
        - Die App verwendet für die **Steuer Entnahmephase** dein eingegebenes `Steuer Entnahmephase (%)`.
        - Zusätzlich reduziert der ETF-Anteil die Steuerlast durch den **30% Rabatt**.

        Dein eingestellter Aktienanteil: **{aktien_quote}%**
        - Teilfreistellung-Faktor (TF): **{tf:.3f}**
        - Effektiver Steuerfaktor in der Rente: **{entn_eff_tax_pct:.3f}**

        Das bedeutet: Statt der vollen Steuer wird auf den Gewinnanteil nur noch ein geringerer Prozentsatz angewendet.

        > Je höher dein Aktienanteil, desto stärker wirkt der ETF-Rabatt.
        """)

    with tab3:
        st.markdown(f"""
        ### 🛡️ Sequence of Return Risk (SoRR)
        SoRR bedeutet: Wenn die Börse in den ersten Rentenjahren fällt, kann das Depot schneller schrumpfen.
        Die App simuliert daher einen möglichen **Bärenmarkt** in den ersten 3 Jahren deiner Entnahmephase:
        - Jahr 1: **-20%**
        - Jahr 2: **-10%**
        - Jahr 3: **0%**

        Wenn der Schalter für den Stresstest aktiv ist, prüft die Simulation genau diese kritische Phase. Die Simulation ist dabei deterministisch: Die Renditen verlaufen nicht zufällig, sondern folgen diesen festen Werten.

        ### 🚨 Vereinfachte Guardrail-Logik inspiriert von Guyton-Klinger
        Die App nutzt eine dynamische Anpassung der Entnahmen:
        - Wenn dein Depot durch schlechte Märkte fällt und die aktuelle Entnahmerate (SWR) um mehr als **20% über die Start-SWR** steigt, wird der Bedarf schrittweise reduziert.
        - Erholt sich das Depot wieder und die SWR fällt unter **80% der Start-SWR**, kann die Entnahme wieder leicht steigen.

        Das ist kein permanenter Verzicht, sondern ein Modell für **temporäres Krisenmanagement**:
        - kein teurer Urlaub,
        - weniger Restaurantbesuche,
        - konservativerer Lebensstil.

        Diese Regel hilft dabei, den Plan auch in schlechten Marktphasen stabil zu halten.
        """)

    with tab4:
        st.markdown(f"""
        ### 📈 Nominal vs. Real
        - **Nominal** bedeutet: echte Euro-Beträge ohne Kaufkraftanpassung.
        - **Real** bedeutet: Beträge in heutiger Kaufkraft.

        Die App rechnet intern nominal, weil das die echten Depotbewegungen abbildet.
        Für die Charts zeigt sie die Werte aber in **heutiger Kaufkraft**, damit du das Ergebnis besser verstehst.

        ### 💸 Spending Smile
        Die Lebensphasen werden wie folgt modelliert:
        - **Go-Go Phase** bis 70: **100% Bedarf**. In den ersten Rentenjahren sind Reisen, Freizeit und Lifestyle oft am teuersten.
        - **Slow-Go Phase** 70–80: **80% Bedarf**. Danach wird das Leben ruhiger und günstiger.
        - **No-Go Phase** ab 80: **120% Bedarf**. Pflege, Gesundheit und unerwartete Kosten steigen wieder.

        Dieses Modell soll dir helfen zu verstehen, dass Rente nicht „konstant“ ist, sondern verschiedene Kostenphasen hat.
        """)

st.caption("Hinweis: Dies ist eine Simulation basierend auf historischen Wahrscheinlichkeiten und steuerlichen Annahmen. Keine Anlageberatung.")
