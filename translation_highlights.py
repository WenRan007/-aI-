"""Find sale and price phrases in translated copy.

The module deliberately has no UI or network dependencies.  Offsets are Python
string offsets, which can be passed to Tk's ``Text`` widget as ``1.0+Nc``.
"""

from __future__ import annotations

import re
from typing import List, Tuple

Highlight = Tuple[int, int, str]

# Currency amounts.  A unit is required for an unlabelled number so that things
# such as ``10厘米`` and ``2分钟`` are not mistaken for prices.  A price cue
# (到手价/券后/仅售...) is allowed to omit a unit (例如 ``到手价99``).
_DIGITS = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:\.\d+)?)(?:\s*[万千])?"
_CN_NUMBER = r"[零〇一二两三四五六七八九十百千万亿]+"
# Spoken copy frequently uses approximate amounts such as ``几千``/``几万``.
# They are accepted only as part of a price cue or when followed by a currency
# suffix, so measurements such as ``几千米`` are not marked as prices.
_APPROX_NUMBER = r"(?:几(?:十|百|千|万)?|数十?|上百|上千|上万)(?:\s*[万千])?"
_NUMBER = rf"(?:{_DIGITS}|{_APPROX_NUMBER}|{_CN_NUMBER})"
_CURRENCY_PREFIX = r"(?:¥|￥|\$|₫|đ|RMB|CNY|USD|VND)\s*"
_CURRENCY_SUFFIX = r"(?:元|块|人民币|RMB|CNY|美元|USD|越南盾|VND|₫|đ)"
_PRICE_CUE = r"(?:到手价|原价|现价|券后价?|售价|价格|仅售|只要|低至|折后价?|优惠价|特价)"

# The first branch includes a cue so ``到手价99`` is useful to highlight; the
# second branch requires a currency marker.  Word boundaries around Latin
# codes prevent ``USD`` in a longer identifier from being selected.
_PRICE_RE = re.compile(
    rf"(?:{_PRICE_CUE}\s*(?:{_CURRENCY_PREFIX})?{_NUMBER}(?:\s*{_CURRENCY_SUFFIX})?"
    rf"|{_CURRENCY_PREFIX}{_NUMBER}(?:\s*{_CURRENCY_SUFFIX})?"
    rf"|{_NUMBER}\s*{_CURRENCY_SUFFIX})",
    re.IGNORECASE,
)

# E-commerce offer patterns.  Keep the complete offer together where possible:
# ``拍1发5`` and ``拍1发3`` are common short-video calls to action, while
# ``买2免1``/``第二件半价`` cover the equivalent buy-more promotions.  Chinese
# numerals are accepted because speech-to-text output often uses them instead
# of Arabic digits.
_PROMO_NUMBER = r"(?:\d{1,3}|几|数十?|[零〇一二两三四五六七八九十百千万亿]+)"
_BUY_GIVE_RE = (
    rf"(?:拍|买|购)\s*{_PROMO_NUMBER}\s*(?:件|个|盒|瓶|份)?\s*"
    rf"(?:发|送|免|得)\s*{_PROMO_NUMBER}\s*(?:件|个|盒|瓶|份)?"
)
_HALF_PRICE_RE = (
    rf"(?:第\s*{_PROMO_NUMBER}\s*件|第二件)\s*"
    rf"(?:半价|0元|免费|(?:\d+(?:\.\d+)?|[一二两三四五六七八九十])\s*折)"
)
_FULL_DISCOUNT_RE = rf"(?:全场|全店|全网|全站)\s*(?:\d+(?:\.\d+)?|几|[一二两三四五六七八九十])\s*折"
_EARLY_BIRD_RE = (
    rf"前\s*{_PROMO_NUMBER}\s*(?:名|位)\s*(?:下单\s*)?"
    rf"(?:立减|直减|减免|优惠|半价)(?:\s*{_NUMBER}\s*(?:元|块|人民币)?)?"
)

# Longer phrases are listed first to keep one highlight for ``买一送一`` and
# ``满100减20`` instead of highlighting a shorter word inside it.  The
# generic discount/percent alternatives remain below these specific phrases
# for expressions such as ``全场8.8折`` and ``优惠20%``.
_PROMOTION_RE = re.compile(
    rf"{_BUY_GIVE_RE}|{_HALF_PRICE_RE}|{_FULL_DISCOUNT_RE}|{_EARLY_BIRD_RE}"
    rf"|买一送一|买二送一|买1送1|买2送1|满\s*{_NUMBER}\s*(?:元|块)?\s*(?:减|省)\s*{_NUMBER}\s*(?:元|块)?"
    rf"|满\s*{_NUMBER}\s*(?:元|块)?\s*(?:赠|送)\s*(?:礼品|赠品|一件|一份)?"
    r"|(?:\d+(?:\.\d+)?|[一二两三四五六七八九十]|几)\s*折|\d+(?:\.\d+)?\s*%"
    r"|优惠|折扣|满减|包邮|赠品|限时|特价|秒杀|立减|买赠|返现|优惠券|代金券|免费",
    re.IGNORECASE,
)


def _valid_price_match(text: str, match: re.Match[str]) -> bool:
    """Filter false positives from currency-code matches in identifiers."""
    start, end = match.span()
    value = match.group()
    # Keep a Latin currency code from matching in ``MYRMBTEST`` etc.
    if re.search(r"(?:RMB|CNY|USD|VND)$", value, re.IGNORECASE):
        if start and (text[start - 1].isalnum() or text[start - 1] == "_"):
            return False
        if end < len(text) and (text[end].isalnum() or text[end] == "_"):
            return False
    # A cue-only number must not consume a following measurement word.  The
    # currency branch already requires a suffix, so this mainly documents the
    # intended boundary for future pattern additions.
    return True


def find_highlights(text: str) -> List[Highlight]:
    """Return non-overlapping ``(start, end, tag)`` spans for *text*.

    ``price`` spans have precedence over ``promotion`` spans.  When candidates
    begin at the same offset, the longer span wins; this makes a monetary value
    inside ``满100减20`` yield one coherent promotion span rather than overlap.
    Results are sorted in reading order and use end-exclusive offsets.
    """
    if not text:
        return []

    candidates: List[Highlight] = []
    for match in _PRICE_RE.finditer(text):
        if _valid_price_match(text, match):
            candidates.append((*match.span(), "price"))
    for match in _PROMOTION_RE.finditer(text):
        candidates.append((*match.span(), "promotion"))

    # Price > promotion, then longer > shorter.  A greedy interval selection
    # from left to right guarantees no overlapping Tk tags.
    priority = {"price": 0, "promotion": 1}
    candidates.sort(key=lambda item: (item[0], priority[item[2]], -(item[1] - item[0])))
    selected: List[Highlight] = []
    for candidate in candidates:
        start, end, _ = candidate
        if any(start < old_end and end > old_start for old_start, old_end, _ in selected):
            continue
        selected.append(candidate)
    selected.sort(key=lambda item: item[0])
    return selected


# Suggested UI colors for a dark theme: warm amber for money and vivid coral
# for promotions.  The host application may use these constants directly.
PRICE_HIGHLIGHT_COLOR = "#FFD166"
PROMOTION_HIGHLIGHT_COLOR = "#FF7A90"


__all__ = ["find_highlights", "PRICE_HIGHLIGHT_COLOR", "PROMOTION_HIGHLIGHT_COLOR"]
