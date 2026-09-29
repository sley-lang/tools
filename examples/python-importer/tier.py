def tier(score: int) -> int:
    if score < 25:
        return 0
    if score < 50:
        return 1
    if score < 75:
        return 2
    return 3
