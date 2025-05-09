from openai import OpenAI
from openai.evals import evaluation, runner

client = OpenAI()

@evaluation
def basic_accuracy_eval(sample: dict):
    """
    Simple pass/fail if the agent response equals the ideal output.
    Sample format: {"input": "...", "ideal": "..."}
    """
    # 1. call chat
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": sample["input"]}]
    )
    out = resp.choices[0].message.content.strip()
    return out == sample["ideal"]

if __name__ == "__main__":
    # CLI mode to run a small batch
    samples = [
        {"input":"2+2=?", "ideal":"4"},
        {"input":"Hello -> Bonjour", "ideal":"Bonjour"},
    ]
    results = runner.run(basic_accuracy_eval, samples)
    print("Eval results:", results)
