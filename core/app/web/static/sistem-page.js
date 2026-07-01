(function () {
  "use strict";

  const form = document.getElementById("system-auth-form");
  const testBtn = document.getElementById("ldap-test-btn");
  const testResult = document.getElementById("ldap-test-result");
  const authMode = form && form.querySelector('[name="auth_mode"]');
  const ldapBlock = document.getElementById("ldap-settings-block");
  const localBlock = document.getElementById("local-users-block");
  const boot = document.getElementById("sistem-clock-boot");
  const assetBase = window.yedekAssetBase ? window.yedekAssetBase() : "";

  function syncVisibility() {
    const mode = authMode ? authMode.value : "ldap_and_local";
    if (ldapBlock) ldapBlock.style.display = mode === "local" ? "none" : "";
    if (localBlock) localBlock.style.display = mode === "ldap" ? "none" : "";
  }

  if (authMode) {
    authMode.addEventListener("change", syncVisibility);
    syncVisibility();
  }

  const dateField = document.getElementById("sistem-clock-date");
  const timeField = document.getElementById("sistem-clock-time");
  const hostDate = boot ? boot.dataset.date || "" : "";
  const hostTime = boot ? boot.dataset.time || "" : "";
  if (dateField && hostDate && hostDate.includes(".")) {
    const parts = hostDate.split(".");
    if (parts.length === 3) dateField.value = parts[2] + "-" + parts[1] + "-" + parts[0];
  }
  if (timeField && hostTime) timeField.value = hostTime;

  if (testBtn && form) {
    testBtn.addEventListener("click", async function () {
      testResult.textContent = "Test ediliyor...";
      const data = new FormData(form);
      try {
        const res = await fetch(assetBase + "/sistem/ldap/test", { method: "POST", body: data });
        const json = await res.json();
        testResult.textContent = json.message || (json.ok ? "OK" : "Hata");
        testResult.className = "form-hint " + (json.ok ? "ok" : "error");
      } catch (err) {
        testResult.textContent = "Test basarisiz: " + err;
        testResult.className = "form-hint error";
      }
    });
  }
})();
