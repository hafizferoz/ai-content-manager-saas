import argparse
import json
import os
import sys

from app.ai_evaluation import evaluate_cases, load_evaluation_cases
from app.ai_provider import AIProviderError, create_provider


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate configured AI workflows against the Phase 2 corpus."
    )
    parser.add_argument(
        "--allow-live-provider",
        action="store_true",
        help="Permit configured non-mock provider calls (may incur cost and send evaluation data).",
    )
    args = parser.parse_args()

    provider_name = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    if provider_name != "mock" and not args.allow_live_provider:
        print(
            "Refusing live provider calls without --allow-live-provider.",
            file=sys.stderr,
        )
        return 2

    try:
        provider = create_provider()
        report = evaluate_cases(provider, load_evaluation_cases())
    except (AIProviderError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2))
    return 0 if report["checks_passed"] == report["checks_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
