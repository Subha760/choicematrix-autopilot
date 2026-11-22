class RequestRejected(ValueError):
    """An explicit provider rejection confirms that this request did not publish."""
    def __init__(self, status, code):
        super().__init__(f"Provider rejected request (HTTP {status}, code {code})")
        self.status = status
        self.code = code
        self.retryable = status in (401, 403) or code in (190, 4, 17, 32, 613)
