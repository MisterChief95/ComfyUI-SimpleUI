"""Turn a raw ``/object_info`` payload into a client-safe node projection.

Two outputs come out of one pass:

* ``nodes`` — the **sanitized projection**. This is the only catalog shape a
  profile ever sees. Shared ComfyUI input-directory filenames and anything that
  looks like an internal path are withheld here, not filtered later at the API
  edge, so no route can forget to do it.
* ``withheld`` — the choice lists that were removed, kept server-side for
  upload staging and submission validation (INPUT-001, GEN-001).

The shape of ``nodes`` is specified by
``tests/fixtures/catalog/sanitized_projection.expected.json`` and is asserted
against that fixture in backend/tests/catalog/.

Both metadata shapes in the fixture set must normalize: the current one and
``object_info.legacy-shape.json``, which omits ``python_module``,
``input_order`` and the hint keys and gives combo inputs as a bare option list
with no trailing options object. Anything not understood is preserved and
marked for an adapter — never dropped, and never fallen through to a generic
dropdown.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

#: Beyond this, JavaScript loses precision; such values cross the wire as
#: decimal strings (contracts.ExactInt) and never drive a slider.
JS_SAFE_INT = 2**53 - 1

#: Scalar types that become form controls.
PRIMITIVE_TYPES = frozenset({"STRING", "INT", "FLOAT", "BOOLEAN"})

#: Internal data sockets: connections, never ordinary form controls. A custom
#: type outside this set is NOT assumed to be a socket — it gets an
#: adapter-required diagnostic, because its editing contract is unknown.
KNOWN_SOCKET_TYPES = frozenset(
    {
        "MODEL",
        "CLIP",
        "VAE",
        "CLIP_VISION",
        "CLIP_VISION_OUTPUT",
        "CONDITIONING",
        "LATENT",
        "IMAGE",
        "MASK",
        "VIDEO",
        "AUDIO",
        "CONTROL_NET",
        "STYLE_MODEL",
        "GLIGEN",
        "UPSCALE_MODEL",
        "SIGMAS",
        "SAMPLER",
        "GUIDER",
        "NOISE",
        "PHOTOMAKER",
        "WEBCAM",
        "FLOATS",
        "INT_LIST",
    }
)

MODEL_EXTENSIONS = (
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
    ".gguf",
    ".sft",
    ".onnx",
)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif")
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v")
AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")

#: An option naming an absolute location is an internal path, withheld outright.
#: A relative subfolder ("SDXL/model.safetensors") is a normal model name.
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2}|~)")

#: Dynamically named inputs (input_1, input_2, ...) that object_info does not
#: enumerate. Their presence means exact-name matching must not reject a graph.
_DYNAMIC_NAME = re.compile(r"^(.+)_(\d+)$")


class NormalizeError(ValueError):
    """The payload is not a usable object_info document."""


@dataclass(frozen=True)
class NormalizedCatalog:
    """One validated snapshot. ``nodes`` is safe to serve; ``withheld`` is not."""

    nodes: dict[str, Any]
    withheld: dict[str, dict[str, list[str]]]
    #: Changes when any type/constraint/link contract changes. Ignores choice
    #: lists, so installing a model does not read as a structural change.
    schema_hash: str
    #: Changes when anything at all changes, choice lists included.
    content_hash: str
    node_packs: list[str] = field(default_factory=list)

    @property
    def revision(self) -> str:
        return self.content_hash[:12]


def normalize(raw: Any) -> NormalizedCatalog:
    """Validate and project a raw ``/object_info`` document."""
    if not isinstance(raw, dict) or not raw:
        raise NormalizeError("object_info must be a non-empty JSON object")

    nodes: dict[str, Any] = {}
    withheld: dict[str, dict[str, list[str]]] = {}
    packs: set[str] = set()

    for class_type, definition in raw.items():
        if class_type.startswith("_"):
            continue  # fixture commentary, never a node class
        if not isinstance(definition, dict):
            raise NormalizeError(f"Node {class_type!r} is not an object")
        node, node_withheld = _project_node(class_type, definition)
        nodes[class_type] = node
        if node_withheld:
            withheld[class_type] = node_withheld
        module = definition.get("python_module")
        if isinstance(module, str) and module.startswith("custom_nodes."):
            packs.add(module.split(".")[1])

    if not nodes:
        raise NormalizeError("object_info contained no node classes")

    return NormalizedCatalog(
        nodes=nodes,
        withheld=withheld,
        schema_hash=_hash(_structure_only(nodes)),
        content_hash=_hash(raw),
        node_packs=sorted(packs),
    )


# --- per node -------------------------------------------------------------


def _project_node(
    class_type: str, definition: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    spec = definition.get("input")
    spec = spec if isinstance(spec, dict) else {}
    required = spec.get("required") if isinstance(spec.get("required"), dict) else {}
    optional = spec.get("optional") if isinstance(spec.get("optional"), dict) else {}
    # `hidden` is deliberately absent: backend-hidden inputs are never
    # fabricated as form values (docs/WORKFLOW_MAPPING.md).

    output_node = bool(definition.get("output_node"))
    inputs: dict[str, Any] = {}
    withheld: dict[str, list[str]] = {}

    for name, raw_input in list(required.items()) + list(optional.items()):
        projected, hidden_choices = _project_input(
            class_type, name, raw_input, definition, output_node
        )
        if name in optional:
            projected["optional"] = True
            if projected.get("logical_type") == "LINK":
                projected["present_in_graph_required"] = False
        inputs[name] = projected
        if hidden_choices:
            withheld[name] = hidden_choices

    node: dict[str, Any] = {
        "inputs": inputs,
        "outputs": [o for o in definition.get("output", []) if isinstance(o, str)],
        "output_node": output_node,
    }
    if output_node:
        node["output_role"] = _output_role(class_type, definition, inputs)
    if any(_DYNAMIC_NAME.match(name) for name in optional):
        node["flexible_inputs"] = True
    return node, withheld


def _output_role(
    class_type: str, definition: dict[str, Any], inputs: dict[str, Any]
) -> str:
    """image / image_ephemeral / video, from declared metadata only.

    ponytail: a three-rule heuristic over category, module and the presence of a
    filename input. MEDIA-001 keys real classification off the actual result
    descriptor; an unexpected role here is a label, not a behavior.
    """
    category = str(definition.get("category", "")).lower()
    module = str(definition.get("python_module", "")).lower()
    if (
        "video" in category
        or "video" in module
        or "video" in class_type.lower()
        or "webm" in class_type.lower()
    ):
        return "video"
    # A saver names its file; a preview node writes to the temp directory and
    # its results are ephemeral until privately captured.
    if "filename_prefix" not in inputs:
        return "image_ephemeral"
    return "image"


# --- per input ------------------------------------------------------------


def _project_input(
    class_type: str,
    name: str,
    raw_input: Any,
    definition: dict[str, Any],
    output_node: bool,
) -> tuple[dict[str, Any], list[str]]:
    """Return ``(projection, withheld_choices)`` for one declared input."""
    type_spec, opts = _split_input(raw_input)

    if isinstance(type_spec, list) or type_spec == "COMBO":
        return _project_combo(name, _combo_choices(type_spec, opts), opts)

    if not isinstance(type_spec, str):
        return (
            {
                "logical_type": "UNSUPPORTED",
                "editable": False,
                "diagnostic": "adapter_required",
                "value_preserved": True,
            },
            [],
        )

    if type_spec == "*":
        return {"logical_type": "WILDCARD", "socket_type": "*"}, []

    if type_spec in KNOWN_SOCKET_TYPES:
        return {"logical_type": "LINK", "socket_type": type_spec}, []

    if type_spec == "STRING":
        projected: dict[str, Any] = {"logical_type": "STRING"}
        if opts.get("multiline"):
            projected["multiline"] = True
        if opts.get("dynamicPrompts"):
            # Preserved as a hint only; no macro evaluation happens anywhere.
            projected["dynamic_prompts_hint"] = True
        if output_node and name == "filename_prefix":
            # The application assigns a unique per-generation prefix; the user
            # label is a suffix, never an arbitrary directory.
            projected["managed"] = True
        elif "default" in opts:
            projected["default"] = opts["default"]
        return _with_hints(projected, opts), []

    if type_spec == "BOOLEAN":
        projected = {"logical_type": "BOOLEAN"}
        if "default" in opts:
            projected["default"] = bool(opts["default"])
        return _with_hints(projected, opts), []

    if type_spec == "INT":
        return _with_hints(_project_int(name, opts), opts), []

    if type_spec == "FLOAT":
        projected = {"logical_type": "FLOAT"}
        for key in ("min", "max", "step", "default"):
            if (
                key in opts
                and isinstance(opts[key], (int, float))
                and not isinstance(opts[key], bool)
            ):
                projected[key] = opts[key]
        return _with_hints(projected, opts), []

    # A custom object type with no described editing contract. The literal is
    # preserved untouched and an adapter is required to edit it.
    return (
        {
            "logical_type": "UNSUPPORTED",
            "socket_type": type_spec,
            "editable": False,
            "diagnostic": "adapter_required",
            "value_preserved": True,
        },
        [],
    )


def _with_hints(projected: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    """Attach presentation hints that must not be mistaken for validation rules.

    ``forceInput`` controls ComfyUI's own widget/socket presentation. An
    exported literal for such an input remains valid, editable and submittable
    (docs/WORKFLOW_MAPPING.md), so it is recorded as a hint and the control
    stays editable.
    """
    if opts.get("forceInput"):
        projected["force_input_hint"] = True
        projected["editable"] = True
    return projected


def _split_input(raw_input: Any) -> tuple[Any, dict[str, Any]]:
    """``["INT", {...}]``, ``["INT"]`` (legacy) or ``[[...]]`` -> (type, options)."""
    if not isinstance(raw_input, list) or not raw_input:
        return None, {}
    type_spec = raw_input[0]
    opts = raw_input[1] if len(raw_input) > 1 and isinstance(raw_input[1], dict) else {}
    return type_spec, opts


def _project_int(name: str, opts: dict[str, Any]) -> dict[str, Any]:
    bounds = {
        key: opts[key]
        for key in ("min", "max", "default")
        if isinstance(opts.get(key), int) and not isinstance(opts.get(key), bool)
    }
    seed_like = (
        name == "seed"
        or name.endswith("_seed")
        or bool(opts.get("control_after_generate"))
    )
    exact = seed_like or any(abs(int(v)) > JS_SAFE_INT for v in bounds.values())

    if exact:
        # Exact integers cross the wire as decimal strings and never as JSON
        # numbers; a slider cannot represent them (app/contracts.py).
        projected: dict[str, Any] = {"logical_type": "INT_EXACT"}
        projected.update({key: str(value) for key, value in bounds.items()})
        projected["transport"] = "decimal_string"
        projected["seed_like"] = seed_like
        projected["slider_allowed"] = False
        return projected

    projected = {"logical_type": "INT", **bounds}
    if isinstance(opts.get("step"), int) and not isinstance(opts.get("step"), bool):
        projected["step"] = opts["step"]
    # Key order follows the expected projection: min, max, step, default.
    ordered = ("min", "max", "step", "default")
    return {
        "logical_type": "INT",
        **{k: projected[k] for k in ordered if k in projected},
    }


def _combo_choices(type_spec: Any, opts: dict[str, Any]) -> list[Any]:
    """Newer ``["COMBO", {"options": [...]}]`` first, else the legacy ``[[...]]`` list."""
    newer = opts.get("options") if type_spec == "COMBO" else None
    if isinstance(newer, list) and newer:
        return newer
    if isinstance(type_spec, list):
        return type_spec
    return newer if isinstance(newer, list) else []


def is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


def same_choice(a: Any, b: Any) -> bool:
    """Option equality that keeps ``True`` apart from ``1``; 1 and 1.0 are the same number."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return is_scalar(a) and is_scalar(b) and a == b


