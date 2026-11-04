"""Display-only number formatting; never an ASR or model-output replacement."""

import re

DIGITS = dict(zip("零〇一二兩三四五六七八九幺", "0012234567891"))


EN_DIGITS = dict(zip(
    ("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"),
    "0123456789",
))


TEENS = dict(zip(
    ("TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN",
     "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN"), range(10, 20),
))


TENS = dict(zip(
    ("TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"),
    range(20, 100, 10),
))


SEPARATORS = re.compile(r"[\s,，.。:：\-]+")


def expand_digit_repetitions(text: str) -> str:
    counts = {word: int(value) for word, value in EN_DIGITS.items()}
    counts.update(TEENS)
    counts.update({word: int(value) for word, value in DIGITS.items()})
    counts.update({"十": 10, "十一": 11, "十二": 12, "十三": 13, "DOUBLE": 2, "TRIPLE": 3})

    def repeated(match: re.Match[str]) -> str:
        count_word, digit_word = match.groups()
        if "個" not in match[0] and count_word not in {"DOUBLE", "TRIPLE"} and not digit_word.endswith("S"):
            return match[0]
        count = int(count_word) if count_word.isascii() and count_word.isdigit() else counts.get(count_word, 0)
        singular = "SIX" if digit_word == "SIXES" else digit_word.removesuffix("S")
        digit = EN_DIGITS.get(singular, DIGITS.get(digit_word, digit_word))
        return digit * count if 1 <= count <= 13 and re.fullmatch(r"[0-9]", digit) else match[0]

    text = re.sub(
        r"(?<![0-9零〇一二兩三四五六七八九幺十])"
        r"([0-9]{1,2}|[零〇一二兩三四五六七八九幺十]+)\s*個\s*([0-9零〇一二兩三四五六七八九幺])",
        repeated, text,
    )
    return re.sub(
        r"\b(DOUBLE|TRIPLE|[0-9]{1,2}|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE|THIRTEEN)"
        r"\s+(ZERO[S]?|ONE[S]?|TWO[S]?|THREE[S]?|FOUR[S]?|FIVE[S]?|SIX(?:ES)?|SEVEN[S]?|EIGHT[S]?|NINE[S]?)\b",
        repeated, text,
    )


def digit_sequence(text: str) -> str | None:
    text = re.sub(r"然後|接著|\bFOLLOWED BY\b", " THEN ", expand_digit_repetitions(text))
    parts = [
        token for segment in SEPARATORS.split(text.strip())
        for token in re.findall(r"[A-Z]+|[0-9]+|[零〇一二兩三四五六七八九幺]+|.", segment)
    ]
    result = []
    for index, part in enumerate(parts):
        if not part:
            continue
        if part == "THEN":
            if not result or index == len(parts) - 1 or parts[index - 1] == "THEN":
                return None
        elif part in EN_DIGITS:
            result.append(EN_DIGITS[part])
        elif re.fullmatch(r"[0-9]+", part):
            result.append(part)
        elif all(character in DIGITS for character in part):
            result.append("".join(DIGITS[character] for character in part))
        else:
            return None
    return "".join(result) or None


def english_cardinal(words: list[str]) -> int | None:
    if not words:
        return None
    for unit, multiplier in (("THOUSAND", 1000), ("HUNDRED", 100)):
        if unit in words:
            if words.count(unit) != 1 or words.index(unit) != 1:
                return None
            leading = EN_DIGITS.get(words[0])
            if leading is None or leading == "0":
                return None
            tail = words[2:]
            if tail and tail[0] == "AND":
                tail = tail[1:]
                if not tail:
                    return None
            remainder = english_cardinal(tail) if tail else 0
            if remainder is None or remainder >= multiplier:
                return None
            return int(leading) * multiplier + remainder
    if len(words) == 1:
        return TEENS.get(words[0], TENS.get(words[0], (
            int(EN_DIGITS[words[0]]) if words[0] in EN_DIGITS else None
        )))
    if len(words) == 2 and words[0] in TENS and words[1] in EN_DIGITS:
        return TENS[words[0]] + int(EN_DIGITS[words[1]])
    return None


def flight_digits(text: str) -> str | None:
    sequence = digit_sequence(text)
    if sequence is not None:
        return sequence
    compact = SEPARATORS.sub("", text)
    if re.fullmatch(r"[一二兩三四五六七八九十百千零]+", compact):
        # Cardinal grammar is confined to a flight's numeric component.
        if compact.startswith("零") or re.search(r"[千百][一二兩三四五六七八九]$", compact):
            return None
        match = re.fullmatch(
            r"(?:([一二兩三四五六七八九])千)?"
            r"(?:零?([一二兩三四五六七八九])百)?"
            r"(?:零?([一二兩三四五六七八九]?)十)?"
            r"(?:零?([一二兩三四五六七八九]))?", compact,
        )
        if match and any(unit in compact for unit in "十百千"):
            thousands, hundreds, tens, ones = match.groups()
            value = (
                int(DIGITS[thousands]) * 1000 if thousands else 0
            ) + (int(DIGITS[hundreds]) * 100 if hundreds else 0)
            if "十" in compact:
                value += int(DIGITS[tens]) * 10 if tens else 10
            return str(value + (int(DIGITS[ones]) if ones else 0))
    value = english_cardinal([word for word in SEPARATORS.split(text) if word])
    return str(value) if value is not None else None
def format_spoken_codes(text: str) -> str:
    text = re.sub(
        r"[零〇一二兩两三四五六七八九幺十百千]+",
        lambda match: flight_digits(match[0].replace("两", "兩")) or match[0], text,
    )
    words = "|".join(sorted((*EN_DIGITS, *TEENS, *TENS, "HUNDRED", "THOUSAND", "AND"), key=len, reverse=True))
    text = re.sub(
        rf"\b(?:{words})(?:[\s-]+(?:{words}))*\b",
        lambda match: flight_digits(match[0].upper().replace("-", " ")) or match[0],
        text, flags=re.IGNORECASE,
    )
    text = re.sub(r"\b[a-z]\b|[a-zA-Z]*\d[a-zA-Z0-9]*", lambda match: match[0].upper(), text)
    return re.sub(
        r"(?<![a-zA-Z0-9])[A-Z0-9]+(?:[\s,，、]+[A-Z0-9]+)+(?![a-zA-Z0-9])",
        lambda match: re.sub(r"[\s,，、]+", "", match[0]), text,
    ).strip()


def native_rows(fragments: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for fragment in sorted(fragments, key=lambda item: item["start_ms"]):
        if groups and fragment["start_ms"] - groups[-1]["end_ms"] < 1000:
            groups[-1]["raw"] += fragment["delta"]
            groups[-1]["end_ms"] = max(groups[-1]["end_ms"], fragment["end_ms"])
        else:
            groups.append({"raw": fragment["delta"], "end_ms": fragment["end_ms"]})
    return [{"raw": group["raw"], "text": format_spoken_codes(group["raw"])} for group in groups]
