import argparse
import asyncio
import os
from urllib.parse import urlparse

import httpx

from .worker import AgentWorker


async def main():
    parser = argparse.ArgumentParser(description="AssureDen HTTP browser agent")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--channel", default=None, help="Installed browser, e.g. msedge")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("ASSUREDEN_AGENT_API_KEY")
    if not key:
        parser.error("Set ASSUREDEN_AGENT_API_KEY to the registered agent key")
    url = urlparse(args.server)
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
        parser.error("Remote agent connections require HTTPS")
    async with httpx.AsyncClient(base_url=args.server, headers={"X-Agent-Api-Key": key}, timeout=30) as client:
        worker = AgentWorker(client, channel=args.channel, headless=not args.headed)
        while True:
            try:
                run_id = await worker.poll_once()
                if run_id:
                    print(f"Finished processing run {run_id}", flush=True)
            except httpx.HTTPError:
                print("Agent connection or lease rejected; check server and registration", flush=True)
                if args.once:
                    raise SystemExit(1)
            if args.once:
                return
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
