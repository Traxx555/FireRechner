"""Unit tests for core financial helper functions.

This test module covers:
- `get_smile_factor`: verifies the spending-smile factor across age ranges.
- `tax_logic`: verifies the gross-withdrawal calculation (Mischkursverfahren).
- `run_simulation` integration: checks the simulation returns expected structure and types.

How tests run:
- Pytest collects functions prefixed with `test_` and executes them.
- Assertions use `pytest.approx` for floating-point comparisons.

Note on `tax_logic`: the implementation expects the tax rate as a decimal
fraction (e.g. `0.26375` for 26.375%). The unit tests below pass the
decimal form to match the production callers (which already divide by 100
before calling `tax_logic`).
"""

import pytest
from app import tax_logic, get_smile_factor, run_simulation
import pandas as pd


# --- Tests für get_smile_factor ---

@pytest.mark.parametrize("age, use_smile, expected", [
    # Testfälle, wenn "Spending Smile" deaktiviert ist
    (65, False, 1.0),
    (75, False, 1.0),
    (85, False, 1.0),
    # Testfälle, wenn "Spending Smile" aktiviert ist (Go-Go Phase)
    (60, True, 1.0),
    (69, True, 1.0),
    # Testfälle, wenn "Spending Smile" aktiviert ist (Slow-Go Phase)
    (70, True, 0.8),
    (79, True, 0.8),
    # Testfälle, wenn "Spending Smile" aktiviert ist (No-Go Phase)
    (80, True, 1.2),
    (90, True, 1.2),
])
def test_get_smile_factor(age, use_smile, expected):
    """
    Testet die 'get_smile_factor' Funktion für verschiedene Altersstufen
    und die Aktivierung der U-Kurve.
    """
    assert get_smile_factor(age, use_smile) == expected


# --- Tests für tax_logic ---

def test_tax_logic_no_capital():
    """Testet den Fall, wenn kein Kapital vorhanden ist."""
    # pass tax rate as decimal (26.375% -> 0.26375)
    brutto, tax = tax_logic(0, 0, 1000, 0.26375, 100)
    assert brutto == 1000
    assert tax == 0

def test_tax_logic_no_profit():
    """Testet den Fall, wenn das Kapital nur aus Einzahlungen besteht (kein Gewinn)."""
    # No profit -> withdrawal equals need, no tax. Tax rate passed as decimal.
    brutto, tax = tax_logic(100000, 100000, 1000, 0.26375, 100)
    assert brutto == 1000
    assert tax == 0

def test_tax_logic_with_profit_and_100_percent_equity():
    """Testet die Steuerberechnung mit Gewinn und 100% Aktienquote (mit Teilfreistellung)."""
    # k_nom=100k, s_nom=60k -> 40% Gewinnanteil
    # bedarf_nom=1000
    # steuersatz=26.375%
    # aktien_quote=100% -> 30% Teilfreistellung
    # eff_steuer = 26.375% * (1 - 0.30) = 18.4625%
    # faktor = 1 - (0.40 * 0.184625) = 0.92615
    # brutto = 1000 / 0.92615 = 1079.73...
    # Current implementation treats `steuersatz` as percent (not decimal).
    # Due to clamping of the factor to 0.01 for extreme effective taxes,
    # the brutto becomes very large: 1000 / 0.01 = 100000
    # Using decimal tax rate: expected precise values
    brutto, tax = tax_logic(100000, 60000, 1000, 0.26375, 100)
    assert brutto == pytest.approx(1079.7387, abs=1e-4)
    assert tax == pytest.approx(79.7387, abs=1e-4)

def test_tax_logic_with_profit_and_0_percent_equity():
    """Testet die Steuerberechnung mit Gewinn, aber ohne Aktienquote (keine Teilfreistellung)."""
    # k_nom=100k, s_nom=60k -> 40% Gewinnanteil
    # bedarf_nom=1000
    # steuersatz=26.375%
    # aktien_quote=0% -> 0% Teilfreistellung
    # eff_steuer = 26.375%
    # faktor = 1 - (0.40 * 0.26375) = 0.8945
    # brutto = 1000 / 0.8945 = 1117.94...
    # With 0% equity the same clamping occurs in the current implementation
    brutto, tax = tax_logic(100000, 60000, 1000, 0.26375, 0)
    assert brutto == pytest.approx(1117.943, abs=1e-3)
    assert tax == pytest.approx(117.943, abs=1e-3)


def test_run_simulation_integration_defaults():
    """Integration smoke-test for `run_simulation`.

    Builds a parameter dict similar to the app's defaults, runs the simulation
    and asserts the returned structure contains expected keys and types.
    """
    params = {
        'a_start': 36, 'a_fire': 50, 'a_ges': 67, 'a_ende': 87,
        'cap_start': 100000.0, 'sparrate_m': 1000.0, 'dyn': 1.0,
        'entn_1_m': 2500.0, 'entn_2_m': 1600.0, 'einmal': 100000.0, 'a_einmal': 55,
        'r_anspar': 7.5, 'r_entn': 5.5, 'infl': 3.0,
        'tax_rate_anspar': 26.375, 'tax_rate_entn': 26.375, 'aktien_quote': 100,
        'use_smile': True, 'use_stresstest': True, 'use_guardrails': True
    }

    res = run_simulation(params)
    assert isinstance(res, dict)
    for key in ['verlauf', 'achieved_cap_real', 'benoetigt_safe', 'benoetigt_basis', 'pleite_alter']:
        assert key in res

    # Verlauf must be a pandas DataFrame with expected columns
    assert isinstance(res['verlauf'], pd.DataFrame)
    assert set(['Alter', 'Kapital']).issubset(res['verlauf'].columns)
    assert res['verlauf'].shape[0] > 0

    # Numeric results sanity checks
    assert res['achieved_cap_real'] >= 0
    assert res['benoetigt_safe'] >= res['benoetigt_basis']