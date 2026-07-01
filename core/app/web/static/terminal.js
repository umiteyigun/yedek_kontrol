(function () {
  "use strict";

  var mount = document.getElementById("terminal-mount");
  var statusEl = document.getElementById("term-status");
  var errorEl = document.getElementById("term-error");
  var reconnectBtn = document.getElementById("term-reconnect");

  var TERM_THEME = {
    background: "#0a0e14",
    foreground: "#e6edf3",
    cursor: "#2ee8c0",
    cursorAccent: "#0a0e14",
    selectionBackground: "rgba(46, 232, 192, 0.28)",
    selectionForeground: "#ffffff",
    black: "#1a2030",
    red: "#ff7b7b",
    green: "#2ee8c0",
    yellow: "#f0c674",
    blue: "#6ec8ff",
    magenta: "#d2a8ff",
    cyan: "#56d4dd",
    white: "#c8d3e0",
    brightBlack: "#5c6778",
    brightRed: "#ff9a9a",
    brightGreen: "#5dffc8",
    brightYellow: "#ffe08a",
    brightBlue: "#91caff",
    brightMagenta: "#e0b0ff",
    brightCyan: "#7ee8f0",
    brightWhite: "#ffffff",
  };

  function assetBase() {
    if (typeof window.yedekAssetBase === "function") {
      return window.yedekAssetBase();
    }
    return window.__YEDEK_BASE__ || "";
  }

  function setStatus(kind, text) {
    if (!statusEl) return;
    statusEl.className = "term-badge term-badge--" + kind;
    statusEl.textContent = text;
  }

  function showError(msg) {
    if (errorEl) {
      errorEl.hidden = false;
      errorEl.textContent = msg;
    }
    if (reconnectBtn) reconnectBtn.hidden = false;
    setStatus("error", "Baglanti kapali");
  }

  function clearError() {
    if (errorEl) {
      errorEl.hidden = true;
      errorEl.textContent = "";
    }
    if (reconnectBtn) reconnectBtn.hidden = true;
  }

  function loadScript(path) {
    return new Promise(function (resolve, reject) {
      var src = assetBase() + path;
      var existing = document.querySelector('script[data-yedek-src="' + src + '"]');
      if (existing) {
        existing.addEventListener("load", function () { resolve(); }, { once: true });
        existing.addEventListener("error", function () { reject(new Error(path)); }, { once: true });
        return;
      }
      var el = document.createElement("script");
      el.src = src;
      el.async = false;
      el.setAttribute("data-yedek-src", src);
      el.onload = function () { resolve(); };
      el.onerror = function () { reject(new Error(path)); };
      document.head.appendChild(el);
    });
  }

  function ensureVendorLibs() {
    var chain = Promise.resolve();
    if (typeof Terminal === "undefined") {
      chain = chain.then(function () {
        return loadScript("/static/vendor/xterm.min.js?v=2");
      });
    }
    if (typeof FitAddon === "undefined") {
      chain = chain.then(function () {
        return loadScript("/static/vendor/addon-fit.min.js?v=2");
      });
    }
    return chain;
  }

  function startTerminal() {
    var FitCtor = null;
    if (typeof FitAddon !== "undefined") {
      FitCtor = typeof FitAddon === "function" ? FitAddon : FitAddon.FitAddon;
    }

    var term = new Terminal({
      cursorBlink: true,
      cursorStyle: "bar",
      fontSize: 14,
      lineHeight: 1.15,
      fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
      theme: TERM_THEME,
      scrollback: 5000,
      allowTransparency: false,
      drawBoldTextInBrightColors: true,
      minimumContrastRatio: 1,
      convertEol: false,
    });

    var fitAddon = FitCtor ? new FitCtor() : null;
    if (fitAddon) term.loadAddon(fitAddon);

    term.open(mount);
    if (fitAddon) {
      try {
        fitAddon.fit();
      } catch (e) {}
    }

    var ws = null;
    var closed = false;
    var connectTimer = null;
    var gotData = false;

    function wsUrl() {
      var proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      return proto + "//" + window.location.host + assetBase() + "/ws/terminal";
    }

    function sendResize() {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(
        JSON.stringify({
          type: "resize",
          cols: term.cols,
          rows: term.rows,
        })
      );
    }

    function connect() {
      closed = false;
      gotData = false;
      clearError();
      setStatus("idle", "Baglaniyor...");
      if (connectTimer) {
        clearTimeout(connectTimer);
      }

      ws = new WebSocket(wsUrl());
      ws.binaryType = "arraybuffer";

      connectTimer = setTimeout(function () {
        if (!gotData && ws && ws.readyState !== WebSocket.OPEN) {
          showError("Terminal baglantisi zaman asimina ugradi");
          try {
            ws.close();
          } catch (e) {}
        }
      }, 15000);

      ws.onopen = function () {
        setStatus("idle", "Oturum aciliyor...");
        if (fitAddon) {
          try {
            fitAddon.fit();
          } catch (e) {}
        }
        sendResize();
        term.focus();
      };

      ws.onmessage = function (ev) {
        if (typeof ev.data === "string") return;
        if (!gotData) {
          gotData = true;
          if (connectTimer) {
            clearTimeout(connectTimer);
            connectTimer = null;
          }
          setStatus("live", "Canli");
        }
        term.write(new Uint8Array(ev.data));
      };

      ws.onclose = function (ev) {
        if (connectTimer) {
          clearTimeout(connectTimer);
          connectTimer = null;
        }
        if (closed) return;
        var reason = ev.reason || "Baglanti sonlandi";
        if (ev.code === 4403) reason = "Yetkisiz erisim — tam yetki gerekli";
        if (ev.code === 4003) {
          reason = ev.reason || "Terminal oturum limiti doldu";
        }
        if (ev.code === 4000) reason = "Maksimum oturum suresi doldu";
        if (ev.code === 4001) reason = "Hareketsizlik nedeniyle kapatildi";
        if (ev.code === 1000 && !ev.reason) reason = "Baglanti kapandi";
        showError(reason + " (kod: " + ev.code + ")");
      };

      ws.onerror = function () {
        showError("WebSocket baglantisi kurulamadi");
      };
    }

    term.onData(function (data) {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(new TextEncoder().encode(data));
    });

    window.addEventListener("resize", function () {
      if (fitAddon) {
        try {
          fitAddon.fit();
        } catch (e) {}
      }
      sendResize();
    });

    if (reconnectBtn) {
      reconnectBtn.addEventListener("click", function () {
        closed = true;
        if (ws) {
          try {
            ws.close();
          } catch (e) {}
        }
        term.clear();
        connect();
      });
    }

    window.addEventListener("beforeunload", function () {
      closed = true;
      if (ws) ws.close();
    });

    connect();
  }

  if (!mount) {
    showError("Terminal alani bulunamadi");
    return;
  }

  setStatus("idle", "Kutuphane yukleniyor...");
  ensureVendorLibs()
    .then(function () {
      if (typeof Terminal === "undefined") {
        throw new Error("Terminal globali yok");
      }
      startTerminal();
    })
    .catch(function () {
      showError("Terminal kutuphanesi yuklenemedi. Sayfayi yenileyin veya ag baglantinizi kontrol edin.");
    });
})();
