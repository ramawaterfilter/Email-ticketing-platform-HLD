class ApiError(Exception):
    """Raised by routes; main.py turns it into {"detail": ..., "code": ...}."""

    def __init__(self, status: int, code: str, detail: str):
        self.status, self.code, self.detail = status, code, detail
