(function () {
  "use strict";

  const form = document.getElementById("system-auth-form");
  const testBtn = document.getElementById("ldap-test-btn");
  const testResult = document.getElementById("ldap-test-result");
  const authMode = form && form.querySelector('[name="auth_mode"]');
  const ldapBlock = document.getElementById("ldap-settings-block");
  const localBlock = document.getElementById("local-users-block");
  const rolesBlock = document.getElementById("local-roles-block");
  const boot = document.getElementById("sistem-clock-boot");
  const assetBase = window.yedekAssetBase ? window.yedekAssetBase() : "";

  const presetsEl = document.getElementById("role-perm-presets");
  const rolesDataEl = document.getElementById("local-roles-data");
  let rolePresets = {};
  let localRoles = [];

  if (presetsEl && presetsEl.textContent) {
    try { rolePresets = JSON.parse(presetsEl.textContent); } catch (_e) { rolePresets = {}; }
  }
  if (rolesDataEl && rolesDataEl.textContent) {
    try { localRoles = JSON.parse(rolesDataEl.textContent); } catch (_e) { localRoles = []; }
  }

  function roleById(roleId) {
    return localRoles.find(function (r) { return r.role_id === roleId; }) || null;
  }

  function applyPermsToMatrix(container, perms) {
    if (!container || !perms) return;
    container.querySelectorAll(".perm-box").forEach(function (box) {
      const mod = box.dataset.module;
      const action = box.dataset.action;
      box.checked = !!(perms[mod] && perms[mod][action]);
    });
  }

  function applyPresetToMatrix(container, roleId) {
    if (!container) return;
    const preset = rolePresets[roleId] || (roleById(roleId) && roleById(roleId).permissions);
    if (preset) applyPermsToMatrix(container, preset);
  }

  function closeModals() {
    document.querySelectorAll(".sistem-modal").forEach(function (dlg) {
      if (dlg.open) dlg.close();
    });
  }

  document.querySelectorAll(".sistem-modal-close").forEach(function (btn) {
    btn.addEventListener("click", closeModals);
  });

  const roleModal = document.getElementById("role-modal");
  const roleForm = document.getElementById("role-modal-form");
  const roleTitle = document.getElementById("role-modal-title");
  const roleIdWrap = document.getElementById("role-id-wrap");
  const roleIdInput = document.getElementById("role-id-input");
  const roleLabelInput = document.getElementById("role-label-input");
  const rolePermHost = document.getElementById("role-perm-host");

  function openRoleModal(mode, roleId) {
    if (!roleModal || !roleForm) return;
    const row = roleId ? roleById(roleId) : null;
    if (mode === "add") {
      roleForm.action = assetBase + "/sistem/rol/ekle";
      roleTitle.textContent = "Rol Ekle";
      roleIdWrap.hidden = false;
      roleIdInput.disabled = false;
      roleIdInput.required = true;
      roleIdInput.value = "";
      roleLabelInput.value = "";
      applyPresetToMatrix(rolePermHost, "limited");
    } else {
      roleForm.action = assetBase + "/sistem/rol/" + encodeURIComponent(roleId) + "/guncelle";
      roleTitle.textContent = "Rol Duzenle — " + (row ? row.label : roleId);
      roleIdWrap.hidden = true;
      roleIdInput.disabled = true;
      roleIdInput.required = false;
      roleIdInput.value = roleId || "";
      roleLabelInput.value = row ? row.label : "";
      applyPermsToMatrix(rolePermHost, row ? row.permissions : null);
    }
    roleModal.showModal();
  }

  const roleAddBtn = document.getElementById("role-add-open");
  if (roleAddBtn) {
    roleAddBtn.addEventListener("click", function () { openRoleModal("add"); });
  }
  document.querySelectorAll(".role-edit-open").forEach(function (btn) {
    btn.addEventListener("click", function () {
      openRoleModal("edit", btn.dataset.roleId || "");
    });
  });

  const userModal = document.getElementById("user-modal");
  const userForm = document.getElementById("user-modal-form");
  const userTitle = document.getElementById("user-modal-title");
  const userNameWrap = document.getElementById("user-name-wrap");
  const userNameInput = document.getElementById("user-name-input");
  const userPassWrap = document.getElementById("user-pass-wrap");
  const userPassInput = document.getElementById("user-pass-input");
  const userPassEditWrap = document.getElementById("user-pass-edit-wrap");
  const userPassEditInput = document.getElementById("user-pass-edit-input");
  const userRoleSelect = document.getElementById("user-role-select");
  const userEnabledWrap = document.getElementById("user-enabled-wrap");
  const userEnabledInput = document.getElementById("user-enabled-input");

  function openUserModal(mode, username) {
    if (!userModal || !userForm) return;
    if (mode === "add") {
      userForm.action = assetBase + "/sistem/yerel/ekle";
      userTitle.textContent = "Kullanici Ekle";
      userNameWrap.hidden = false;
      userNameInput.required = true;
      userNameInput.value = "";
      userPassWrap.hidden = false;
      userPassInput.required = true;
      userPassInput.value = "";
      userPassInput.name = "password";
      userPassEditWrap.hidden = true;
      userPassEditInput.required = false;
      userPassEditInput.value = "";
      userPassEditInput.removeAttribute("name");
      userEnabledWrap.hidden = true;
      if (userRoleSelect) userRoleSelect.value = "limited";
    } else {
      const btn = document.querySelector('.user-edit-open[data-username="' + username + '"]');
      userForm.action = assetBase + "/sistem/yerel/" + encodeURIComponent(username) + "/guncelle";
      userTitle.textContent = "Kullanici Duzenle — " + username;
      userNameWrap.hidden = true;
      userNameInput.required = false;
      userPassWrap.hidden = true;
      userPassInput.required = false;
      userPassInput.removeAttribute("name");
      userPassEditWrap.hidden = false;
      userPassEditInput.required = false;
      userPassEditInput.value = "";
      userPassEditInput.name = "password";
      userEnabledWrap.hidden = false;
      if (userRoleSelect && btn) userRoleSelect.value = btn.dataset.role || "limited";
      if (userEnabledInput && btn) userEnabledInput.checked = btn.dataset.enabled === "1";
    }
    userModal.showModal();
  }

  const userAddBtn = document.getElementById("user-add-open");
  if (userAddBtn) {
    userAddBtn.addEventListener("click", function () { openUserModal("add"); });
  }
  document.querySelectorAll(".user-edit-open").forEach(function (btn) {
    btn.addEventListener("click", function () {
      openUserModal("edit", btn.dataset.username || "");
    });
  });

  function syncVisibility() {
    const mode = authMode ? authMode.value : "ldap_and_local";
    if (ldapBlock) ldapBlock.style.display = mode === "local" ? "none" : "";
    if (localBlock) localBlock.style.display = mode === "ldap" ? "none" : "";
    if (rolesBlock) rolesBlock.style.display = mode === "ldap" ? "none" : "";
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
