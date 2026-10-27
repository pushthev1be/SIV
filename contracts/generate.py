"""Write the combined JSON Schema that json-schema-to-typescript turns into web/src/contracts.ts.

uv run python -m contracts.generate      # then: npm --prefix web run gen:contracts
"""

import json
from pathlib import Path

from pydantic.json_schema import models_json_schema

from contracts import ROOT_MODELS

OUT = Path(__file__).resolve().parent.parent / "web" / "src" / "contracts.schema.json"


def _normalize(node, keep_title: bool = False):
    """Make Pydantic's 2020-12 output readable by json2ts.

    Tuples: `prefixItems` becomes a draft-07 `items` array, so [x, y] is typed, not unknown.
    Titles: dropped below the model level, so fields inline instead of becoming aliases like `Clock1`.
    """
    if isinstance(node, list):
        return [_normalize(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key == "title" and not keep_title:
            continue
        if key == "prefixItems":
            out["items"] = _normalize(value)
            out["additionalItems"] = False
        elif key in ("$defs", "properties"):
            # keys here are names, not schema keywords; model definitions keep their titles
            out[key] = {k: _normalize(v, keep_title=key == "$defs") for k, v in value.items()}
        else:
            out[key] = _normalize(value)
    return out


def build_schema() -> dict:
    _, schema = models_json_schema([(m, "validation") for m in ROOT_MODELS], ref_template="#/$defs/{model}")
    schema = _normalize(schema)
    schema["title"] = "SivContracts"
    # A root object referencing every model, so json2ts emits all of them as named exports.
    schema["type"] = "object"
    schema["properties"] = {m.__name__: {"$ref": f"#/$defs/{m.__name__}"} for m in ROOT_MODELS}
    schema["additionalProperties"] = False
    return schema


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
