# SPDX-License-Identifier: GPL-3.0-or-later
SCRIPT = r"""
try { (function () {
  const root = document.getElementById('jaryo-setup');
  if (root) {
    const panels = Array.from(root.querySelectorAll('[data-jstep]'));
    const names = panels.map(e => e.dataset.jstep);
    const summary = document.getElementById('jaryo-summary');
    const summaryBack = document.getElementById('jaryo-summary-back');
    const end = document.getElementById('jaryo-disclaimer-end');
    const error = document.getElementById('jaryo-error');
    const retry = document.getElementById('jaryo-retry');
    // JRY32: the leading "Choose your edition" panel is not a data-jstep. An edition is the existing
    // device_type answer, so the seven steps, resume and skip-all below are unchanged.
    const edition = document.getElementById('jaryo-edition');
    const useEdition = document.getElementById('jaryo-use-edition');
    const customise = document.getElementById('jaryo-customise');
    let answers = {}, skipped = [], busy = false, prepared = false, editionBase;
    function showAlert(message) {
      error.textContent = message || '';
      error.hidden = !message;
      retry.hidden = !message;
    }
    async function request(url, body) {
      const headers = {'X-OPOS-Token': document.querySelector('meta[name="opos-token"]').content};
      const options = {headers};
      if (body !== undefined) {
        headers['Content-Type'] = 'application/json';
        options.method = 'POST'; options.body = JSON.stringify(body);
      }
      const response = await fetch(url, options);
      if (!response.ok) throw new Error('request');
      return response.json();
    }
    function hideAll() {
      panels.forEach(p => { p.hidden = true; });
      if (edition) edition.hidden = true;
      summary.hidden = true; summaryBack.hidden = true; end.hidden = true;
    }
    function show(i) {
      hideAll();
      panels[i].hidden = false;
      window.jaryoSetup.stepIndex = i;
      const heading = panels[i].querySelector('h3, legend');
      heading.focus();
    }
    function label(name, value) {
      const input = panels[names.indexOf(name)].querySelectorAll('input');
      const match = Array.from(input).find(e => e.value === value);
      return match ? match.parentElement.textContent.trim() : value === 'allapps' ? 'All apps' : 'Saved choice';
    }
    function complete() {
      hideAll();
      summary.textContent = names.map((name, i) => {
        const value = answers[name];
        let text = skipped.includes(name) ? 'Skipped (recommended settings)' : 'Recommended settings';
        if (!skipped.includes(name) && value !== undefined) {
          if (name === 'layout') text = (value.favourites || []).map(v => label(name, v)).join(', ') || 'No favourites';
          else if (name === 'wifi') text = 'Set up later in Settings › Network';
          else if (name === 'pin') text = 'Set up later in Settings › Device';
          else text = label(name, value);
        }
        if (name === 'wifi' && skipped.includes(name)) text += '; Set up later in Settings › Network';
        if (name === 'pin' && skipped.includes(name)) text += '; Set up later in Settings › Device';
        return panels[i].querySelector('h3, legend').textContent + ': ' + text;
      }).join('\n');
      summary.hidden = false; summaryBack.hidden = false; end.hidden = false; summary.focus();
    }
    function editionInputs() { return edition ? Array.from(edition.querySelectorAll('input')) : []; }
    function pickedEdition() { return editionInputs().find(e => e.checked) || null; }
    // Same heuristic as the kiosk's Settings "Auto looks like" line (phone-b.js); the server exposes no form factor.
    function detectEdition() {
      try {
        const mq = q => window.matchMedia(q).matches;
        if (mq('(min-width:600px) and (min-aspect-ratio:3/4) and (max-aspect-ratio:4/3)')) return 'foldable';
        if (mq('(orientation:landscape) and (max-height:520px)')) return 'slideout';
      } catch (_) {}
      return 'slab';
    }
    function useLabel() {
      const picked = pickedEdition();
      if (useEdition) useEdition.textContent = 'Use ' + (picked ? picked.dataset.jname : 'edition');
    }
    function prepareEdition() {
      const detected = detectEdition(), saved = answers.device_type, inputs = editionInputs();
      const pick = inputs.some(e => e.value === saved) ? saved : detected;
      inputs.forEach(input => {
        input.checked = input.value === pick;
        const kicker = input.parentElement.querySelector('[data-jkicker]');
        if (kicker) kicker.textContent = input.value === detected ? 'Suggested' : kicker.dataset.jkicker;
      });
      useLabel();
      prepared = true; editionBase = saved;
    }
    function showEdition() {
      // Back from the steps: a device type saved at j4 since the panel was prepared is what Use must offer, so the
      // panel is prepared again from it. Otherwise an edition picked here but not yet used is kept.
      if (!prepared || answers.device_type !== editionBase) prepareEdition();
      hideAll();
      edition.hidden = false;
      window.jaryoSetup.stepIndex = 0;
      const heading = edition.querySelector('legend');
      if (heading) heading.focus();
    }
    async function read() {
      const data = await request('/setup/api/prefs');
      if (!data || !data.state || !data.state.answers || typeof data.state.answers !== 'object' ||
          Array.isArray(data.state.answers) || !Array.isArray(data.state.skipped) ||
          (data.resume != null && data.resume !== 'done' && !names.includes(data.resume))) throw new Error('state');
      const saved = data.state.answers;
      if (data.state.skipped.some(name => !names.includes(name)) ||
          Object.keys(saved).some(name => !names.includes(name))) throw new Error('state');
      for (const [name, value] of Object.entries(saved)) {
        const inputs = Array.from(panels[names.indexOf(name)].querySelectorAll('input'));
        if (name === 'layout') {
          if (!value || typeof value !== 'object' || !Array.isArray(value.favourites) ||
              value.favourites.some(v => v !== 'allapps' && !inputs.some(input => input.value === v))) throw new Error('state');
        } else if (name === 'wifi') {
          if (value !== 'later' && value !== 'skip' && !(value && typeof value.ssid === 'string')) throw new Error('state');
        } else if (name === 'pin') {
          // JRY34: record-only; a saved PIN value other than later/skip is a corrupt state, never shown.
          if (value !== 'later' && value !== 'skip') throw new Error('state');
        } else if (!inputs.some(input => input.value === value)) throw new Error('state');
      }
      answers = saved; skipped = data.state.skipped;
      panels.forEach(p => {
        const name = p.dataset.jstep, value = answers[name];
        p.querySelectorAll('input').forEach(input => {
          input.checked = name === 'layout' ? !!(value && (value.favourites || []).includes(input.value)) : input.value === value;
        });
      });
      return data.resume;
    }
    async function run(action) {
      if (busy) return;
      busy = true; showAlert('');
      try { await action(); }
      catch (_) { showAlert('Your choices could not be loaded or saved. Check that setup is still running, then try loading choices again.'); }
      finally { busy = false; }
    }
    async function start() {
      root.hidden = false;
      await run(async () => {
        const resume = await read();
        const device = typeof curDev === 'function' ? curDev() : {};
        const radios = device && Array.isArray(device.radios_present) ? device.radios_present : [];
        document.getElementById('jaryo-radio').textContent = 'Wi-Fi radio: ' + (radios.includes('wifi') ? 'present' : 'not detected');
        if (resume == null || resume === 'done') complete();
        // A fresh install leads with the edition; anything already answered or skipped resumes at its step.
        else if (edition && resume === names[0] && !Object.keys(answers).length && !skipped.length) { prepareEdition(); showEdition(); }
        else show(names.indexOf(resume));
      });
    }
    async function save(skip) {
      await run(async () => {
        const i = window.jaryoSetup.stepIndex, name = names[i];
        const panel = panels[i];
        const selected = Array.from(panel.querySelectorAll('input')).filter(e => e.checked);
        if (!skip && name !== 'wifi' && name !== 'layout' && name !== 'pin' && !selected.length) {
          showAlert('Choose an option above, or use Skip to keep the recommended setting.');
          const input = panel.querySelector('input'); if (input) input.focus();
          return;
        }
        // Wi-Fi and the lock PIN are record-only steps: Next, Skip and "Set up later" all record a skip.
        if (skip || name === 'wifi' || name === 'pin') await request('/setup/api/prefs/skip', {step: name});
        else {
          const value = name === 'layout' ? {favourites: selected.map(e => e.value)} : selected[0].value;
          await request('/setup/api/prefs', {step: name, value});
        }
        await read();
        if (i + 1 === names.length) complete(); else show(i + 1);
      });
    }
    root.querySelectorAll('[data-jnext]').forEach(b => { b.onclick = () => save(false); });
    root.querySelectorAll('[data-jskip], [data-jwifi], [data-jpin]').forEach(b => { b.onclick = () => save(true); });
    root.querySelectorAll('[data-jback]').forEach(b => {
      b.onclick = () => {
        if (busy) return;
        showAlert('');
        const i = window.jaryoSetup.stepIndex;
        if (i === 0 && edition) showEdition(); else show(Math.max(0, i - 1));
      };
    });
    document.getElementById('jaryo-skip-all').onclick = () => run(async () => {
      await request('/setup/api/prefs/skip-all', {}); await read(); complete();
    });
    // Use <edition>: save the edition as device_type, record the recommended settings for the rest, show the summary.
    if (useEdition) useEdition.onclick = () => run(async () => {
      const picked = pickedEdition();
      if (!picked) { showAlert('Choose an edition above, or tap Customise instead.'); return; }
      await request('/setup/api/prefs', {step: 'device_type', value: picked.value});
      await request('/setup/api/prefs/skip-all', {});
      await read(); complete();
    });
    if (customise) customise.onclick = () => { if (!busy) { showAlert(''); show(0); } };
    summaryBack.onclick = () => { if (!busy) { showAlert(''); show(names.length - 1); } };
    retry.onclick = start;
    root.addEventListener('change', e => {
      if (e.target.name === 'theme') root.dataset.theme = e.target.value;
      if (e.target.name === 'edition') useLabel();
    });
    window.jaryoSetup = {stepIndex: 0, start};
  }
})(); } catch (_) {}
"""
