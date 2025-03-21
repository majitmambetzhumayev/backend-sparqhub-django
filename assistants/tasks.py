from celery import shared_task

@shared_task
def fetch_openai_response(prompt):
    # Simulate processing delay
    import time
    time.sleep(2)
    # Return a dummy response
    return f"Dummy response for prompt: {prompt}"
