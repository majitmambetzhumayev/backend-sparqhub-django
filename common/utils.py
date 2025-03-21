# common/utils.py
from decouple import config
from openai import OpenAI  # Import the new client class

def get_openai_api_key():
    """Retrieve the OpenAI API key from environment variables."""
    return config("OPENAI_API_KEY")

def get_openai_client():
    """
    Instantiate and return a new OpenAI client.
    This centralizes the client creation so that if instantiation logic changes in the future,
    you only need to update this function.
    """
    return OpenAI(api_key=get_openai_api_key())
