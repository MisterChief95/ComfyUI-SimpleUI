# Compatibility fixtures

Produced by COMPAT-001. `MANIFEST.json` is the index: every file, its provenance, and
the contract cases it covers. `docs/COMPATIBILITY.md` is the narrative record.

**Every fixture here is synthetic.** No ComfyUI installation was reachable when this set
was built, so nothing here is observed behavior. Synthetic fixtures are enough to pin the
import, mapping, transport, and privacy contracts. They cannot support a release claim
about image or video generation.

## Layout

| Directory | Contents |
| --- | --- |
| `catalog/` | `object_info` snapshots (current and older shape), the expected sanitized client projection, and the capability record |
| `graphs/` | API JSON graphs that must import successfully |
| `graphs/rejected/` | Graphs that must be refused, with the reason in `expectations/rejections.json` |
| `events/` | WebSocket execution event streams, one JSON object per line |
| `outputs/` | `history` responses carrying output descriptors, plus submission error shapes |
| `expectations/` | What the implementation must do with the fixtures above |

## Self-check

```cmd
python -m unittest discover -s tests/fixtures -v
```

This checks the fixture set itself, not application code: the manifest matches the files
on disk, required coverage is claimed, accepted graphs parse under a strict parser while
the rejection cases fail it, the large seeds survive a Python round trip unchanged, the
sanitized projection withholds the shared input filenames present in the raw catalog, and
no personal path, prompt, or credential appears anywhere in the set.

## Conventions

- Prompt text is the word "placeholder" plus generic words. Never paste real prompt text here.
- Model and input filenames are `placeholder-*`. Never commit a real filename from a machine.
- Prompt IDs are fixed UUIDs so fixtures stay diffable.
- Keys beginning with an underscore are fixture commentary, not part of any ComfyUI contract.
  Graph files are the exception: they contain no commentary keys other than ComfyUI's own
  `_meta`, so each one is importable as-is.

## Adding live fixtures later

When a ComfyUI installation is configured, add captured files beside these rather than
replacing them, set `provenance` to `live` in the manifest, fill in the installation block
of the capability record, and flip `live_gate`. The self-check enforces that no file claims
live provenance while the gate is still marked blocked. Sanitize every capture: real prompt
text, real filenames, host paths, and any credential must not be committed.
