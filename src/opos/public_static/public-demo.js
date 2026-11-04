// SPDX-License-Identifier: GPL-3.0-or-later
(function () {
  'use strict';
  const notice = 'Public demo: answers come from the rules; Muse Glimmer runs only on your own computer.';
  const nativeFetch = window.fetch.bind(window);
  // Only this wrapper learns rotated tokens. Never resend the expired action.
  window.fetch = async function (input, options) {
    const response = await nativeFetch(input, options);
    if (typeof input === 'string' && (input === '/api' || input === '/api/state')) {
      const token = response.headers.get('X-OPOS-Token');
      if (token) document.querySelector('meta[name="opos-token"]').content = token;
      if (response.headers.get('X-OPOS-Session-Reset') === '1') {
        document.getElementById('public-session').textContent = 'A fresh demo phone is ready. Your previous session ended; try that action again.';
        const state = await response.clone().json();
        if (typeof render === 'function') await render(state);
      }
    }
    return response;
  };
  function ready() {
    // The standalone phone focuses Unlock at startup. Keep the public masthead
    // in view, while preserving an explicit #try-phone navigation.
    if (window.location && !window.location.hash && document.activeElement &&
        document.activeElement.id === 'unlock') {
      document.activeElement.blur();
      window.scrollTo({top: 0, behavior: 'instant'});
    }
    const credit = document.getElementById('glimmer-credit');
    const where = document.getElementById('helper-where');
    const helperNotice = 'Demo phone. These answers come from the battery rules. Muse Glimmer runs only on your own computer.';
    function honestCredit() {
      if (credit.textContent !== notice) credit.textContent = notice;
      if (where && where.textContent !== helperNotice) where.textContent = helperNotice;
    }
    honestCredit();
    const observer = new MutationObserver(honestCredit);
    observer.observe(credit, {childList: true});
    if (where) observer.observe(where, {childList: true});
    const web = document.getElementById('webq');
    web.checked = false;
    web.disabled = true;
    document.getElementById('helperrestart').disabled = true;
  }
  document.addEventListener('DOMContentLoaded', ready, {once: true});
}());
