## What and why

<!-- One paragraph: the problem, and how this change solves it. Link the issue. -->

## How it was verified

- [ ] `make lint && make typecheck && make test`
- [ ] `make e2e` (if the inference path or infrastructure changed)
- [ ] `make tf-validate` (if Terraform changed)

## Checklist

- [ ] Docs updated (README, `docs/`, configuration reference) for any behaviour or config change
- [ ] Memory plans never exceed the detected hardware budget
- [ ] No secrets, account IDs or real hostnames
- [ ] Security-relevant change? Described the threat and mitigation above
