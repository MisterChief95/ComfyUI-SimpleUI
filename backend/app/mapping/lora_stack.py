"""The node pack's ``SimpleUILoraStack.loras`` payload as a dedicated control.

Since pack contract 2, ``loras`` is an optional socket with no canvas widget,
so an exported graph usually has no ``loras`` key on the node. The mapping then
builds the control from :data:`EMPTY_PAYLOAD` and the edited payload is written
into ``inputs.loras`` at submission (an untouched empty stack is simply left
out; the pack treats both the same). A ``loras`` literal already in the graph
is read as before, and a linked ``loras`` stays a link with no control.

The payload is submitted exactly as the browser serialized it. A payload this
build cannot read (invalid JSON, an unknown ``schema``) keeps the plain
textarea and gets a warning, so the raw value stays editable. LoRA names the
catalog does not offer are flagged, never dropped.

Payload (comfyui-simpleui-nodes contract 2, payload schema 1)::

    {"schema": 1, "loras": [{"name": "styles/foo.safetensors",
      "strength_model": 1.0, "strength_clip": 1.0, "enabled": true,
      "trigger_words": "foo style"}]}

Names use forward slashes; ComfyUI's catalog lists them with OS separators.
"""

from __future__ import annotations

import json
from typing import Any

from ..contracts import ControlDescriptor, EnumOption, ErrorDetail

CLASS_TYPE = "SimpleUILoraStack"
INPUT_NAME = "loras"
SUPPORTED_SCHEMA = 1
EMPTY_PAYLOAD = '{"schema":1,"loras":[]}'


def catalog_lora_names(catalog_nodes: dict[str, Any]) -> list[str]:
    """LoRA filenames the catalog offers, in catalog spelling (as LoraLoader lists them)."""
    return sorted(
        {
            choice
            for node in catalog_nodes.values()
            for name, spec in (node.get("inputs") or {}).items()
            if name == "lora_name"
            for choice in spec.get("choices") or []
            if isinstance(choice, str)
        }
    )


def payload_problem(text: str) -> str | None:
    """Why ``text`` is not a readable schema-1 payload, or None."""
    try:
        data = json.loads(text)
    except ValueError:
        return "is not valid JSON"
    if not isinstance(data, dict) or not isinstance(data.get("loras"), list):
        return "is not an object with a 'loras' list"
    if data.get("schema") != SUPPORTED_SCHEMA or isinstance(data.get("schema"), bool):
        return f"uses schema {data.get('schema')!r}; this app reads schema {SUPPORTED_SCHEMA}"
    if not all(isinstance(entry, dict) for entry in data["loras"]):
        return "has a 'loras' item that is not an object"
    return None


def adapt(
    control: ControlDescriptor,
    lora_names: list[str],
    warnings: list[ErrorDetail],
    *,
    injected: bool = False,
) -> ControlDescriptor:
    """Present ``loras`` as a LoRA stack when its payload is readable.

    ``injected`` marks a control built for a node whose graph has no ``loras``.
    """
    if control.component not in ("text", "textarea") or not isinstance(
        control.value, str
    ):
        return control
    field = f"{control.node_id}.inputs.{control.input_name}"
    problem = payload_problem(control.value)
    if problem is not None:
        warnings.append(
            ErrorDetail(
                field=field,
                code="lora_stack_unreadable",
                message=(
                    f"The LoRA Stack payload on node {control.node_id} {problem}. It is shown "
                    "as raw text and submitted unchanged."
                ),
            )
        )
        # The contract-2 socket declares no `multiline`; JSON still needs room.
        return control.model_copy(update={"component": "textarea", "multiline": True})

    known = {name.replace("\\", "/") for name in lora_names}
    missing = [
        entry["name"]
        for entry in json.loads(control.value)["loras"]
        if isinstance(entry.get("name"), str) and entry["name"] not in known
    ]
    if missing:
        warnings.append(
            ErrorDetail(
                field=field,
                code="lora_missing",
                message=(
                    f"LoRA Stack on node {control.node_id} names LoRAs this ComfyUI does not "
                    f"list: {', '.join(missing)}. They are kept; ComfyUI rejects enabled ones."
                ),
            )
        )
    return control.model_copy(
        update={
            "component": "lora_stack",
            "group": "inactive" if control.group == "inactive" else "model",
            "options": [EnumOption(value=n, label=n) for n in lora_names],
            "inference_reason": (
                f"pack:lora_stack{',injected' if injected else ''};"
                f"{control.inference_reason}"
            ),
        }
    )
