def clamp(value: int) -> int:
    if value < -5:
        return -5
    if value > 5:
        return 5
    return value
