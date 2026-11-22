/* Forest Ledger national-economics-v2.6 - static publication guard.
   This copy is a static demonstration. It must never reach a live runner, even
   if someone serves it behind an API-capable origin. All /api requests fail
   locally so the app selects its "Static mode - no backend" path. */

(function () {
  'use strict';

  window.CLEWS_PUBLICATION = {
    static_only: true,
    release: 'national-economics-v2.6',
    tag: 'ui-national-economics-v2.6',
    commit: '32dd279d2a9161e626835a3aabcea848258f47b3'
  };

  var nativeFetch = window.fetch ? window.fetch.bind(window) : null;
  if (!nativeFetch) return;

  function isLiveApi(input) {
    var raw = typeof input === 'string' ? input : (input && input.url) || '';
    if (!raw) return false;
    try {
      var url = new URL(raw, window.location.href);
      return url.origin === window.location.origin && /^\/api(?:\/|$)/.test(url.pathname);
    } catch (error) {
      return false;
    }
  }

  window.fetch = function (input, init) {
    if (isLiveApi(input)) {
      return Promise.reject(new TypeError('static publication: live API disabled'));
    }
    return nativeFetch(input, init);
  };
}());
