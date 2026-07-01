(function () {
  const yedekBase = window.yedekAssetBase ? window.yedekAssetBase() : "";

  function normalizeFtpPath(path) {
    var text = String(path || "").trim();
    if (!text || text === ".") return "/";
    if (/^\/o\/[a-z0-9][a-z0-9_-]*\/n\/[0-9a-f-]{36}\/?$/i.test(text)) return "/";
    if (!text.startsWith("/")) text = "/" + text;
    return text.replace(/\/+/g, "/");
  }

  const tabs = document.querySelectorAll('.instance-tab');
  const panels = document.querySelectorAll('.instance-tab-panel');

  function activateInstanceTab(tabId, updateUrl) {
    tabs.forEach(function (tab) {
      const active = tab.dataset.tab === tabId;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    panels.forEach(function (panel) {
      panel.classList.toggle('active', panel.id === tabId);
    });
    if (updateUrl && tabId && tabId.startsWith('inst-')) {
      const instanceId = tabId.slice(5);
      const url = new URL(window.location.href);
      url.searchParams.set('instance', instanceId);
      history.replaceState(null, '', url.toString());
    }
  }

  tabs.forEach(function (tab) {
    if (tab.classList.contains('instance-tab-add')) {
      return;
    }
    tab.addEventListener('click', function () {
      activateInstanceTab(tab.dataset.tab, true);
    });
  });

  function syncFtpUploadFields(instanceId) {
    const form = document.getElementById('instance-form-' + instanceId);
    if (!form) return;
    const ftpToggle = form.querySelector('.ftp-upload-toggle');
    const ftpOn = ftpToggle ? ftpToggle.checked : false;
    const instToggle = form.querySelector('[name="enabled"]');
    const instOn = instToggle ? instToggle.checked : true;
    const requireFtp = ftpOn && instOn;
    const wrap = document.getElementById('ftp-fields-' + instanceId);
    if (!wrap) return;
    wrap.dataset.ftpDisabled = ftpOn ? '' : '1';
    wrap.querySelectorAll('input[name^="localftp"]').forEach(function (input) {
      input.disabled = !ftpOn;
      if (!requireFtp) {
        input.required = false;
        return;
      }
      if (input.name === 'localftppass') {
        input.required = input.dataset.hasPass !== '1';
      } else {
        input.required = true;
      }
    });
  }

  document.querySelectorAll('.ftp-upload-toggle').forEach(function (toggle) {
    const instanceId = toggle.dataset.instanceId;
    syncFtpUploadFields(instanceId);
    toggle.addEventListener('change', function () {
      syncFtpUploadFields(instanceId);
    });
    const form = document.getElementById('instance-form-' + instanceId);
    const instToggle = form && form.querySelector('[name="enabled"]');
    if (instToggle) {
      instToggle.addEventListener('change', function () {
        syncFtpUploadFields(instanceId);
      });
    }
  });

  const modal = document.getElementById('schedule-modal');
  const form = document.getElementById('schedule-form');
  const title = document.getElementById('schedule-modal-title');
  const submitBtn = document.getElementById('schedule-submit-btn');
  const backupType = document.getElementById('schedule-backup-type');
  const dayWrap = document.getElementById('schedule-day-wrap');
  const daySelect = document.getElementById('schedule-day');
  const timeInput = document.getElementById('schedule-time');
  const labelInput = document.getElementById('schedule-label');
  const enabledInput = document.getElementById('schedule-enabled');
  const ruleIdInput = document.getElementById('schedule-rule-id');

  function toggleWeeklyDay() {
    const weekly = backupType.value === 'HAFTALIK';
    dayWrap.style.display = weekly ? '' : 'none';
    daySelect.disabled = !weekly;
    if (!weekly) {
      daySelect.value = '';
    } else if (daySelect.value === '') {
      daySelect.value = '6';
    }
  }

  window.toggleWeeklyDay = toggleWeeklyDay;

  window.openScheduleModal = function (mode, instanceId, instanceName, rule) {
    const isEdit = mode === 'edit';
    title.textContent = (isEdit ? 'Zamanlama Duzenle — ' : 'Zamanlama Ekle — ') + instanceName;
    submitBtn.textContent = isEdit ? 'Guncelle' : 'Ekle';
    form.action = isEdit
      ? yedekBase + `/ayarlar/instance/${instanceId}/zamanlama/${rule.id}/duzenle`
      : yedekBase + `/ayarlar/instance/${instanceId}/zamanlama/ekle`;

    ruleIdInput.value = isEdit ? rule.id : '';
    backupType.value = isEdit ? rule.backup_type : 'GUNLUK';
    timeInput.value = isEdit ? rule.time : '02:00';
    labelInput.value = isEdit ? (rule.label || '') : '';
    enabledInput.checked = isEdit ? rule.enabled : true;

    if (isEdit && rule.day_of_week !== null && rule.day_of_week !== '') {
      daySelect.value = String(rule.day_of_week);
    } else {
      daySelect.value = '6';
    }
    toggleWeeklyDay();
    modal.showModal();
  };

  window.closeScheduleModal = function () {
    modal.close();
  };

  document.querySelectorAll('.btn-schedule-add').forEach(function (btn) {
    btn.addEventListener('click', function () {
      openScheduleModal('add', btn.dataset.instanceId, btn.dataset.instanceName);
    });
  });

  document.querySelectorAll('.btn-schedule-edit').forEach(function (btn) {
    btn.addEventListener('click', function () {
      openScheduleModal('edit', btn.dataset.instanceId, btn.dataset.instanceName, {
        id: btn.dataset.ruleId,
        backup_type: btn.dataset.backupType,
        time: btn.dataset.time,
        day_of_week: btn.dataset.day === '' ? null : Number(btn.dataset.day),
        enabled: btn.dataset.enabled === '1',
        label: btn.dataset.label || '',
      });
    });
  });

  modal.addEventListener('click', function (event) {
    if (event.target === modal) {
      closeScheduleModal();
    }
  });

  const addInstanceModal = document.getElementById('add-instance-modal');
  const addInstanceForm = document.getElementById('add-instance-form');

  window.openAddInstanceModal = function () {
    if (addInstanceForm) {
      addInstanceForm.reset();
    }
    addInstanceModal.showModal();
  };

  window.closeAddInstanceModal = function () {
    addInstanceModal.close();
  };

  addInstanceModal.addEventListener('click', function (event) {
    if (event.target === addInstanceModal) {
      closeAddInstanceModal();
    }
  });

  const schemaModal = document.getElementById('schema-picker-modal');
  const schemaTitle = document.getElementById('schema-picker-title');
  const schemaMeta = document.getElementById('schema-picker-meta');
  const schemaStatus = document.getElementById('schema-picker-status');
  const schemaList = document.getElementById('schema-picker-list');
  const schemaPreview = document.getElementById('schema-picker-preview');
  const schemaFilter = document.getElementById('schema-picker-filter');
  const schemaRefreshBtn = document.getElementById('schema-picker-refresh');
  const schemaSelectAllBtn = document.getElementById('schema-picker-select-all');
  const schemaClearBtn = document.getElementById('schema-picker-clear');
  const schemaApplyBtn = document.getElementById('schema-picker-apply');

  const schemaState = {
    instanceId: '',
    instanceName: '',
    oracleSid: '',
    schemas: [],
    selected: new Set(),
    loading: false,
    filter: '',
  };

  function parseSchemaValue(value) {
    return String(value || '')
      .split(',')
      .map(function (part) { return part.trim().toUpperCase(); })
      .filter(Boolean);
  }

  function formatSchemaValue(names) {
    return names
      .map(function (name) { return String(name).trim().toUpperCase(); })
      .filter(Boolean)
      .join(',');
  }

  function updateSchemaPreview() {
    if (!schemaPreview) {
      return;
    }
    const ordered = schemaState.schemas.filter(function (name) {
      return schemaState.selected.has(name);
    });
    const extras = Array.from(schemaState.selected).filter(function (name) {
      return schemaState.schemas.indexOf(name) === -1;
    });
    const all = ordered.concat(extras.sort());
    schemaPreview.textContent = formatSchemaValue(all) || '(bos)';
  }

  function setSchemaStatus(text, kind) {
    if (!schemaStatus) {
      return;
    }
    schemaStatus.textContent = text;
    schemaStatus.className = 'schema-picker-status' + (kind ? ' ' + kind : '');
  }

  function renderSchemaPickerList() {
    if (!schemaList) {
      return;
    }
    const filter = (schemaState.filter || '').trim().toUpperCase();
    const visible = schemaState.schemas.filter(function (name) {
      return !filter || name.toUpperCase().indexOf(filter) !== -1;
    });
    if (!visible.length) {
      schemaList.innerHTML = '<p class="schema-picker-empty">Gosterilecek schema yok.</p>';
      return;
    }
    schemaList.innerHTML = visible.map(function (name) {
      const checked = schemaState.selected.has(name) ? ' checked' : '';
      const safe = name.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
      return (
        '<label class="schema-picker-item">' +
        '<input type="checkbox" class="schema-picker-check" value="' + safe + '"' + checked + '>' +
        '<span>' + safe + '</span>' +
        '</label>'
      );
    }).join('');
    schemaList.querySelectorAll('.schema-picker-check').forEach(function (input) {
      input.addEventListener('change', function () {
        const value = String(input.value || '').toUpperCase();
        if (input.checked) {
          schemaState.selected.add(value);
        } else {
          schemaState.selected.delete(value);
        }
        updateSchemaPreview();
      });
    });
  }

  const yedekBaseForSchema = yedekBase;

  async function loadSchemaPickerList() {
    if (!schemaState.instanceId || schemaState.loading) {
      return;
    }
    schemaState.loading = true;
    setSchemaStatus('Oracle schema listesi yukleniyor...', 'loading');
    try {
      const response = await fetch(yedekBaseForSchema + '/ayarlar/instance/' + encodeURIComponent(schemaState.instanceId) + '/schemas/list', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error((data && data.error) || 'Schema listesi alinamadi');
      }
      schemaState.schemas = (data.schemas || []).map(function (name) {
        return String(name).trim().toUpperCase();
      });
      const known = new Set(schemaState.schemas);
      Array.from(schemaState.selected).forEach(function (name) {
        if (!known.has(name)) {
          schemaState.schemas.push(name);
        }
      });
      schemaState.schemas.sort();
      renderSchemaPickerList();
      setSchemaStatus(schemaState.schemas.length + ' schema listelendi (SID: ' + (data.oracle_sid || schemaState.oracleSid) + ')', 'ok');
    } catch (err) {
      setSchemaStatus(err.message || 'Schema listesi alinamadi', 'failed');
      if (schemaList) {
        schemaList.innerHTML = '<p class="schema-picker-empty">' + (err.message || 'Hata') + '</p>';
      }
    } finally {
      schemaState.loading = false;
    }
  }

  function openSchemaPicker(instanceId, instanceName) {
    const input = document.getElementById('schemas-' + instanceId);
    if (!input) {
      return;
    }
    schemaState.instanceId = instanceId;
    schemaState.instanceName = instanceName || instanceId;
    schemaState.oracleSid = input.dataset.oracleSid || '';
    schemaState.filter = '';
    schemaState.selected = new Set(parseSchemaValue(input.value));
    if (schemaFilter) {
      schemaFilter.value = '';
    }
    if (schemaTitle) {
      schemaTitle.textContent = 'Schema Secimi — ' + schemaState.instanceName;
    }
    if (schemaMeta) {
      schemaMeta.textContent = 'SID: ' + (schemaState.oracleSid || '-') + ' · Secilenler virgul ile kaydedilir (expdp formati).';
    }
    updateSchemaPreview();
    schemaModal.showModal();
    loadSchemaPickerList();
  }

  window.closeSchemaPickerModal = function () {
    schemaModal.close();
  };

  function applySchemaPickerSelection() {
    const input = document.getElementById('schemas-' + schemaState.instanceId);
    if (!input) {
      closeSchemaPickerModal();
      return;
    }
    const ordered = schemaState.schemas.filter(function (name) {
      return schemaState.selected.has(name);
    });
    const extras = Array.from(schemaState.selected).filter(function (name) {
      return schemaState.schemas.indexOf(name) === -1;
    });
    input.value = formatSchemaValue(ordered.concat(extras.sort()));
    closeSchemaPickerModal();
  }

  document.querySelectorAll('.schema-input, .schema-pick-btn').forEach(function (el) {
    el.addEventListener('click', function (event) {
      event.preventDefault();
      const instanceId = el.dataset.instanceId;
      const form = document.getElementById('instance-form-' + instanceId);
      const labelInput = form ? form.querySelector('[name="label"]') : null;
      const instanceName = labelInput ? labelInput.value : instanceId;
      openSchemaPicker(instanceId, instanceName);
    });
  });

  if (schemaRefreshBtn) {
    schemaRefreshBtn.addEventListener('click', function () {
      loadSchemaPickerList();
    });
  }
  if (schemaSelectAllBtn) {
    schemaSelectAllBtn.addEventListener('click', function () {
      const filter = (schemaState.filter || '').trim().toUpperCase();
      schemaState.schemas.forEach(function (name) {
        if (!filter || name.toUpperCase().indexOf(filter) !== -1) {
          schemaState.selected.add(name);
        }
      });
      renderSchemaPickerList();
      updateSchemaPreview();
    });
  }
  if (schemaClearBtn) {
    schemaClearBtn.addEventListener('click', function () {
      schemaState.selected = new Set();
      renderSchemaPickerList();
      updateSchemaPreview();
    });
  }
  if (schemaFilter) {
    schemaFilter.addEventListener('input', function () {
      schemaState.filter = schemaFilter.value || '';
      renderSchemaPickerList();
    });
  }
  if (schemaApplyBtn) {
    schemaApplyBtn.addEventListener('click', applySchemaPickerSelection);
  }
  if (schemaModal) {
    schemaModal.addEventListener('click', function (event) {
      if (event.target === schemaModal) {
        closeSchemaPickerModal();
      }
    });
  }

  const ftpModal = document.getElementById('ftp-browser-modal');
  const ftpTitle = document.getElementById('ftp-browser-title');
  const ftpMeta = document.getElementById('ftp-browser-meta');
  const ftpStatus = document.getElementById('ftp-browser-status');
  const ftpTbody = document.getElementById('ftp-browser-tbody');
  const ftpHeadRow = document.getElementById('ftp-browser-head-row');
  const ftpBreadcrumb = document.getElementById('ftp-breadcrumb');
  const ftpUpBtn = document.getElementById('ftp-browser-up');
  const ftpRefreshBtn = document.getElementById('ftp-browser-refresh');
  const ftpDeleteBtn = document.getElementById('ftp-browser-delete');
  const ftpCancelDeleteBtn = document.getElementById('ftp-browser-cancel-delete');
  const ftpConfirmDeleteBtn = document.getElementById('ftp-browser-confirm-delete');

  const ftpState = {
    instanceId: '',
    instanceName: '',
    path: '/',
    loading: false,
    deleteMode: false,
    entries: [],
    analysis: {},
    selected: new Set(),
  };

  function formatFtpSize(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024) {
      return value + ' B';
    }
    if (value < 1024 * 1024) {
      return (value / 1024).toFixed(1) + ' KB';
    }
    if (value < 1024 * 1024 * 1024) {
      return (value / (1024 * 1024)).toFixed(1) + ' MB';
    }
    return (value / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
  }

  function joinFtpPath(base, name) {
    const root = (base || '/').replace(/\/+$/, '') || '';
    if (!name) {
      return '/';
    }
    if (name.startsWith('/')) {
      return name;
    }
    return (root + '/' + name).replace(/\/+/g, '/');
  }

  function parentFtpPath(path) {
    const cleaned = (path || '/').replace(/\/+$/, '') || '/';
    if (cleaned === '' || cleaned === '/') {
      return '/';
    }
    const parts = cleaned.split('/').filter(Boolean);
    parts.pop();
    return parts.length ? '/' + parts.join('/') : '/';
  }

  function readInstanceFtpCredentials(instanceId) {
    const form = document.getElementById('instance-form-' + instanceId);
    if (!form) {
      return null;
    }
    return {
      host: (form.querySelector('[name="localftpip"]') || {}).value || '',
      user: (form.querySelector('[name="localftpuser"]') || {}).value || '',
      password: (form.querySelector('[name="localftppass"]') || {}).value || '',
      baseDir: normalizeFtpPath((form.querySelector('[name="localftpdir"]') || {}).value || '/'),
    };
  }

  function setFtpDeleteMode(enabled) {
    ftpState.deleteMode = enabled;
    if (!enabled) {
      ftpState.selected = new Set();
    }
    document.querySelectorAll('.ftp-delete-only').forEach(function (el) {
      el.hidden = !enabled;
    });
    if (ftpDeleteBtn) {
      ftpDeleteBtn.hidden = enabled;
    }
    if (ftpHeadRow) {
      const selectCol = ftpHeadRow.querySelector('.ftp-col-select');
      if (selectCol) {
        selectCol.hidden = !enabled;
      }
    }
    if (ftpModal) {
      ftpModal.classList.toggle('ftp-delete-mode', enabled);
    }
    renderFtpEntries(ftpState.entries, ftpState.analysis);
  }

  function renderFtpBreadcrumb(path) {
    ftpBreadcrumb.innerHTML = '';
    const parts = (path || '/').split('/').filter(Boolean);
    const rootBtn = document.createElement('button');
    rootBtn.type = 'button';
    rootBtn.className = 'ftp-crumb';
    rootBtn.textContent = '/';
    rootBtn.addEventListener('click', function () {
      if (ftpState.deleteMode) {
        return;
      }
      loadFtpDirectory('/');
    });
    ftpBreadcrumb.appendChild(rootBtn);

    let built = '';
    parts.forEach(function (part) {
      built += '/' + part;
      const sep = document.createElement('span');
      sep.className = 'ftp-crumb-sep';
      sep.textContent = '/';
      ftpBreadcrumb.appendChild(sep);

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ftp-crumb';
      btn.textContent = part;
      const target = built;
      btn.addEventListener('click', function () {
        if (ftpState.deleteMode) {
          return;
        }
        loadFtpDirectory(target);
      });
      ftpBreadcrumb.appendChild(btn);
    });
  }

  function entryNameClass(entry) {
    if (entry.zero_size || entry.suspicious) {
      return 'ftp-name-bad';
    }
    return '';
  }

  function renderFtpEntries(entries, analysis) {
    ftpTbody.innerHTML = '';
    const colCount = ftpState.deleteMode ? 4 : 3;
    if (!entries.length) {
      const row = document.createElement('tr');
      row.innerHTML = '<td colspan="' + colCount + '" class="ftp-empty">Dizin bos</td>';
      ftpTbody.appendChild(row);
      return;
    }

    entries.forEach(function (entry) {
      const row = document.createElement('tr');
      row.className = entry.type === 'dir' ? 'ftp-row-dir' : 'ftp-row-file';
      if (entry.protected) {
        row.classList.add('ftp-row-protected');
      }
      if (entry.zero_size) {
        row.classList.add('ftp-row-zero');
      }
      if (entry.suspicious) {
        row.classList.add('ftp-row-suspicious');
      }

      if (ftpState.deleteMode) {
        const selectCell = document.createElement('td');
        selectCell.className = 'ftp-col-select';
        if (entry.type === 'file') {
          if (entry.zero_size) {
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.checked = true;
            box.disabled = true;
            box.title = 'Boyutu 0 — otomatik silinecek';
            selectCell.appendChild(box);
          } else if (entry.protected) {
            const badge = document.createElement('span');
            badge.className = 'ftp-protected-badge';
            badge.title = 'Dosya tarihine gore son ' + ((analysis && analysis.protected_count) || 5) + ' yedek korunuyor';
            badge.textContent = 'Korunuyor';
            selectCell.appendChild(badge);
          } else if (entry.deletable) {
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.checked = ftpState.selected.has(entry.name);
            box.addEventListener('change', function () {
              if (box.checked) {
                ftpState.selected.add(entry.name);
              } else {
                ftpState.selected.delete(entry.name);
              }
            });
            selectCell.appendChild(box);
          }
        }
        row.appendChild(selectCell);
      }

      const nameCell = document.createElement('td');
      nameCell.className = 'ftp-name-cell';
      if (entry.type === 'dir') {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ftp-dir-link';
        btn.textContent = entry.name;
        btn.disabled = ftpState.deleteMode;
        if (!ftpState.deleteMode) {
          btn.addEventListener('click', function () {
            loadFtpDirectory(joinFtpPath(ftpState.path, entry.name));
          });
        }
        nameCell.appendChild(btn);
      } else {
        const label = document.createElement('span');
        label.className = entryNameClass(entry);
        label.textContent = entry.name;
        nameCell.appendChild(label);
        if (entry.suspicious) {
          const hint = document.createElement('span');
          hint.className = 'ftp-bad-hint';
          hint.textContent = ' sorunlu yedek';
          nameCell.appendChild(hint);
        }
      }

      const dateCell = document.createElement('td');
      dateCell.className = 'ftp-date-cell';
      dateCell.textContent = entry.type === 'dir' ? '—' : (entry.modified_display || '—');

      const sizeCell = document.createElement('td');
      sizeCell.textContent = entry.type === 'dir' ? '—' : formatFtpSize(entry.size);

      row.appendChild(nameCell);
      row.appendChild(dateCell);
      row.appendChild(sizeCell);
      ftpTbody.appendChild(row);
    });
  }

  function collectDeleteTargets() {
    const forced = ftpState.entries
      .filter(function (entry) {
        return entry.type === 'file' && entry.zero_size;
      })
      .map(function (entry) {
        return entry.name;
      });
    const manual = Array.from(ftpState.selected);
    const merged = Array.from(new Set(forced.concat(manual)));
    return merged;
  }

  async function loadFtpDirectory(path) {
    if (!ftpState.instanceId || ftpState.loading) {
      return;
    }
    const creds = readInstanceFtpCredentials(ftpState.instanceId);
    if (!creds) {
      ftpStatus.textContent = 'Form bulunamadi';
      ftpStatus.className = 'ftp-browser-status failed';
      return;
    }
    if (!creds.host || !creds.user) {
      ftpStatus.textContent = 'FTP IP ve kullanici adini doldurun';
      ftpStatus.className = 'ftp-browser-status failed';
      return;
    }

    const targetPath = normalizeFtpPath(path || creds.baseDir || '/');
    ftpState.loading = true;
    ftpState.path = targetPath;
    ftpStatus.textContent = 'Baglaniliyor...';
    ftpStatus.className = 'ftp-browser-status loading';
    ftpTbody.innerHTML = '';
    renderFtpBreadcrumb(targetPath);

    try {
      const response = await fetch(yedekBase + '/ayarlar/instance/' + encodeURIComponent(ftpState.instanceId) + '/ftp/browse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          host: creds.host,
          user: creds.user,
          password: creds.password,
          path: targetPath,
        }),
      });
      const data = await response.json();
      if (!data.ok) {
        throw new Error(data.error || 'FTP listeleme basarisiz');
      }
      ftpState.path = data.path || targetPath;
      ftpState.entries = data.entries || [];
      ftpState.analysis = data.analysis || {};
      ftpMeta.textContent = data.host + ':' + data.port + ' · ' + ftpState.path;
      renderFtpBreadcrumb(ftpState.path);
      renderFtpEntries(ftpState.entries, ftpState.analysis);

      const backupCount = ftpState.entries.filter(function (e) {
        return e.is_backup;
      }).length;
      let statusText = ftpState.entries.length + ' oge · dosya tarihine gore (eski ustte)';
      if (backupCount) {
        statusText += ' · tarihe gore son ' + ((data.analysis && data.analysis.protected_count) || 5) + ' yedek korunuyor';
      }
      if (ftpState.deleteMode) {
        statusText += ' · silme modu aktif';
      }
      ftpStatus.textContent = statusText;
      ftpStatus.className = 'ftp-browser-status ok';
    } catch (err) {
      ftpStatus.textContent = err.message || String(err);
      ftpStatus.className = 'ftp-browser-status failed';
      ftpState.entries = [];
      renderFtpEntries([], {});
    } finally {
      ftpState.loading = false;
    }
  }

  async function deleteSelectedFtpFiles() {
    const files = collectDeleteTargets();
    if (!files.length) {
      ftpStatus.textContent = 'Silinecek dosya secilmedi';
      ftpStatus.className = 'ftp-browser-status failed';
      return;
    }
    const preview = files.slice(0, 5).join(', ');
    const suffix = files.length > 5 ? ' ...' : '';
    if (!window.confirm(files.length + ' dosya silinecek:\n' + preview + suffix + '\n\nDevam edilsin mi?')) {
      return;
    }

    const creds = readInstanceFtpCredentials(ftpState.instanceId);
    if (!creds) {
      return;
    }

    ftpState.loading = true;
    ftpStatus.textContent = 'Siliniyor...';
    ftpStatus.className = 'ftp-browser-status loading';

    try {
      const response = await fetch(yedekBase + '/ayarlar/instance/' + encodeURIComponent(ftpState.instanceId) + '/ftp/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          host: creds.host,
          user: creds.user,
          password: creds.password,
          path: ftpState.path,
          files: files,
        }),
      });
      const data = await response.json();
      if (!data.ok) {
        throw new Error(data.error || 'Silme basarisiz');
      }
      setFtpDeleteMode(false);
      ftpStatus.textContent = (data.count || files.length) + ' dosya silindi';
      ftpStatus.className = 'ftp-browser-status ok';
      await loadFtpDirectory(ftpState.path);
    } catch (err) {
      ftpStatus.textContent = err.message || String(err);
      ftpStatus.className = 'ftp-browser-status failed';
    } finally {
      ftpState.loading = false;
    }
  }

  window.openFtpBrowserModal = function (instanceId, instanceName) {
    ftpState.instanceId = instanceId;
    ftpState.instanceName = instanceName || instanceId;
    setFtpDeleteMode(false);
    ftpTitle.textContent = 'FTP Tarayici — ' + ftpState.instanceName;
    const creds = readInstanceFtpCredentials(instanceId);
    if (creds) {
      ftpMeta.textContent = (creds.host || '—') + ' · baslangic: ' + (creds.baseDir || '/');
    }
    ftpModal.showModal();
    loadFtpDirectory((creds && creds.baseDir) || '/');
  };

  window.closeFtpBrowserModal = function () {
    setFtpDeleteMode(false);
    ftpModal.close();
  };

  document.querySelectorAll('.btn-ftp-browse').forEach(function (btn) {
    btn.addEventListener('click', function () {
      openFtpBrowserModal(btn.dataset.instanceId, btn.dataset.instanceName);
    });
  });

  if (ftpUpBtn) {
    ftpUpBtn.addEventListener('click', function () {
      if (ftpState.deleteMode) {
        return;
      }
      loadFtpDirectory(parentFtpPath(ftpState.path));
    });
  }

  if (ftpRefreshBtn) {
    ftpRefreshBtn.addEventListener('click', function () {
      loadFtpDirectory(ftpState.path);
    });
  }

  if (ftpDeleteBtn) {
    ftpDeleteBtn.addEventListener('click', function () {
      if (!ftpState.entries.length) {
        return;
      }
      setFtpDeleteMode(true);
      ftpStatus.textContent = 'Silme modu: boyutu 0 otomatik secilir · dosya tarihine gore son 5 yedek korunur';
      ftpStatus.className = 'ftp-browser-status ok';
    });
  }

  if (ftpCancelDeleteBtn) {
    ftpCancelDeleteBtn.addEventListener('click', function () {
      setFtpDeleteMode(false);
      ftpStatus.textContent = 'Silme modu iptal edildi';
      ftpStatus.className = 'ftp-browser-status ok';
    });
  }

  if (ftpConfirmDeleteBtn) {
    ftpConfirmDeleteBtn.addEventListener('click', function () {
      deleteSelectedFtpFiles();
    });
  }

  if (ftpModal) {
    ftpModal.addEventListener('click', function (event) {
      if (event.target === ftpModal) {
        closeFtpBrowserModal();
      }
    });
  }

  toggleWeeklyDay();

  document.querySelectorAll('.backup-protect-mode').forEach(function (select) {
    function refreshProtectUi() {
      const instanceId = select.dataset.instanceId;
      const wrap = document.getElementById('protect-pass-wrap-' + instanceId);
      const hint = document.getElementById('protect-hint-' + instanceId);
      const mode = select.value;
      const needsPass = mode === 'oracle' || mode === 'zip';
      if (wrap) {
        wrap.hidden = !needsPass;
      }
      if (hint) {
        if (mode === 'oracle') {
          hint.innerHTML = 'expdp compression=all + encryption=all, ardindan <strong>gzip</strong>. Dosya: <strong>.dmp.gz</strong>';
        } else if (mode === 'zip') {
          hint.innerHTML = 'expdp sikistirma + <strong>zip -P</strong> sifreli arsiv. Dosya: <strong>.zip</strong>';
        } else {
          hint.innerHTML = 'Mevcut yontem: expdp + gzip (<strong>.dmp.gz</strong>).';
        }
      }
    }
    select.addEventListener('change', refreshProtectUi);
    refreshProtectUi();
  });

  const TURKISH_ASCII = {
    '\u0131': 'I', '\u0130': 'I', '\u011f': 'G', '\u011e': 'G',
    '\u00fc': 'U', '\u00dc': 'U', '\u015f': 'S', '\u015e': 'S',
    '\u00f6': 'O', '\u00d6': 'O', '\u00e7': 'C', '\u00c7': 'C',
  };

  function normalizeUpperAscii(value) {
    let text = String(value || '');
    Object.keys(TURKISH_ASCII).forEach(function (ch) {
      text = text.split(ch).join(TURKISH_ASCII[ch]);
    });
    text = text.toUpperCase().replace(/[^A-Z ]/g, '');
    return text.replace(/\s+/g, ' ').trimStart();
  }

  document.querySelectorAll('.upper-ascii-field').forEach(function (input) {
    input.addEventListener('input', function () {
      const normalized = normalizeUpperAscii(input.value);
      if (input.value !== normalized) {
        input.value = normalized;
      }
    });
    input.addEventListener('blur', function () {
      input.value = normalizeUpperAscii(input.value).trim();
    });
  });

  document.querySelectorAll('.btn-reveal-secret').forEach(function (btn) {
    const targetId = btn.dataset.target;
    const input = targetId ? document.getElementById(targetId) : null;
    if (!input) {
      return;
    }

    input.addEventListener('input', function () {
      input.dataset.userEdited = '1';
    });

    btn.addEventListener('click', function () {
      const secret = btn.dataset.secret || '';
      const visible = btn.dataset.visible === '1';
      if (visible) {
        input.type = 'password';
        if (input.dataset.userEdited !== '1') {
          input.value = '';
        }
        btn.textContent = 'Goster';
        btn.dataset.visible = '0';
        return;
      }
      input.type = 'text';
      input.value = secret;
      input.dataset.userEdited = '0';
      input.focus();
      input.select();
      btn.textContent = 'Gizle';
      btn.dataset.visible = '1';
    });
  });
})();
