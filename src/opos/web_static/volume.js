// SPDX-License-Identifier: GPL-3.0-or-later
// Reuse the existing state poll and volume meter. This never changes sound.
(() => {
  const slider = document.getElementById('vol');
  const output = document.getElementById('vol-val');
  const label = slider.closest('label');
  slider.disabled = true;
  label.firstChild.textContent = 'Volume keys (simulated; sound is not changed)';
  let known = false;
  const paint = barPaint;
  barPaint = function() {
    paint();
    if (!known) {
      output.textContent = 'Unavailable';
      if (barS.value === 'v') {
        meterEl.dataset.caption = 'Volume unavailable';
        document.getElementById('meter-fill').style.width = '0%';
        meterEl.setAttribute('aria-label', 'Simulated volume unavailable, pull down for details');
      }
    }
  };
  const previous = render;
  render = async function(state) {
    await previous(state);
    const report = state && !state.error && state.volume_keys;
    known = !!(report && report.simulated === true && Number.isInteger(report.percent)
        && report.percent >= 0 && report.percent <= 100);
    if (known) {
      slider.value = report.percent;
    }
    barPaint();
  };
  barPaint();
})();
