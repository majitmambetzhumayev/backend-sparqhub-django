class AIProviderBase:
    supports_crud = False

    def create_assistant(self, user, **kwargs): raise NotImplementedError()
    def update_assistant(self, assistant_id, user, **kwargs): raise NotImplementedError()
    def delete_assistant(self, assistant_id, user): raise NotImplementedError()
    def chat(self, assistant, messages, stream=False): raise NotImplementedError()
