(function () {
  const modal = document.getElementById('log-viewer-modal');
  if (!modal) {
    return;
  }

  const titleEl = document.getElementById('log-viewer-title');
  const bodyEl = document.getElementById('log-viewer-body');
  const statusEl = document.getElementById('log-viewer-status');

  function setStatus(text, kind) {
    if (!statusEl) {
      return;
    }
    statusEl.textContent = text;
    statusEl.className = 'log-viewer-status' + (kind ? ' ' + kind : '');
    statusEl.hidden = !text;
  }

  window.closeLogViewerModal = function () {
    modal.close();
  };

  window.openLogViewer = function (source, name, instanceId) {
    if (!name) {
      return;
    }
    const params = new URLSearchParams({
      source: source || 'expdp',
      name: name,
    });
    if (instanceId) {
      params.set('instance_id', instanceId);
    }
    setStatus('Log yukleniyor...', 'loading');
    if (titleEl) {
      titleEl.textContent = name;
    }
    if (bodyEl) {
      bodyEl.textContent = '';
    }
    modal.showModal();

    fetch((window.__YEDEK_BASE__ || '') + '/api/log/content?' + params.toString(), { credentials: 'same-origin' })
      .then(function (response) {
        return response.json().then(function (data) {
          return { response: response, data: data };
        });
      })
      .then(function (result) {
        const data = result.data || {};
        if (!result.response.ok || !data.ok) {
          throw new Error((data && data.error) || 'Log alinamadi');
        }
        if (titleEl) {
          titleEl.textContent = data.name || name;
        }
        if (bodyEl) {
          bodyEl.textContent = data.content || '(bos)';
        }
        setStatus('', '');
      })
      .catch(function (err) {
        if (bodyEl) {
          bodyEl.textContent = err.message || 'Log alinamadi';
        }
        setStatus('Hata', 'failed');
      });
  };

  document.querySelectorAll('.btn-log-view').forEach(function (btn) {
    btn.addEventListener('click', function (event) {
      event.preventDefault();
      openLogViewer(btn.dataset.logSource, btn.dataset.logName, btn.dataset.logInstance || '');
    });
  });

  modal.addEventListener('click', function (event) {
    if (event.target === modal) {
      closeLogViewerModal();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && modal.open) {
      closeLogViewerModal();
    }
  });
})();
