import math

from vesterian.charts import build_figure, tick_price

NOW = 1794704400


def texts(fig):
    return (
        " ".join(t.get_text() for t in fig.texts)
        + " "
        + " ".join(t.get_text() for ax in fig.axes for t in ax.texts)
    )


def test_single_observation_does_not_claim_a_trend():
    fig = build_figure(
        "Single sale",
        {"days": [{"day": "2026-11-14", "averagePrice": 0.001, "purchases": 1, "units": 10}]},
        "7d",
        "averagePrice",
        NOW,
    )
    labels = texts(fig)
    assert "0.001 Bronze" in labels
    assert "Not comparable" in labels
    assert "Low volume" in labels
    assert "1 transaction = 1 purchase" in labels
    assert tick_price(0.001) != "0"


def test_unknown_volume_is_not_drawn_as_zero_and_gaps_are_preserved():
    fig = build_figure(
        "Missing",
        {
            "days": [
                {"day": "2026-11-11", "averagePrice": 100, "units": 10},
                {"day": "2026-11-14", "averagePrice": 200, "purchases": 1, "units": 20},
            ]
        },
        "7d",
        "averagePrice",
        NOW,
    )
    assert "Volume incomplete" in texts(fig)
    price, volume = fig.axes[3:5]
    assert any(math.isnan(v) for v in price.lines[0].get_ydata())
    assert math.isnan(volume.containers[1].patches[0].get_height())
    # Both bar series have different centers, not overlapping stacks.
    assert volume.containers[0].patches[0].get_x() != volume.containers[1].patches[0].get_x()


def test_asking_price_and_empty_states_explain_meaning():
    fig = build_figure("Empty", {"days": []}, "30d", "averageAskingPrice", NOW)
    labels = texts(fig)
    assert "not completed sales" in labels
    assert "No comparable prices" in labels
    assert "No trading activity data" in labels
    assert "No price data" in labels


def test_long_name_cannot_overflow_header_and_zero_price_is_preserved():
    fig = build_figure(
        "$" * 100,
        {
            "days": [
                {"day": "2026-11-13", "averagePrice": 100, "purchases": 1, "units": 1},
                {"day": "2026-11-14", "averagePrice": 0, "purchases": 1, "units": 1},
            ]
        },
        "7d",
        "averagePrice",
        NOW,
    )
    assert len(fig.texts[0].get_text()) <= 55
    assert "0 Bronze" in texts(fig)
    assert "-100.0%" in texts(fig)
    fig.canvas.draw()
