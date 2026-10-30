class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None):
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        super().__init__(message)
