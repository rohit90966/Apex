#!/usr/bin/env python3
"""
Evaluation Harness for the Autonomous Stateful Investigation Agent.
Runs the investigation engine against target fixtures or websites,
evaluates discovery coverage, oracle violations, skeptic precision,
and 2-of-2 isolated browser context reproduction.
"""

import sys
import os
import asyncio
import json
import argparse
import time
from typing import Dict, Any, List

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.investigation_agent import run_stateful_investigation
from app.investigation_agent.models import InvestigationReport


async def evaluate_target(url: str, max_actions: int = 15, max_time_seconds: float = 120.0) -> Dict[str, Any]:
    print(f"\n=================================================================")
    print(f"🚀 Starting Autonomous Investigation Evaluation on: {url}")
    print(f"   Max Actions Budget: {max_actions} | Max Time: {max_time_seconds}s")
    print(f"=================================================================\n")

    start_time = time.time()
    report: InvestigationReport = await run_stateful_investigation(
        url=url,
        max_actions=max_actions,
        max_time_seconds=max_time_seconds,
        headless=True,
    )
    elapsed = time.time() - start_time

    ov = report.overview
    confirmed = report.confirmed_bug_leads or []
    rejected = report.unstable_or_rejected_leads or []
    total_candidates = len(confirmed) + len(rejected)

    precision = len(confirmed) / total_candidates if total_candidates > 0 else 1.0

    eval_result = {
        "target_url": url,
        "evaluation_duration_seconds": round(elapsed, 2),
        "pages_discovered": ov.pages_discovered,
        "meaningful_states_discovered": ov.meaningful_states_discovered,
        "transitions_observed": ov.transitions_observed,
        "route_transitions_observed": ov.route_transitions_observed,
        "same_route_state_transitions": ov.same_route_state_transitions,
        "actions_evaluated": ov.actions_evaluated,
        "actions_attempted": ov.actions_attempted,
        "actions_successful": ov.actions_successful,
        "actions_verified_effects": ov.actions_verified_effects,
        "candidate_leads_total": total_candidates,
        "confirmed_leads_2_of_2": len(confirmed),
        "skeptic_rejected_leads": len(rejected),
        "reproduction_precision": round(precision, 2),
        "confirmed_leads": [
            {
                "lead_id": b.lead_id,
                "title": b.title,
                "severity": b.severity,
                "oracle_type": b.oracle_type.value if hasattr(b.oracle_type, "value") else str(b.oracle_type),
                "minimized_steps_count": len(b.minimized_steps),
            }
            for b in confirmed
        ],
        "stopping_reason": ov.stopping_reason,
    }

    print("\n---------------------- Evaluation Results ----------------------")
    print(f"⏱️  Duration: {eval_result['evaluation_duration_seconds']}s")
    print(f"🌐 Pages Discovered: {eval_result['pages_discovered']}")
    print(f"🏛️  Meaningful States: {eval_result['meaningful_states_discovered']}")
    print(f"🔄 Transitions Observed: {eval_result['transitions_observed']} (Route: {eval_result['route_transitions_observed']}, In-Page: {eval_result['same_route_state_transitions']})")
    print(f"⚡ Probes Attempted: {eval_result['actions_attempted']} (Successful: {eval_result['actions_successful']}, Verified Effects: {eval_result['actions_verified_effects']})")
    print(f"🎯 Total Leads Evaluated: {eval_result['candidate_leads_total']}")
    print(f"✅ Confirmed Bug Leads (2/2 Fresh Contexts): {eval_result['confirmed_leads_2_of_2']}")
    print(f"🛡️  Skeptic Filtered Candidates: {eval_result['skeptic_rejected_leads']}")
    print(f"📊 Reproducibility Precision: {eval_result['reproduction_precision'] * 100:.1f}%")
    print(f"🛑 Stopping Reason: {eval_result['stopping_reason']}")
    print("----------------------------------------------------------------\n")

    return eval_result


def main():
    parser = argparse.ArgumentParser(description="VerityQA Autonomous Investigation Evaluator")
    parser.add_argument("--url", default="http://127.0.0.1:8001/login", help="Target URL to investigate")
    parser.add_argument("--actions", type=int, default=12, help="Max actions budget")
    parser.add_argument("--time", type=float, default=60.0, help="Max time budget in seconds")
    parser.add_argument("--output", default=None, help="Path to save evaluation summary JSON")

    args = parser.parse_args()

    result = asyncio.run(evaluate_target(args.url, max_actions=args.actions, max_time_seconds=args.time))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"💾 Saved evaluation report to: {args.output}")


if __name__ == "__main__":
    main()
