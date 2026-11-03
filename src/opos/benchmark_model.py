# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared model loader for benchmark harnesses."""
import json
import os


def _load_model(path):
    if not path:
        raise FileNotFoundError(f"model unavailable: no path provided: {path!r}")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"model unavailable: missing file {path!r}")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"model invalid: corrupt JSON {path!r}: {e}") from e
    except OSError as e:
        raise ValueError(f"model invalid: unreadable {path!r}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"model invalid: expected JSON object {path!r}")
    if "version" not in data:
        raise ValueError(f"model invalid: missing version {path!r}")
    return data


def resolve_model_path(model_path=None, *args, **kwargs):
    path = model_path
    if path is None and args:
        first = args[0]
        if isinstance(first, str):
            path = first
    if path is None:
        path = kwargs.get("model_path") or kwargs.get("model") or kwargs.get("path") or os.environ.get("OPOS_MODEL_PATH") or os.environ.get("OPOS_MODEL")
    return path
