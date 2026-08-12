def saturating_add(left, right):
    total = left + right
    overflow = int(total > 0x100)
    return (total | -overflow) & 0xFF
