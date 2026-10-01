"""Test doubles shared by the test modules."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from leadqual.llm import Tool
from leadqual.web import FetchError, Page


@dataclass
class LlmCall:
    system: str
    prompt: str
    tools: list[Tool]
    web_search: bool


@dataclass
class FakeLlm:
    """Returns scripted results in order; an exception in the script is raised instead."""

    script: list[BaseModel | Exception]
    calls: list[LlmCall] = field(default_factory=list)

    def run(
        self,
        *,
        system: str,
        prompt: str,
        result_type: type[BaseModel],
        tools: Sequence[Tool] = (),
        web_search: bool = False,
    ) -> BaseModel:
        self.calls.append(LlmCall(system, prompt, list(tools), web_search))
        result = self.script.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeSite:
    """A fetcher over an in-memory website: {url: page text}; links are every other page."""

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages
        self.fetched: list[str] = []

    def __call__(self, url: str) -> Page:
        self.fetched.append(url)
        if url not in self._pages:
            raise FetchError(f"HTTP 404 from {url}")
        links = tuple(other for other in self._pages if other != url)
        return Page(url=url, text=self._pages[url], links=links)
