# Contributing

Thanks for helping improve `llm-eval-drift-radar`.

## Development Setup

This project intentionally uses no runtime dependencies.

```bash
python -m pip install -e .
python -m unittest discover -s tests
```

You can also run the package directly:

```bash
PYTHONPATH=src python -m llm_eval_drift_radar --help
```

## Contribution Guidelines

- Keep the CLI usable offline.
- Do not add network calls to core comparison or reporting.
- Prefer Python standard library modules unless a dependency unlocks a clearly necessary capability.
- Preserve stable machine-readable JSON and CSV fields when possible.
- Add or update unittest coverage for behavior changes.
- Avoid logging prompts, expected answers, actual answers, or secrets beyond the user-requested report outputs.

## Testing Checklist

Before opening a pull request:

```bash
python -m unittest discover -s tests
PYTHONPATH=src python -m llm_eval_drift_radar --baseline examples/baseline.jsonl --current examples/current.jsonl --thresholds examples/thresholds.json --format json
```

## Security

Do not include API keys, GitHub tokens, customer prompts, or private eval data in issues, tests, examples, or pull requests. If you discover a security issue, report it privately to the maintainers rather than posting exploit details publicly.

