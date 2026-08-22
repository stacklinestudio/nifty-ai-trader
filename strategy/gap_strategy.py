def gap_percent(previous_close: float, opening_price: float) -> float:
    if previous_close <= 0:
        raise ValueError("previous close must be positive")
    return (opening_price - previous_close) / previous_close
