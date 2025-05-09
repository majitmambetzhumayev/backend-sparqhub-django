#ai_providers/base.py
class AIProviderBase:
    def create_assistant(self, user, **kwargs): raise NotImplementedError()
    def update_assistant(self, assistant_id, user, **kwargs): raise NotImplementedError()
    def delete_assistant(self, assistant_id, user): raise NotImplementedError()
    def chat(self, assistant, message_text, stream=False): raise NotImplementedError()
    def supports_crud(self) -> bool: return True
