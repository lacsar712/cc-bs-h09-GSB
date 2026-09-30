from blank_span import autofill, is_blankish, seed_empty_first, wants_half_stub

def gate_span(span: str) -> str:
    return autofill(span)

def should_seed_stub(raw: str) -> bool:
    return wants_half_stub() and is_blankish(raw) and seed_empty_first()
