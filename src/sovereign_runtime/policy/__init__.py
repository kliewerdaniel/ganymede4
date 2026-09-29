"""The policy gateway: fail-closed authorization (ADR-007).

The gate between a proposed action and a performed one. Nothing in this package
executes anything — it judges requests and records the judgement, and execution
is a separate concern (ADR-008) so that the gate can be tested without a
sandbox.
"""
