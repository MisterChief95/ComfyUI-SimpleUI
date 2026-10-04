"""Read embedded provenance without repairing graphs or decoding pixel data."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

MAX_METADATA_BYTES = 2 * 1024 * 1024
VIDEO_SUFFIXES = {".avi", ".mkv", ".mov", ".mp4", ".webm"}


def parse_metadata(tags: dict[str, Any]) -> dict[str, Any]:
    raw, values, diagnostics = {}, {}, []
    for key in ("prompt", "workflow", "parameters"):
        value = tags.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            diagnostics.append(f"The {key} metadata is not text.")
        elif len(value.encode("utf-8")) > MAX_METADATA_BYTES:
            diagnostics.append(f"The {key} metadata exceeds the 2 MiB limit.")
        else:
            raw[key] = value
    source = "unknown"
    if "prompt" in raw or "workflow" in raw:
        source = "comfyui"
        for key in ("prompt", "workflow"):
            if key not in raw:
                continue
            try:
                parsed = json.loads(raw[key])
                if not isinstance(parsed, dict):
                    raise TypeError("Expected an object")
            except (ValueError, TypeError, RecursionError):
                diagnostics.append(f"The embedded {key} is malformed JSON.")
                continue
            if key == "prompt":
                for node_id, node in parsed.items():
                    if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
                        for name, value in node["inputs"].items():
                            if type(value) in (str, int, float, bool):
                                # Public display and search retain every integer digit.
                                values[f"{node_id}:{name}"] = (
                                    json.dumps(value)
                                    if type(value) is bool
                                    else str(value)
                                )
        if "workflow" in raw and "prompt" not in raw:
            diagnostics.append(
                "Only a UI-format workflow is embedded. Export API JSON from ComfyUI to import it."
            )
    elif "parameters" in raw:
        source = "a1111"
        text = raw["parameters"]
        match = re.search(r"(?:^|\n)Steps:\s*", text)
        if match:
            prompt = text[: match.start()].strip()
            options = text[match.start() :].strip()
            fields = re.split(r",\s*(?=[\w /.-]+:\s*)", options)
            for field in fields:
                key, separator, value = field.partition(":")
                if separator:
                    values[key.strip().lower().replace(" ", "_")] = value.strip()
        else:
            prompt = text.strip()
            diagnostics.append(
                "A1111 parameters have no Steps/settings line; only prompt text was recovered."
            )
        positive, separator, negative = prompt.partition("\nNegative prompt:")
        values["prompt"] = positive.strip()
        if separator:
            values["negative_prompt"] = negative.strip()
    if not raw and not diagnostics:
        diagnostics.append(
            "No embedded generation metadata found. Use a ComfyUI API JSON export instead."
        )
    return {"source": source, "raw": raw, "values": values, "diagnostics": diagnostics}


def read_metadata(path: Path) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    try:
        if path.suffix.lower() in VIDEO_SUFFIXES:
            ffprobe = shutil.which("ffprobe")
            if ffprobe is None:
                result = parse_metadata({})
                result["diagnostics"] = [
                    "Video metadata requires ffprobe on the server; use API JSON instead."
                ]
                return result
            probe = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format_tags=prompt,workflow,parameters,comment:stream_tags=prompt,workflow,parameters,comment",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                check=True,
                timeout=15,
            )
            if len(probe.stdout) > MAX_METADATA_BYTES:
                raise ValueError("Video metadata exceeds the 2 MiB limit.")
            container = json.loads(probe.stdout)
            for section in [container.get("format", {}), *container.get("streams", [])]:
                embedded = {
                    key.lower(): value for key, value in section.get("tags", {}).items()
                }
                tags.update(embedded)
                comment = embedded.get("comment")
                if isinstance(comment, str):
                    try:
                        parsed = json.loads(comment)
                        if isinstance(parsed, dict):
                            tags.update(
                                {
                                    key: value
                                    if isinstance(value, str)
                                    else json.dumps(value)
                                    for key, value in parsed.items()
                                    if key in {"prompt", "workflow", "parameters"}
                                }
                            )
                    except ValueError:
                        pass
        else:
            with Image.open(path) as image:
                tags.update(image.info)
                # WebP/JPEG A1111 usually stores the parameters as EXIF UserComment.
                comment = image.getexif().get(37510)
                if comment is None:
                    comment = image.getexif().get_ifd(34665).get(37510)
                if isinstance(comment, bytes):
                    prefix, body = comment[:8], comment[8:]
                    if prefix == b"UNICODE\0":
                        comment = body.decode(
                            "utf-16"
                            if body.startswith((b"\xff\xfe", b"\xfe\xff"))
                            else "utf-16-be"
                        )
                    elif prefix == b"ASCII\0\0\0":
                        comment = body.decode("utf-8")
                    else:
                        comment = comment.decode("utf-8")
                if isinstance(comment, str):
                    # ComfyUI WebP writes prompt/workflow prefixed EXIF strings.
                    if comment.startswith("prompt:"):
                        tags["prompt"] = comment[7:]
                    else:
                        tags.setdefault("parameters", comment)
                # ComfyUI uses camera Model (0x0110) for prompt and Make
                # (0x010f, descending for extra keys) for workflow metadata.
                for value in image.getexif().values():
                    if isinstance(value, str):
                        key, separator, text = value.partition(":")
                        if separator and key.lower() in {"prompt", "workflow"}:
                            tags[key.lower()] = text
    except (
        OSError,
        SyntaxError,
        ValueError,
        subprocess.SubprocessError,
        Image.DecompressionBombError,
    ) as exc:
        result = parse_metadata({})
        # Do not put host paths or external tool stderr in client diagnostics.
        result["diagnostics"] = [
            "Embedded metadata could not be read; use a ComfyUI API JSON export."
        ]
        if isinstance(exc, ValueError) and "2 MiB" in str(exc):
            result["diagnostics"] = [str(exc)]
        return result
    return parse_metadata(tags)


def embedded_api_prompt(provenance: dict[str, Any]) -> bytes:
    """Return the original JSON text; the existing graph importer validates it."""
    prompt = provenance["raw"].get("prompt")
    if not prompt:
        raise ValueError(
            " ".join(provenance["diagnostics"])
            or "No embedded API prompt found. Use ComfyUI API JSON."
        )
    return prompt.encode("utf-8")
