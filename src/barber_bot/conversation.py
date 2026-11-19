import re
import unicodedata


def normalize_text(value: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKD", value.lower())
                   if not unicodedata.combining(character))


def sanitize_text(value: str) -> str:
    value = re.sub(r"https://t\.me/[A-Za-z0-9_]+\?start=recover_[A-Za-z0-9_-]+", "[link de verificação]", value)
    value = re.sub(r"BI-[A-Za-z0-9_-]{12,}", "[código privado]", value, flags=re.I)
    value = re.sub(r"(?<![\w])\d{6}(?![\w])", "[código de verificação]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[e-mail]", value)
    return re.sub(r"(?<!\d)(?:\+?55[ .-]?)?(?:\(?\d{2}\)?[ .-]?)?\d{4,5}[ .-]?\d{4}(?!\d)", "[telefone]", value)


def sanitize_chat_messages(messages: list[dict]) -> list[dict]:
    return [{"role": message["role"], "content": sanitize_text(message["content"])} for message in messages]
