def saturating_add(left, right):
    total = left + right
    overflow = int(total > 0xFF)
    return (total | -overflow) & 0xFF
