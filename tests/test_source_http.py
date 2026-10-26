

def test_a_keyless_source_that_refuses_us_says_refused_not_no_key():
    # Oct 2026: The Gazette started answering the Space with 403, which read as "isn't configured (no API key)".
    import asyncio

    import httpx

    from src.prescreen.sources.http import SourceError, get_json

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    async def go(keyed: bool) -> SourceError:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                await get_json(client, "The Gazette", "https://example.test/x", keyed=keyed)
            except SourceError as err:
                return err
        raise AssertionError("no error raised")
    refused = asyncio.run(go(False))
    assert refused.kind == "refused" and refused.plain == "The Gazette refused the request (HTTP 403)."
    assert asyncio.run(go(True)).kind == "no_key"
