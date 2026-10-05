"""Bind loader-typed controls to each profile's private uploads.

``controls.py`` already classifies which literals are owned-input references
and, for those it knows how to bind, names the adapter contract in
``ControlDescriptor.inference_reason`` as ``"loader_adapter:<name>"`` (see
``_literal_control``'s ``OWNED_INPUT_REF`` branch). This module is the other
half: given such a control plus a caller's own uploaded asset id, validate the
binding and return the literal string to write into the submission graph.

ComfyUI's /upload/image route accepts arbitrary bytes. The returned subfolder
and filename are the exact literal written into the loader input.

GEN-001 is expected to call ``bind_upload`` once per file-typed edit --
resolving the ``ControlDescriptor`` from the workflow's ``ControlSchema`` and
the raw edit value as an upload id -- before writing a value into the
submission graph that ``mapping/submission.py`` builds. The returned string is
exactly what belongs at ``node["inputs"][control.input_name]``.
"""

from __future__ import annotations

from ..comfy_client import ComfyUnavailable
from ..contracts import ControlDescriptor, ErrorDetail
from ..media.service import LocatedMedia, MediaService
from ..storage.db import in_thread
from ..uploads.service import UploadError, UploadService

#: Loader adapter names this build actually knows how to stage and bind.
#: Anything else -- including ``None`` (normalize.py's video/audio case) --
#: is an unavailable adapter, reported rather than silently accepted.
SUPPORTED_LOADER_ADAPTERS = frozenset(
    {
        "comfy_input_filename_image",
        "comfy_input_filename_video",
        "comfy_input_filename_audio",
    }
)

_LOADER_ADAPTER_PREFIX = "loader_adapter:"

#: Adapter name -> the upload kind (uploads/service.py classify()) it expects.
_ADAPTER_MEDIA_KIND = {
    "comfy_input_filename_image": "image",
    "comfy_input_filename_video": "video",
    "comfy_input_filename_audio": "audio",
}


class InputAdapterError(ValueError):
    """A control cannot be bound to an upload. ``detail`` is client-safe."""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.detail = ErrorDetail(field=field, code=code, message=message)


async def bind_upload(
    control: ControlDescriptor, uploads: UploadService, owner_id: str, upload_id: str
) -> str:
    """Validate ``control`` accepts an uploaded input and return the graph literal.

    Raises ``InputAdapterError`` (never crashes) when:

    * ``control`` is not an owned-input-reference control at all;
    * it is one, but this build has no adapter for its loader contract yet
      (today: anything other than the image/mask ``LoadImage`` filename
      contract -- most notably video);
    * ``upload_id`` does not resolve to one of *this* owner's own uploads --
      the same message whether it belongs to another profile or never
      existed, so neither can be distinguished by probing;
    * the upload's kind does not match what the control expects;
    * ComfyUI rejects the upload or it went missing since upload.
    """
    if control.logical_type != "file":
        raise InputAdapterError(
            "not_a_loader_control",
            f"{control.label} does not accept an uploaded input.",
            control.binding_id,
        )

    if control.component != "file":
        # controls.py already produced a warning for this (adapter_required);
        # this is the same fact, surfaced as a hard error for a bind attempt.
        kind = _control_media_kind(control) or "file"
        raise InputAdapterError(
            "unsupported_loader_adapter",
            f"{control.label} needs a {kind} input from the shared ComfyUI input "
            "directory, and no upload adapter is available for it yet. Its "
            "imported value is preserved and left as imported.",
            control.binding_id,
        )

    adapter = _adapter_name(control)
    if adapter not in SUPPORTED_LOADER_ADAPTERS:
        raise InputAdapterError(
            "unsupported_loader_adapter",
            f"{control.label} uses the {adapter!r} loader contract, which this "
            "build does not support binding an upload to.",
            control.binding_id,
        )

    upload = uploads.get(owner_id, upload_id)
    if upload is None:
        raise InputAdapterError(
            "upload_missing",
            f"{control.label}: that upload was not found; choose a replacement.",
            control.binding_id,
        )

    expected_kind = _ADAPTER_MEDIA_KIND.get(adapter)
    actual_kind = uploads.classify(upload)
    if (
        expected_kind is not None
        and actual_kind != expected_kind
        and not (expected_kind == "audio" and actual_kind == "video")
    ):
        raise InputAdapterError(
            "upload_kind_mismatch",
            f"{control.label} needs a {expected_kind} file; the selected upload is {actual_kind}.",
            control.binding_id,
        )

    try:
        return await uploads.ensure_staged(
            owner_id, upload_id, expected_kind or "image"
        )
    except (UploadError, ComfyUnavailable) as exc:
        message = str(exc) if isinstance(exc, UploadError) else exc.message
        raise InputAdapterError(
            exc.code, f"{control.label}: {message}", control.binding_id
        ) from exc


async def bind_media(
    control: ControlDescriptor,
    uploads: UploadService,
    media: MediaService,
    owner_id: str,
    media_id: str,
) -> tuple[str, LocatedMedia]:
    if (
        control.logical_type != "file"
        or control.component != "file"
        or _adapter_name(control) not in SUPPORTED_LOADER_ADAPTERS
    ):
        raise InputAdapterError(
            "unsupported_loader_adapter",
            f"{control.label} cannot accept gallery media.",
            control.binding_id,
        )
    located = await in_thread(media.locate, owner_id, media_id)
    if located is None:
        raise InputAdapterError(
            "media_missing",
            f"{control.label}: that media item was not found; choose a replacement.",
            control.binding_id,
        )
    expected = _ADAPTER_MEDIA_KIND[_adapter_name(control)]
    if located.row["media_kind"] != expected and not (
        expected == "audio" and located.row["media_kind"] == "video"
    ):
        raise InputAdapterError(
            "media_kind_mismatch",
            f"{control.label} needs a {expected} file; the selected media is {located.row['media_kind']}.",
            control.binding_id,
        )
    try:
        staged = await uploads.ensure_staged_path(
            owner_id, located.path, expected, located.row["file_version"]
        )
    except (UploadError, ComfyUnavailable) as exc:
        message = str(exc) if isinstance(exc, UploadError) else exc.message
        raise InputAdapterError(
            getattr(exc, "code", "upstream_unavailable"),
            f"{control.label}: {message}",
            control.binding_id,
        ) from exc
    return staged, located


def _adapter_name(control: ControlDescriptor) -> str | None:
    reason = (control.inference_reason or "").split(";", 1)[0]
    if reason.startswith(_LOADER_ADAPTER_PREFIX):
        return reason[len(_LOADER_ADAPTER_PREFIX) :]
    return None


def _control_media_kind(control: ControlDescriptor) -> str | None:
    meta = control.raw_metadata or {}
    kind = meta.get("media_kind")
    return kind if isinstance(kind, str) else None
