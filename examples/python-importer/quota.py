def quota_remaining(capacity: int, used: int) -> int:
    if used >= capacity:
        return 0
    remaining = capacity - used
    return remaining
