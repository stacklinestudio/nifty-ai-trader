def breadth_score(advances: int | None, declines: int | None) -> float:
    if advances is None or declines is None or advances + declines == 0:
        return 0.0
    return 100 * (advances - declines) / (advances + declines)
