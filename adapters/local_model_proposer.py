"""A Proposer backed by a real local model (Phase 19 Task 4).

This lives *outside* the package core, deliberately. The core stays
stdlib-only and offline and never knows a model exists; this module is the
only place that speaks HTTP, and it satisfies the same `Proposer` protocol
`RuleProposer` does.

The design constraint that matters: this class is allowed to be wrong,
creative, adversarial, or hallucinating. The loop must not care. Every
proposal it emits goes through the same gateway, and the gateway has no idea
where the text came from. That is the whole claim Task 4 exists to test --
so nothing here is trusted, and there is no path from model output to a store
write that does not pass through L3.

Local-only by construction: the endpoint must resolve to a loopback address
or this refuses at construction rather than at first request, so a typo in a
config cannot turn into a covert network call.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Sequence

from ganymede4.runtime.loop import Proposal
from ganymede4.witness.witness import Witness

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b"

#: Ollama's `/api/chat` on a qwen3 build returns the answer in
#: `message.thinking`, not `message.content`, and `enable_thinking:false`
#: does NOT redirect it. Read every candidate field.
_ANSWER_FIELDS = ("content", "thinking", "reasoning_content")

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class ModelUnavailable(RuntimeError):
    """The endpoint could not be reached or produced nothing usable."""


def _refuse_non_local(url: str) -> str:
    """Fail closed on any endpoint that is not this machine.

    Construction-time, not request-time: a non-local endpoint should fail
    before a single prompt is sent, not after.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    try:
        address = ipaddress.ip_address(host)
        if not address.is_loopback:
            raise ValueError(f"{host} is not loopback")
    except ValueError as exc:
        if host not in ("localhost", "localhost.localdomain"):
            raise ValueError(
                f"refusing non-local inference endpoint {host!r}: this adapter "
                "may only talk to this machine (localhost/127.0.0.0/8/[::1])"
            ) from exc
    return url


@dataclass
class LocalModelProposer:
    """Proposes claims from what a real model says about the witness output.

    The model is asked to *restate* what the witness surfaced and to cite the
    ids it was given. It is not asked whether anything is true -- that is not
    its job and giving it the job would make the loop's guarantee a property
    of the prompt instead of of the architecture.
    """

    name: str = "ollama"
    url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    timeout: float = 180.0
    #: Cited ids the model was shown, for this question only.
    allowed_cites: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _refuse_non_local(self.url)

    # -- transport ---------------------------------------------------------
    def available(self) -> bool:
        """Never raises. A dead endpoint is a False, not an exception."""
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=5) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def _chat(self, system: str, user: str) -> str:
        """One chat call. Returns the answer text from wherever the build put it."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            # Ollama-side structured output. Without it qwen3 narrates its
            # reasoning and gets cut off by num_predict before emitting any
            # JSON at all (measured: 23.8s, zero parseable objects at 1200
            # tokens; the entire budget went to the trace).
            "format": "json",
            # llama.cpp/ollama-server top-level `think:false`. Measured on
            # qwen3:4b against real corpus evidence: WITHOUT it the model
            # spends the entire 4,000-token budget narrating and returns
            # `content: ""` (eval_count 4000, thinking 12,828 chars). WITH
            # it, eval_count 534 and clean JSON in `content`.
            #
            # `options.enable_thinking: false` does NOT work here and is not
            # an error -- the server accepts it and ignores it (measured:
            # content empty, eval_count 2000). Both were tried; only the
            # top-level key suppresses the trace.
            "think": False,
            "options": {"num_predict": 2000, "temperature": 0.2},
        }
        request = urllib.request.Request(
            f"{self.url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ModelUnavailable(f"{self.url}: {exc}") from exc
        message = data.get("message") or {}
        for field_name in _ANSWER_FIELDS:
            value = message.get(field_name)
            if isinstance(value, str) and value.strip():
                return value
        raise ModelUnavailable("model returned no answer in any known field")

    # -- parsing -----------------------------------------------------------
    @staticmethod
    def _first_json(text: str) -> dict | None:
        match = _JSON_BLOCK.search(text)
        if match is None:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    # -- the protocol ------------------------------------------------------
    def propose(self, witness: Witness, budget: int) -> Sequence[Proposal]:
        """Return proposals for whatever the witness can answer.

        Fail-closed at the transport: if the model is unreachable this raises
        rather than returning an empty tuple, because "the model said nothing"
        and "the model was not there" must not look the same to the caller.
        """
        if budget <= 0:
            return ()
        if not self.available():
            raise ModelUnavailable(f"no model at {self.url}")

        counts = witness.boundary().state_counts
        if sum(counts.values()) == 0:
            # Same rule as RuleProposer: an empty artifact gets no proposals.
            return ()

        question = "provenance"
        answer = witness.ask(question)
        if not answer.claims:
            return ()

        cites = tuple(claim_id for claim_id, _ in answer.claims)
        # The id is shown IN FULL. Truncating it -- even to a readable
        # prefix -- produces ids the model cannot possibly reproduce, so it
        # either cites garbage or cites nothing. Measured: truncating to
        # `clm-3ebc73b...` made qwen3:4b unable to emit a single valid
        # citation, and the gateway correctly refused the result. The bug was
        # in the prompt, and the defence worked, which is exactly why a
        # refusal needs its cause recorded rather than assumed.
        excerpts = "\n".join(
            f"[{claim_id}] {text[:300]}" for claim_id, text in answer.claims[:8]
        )
        system = (
            "You summarise evidence. Reply with ONE JSON object and nothing "
            "else. Keys: {\"claim\": string, \"cites\": [string], "
            "\"state\": \"supported\"}. Cite only ids that appear in the input."
        )
        user = (
            f"Evidence about {question!r}:\n{excerpts}\n\n"
            "State what the evidence establishes. Cite the ids you used."
        )
        answer_text = self._chat(system, user)
        parsed = self._first_json(answer_text)
        if parsed is None:
            # Unparseable output is not a proposal. Returning the raw text
            # would let prose become a claim.
            return ()

        claim_text = parsed.get("claim")
        if not isinstance(claim_text, str) or not claim_text.strip():
            return ()
        raw_cites = parsed.get("cites")
        proposed_cites = (
            tuple(c for c in raw_cites if isinstance(c, str))
            if isinstance(raw_cites, list)
            else ()
        )
        state = parsed.get("state")
        proposed_state = state if state in ("supported", "contradicted") else "supported"

        return (
            Proposal.make(
                question=question,
                text=claim_text,
                proposed_state=proposed_state,
                cites=proposed_cites,
                rationale=f"{self.name}:{self.model}",
            ),
        )