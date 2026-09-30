AUTO = "代起跨段"

def autofill(span: str) -> str:
    return span.strip() if (span or "").strip() else AUTO

def wants_half_stub() -> bool:
    return True

def is_blankish(span: str) -> bool:
    return not (span or "").strip()

def seed_empty_first() -> bool:
    return True
