(() => {
  const els = {
    video: document.getElementById('video'),
    overlay: document.getElementById('overlay'),
    overlayTitle: document.getElementById('overlayTitle'),
    overlayBody: document.getElementById('overlayBody'),
    retryBtn: document.getElementById('retryBtn'),
    camStatusText: document.getElementById('camStatusText'),
    camDot: document.getElementById('camDot'),

    resolution: document.getElementById('resolution'),
    session: document.getElementById('session'),

    faces: document.getElementById('faces'),
    fps: document.getElementById('fps'),
    detection: document.getElementById('detection'),

    captureBtn: document.getElementById('captureBtn'),
    downloadLatest: document.getElementById('downloadLatest'),

    gallery: document.getElementById('gallery'),
    refreshGallery: document.getElementById('refreshGallery'),

    history: document.getElementById('history'),
    refreshHistory: document.getElementById('refreshHistory'),
  };

  const state = {
    latestCaptureUrl: null,
    pollTimer: null,
  };

  const formatDuration = (seconds) => {
    seconds = Math.max(0, Math.floor(seconds));
    const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
    const ss = String(seconds % 60).padStart(2, '0');
    return `${mm}:${ss}`;
  };

  const escapeHtml = (s) => String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '<')
    .replaceAll('>', '>')
    .replaceAll('"', '"')
    .replaceAll("'", '&#039;');

  const setCameraUI = (camera) => {
    const online = camera === 'Online';

    if (online) {
      els.overlay.classList.add('hidden');
      els.camDot.style.background = 'var(--ok)';
      els.camDot.style.boxShadow = '0 0 0 6px rgba(54,226,123,.12)';
      els.camStatusText.textContent = `Camera: ${camera}`;
      els.video.style.opacity = '1';
    } else {
      els.overlayTitle.textContent = 'Camera Offline';
      els.overlayBody.textContent = 'Please reconnect webcam.';
      els.overlay.classList.remove('hidden');
      els.camDot.style.background = 'var(--danger)';
      els.camDot.style.boxShadow = '0 0 0 6px rgba(255,92,122,.12)';
      els.camStatusText.textContent = `Camera: ${camera}`;
      els.video.style.opacity = '.25';
    }

    els.captureBtn.disabled = !online;
  };

  const setDetectionUI = (detection) => {
    els.detection.textContent = detection;
  };

  const fetchJson = async (url, options) => {
    const res = await fetch(url, {
      ...options,
      headers: {
        'Accept': 'application/json',
        ...(options?.headers || {})
      }
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
    }
    return res.json();
  };

  const loadGallery = async () => {
    const res = await fetchJson('/gallery');
    const images = res.images || [];

    if (!images.length) {
      els.gallery.innerHTML = '<div class="empty">No captures yet.</div>';
      return;
    }

    // Latest = first
    const latest = images[0];
    state.latestCaptureUrl = latest?.url || null;
    els.downloadLatest.href = state.latestCaptureUrl || '#';

    els.gallery.innerHTML = images.map((img) => {
      const ts = escapeHtml(img.timestamp || '');
      const label = escapeHtml(img.filename || 'capture');
      return `
        <div class="thumb">
          <div class="thumb__badge">${ts || 'Capture'}</div>
          <img src="${img.url}" alt="Captured face" loading="lazy" />
          <div class="thumb__label" title="${label}">${label}</div>
        </div>
      `;
    }).join('');
  };

  const loadHistory = async () => {
    const res = await fetchJson('/history');
    const items = res.history || [];

    if (!items.length) {
      els.history.innerHTML = '<div class="empty">No history yet.</div>';
      return;
    }

    els.history.innerHTML = items.map((it) => {
      const t = escapeHtml(it.timestamp || '');
      const faces = Number.isFinite(it.faces) ? it.faces : '--';
      const label = escapeHtml(it.detection || '');
      return `
        <div class="item">
          <div class="item__row">
            <div class="item__faces">${faces} face(s)</div>
            <div class="item__time">${t}</div>
          </div>
          <div style="margin-top:6px; color: rgba(234,242,255,.75); font-size:12.5px;">${label}</div>
        </div>
      `;
    }).join('');
  };

  const pollStats = async () => {
    try {
      const data = await fetchJson('/stats?ts=' + Date.now());

      // Live stats
      els.faces.textContent = String(data.faces ?? '--');
      els.fps.textContent = typeof data.fps === 'number'
        ? data.fps.toFixed(1)
        : String(data.fps ?? '--');

      els.session.textContent = formatDuration(data.session_seconds ?? 0);
      els.resolution.textContent = data.resolution || '--';

      setDetectionUI(data.detection || 'Stopped');
      setCameraUI(data.camera || 'Offline');

    } catch (e) {
      // If /stats fails, assume offline (don’t spam error)
      setCameraUI('Offline');
      setDetectionUI('Stopped');
      els.faces.textContent = '--';
      els.fps.textContent = '--';
    }
  };

  const startPolling = () => {
    if (state.pollTimer) return;
    pollStats();
    state.pollTimer = setInterval(pollStats, 1000);
  };

  const stopPolling = () => {
    if (!state.pollTimer) return;
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  };

  const capture = async () => {
    try {
      els.captureBtn.disabled = true;
      const res = await fetch('/capture', {
        method: 'POST',
        headers: { 'Accept': 'application/json' },
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data?.error || 'Capture failed');
      }
      await loadGallery();
      // auto refresh latest after capture
    } catch (e) {
      // best-effort refresh overlay
      els.overlayTitle.textContent = 'Capture Failed';
      els.overlayBody.textContent = e?.message || 'Try again.';
      els.overlay.classList.remove('hidden');
    } finally {
      // Re-enable depending on camera status; next poll will update.
      if (!els.captureBtn.disabled) return;
      // leave disabled until /stats confirms online
    }
  };

  const wireEvents = () => {
    els.captureBtn.addEventListener('click', capture);

    els.retryBtn.addEventListener('click', async () => {
      // Force-reload stream by adding cache buster
      if (els.video) els.video.src = `/video?ts=${Date.now()}`;
      // Immediately poll
      stopPolling();
      startPolling();
      await loadGallery().catch(() => {});
      await loadHistory().catch(() => {});
    });

    document.addEventListener('keydown', (e) => {
      if (e.code === 'Space') {
        e.preventDefault();
        if (!els.captureBtn.disabled) capture();
      }
    });

    els.refreshGallery.addEventListener('click', loadGallery);
    els.refreshHistory.addEventListener('click', loadHistory);
  };

  // Init
  wireEvents();
  startPolling();
  loadGallery().catch(() => {});
  loadHistory().catch(() => {});
})();

