(function () {
  "use strict";

  var boot = document.getElementById("ts-page-boot");
  var addBtn = document.getElementById("ts-add-df-btn");
  var modal = document.getElementById("ts-add-df-modal");
  var form = document.getElementById("ts-add-df-form");
  if (!boot || !addBtn || !modal || !form) return;

  var tablespace = boot.dataset.tablespace || "";
  var instanceId = boot.dataset.instance || "";
  var base = window.yedekAssetBase ? window.yedekAssetBase() : "";

  var pathInput = document.getElementById("ts-add-df-path");
  var sizeInput = document.getElementById("ts-add-df-size");
  var autoInput = document.getElementById("ts-add-df-auto");
  var nextInput = document.getElementById("ts-add-df-next");
  var maxInput = document.getElementById("ts-add-df-max");
  var hintEl = document.getElementById("ts-add-df-hint");
  var statusEl = document.getElementById("ts-add-df-status");
  var nextWrap = document.getElementById("ts-add-df-next-wrap");
  var maxWrap = document.getElementById("ts-add-df-max-wrap");
  var submitBtn = document.getElementById("ts-add-df-submit");
  var dfBody = document.getElementById("ts-df-body");
  var dfCount = document.getElementById("ts-df-count");
  var dfHint = document.getElementById("ts-df-hint");

  function qsInstance() {
    return instanceId ? "?instance=" + encodeURIComponent(instanceId) : "";
  }

  function toggleAutoFields() {
    var on = autoInput && autoInput.checked;
    if (nextWrap) nextWrap.hidden = !on;
    if (maxWrap) maxWrap.hidden = !on;
    if (nextInput) nextInput.disabled = !on;
    if (maxInput) maxInput.disabled = !on;
  }

  function applySuggest(data) {
    if (!data || !data.ok) return;
    if (pathInput && data.suggested_path) pathInput.value = data.suggested_path;
    if (sizeInput && data.size_mb) sizeInput.value = data.size_mb;
    if (autoInput) autoInput.checked = data.auto_extend !== false;
    if (nextInput && data.next_mb) nextInput.value = data.next_mb;
    if (maxInput && data.max_size) maxInput.value = data.max_size;
    if (hintEl && data.hint) hintEl.textContent = data.hint;
    toggleAutoFields();
  }

  function setStatus(text, kind) {
    if (!statusEl) return;
    statusEl.hidden = !text;
    statusEl.textContent = text || "";
    statusEl.className = "form-hint" + (kind ? " " + kind : "");
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function barClass(pct) {
    if (pct >= 95) return "danger";
    if (pct >= 80) return "warn";
    return "";
  }

  function renderDatafiles(rows) {
    if (!dfBody) return;
    if (!rows || !rows.length) {
      dfBody.innerHTML =
        '<tr><td colspan="12" class="ts-empty">Datafile bulunamadi</td></tr>';
      if (dfCount) dfCount.textContent = "0 datafile";
      if (dfHint) dfHint.textContent = "Henuz datafile yok.";
      return;
    }
    dfBody.innerHTML = rows
      .map(function (df) {
        return (
          "<tr>" +
          '<td class="ts-file-name" title="' +
          esc(df.file_name) +
          '">' +
          esc(df.file_name) +
          "</td>" +
          "<td>" +
          df.file_id +
          "</td>" +
          '<td><div class="ts-usage-cell"><div class="ts-bar"><span class="ts-bar-fill ' +
          barClass(df.usage_pct) +
          '" style="width:' +
          df.usage_pct +
          '%"></span></div><span>' +
          df.usage_pct +
          "%</span></div></td>" +
          "<td>" +
          Number(df.size_gb).toFixed(2) +
          " GB</td>" +
          "<td>" +
          Number(df.used_gb).toFixed(2) +
          " GB</td>" +
          "<td>" +
          Number(df.free_gb).toFixed(2) +
          " GB</td>" +
          "<td>" +
          df.blocks +
          "</td>" +
          "<td>" +
          (df.auto_extend ? "✓" : "—") +
          "</td>" +
          "<td>" +
          (df.increment_mb != null ? df.increment_mb : 0) +
          " MB</td>" +
          "<td>" +
          esc(
            String(df.max_size || "").toUpperCase() === "UNLIMITED"
              ? "UNLIMITED"
              : String(df.max_size || "") + " MB"
          ) +
          "</td>" +
          "<td>" +
          esc(df.status) +
          "</td>" +
          "<td>" +
          Number(df.fragmentation_index).toFixed(2) +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
    if (dfCount) dfCount.textContent = rows.length + " datafile";
    if (dfHint) dfHint.textContent = "Toplam " + rows.length + " datafile.";
  }

  async function refreshSuggest() {
    try {
      var url =
        base +
        "/api/tablespaces/" +
        encodeURIComponent(tablespace) +
        "/datafiles/suggest" +
        qsInstance();
      var res = await fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      var data = await res.json();
      if (res.ok && data.ok) applySuggest(data);
    } catch (e) {
      /* SSR onerisi kalsin */
    }
  }

  function openModal() {
    setStatus("", "");
    refreshSuggest();
    if (typeof modal.showModal === "function") modal.showModal();
  }

  function closeModal() {
    if (typeof modal.close === "function") modal.close();
  }

  addBtn.addEventListener("click", openModal);
  document.getElementById("ts-add-df-close")?.addEventListener("click", closeModal);
  document.getElementById("ts-add-df-cancel")?.addEventListener("click", closeModal);
  if (autoInput) autoInput.addEventListener("change", toggleAutoFields);
  toggleAutoFields();

  modal.addEventListener("click", function (ev) {
    if (ev.target === modal) closeModal();
  });

  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    setStatus("Olusturuluyor...", "loading");
    if (submitBtn) submitBtn.disabled = true;

    var payload = {
      file_path: (pathInput && pathInput.value.trim()) || "",
      size_mb: parseInt((sizeInput && sizeInput.value) || "1024", 10),
      auto_extend: !!(autoInput && autoInput.checked),
      next_mb: parseInt((nextInput && nextInput.value) || "100", 10),
      max_size: ((maxInput && maxInput.value.trim()) || "UNLIMITED").toUpperCase(),
    };

    try {
      var url =
        base +
        "/api/tablespaces/" +
        encodeURIComponent(tablespace) +
        "/datafiles" +
        qsInstance();
      var res = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });
      var data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error((data && data.error) || "Ekleme basarisiz");
      }
      renderDatafiles(data.datafiles || []);
      closeModal();
      setStatus("", "");
      if (dfHint) {
        dfHint.textContent = (data.message || "Datafile eklendi") + " — liste guncellendi.";
      }
    } catch (err) {
      setStatus(err.message || String(err), "error");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
})();
