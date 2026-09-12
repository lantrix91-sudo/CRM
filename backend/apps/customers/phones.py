import re


def normalize_phone(value):
    value = value.strip()
    if not re.fullmatch(r"[+0-9() .-]+", value):
        raise ValueError("Некорректный номер телефона.")
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if not 10 <= len(digits) <= 15:
        raise ValueError("Нужен полный номер с кодом страны (10–15 цифр).")
    return "+" + digits
