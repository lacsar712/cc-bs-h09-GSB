"""跨段编号校验：空串或纯空白一律视为空缺。

拦截必须发生在落盘之前：空缺跨段直接拒绝，
系统不得自动代起名，也不得写入任何半截脏行。
"""


def normalize_span(raw) -> str:
    """转成字符串并去掉首尾空白；None 按空缺处理。"""
    if raw is None:
        return ""
    return str(raw).strip()


def is_blankish(raw) -> bool:
    """空串、纯空白或 None 都算空缺跨段。"""
    return normalize_span(raw) == ""
