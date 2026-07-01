(function () {
  "use strict";

  var PROXY_PREFIX_RE = /^\/o\/[a-z0-9][a-z0-9_-]*\/n\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  function sanitizePrefix(value) {
    var text = String(value || "").trim();
    if (!text || !PROXY_PREFIX_RE.test(text)) {
      return "";
    }
    return text;
  }

  function readMetaPrefix() {
    var el = document.querySelector('meta[name="yedek-proxy-prefix"]');
    if (!el) return "";
    return sanitizePrefix(el.getAttribute("content") || "");
  }

  function prefixFromPath() {
    var m = window.location.pathname.match(/^(\/o\/[^/]+\/n\/[^/]+)/);
    return m ? sanitizePrefix(m[1]) : "";
  }

  window.yedekAssetBase = function () {
    return readMetaPrefix() || prefixFromPath() || "";
  };
})();
