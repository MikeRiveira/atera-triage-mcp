# Contributing

Thanks for helping. Issues and pull requests are welcome.

1. Fork the repo and create a branch.
2. `pip install -e ".[test]"`
3. Make your change and add a test for it. Tests must not call the real Atera API; use `DemoClient`
   or a fake transport like the ones in `tests/`.
4. Run `python -m pytest` and open a pull request describing what changed and why.

Good first contributions: a new triage rule, support for another Atera field name, or more demo data.
Please never include real customer, ticket or device data in issues, tests or screenshots.
