class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None):
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        super().__init__(message)


class LeaseLost(Exception):
    """The worker no longer owns the analysis job lease.

    This is an internal control-flow signal, not a user-facing domain error:
    an expired worker must stop writing instead of overwriting the job that a
    newer worker has reclaimed.
    """
