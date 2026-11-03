# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import contextlib
import json
import os
import tempfile
from pathlib import Path
from opos.setup.config import config_dir

# Navigation and resume order. The lock PIN step follows Wi-Fi (design decision 6); Helper stays last.
STEPS = ('theme','layout','text_size','device_type','language','wifi','pin','helper')
VALUES = {
    'theme': ('auto','light','dark'),
    'text_size': ('small','normal','large','xlarge'),
    'device_type': ('auto','slab','foldable','slideout','tablet'),
    'language': ('en',),
    'helper': ('on','off'),
}

def defaults():
    return {
        'theme': 'auto',
        'layout': {'favourites': ['phone','messages','helper','settings'], 'sizes': {}},
        'text_size': 'normal',
        'device_type': 'auto',
        'language': 'en',
        'wifi': 'later',
        'pin': 'later',
        'helper': 'on',
    }

def prefs_path(env=None):
    return config_dir(env) / 'prefs.json'

def load(path=None):
    if path is None:
        path = prefs_path()
    else:
        path = Path(path)
    empty = {'schema':1,'answers':{},'skipped':[],'applied':False}
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return empty
    try:
        data = json.loads(text)
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    answers = data.get('answers')
    skipped = data.get('skipped')
    applied = data.get('applied')
    if not isinstance(answers, dict) or not isinstance(skipped, list) or not isinstance(applied, bool):
        return empty
    return {'schema':1,'answers':answers,'skipped':list(skipped),'applied':applied}

def validate_answer(step, value):
    if step not in STEPS:
        raise ValueError(f'unknown step {step}')
    if step == 'layout':
        if not isinstance(value, dict):
            raise ValueError('invalid layout')
        if 'favourites' not in value:
            raise ValueError('layout missing favourites')
        favourites = value['favourites']
        if not isinstance(favourites, list) or len(favourites) > 8:
            raise ValueError('invalid favourites')
        allowed_ids = {'phone','messages','helper','settings','allapps'}
        for item in favourites:
            if not isinstance(item, str) or item not in allowed_ids:
                raise ValueError('invalid favourite id')
        sizes = value.get('sizes')
        if sizes is not None:
            if not isinstance(sizes, dict):
                raise ValueError('invalid sizes')
            for k, v in sizes.items():
                if not isinstance(k, str) or k not in allowed_ids:
                    raise ValueError('invalid size key')
                if v not in ('small','medium','wide'):
                    raise ValueError('invalid size value')
        extra = set(value.keys()) - {'favourites','sizes'}
        if extra:
            raise ValueError('layout has extra keys')
        return
    if step == 'wifi':
        if value in ('later','skip'):
            return
        if isinstance(value, dict):
            if set(value.keys()) != {'ssid'}:
                raise ValueError('wifi dict has invalid keys')
            ssid = value.get('ssid')
            if not isinstance(ssid, str) or not (1 <= len(ssid) <= 32):
                raise ValueError('invalid ssid')
            return
        raise ValueError('invalid wifi')
    if step == 'pin':
        # Record-only (JRY34): the lock screen has no PIN check yet, so only 'later' or 'skip'
        # may be stored. Digits are never accepted here until a lock backend can verify them.
        if value in ('later', 'skip'):
            return
        raise ValueError('invalid pin')
    if step in VALUES:
        if value not in VALUES[step]:
            raise ValueError(f'invalid value for {step}')
        return
    raise ValueError(f'unknown step {step}')

def save(state, path=None):
    if path is None:
        path = prefs_path()
    else:
        path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, sort_keys=True)
            f.write('\n')
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup of the temp file; the original error is what matters.
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    # Best-effort: some filesystems (for example FAT media) do not honour modes.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)

def answer(step, value, path=None):
    if step not in STEPS:
        raise ValueError(f'unknown step {step}')
    validate_answer(step, value)
    state = load(path)
    state.setdefault('answers', {})
    state.setdefault('skipped', [])
    state['answers'][step] = value
    if step in state['skipped']:
        state['skipped'].remove(step)
    save(state, path)
    return state

def skip(step, path=None):
    if step not in STEPS:
        raise ValueError(f'unknown step {step}')
    state = load(path)
    answers = state.setdefault('answers', {})
    skipped = state.setdefault('skipped', [])
    answers.pop(step, None)
    if step not in skipped:
        skipped.append(step)
    save(state, path)

def skip_all(path=None):
    state = load(path)
    answers = state.get('answers', {})
    skipped_set = set(state.get('skipped', []))
    for step in STEPS:
        if step not in answers and step not in skipped_set:
            skipped_set.add(step)
    state['skipped'] = sorted(skipped_set)
    save(state, path)

def resume_step(state=None):
    if state is None:
        state = load()
    answers = state.get('answers', {})
    skipped = set(state.get('skipped', []))
    for step in STEPS:
        if step not in answers and step not in skipped:
            return step
    return None

def effective(state=None):
    if state is None:
        state = load()
    base = defaults()
    merged = dict(base)
    merged.update(state.get('answers', {}))
    return merged

def finish(path=None):
    if path is None:
        path = prefs_path()
    else:
        path = Path(path)
    state = load(path)
    state['applied'] = True
    save(state, path)
    return effective(state)
