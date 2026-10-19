"""Day-one check: the Foundry model deployment answers through Agent Framework.

uv run python -m agents.smoke
"""

import asyncio
import time

from agent_framework.foundry import FoundryChatClient

from config import credential, setting


async def main() -> None:
    async with credential() as cred:
        client = FoundryChatClient(
            project_endpoint=setting("FOUNDRY_PROJECT_ENDPOINT"),
            model=setting("FOUNDRY_MODEL"),
            credential=cred,
        )
        agent = client.as_agent(
            name="Smoke",
            instructions="One short sentence for a football broadcast graphic. Use only the facts given.",
            # gpt-5-mini: minimal effort is ~1.7 s median warm vs ~2.5 s at the default (medium)
            default_options={"reasoning": {"effort": "minimal"}},
        )
        t0 = time.perf_counter()
        result = await agent.run(
            "Facts: Okafor (Northbridge) sprinted at 33.1 km/h on the counter. Write the caption."
        )
        print(f"{result.text}\n[{setting('FOUNDRY_MODEL')}, {time.perf_counter() - t0:.2f}s]")


if __name__ == "__main__":
    asyncio.run(main())