def _project_combo(
    name: str, choices: list[Any], opts: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Classify an enumerated choice list, withholding anything shared or path-like."""
    # Options keep the JSON type ComfyUI sent (numbers and booleans are not stringified).
    options = [c for c in choices if is_scalar(c)]
    values = [c for c in options if isinstance(c, str)]
    upload_hint = any(str(key).endswith("_upload") and opts[key] for key in opts)
    media_kind = _media_kind(values)

    if upload_hint or media_kind is not None:
        # Shared ComfyUI input-directory filenames: other profiles' uploads can
        # be in this list. Always withheld; the client picks from its own
        # uploads and the backend substitutes the real filename at submission.
        kind = media_kind or "image"
        projected: dict[str, Any] = {
            "logical_type": "OWNED_INPUT_REF",
            "media_kind": kind,
            "choices": [],
            "choices_withheld": True,
        }
        if kind == "image":
            projected["loader_adapter"] = "comfy_input_dir_filename"
        else:
            # No universal video/audio upload route is established, so this
            # picker stays unavailable rather than becoming a generic dropdown.
            projected["loader_adapter"] = None
            projected["editable"] = False
            projected["diagnostic"] = "adapter_required"
        return projected, values

    if any(_ABSOLUTE_PATH.match(value) for value in values):
        return (
            {
                "logical_type": "UNSUPPORTED",
                "choices": [],
                "choices_withheld": True,
                "editable": False,
                "diagnostic": "internal_path_withheld",
                "value_preserved": True,
            },
            values,
        )

    projected = {"logical_type": "COMBO"}
    if any(value.lower().endswith(MODEL_EXTENSIONS) for value in values):
        projected["choices_source"] = "server_model_catalog"
    projected["choices"] = options
    if is_scalar(opts.get("default")):
        projected["default"] = opts["default"]
    return projected, []


def _media_kind(values: list[str]) -> str | None:
    for value in values:
        lowered = value.lower()
        if lowered.endswith(IMAGE_EXTENSIONS):
            return "image"
        if lowered.endswith(VIDEO_EXTENSIONS):
            return "video"
        if lowered.endswith(AUDIO_EXTENSIONS):
            return "audio"
    return None


# --- hashing --------------------------------------------------------------


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evaluate_selections(
    nodes: dict[str, Any], selections: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Check saved control values against a catalog. **Nothing is rewritten.**

    Each selection is ``{node_id, class_type, input_name, value}`` as it was
    saved. The return value is a list of issue dicts (see
    contracts.SelectionIssue); the caller marks those controls and shows the
    missing value. A refresh that drops a node class or a model file therefore
    marks the affected controls uncertain and never erases them, and never
    silently substitutes the first remaining option.
    """
    issues: list[dict[str, Any]] = []
    for selection in selections:
        class_type = selection.get("class_type", "")
        input_name = selection.get("input_name")
        value = selection.get("value")
        common = {
            "node_id": selection.get("node_id"),
            "class_type": class_type,
            "input_name": input_name,
            "value": value,
        }

        node = nodes.get(class_type)
        if node is None:
            issues.append(
                {
                    **common,
                    "severity": "uncertain",
                    "code": "class_missing",
                    "message": (
                        f"Node class {class_type} is not in the current catalog. "
                        "Saved controls are kept; the workflow cannot be validated "
                        "until the node is installed again."
                    ),
                }
            )
            continue

        if input_name is None:
            continue
        spec = node.get("inputs", {}).get(input_name)
        if spec is None:
            if _DYNAMIC_NAME.match(input_name) and node.get("flexible_inputs"):
                # Dynamically named inputs are not enumerated by object_info;
                # absence is not evidence of an invalid input.
                continue
            issues.append(
                {
                    **common,
                    "severity": "uncertain",
                    "code": "input_missing",
                    "message": (
                        f"{class_type} no longer declares an input named {input_name}. "
                        "The saved value is preserved."
                    ),
                }
            )
            continue

        logical = spec.get("logical_type")
        if logical in ("COMBO",) and is_scalar(value):
            choices = spec.get("choices") or []
            if choices and not any(same_choice(value, c) for c in choices):
                issues.append(
                    {
                        **common,
                        "severity": "blocking",
                        "code": "choice_missing",
                        "message": (
                            f"{value!r} is no longer offered for {class_type}.{input_name}. "
                            "Choose a replacement; the saved value is shown until you do."
                        ),
                    }
                )
        elif logical in ("LINK", "UNSUPPORTED") and value is not None:
            issues.append(
                {
                    **common,
                    "severity": "uncertain",
                    "code": "type_changed",
                    "message": (
                        f"{class_type}.{input_name} is no longer an editable literal "
                        f"({logical}). The saved value is preserved for diagnostics."
                    ),
                }
            )
    return issues


def _structure_only(nodes: dict[str, Any]) -> Any:
    """``nodes`` with every choice list removed, for the schema hash.

    Installing or deleting a model changes choices but not the link/type
    contract, and must not read as a structural change.
    """

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != "choices"}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    return strip(nodes)
