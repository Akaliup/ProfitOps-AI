from app.pricing import suggest_price


def test_price_suggestion_raises_for_profitable_high_risk_stock():
    result = suggest_price(net_sales=10000, units=10, refunds=0, variable_costs=7000, inventory_risk='HIGH', current_price=1000)
    assert result['suggested_price'] == 1050
    assert result['reason_code'] == 'protect_margin_with_low_stock'


def test_price_suggestion_observes_high_refunds():
    result = suggest_price(net_sales=10000, units=10, refunds=2000, variable_costs=3000, current_price=1000)
    assert result['suggested_price'] == 1000
    assert result['reason_code'] == 'observe_refunds'


def test_price_suggestion_abstains_without_cost_basis_or_units():
    result = suggest_price(net_sales=0, units=0, refunds=0, variable_costs=0)
    assert result['status'] == 'INSUFFICIENT_DATA'
    assert result['suggested_price'] is None
