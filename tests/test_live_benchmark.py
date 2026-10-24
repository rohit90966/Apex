import asyncio
import json
from app.main import run_benchmark

async def main():
    print("=== RUNNING LIVE BENCHMARK EVALUATION ===")
    response = await run_benchmark()
    body = json.loads(response.body.decode("utf-8"))
    print("\n=== BENCHMARK SUMMARY RESULT ===")
    print(f"Total Planted Bugs: {body.get('total_planted_bugs')}")
    print(f"True Positives:     {body.get('true_positives_detected')}")
    print(f"False Positives:    {body.get('false_positives')}")
    print(f"Missed Bugs:        {body.get('missed_bugs')}")
    print(f"Precision:          {body.get('precision') * 100:.1f}%")
    print(f"Recall:             {body.get('recall') * 100:.1f}%")
    print(f"F1 Score:           {body.get('f1_score') * 100:.1f}%")
    print(f"Detected Bug IDs:   {body.get('detected_bug_ids')}")
    print(f"Missed Bug IDs:     {body.get('missed_bug_ids')}")
    print("\nDetailed Reports:")
    for r in body.get("detailed_reports", []):
        print(f"  - [{r.get('bug_id')}] {r.get('title')}")
        print(f"    Status: {r.get('verification_status')} | Severity: {r.get('severity')}")
        print(f"    Cause:  {r.get('suspected_cause')}")
        print(f"    Fix:    {r.get('suggested_fix')}")

if __name__ == "__main__":
    asyncio.run(main())
