from h09_extra_trap import gate_span, should_seed_stub

def normalize_or_stub(raw: str) -> tuple[str, bool]:
    return gate_span(raw), should_seed_stub(raw)
