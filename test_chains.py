import asyncio
import sys
from lib.providers import LLMProvider

async def test_all_chains():
    provider = LLMProvider()
    operations = [
        "ingest_extract",
        "ingest_synth",
        "ingest_crossref",
        "query_answer",
        "query_fileback",
        "lint_audit",
        "lint_fix"
    ]
    
    for op in operations:
        print(f"\nTesting operation: {op}")
        try:
            res = await provider.acall(
                messages=[{"role": "user", "content": "say hi"}],
                operation=op
            )
            print(f"[{op}] SUCCESS: {res[:50].strip()}...")
        except Exception as e:
            print(f"[{op}] FAILED: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_all_chains())
