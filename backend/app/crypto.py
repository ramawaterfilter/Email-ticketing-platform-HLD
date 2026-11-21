from cryptography.fernet import Fernet

from .config import settings

# Gmail refresh tokens are stored encrypted. The key lives only in the environment
# (DESK_TOKEN_KEY), never in the database, so a database dump alone cannot read mailboxes.
_fernet = Fernet(settings.token_key.encode()) if settings.token_key else None


def encrypt(plaintext: str) -> str:
    if _fernet is None:
        raise RuntimeError("DESK_TOKEN_KEY is not set; cannot store Gmail tokens")
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if _fernet is None:
        raise RuntimeError("DESK_TOKEN_KEY is not set; cannot read Gmail tokens")
    return _fernet.decrypt(ciphertext.encode()).decode()
