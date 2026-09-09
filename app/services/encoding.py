"""Base62 Encoding and Decoding Module.

Converts 64-bit integer IDs (such as Twitter Snowflake IDs or database auto-increment IDs)
into compact, URL-safe alphanumeric strings and vice-versa.
"""

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)  # 62
CHAR_TO_INDEX = {char: idx for idx, char in enumerate(ALPHABET)}


def encode(num: int) -> str:
    """Encodes a non-negative integer into a Base62 string.
    
    Args:
        num: Non-negative integer (e.g., 64-bit ID).
        
    Returns:
        Base62 string representation.
        
    Raises:
        ValueError: If num is negative.
    """
    if num < 0:
        raise ValueError("Cannot encode negative numbers in Base62.")
    if num == 0:
        return ALPHABET[0]

    digits = []
    while num > 0:
        num, rem = divmod(num, BASE)
        digits.append(ALPHABET[rem])

    return "".join(reversed(digits))


def decode(code: str) -> int:
    """Decodes a Base62 string back into its original integer ID.
    
    Args:
        code: Base62 encoded string.
        
    Returns:
        Decoded non-negative integer.
        
    Raises:
        ValueError: If code is empty or contains invalid characters not in Base62 alphabet.
    """
    if not code:
        raise ValueError("Cannot decode an empty short code.")

    num = 0
    for char in code:
        if char not in CHAR_TO_INDEX:
            raise ValueError(f"Invalid character '{char}' in Base62 string.")
        num = num * BASE + CHAR_TO_INDEX[char]

    return num
