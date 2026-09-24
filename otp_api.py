class OTPProvider:
    """Safe boundary for an authorized OTP/testing provider."""

    def __init__(self, api_key=None):
        self.api_key = api_key

    def health(self):
        return {"ok": False, "message": "OTP provider is not configured."}

    def request_test_otp(self, destination):
        raise NotImplementedError(
            "Use only an authorized testing/notification OTP service."
        )
