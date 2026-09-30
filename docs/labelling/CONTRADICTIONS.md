# Labelling the contradiction sample

You are the ground truth. Nothing in this repository can substitute for you —
the whole point of ADR-027 is that a heuristic classification is not evidence,
and the last one was wrong.

## Get the sheet

```bash
python scripts/contradiction_sample.py build build/ganymede4-corpus.db --out /tmp/sheet.csv
```

122 rows: 60 code, 60 prose, 2 non-Latin. The last stratum has only 2 pairs in
the entire artifact, so it is included whole.

## Fill in the `label` column

One label per row. Do not skip rows and do not leave blanks — the scorer counts
blanks separately so a half-done sheet cannot quietly report a precision over
the rows that happened to be easy.

| label | means |
|---|---|
| `real_contradiction` | the two texts assert **opposing propositions**. Both can be true at different times, or one is genuinely wrong. |
| `code_fragment` | two drafts or revisions of the same code. Same intent, different text. |
| `subset` | one text contains the other plus extra material. Neither opposes the other. |
| `boilerplate` | stock phrasing with no propositional content — sign-offs, "Thanks!", "let me know". |
| `other` | none of the above. Use it freely; it is a real answer. |

Two traps worth naming:

- **Subset is not contradiction.** `'a blog path'` vs `'a blog path\nNo.'` is
  a subset. The excerpt inherits the polarity of the whole, so the evaluator
  fires, but nothing opposing is asserted. This is the most common mistake in
  the class, and it is the failure ADR-024 already fixed on the SUPPORTED
  side.
- **Code is not contradiction.** `return None` vs `return ''` are two drafts.
  Labelling them `code_fragment` is a *finding*, not a dismissal — it is the
  measurement that decides ADR-027.

## What a genuine contradiction looks like

These came from the corpus and the evaluator found them unaided:

```
A: 'It is also a pyramid scheme.'
B: 'THIS IS NOT A PYRAMID SCHEME!'

A: 'can you run openai or anthropic locally'
B: 'OpenAI and Anthropic you can not run locally.'
```

Nobody told the evaluator to look for these. They are why the component exists.

## Score it

```bash
python scripts/contradiction_sample.py score /tmp/sheet.csv
```

Then, for each policy under consideration:

```bash
python scripts/contradiction_sample.py score /tmp/sheet.csv --policy no-code
python scripts/contradiction_sample.py score /tmp/sheet.csv --policy no-code-or-subset
```

The policy report gives the number the decision turns on: **how many real
contradictions each rule would destroy.** Real ones are unrecoverable —
`CONTRADICTED` is absorbing — so that asymmetry is the decision, not a
tiebreak.

If that number is greater than zero for every option, the honest outcome is
that this corpus is not suited to automatic contradiction detection and the
finding is worth more than the fix.