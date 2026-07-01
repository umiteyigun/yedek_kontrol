(function () {
  "use strict";

  function readMetaPrefix() {
    var el = document.querySelector('meta[name="yedek-proxy-prefix"]');
    if (!el) return "";
    return el.getAttribute("content") || "";
  }

  function prefixFromPath() {
    var m = window.location.pathname.match(/^(\/o\/[^/]+\/n\/[^/]+)/);
    return m ? m[1] : "";
  }

  window.yedekAssetBase = function () {
    return readMetaPrefix() || prefixFromPath() || "";
  };
})();
