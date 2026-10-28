"""uv run python -m nirnaya.integrations.langsmith prompts|experiments"""

import json
import os
import sys

from nirnaya.config.env import load_dotenv
from nirnaya.integrations.langsmith import experiments, prompts
from nirnaya.integrations.langsmith.client import client_settings, make_client

USAGE = "usage: python -m nirnaya.integrations.langsmith prompts|experiments"


def main(argv: list[str]) -> int:
    if argv not in (["prompts"], ["experiments"]):
        print(USAGE)
        return 2
    load_dotenv()
    settings = client_settings(os.environ)
    if settings is None:
        print("Set LANGSMITH_API_KEY (and LANGSMITH_ENDPOINT for a non-default region) in .env.")
        return 2
    client = make_client(settings)
    if argv == ["prompts"]:
        results = prompts.mirror(client, out=print)
        pushed = sum(status == "pushed" for _, status in results)
        print(f"{pushed} prompt versions pushed, {len(results) - pushed} already mirrored")
        return 0
    report_path = experiments.RESULTS / "report.json"
    if not report_path.is_file():
        print("No eval/results/report.json: run uv run python -m nirnaya.evaluation.report first.")
        return 2
    try:
        experiments.upload(client, json.loads(report_path.read_text(encoding="utf-8")))
    except experiments.SyncError as e:
        print(e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
