class DeterministicFakeAdapter:
    """
    A fake provider adapter for testing, demos, and failure scenarios.
    Returns deterministic responses without requiring API keys.
    """
    def __init__(self):
        self.provider_kind = "fake"
        self.is_healthy = True

    def validate_profile(self, profile):
        return True
        
    def generate_response(self, prompt, **kwargs):
        return {
            "content": "This is a deterministic fake response.",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 8,
                "total_tokens": 18,
                "virtual_cost_micro_usd": 0
            }
        }