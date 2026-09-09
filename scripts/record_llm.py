"""Record LLM responses for the bundled samples so the demo can replay them offline.

    LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... uv run python scripts/record_llm.py

Each sample is profiled, turned into the same request the proposer would send, and the
response is written to `fixtures/llm/<request_hash>.json`. Those files are what
`LLM_PROVIDER=replay` serves, so committing them is what lets someone clone the repo and run
the full ensemble with no API key and no network.

Re-running is cheap: a sample whose fixture already exists is skipped unless --force is
given, because the request hash only changes when the profile or the target schema changes.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.settings import get_settings  # noqa: E402
from app.db import duck  # noqa: E402
from app.ingest.readers import land  # noqa: E402
from app.profile.profiler import profile_frame  # noqa: E402
from app.propose.providers import llm  # noqa: E402
from app.target import loader  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", default="orders", help="target schema name")
    parser.add_argument("--force", action="store_true", help="re-record existing fixtures")
    parser.add_argument("--sample", action="append", help="limit to these sample filenames")
    args = parser.parse_args()

    settings = get_settings()
    if settings.llm_provider.lower() == "replay":
        print(
            "LLM_PROVIDER is 'replay', which reads fixtures rather than writing them.\n"
            "Set LLM_PROVIDER=anthropic (with ANTHROPIC_API_KEY) or =openrouter and re-run.",
            file=sys.stderr,
        )
        return 2

    duck.init_db()
    schema = loader.get(args.schema)
    samples = sorted(p for p in settings.samples.glob("*") if p.is_file() and p.suffix != ".py")
    if args.sample:
        wanted = set(args.sample)
        samples = [p for p in samples if p.name in wanted]
    if not samples:
        print("no samples matched", file=sys.stderr)
        return 1

    written = skipped = failed = 0
    for path in samples:
        profiles = profile_frame(land(path).frame)
        request = llm.build_request(profiles, schema)
        digest = llm.request_hash(request)

        if not args.force and llm.load_fixture(digest) is not None:
            print(f"  skip  {path.name} ({digest}) — already recorded")
            skipped += 1
            continue

        try:
            response = llm.fetch(request, source_id=None)
        except llm.LLMUnavailable as exc:
            print(f"  FAIL  {path.name}: {exc}", file=sys.stderr)
            failed += 1
            continue

        llm.save_fixture(digest, request, response, settings.llm_provider, settings.anthropic_model)
        mapped = sum(1 for m in response.get("mappings", []) if m.get("target_field"))
        print(f"  wrote {path.name} ({digest}) — {mapped} columns mapped")
        written += 1

    print(f"\n{written} written, {skipped} skipped, {failed} failed")
    print(f"fixtures live in {settings.llm_fixtures}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
