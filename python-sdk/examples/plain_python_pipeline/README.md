# Plain Python: a step that drops a field

`broken.py`'s `normalize` step returns a new dict and forgets `customer_id`. Nothing raises:
`lookup_customer` uses `state.get("customer_id")`, finds nobody, and the reply goes out as
"Hi there, ... unknown plan".

```bash
python examples/plain_python_pipeline/broken.py
tervic check          # exit 1: TV002 dropped-field, root cause `normalize`
tervic fix            # prompt pointing at the exact file:line of `normalize`

python examples/plain_python_pipeline/fixed.py
tervic check          # exit 0
```

No framework needed: steps use `@tervic.step` and the run is `with tervic.run("support-pipeline")`.
Tervic learns which fields each step reads from its source (`state["x"]`, `state.get("x")`), which is
how it knows `lookup_customer` needed the dropped field.
