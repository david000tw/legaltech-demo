import math


def estimate_tokens(text: str) -> int:
    """中文約每字 1.3 token 的保守估計，用來決定卷證是否全部送入。"""
    return math.ceil(len(text) * 1.3)
