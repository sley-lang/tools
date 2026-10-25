# Sley Review

Review explains a change between two manifest-bound native Sley program packages. Use it in a Git workflow to see exact artifact identities, changed native objects, and the behavior observed on declared cases. `review verify` recomputes the report from those inputs, including after it has been saved or shared.

```sh
sley-tools review --before BEFORE/program.json --after AFTER/program.json \
  --core "$CORE" --cases CASES.json --output review.json
sley-tools review verify review.json --before BEFORE/program.json \
  --after AFTER/program.json --core "$CORE" --cases CASES.json
```

The [Review guide](../../docs/REVIEW_V0.md) explains package preparation, limits, and the reusable [GitHub Action](../../actions/check/action.yml). A finite case set shows observed behavior; it does not prove every possible input is equivalent. Native core `TestCase` execution remains pending on the selected public core. [Install RC7](../../docs/INSTALL_RC7.md)
