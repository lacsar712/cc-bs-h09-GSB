from h09_pad_trap import normalize_or_stub

def test_blank():
    s, stub = normalize_or_stub("  ")
    assert s == "代起跨段" and stub is True
