"""Command line entry point: `leadqual run <lead.json>` and `leadqual eval`."""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from leadqual import evaluation, notify, tracker
from leadqual.config import DEFAULT_CONFIG_PATH, Settings, load_settings
from leadqual.intake import InvalidLead
from leadqual.llm import ClaudeLlm
from leadqual.models import Result
from leadqual.pipeline import qualify

RUNS_DIR = Path("runs")
EVAL_RESULTS_DIR = Path("eval/results")


def main() -> int:
    parser = argparse.ArgumentParser(prog="leadqual", description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--model", help="override the model named in the config")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="qualify one lead and record the result")
    run.add_argument("lead", type=Path, help="JSON file with one form submission")
    run.add_argument("--no-notify", action="store_true", help="skip the Slack message")
    evaluate = commands.add_parser("eval", help="measure the compliance check on labelled cases")
    evaluate.add_argument("--cases", type=Path, default=evaluation.DEFAULT_CASES_PATH)
    arguments = parser.parse_args()

    load_dotenv()
    configure_logging()
    settings = load_settings(arguments.config)
    if arguments.model:
        llm_settings = settings.llm.model_copy(update={"model": arguments.model})
        settings = settings.model_copy(update={"llm": llm_settings})

    if arguments.command == "eval":
        return _eval(arguments.cases, settings)
    return _run(arguments.lead, settings, notify_sales=not arguments.no_notify)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # The HTTP libraries log every request URL at INFO, and the Slack webhook URL is a secret.
    for http_library in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(http_library).setLevel(logging.WARNING)


def _run(lead_path: Path, settings: Settings, *, notify_sales: bool) -> int:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if notify_sales and not webhook_url:
        print("SLACK_WEBHOOK_URL is not set (use --no-notify to skip Slack).", file=sys.stderr)
        return 2

    llm = ClaudeLlm(settings.llm)
    started = time.perf_counter()
    try:
        result = qualify(
            json.loads(lead_path.read_text(encoding="utf-8")), settings=settings, llm=llm
        )
    except InvalidLead as error:
        print(f"Invalid lead: {error}", file=sys.stderr)
        return 2
    seconds = time.perf_counter() - started

    row = tracker.record(result, settings.tracker_path)
    print(f"{result.decision.route} - {result.decision.reason}")
    print(f"Fit {result.fit.total} ({result.fit.data} data) | compliance: {result.compliance.flag}")
    print(f"Tracker: {settings.tracker_path} row {row}")
    _write_run_log(result, llm, seconds)

    if notify_sales:
        try:
            notify.send(result, webhook_url)
        except notify.NotifyError as error:
            # The decision and the tracker row stand; only the delivery failed.
            print(f"Slack: NOT delivered - {error}", file=sys.stderr)
            return 1
        print("Slack: delivered")
    return 0


def _write_run_log(result: Result, llm: ClaudeLlm, seconds: float) -> None:
    """Keep the full result of every run for debugging and for cost/latency numbers."""
    RUNS_DIR.mkdir(exist_ok=True)
    name = result.company.domain or "no-website"
    path = RUNS_DIR / f"{result.processed_at:%Y%m%d-%H%M%S}-{name}.json"
    log = {
        "seconds": round(seconds, 1),
        "llm_requests": llm.usage.requests,
        "input_tokens": llm.usage.input_tokens,
        "output_tokens": llm.usage.output_tokens,
        "result": result.model_dump(mode="json"),
    }
    path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


def _eval(cases_path: Path, settings: Settings) -> int:
    clients: list[ClaudeLlm] = []

    def make_llm() -> ClaudeLlm:
        clients.append(ClaudeLlm(settings.llm))
        return clients[-1]

    started = time.perf_counter()
    outcomes = evaluation.run_all(evaluation.load_cases(cases_path), make_llm, settings)
    seconds = time.perf_counter() - started
    print(f"model: {settings.llm.model}")
    print(evaluation.report(outcomes))

    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "model": settings.llm.model,
        "passed": sum(outcome.passed for outcome in outcomes),
        "total": len(outcomes),
        "seconds": round(seconds, 1),
        "llm_requests": sum(client.usage.requests for client in clients),
        "input_tokens": sum(client.usage.input_tokens for client in clients),
        "output_tokens": sum(client.usage.output_tokens for client in clients),
        "outcomes": [outcome.model_dump(mode="json") for outcome in outcomes],
    }
    path = EVAL_RESULTS_DIR / f"{settings.llm.model}.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved: {path}")
    return 0 if all(outcome.passed for outcome in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
