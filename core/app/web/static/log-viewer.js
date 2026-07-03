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

  function closeModal() {
    if (modal.open) {
      modal.close();
    }
  }

  window.closeLogViewerModal = closeModal;

  function apiErrorMessage(result) {
    const data = result.data;
    if (data) {
      if (data.error) {
        return String(data.error);
      }
      if (data.detail) {
        if (typeof data.detail === 'string') {
          return data.detail;
        }
        if (Array.isArray(data.detail)) {
          return data.detail
            .map(function (item) {
              if (item && item.msg) {
                return String(item.msg);
              }
              return String(item);
            })
            .join(', ');
        }
      }
      if (data.message) {
        return String(data.message);
      }
    }
    if (!result.response.ok) {
      const snippet = (result.text || '').trim().slice(0, 240);
      return 'HTTP ' + result.response.status + (snippet ? ': ' + snippet : '');
    }
    return 'Log alinamadi';
  }

  function parseApiResponse(response) {
    return response.text().then(function (text) {
      let data = null;
      if (text) {
        try {
          data = JSON.parse(text);
        } catch (err) {
          data = null;
        }
      }
      return { response: response, data: data, text: text };
    });
  }

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

    const base = window.yedekAssetBase ? window.yedekAssetBase() : window.__YEDEK_BASE__ || '';
    fetch(base + '/api/log/content?' + params.toString(), { credentials: 'same-origin' })
      .then(parseApiResponse)
      .then(function (result) {
        const data = result.data || {};
        if (!result.response.ok || data.ok === false) {
          throw new Error(apiErrorMessage(result));
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

  modal.querySelectorAll('.log-viewer-close-btn').forEach(function (btn) {
    btn.addEventListener('click', function (event) {
      event.preventDefault();
      closeModal();
    });
  });

  modal.addEventListener('click', function (event) {
    if (event.target === modal) {
      closeModal();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && modal.open) {
      closeModal();
    }
  });
})();
