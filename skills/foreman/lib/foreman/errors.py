"""Errors that carry a user-facing message and an exit code; the dispatcher prints them as `foreman: <msg>`."""


class ForemanError(Exception):
    code = 2

    def __init__(self, message, code=None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
