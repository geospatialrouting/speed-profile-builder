# Contributing

Thanks for looking. This is a small, focused tool; the bar is that a change
makes a real routing-profile workflow better.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

## Before opening a pull request

```bash
ruff check .
ruff format --check .
pytest -q
```

CI runs exactly these on Python 3.11, 3.12 and 3.13.

## Ground rules

- **Respect the layers.** `spec` parses and validates, `model` normalises,
  `emit_*` generates, and `diff` / `lint` / `simulate` consume the IR. If a
  change needs an emitter to import YAML, or the spec layer to know what OSRM
  is, it is in the wrong place.
- **Nothing may require a live routing engine**, at runtime or in tests.
- **Codegen stays deterministic.** No timestamps, no absolute paths, no
  unordered iteration in generated output.
- **Regenerate goldens deliberately.** `UPDATE_GOLDEN=1 pytest
  tests/test_golden.py`, then read the diff — it is the review of your change.
- **New lint rules** need a test that fires them on a crafted bad spec and one
  that shows them silent on a good one, plus an entry in the README table. The
  rule name in the registry must match the `rule` field on its findings.
- **New spec keys** need validation, a failure-path test with the expected
  message, and handling in both emitters (or an explicit note in the README's
  limitations if one engine cannot express it).
- Every public function and class gets a docstring explaining *why*.

## Reporting a bug

A spec that reproduces it is worth ten paragraphs. Include the output of
`speed-profile validate` and, if codegen is involved, the generated file.
