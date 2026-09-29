"""The runtime: a bounded proposal loop over a read-only witness (ADR-009).

This is the first component that *wants* something, and the first place the
project's central claim becomes testable rather than asserted — that the model
is a probabilistic component inside a governed runtime, never the intelligence
system and never the authority.

``target-architecture.md`` §2, implemented literally: **nothing in L4 can write
to L1.** The :class:`Runtime` holds a witness and a gateway and has no
reference to the store, so there is no call site to audit and no later refactor
that can quietly add one.
"""

from .loop import (
    Proposal,
    ProposalDisposition,
    Proposer,
    RuleProposer,
    RunRecord,
    Runtime,
)

__all__ = [
    "Runtime",
    "Proposal",
    "ProposalDisposition",
    "Proposer",
    "RuleProposer",
    "RunRecord",
]
