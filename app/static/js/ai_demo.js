'use strict';

(() => {
  const root = document.querySelector('[data-demo-ai]');
  if (!root) return;

  const video = document.querySelector('#demo-ai-video');
  const restart = document.querySelector('#demo-restart');
  const manual = root.querySelector('[data-demo-manual]');
  const autoToggle = document.querySelector('#parking-auto');
  const currentTime = document.querySelector('#demo-current-time');
  const duration = document.querySelector('#demo-duration');
  const syncStatus = document.querySelector('#demo-sync-status');
  const overlayCanvas = document.querySelector('#parking-overlay-canvas');
  const overlayWrapper = document.querySelector('[data-video-overlay]');
  const fullscreenButton = document.querySelector('[data-video-fullscreen]');
  const countNodes = {
    occupied: document.querySelector('#demo-count-occupied'),
    empty: document.querySelector('#demo-count-empty'),
    unknown: document.querySelector('#demo-count-unknown')
  };
  const labels = {OCCUPIED: 'Có xe', EMPTY: 'Trống', UNKNOWN: 'Chưa rõ'};
  const slotNodes = new Map(
    Array.from(document.querySelectorAll('[data-demo-slot]'))
      .map(node => [node.dataset.demoSlot, node])
  );

  let data = null;
  let detectionsData = null;
  let hasCompatiblePolygons = true;
  let animationFrame = null;
  let renderedTimelineIndex = -1;
  let currentSnapshot = null;
  let visionJobId = root.dataset.visionJobId || null;

  const polygonColors = {
    OCCUPIED: {stroke: '#ff3b30', fill: 'rgba(255,59,48,.16)'},
    EMPTY: {stroke: '#20d36b', fill: 'rgba(32,211,107,.14)'},
    UNKNOWN: {stroke: '#ffffff', fill: 'rgba(255,255,255,.09)'}
  };

  function drawOverlay(snapshot = currentSnapshot) {
    if (!overlayCanvas) return;
    const rect = overlayCanvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const ratio = window.devicePixelRatio || 1;
    const pixelWidth = Math.max(1, Math.round(rect.width * ratio));
    const pixelHeight = Math.max(1, Math.round(rect.height * ratio));
    if (overlayCanvas.width !== pixelWidth || overlayCanvas.height !== pixelHeight) {
      overlayCanvas.width = pixelWidth;
      overlayCanvas.height = pixelHeight;
    }
    const context = overlayCanvas.getContext('2d');
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);

    const sourceWidth = Number(data?.coordinate_space?.width || detectionsData?.coordinate_space?.width || 1920);
    const sourceHeight = Number(data?.coordinate_space?.height || detectionsData?.coordinate_space?.height || 1080);
    const sourceAspect = sourceWidth / sourceHeight;
    const containerAspect = rect.width / rect.height;
    let displayedWidth = rect.width;
    let displayedHeight = rect.height;
    let offsetX = 0;
    let offsetY = 0;
    if (containerAspect > sourceAspect) {
      displayedWidth = rect.height * sourceAspect;
      offsetX = (rect.width - displayedWidth) / 2;
    } else if (containerAspect < sourceAspect) {
      displayedHeight = rect.width / sourceAspect;
      offsetY = (rect.height - displayedHeight) / 2;
    }

    // 1. Draw Polygons if compatible
    if (hasCompatiblePolygons && data?.polygons) {
      const scaleX = displayedWidth / sourceWidth;
      const scaleY = displayedHeight / sourceHeight;
      const states = snapshot?.states || data.slot_ids.map(() => 'UNKNOWN');

      data.polygons.forEach((polygon, index) => {
        const state = states[index] || 'UNKNOWN';
        const colors = polygonColors[state] || polygonColors.UNKNOWN;
        const points = polygon.points || [];
        if (points.length < 3) return;
        context.beginPath();
        points.forEach(([x, y], pointIndex) => {
          const scaledX = offsetX + Number(x) * scaleX;
          const scaledY = offsetY + Number(y) * scaleY;
          if (pointIndex === 0) context.moveTo(scaledX, scaledY);
          else context.lineTo(scaledX, scaledY);
        });
        context.closePath();
        context.fillStyle = colors.fill;
        context.strokeStyle = colors.stroke;
        context.lineWidth = Math.max(1.5, Math.min(rect.width, rect.height) / 650);
        context.fill();
        context.stroke();

        const center = points.reduce(
          (sum, [x, y]) => [sum[0] + Number(x), sum[1] + Number(y)],
          [0, 0]
        ).map(total => total / points.length);
        const fontSize = Math.max(10, Math.min(18, rect.width / 95));
        context.font = `700 ${fontSize}px system-ui, sans-serif`;
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.lineWidth = Math.max(2, fontSize / 4);
        context.strokeStyle = 'rgba(0,0,0,.9)';
        context.strokeText(polygon.id, offsetX + center[0] * scaleX, offsetY + center[1] * scaleY);
        context.fillStyle = colors.stroke;
        context.fillText(polygon.id, offsetX + center[0] * scaleX, offsetY + center[1] * scaleY);
      });
    }

    // 2. Draw Bounding Boxes if detections are available
    if (detectionsData?.frames && !video.ended) {
      const detSourceW = Number(detectionsData.coordinate_space?.width || 1920);
      const detSourceH = Number(detectionsData.coordinate_space?.height || 1080);
      const scaleBoxX = displayedWidth / detSourceW;
      const scaleBoxY = displayedHeight / detSourceH;
      const fps = Number(detectionsData.fps) || 30.0;
      const totalFrames = Number(detectionsData.total_frames) || 0;
      const currentSeconds = Math.max(0, Number(video.currentTime) || 0);

      const targetFrame = Math.round(currentSeconds * fps);
      if (targetFrame >= 0 && (totalFrames === 0 || targetFrame < totalFrames)) {
        const frameTime = targetFrame / fps;
        const diff = Math.abs(currentSeconds - frameTime);
        if (diff <= (1.0 / fps) + 0.05) {
          const boxes = detectionsData.frames[String(targetFrame)] || [];
          boxes.forEach(item => {
            if (!item.box || item.box.length < 4) return;
            const [x1, y1, x2, y2] = item.box;
            const bx = offsetX + x1 * scaleBoxX;
            const by = offsetY + y1 * scaleBoxY;
            const bw = (x2 - x1) * scaleBoxX;
            const bh = (y2 - y1) * scaleBoxY;
            if (bw <= 0 || bh <= 0) return;

            const isOccupiedSlot = Boolean(hasCompatiblePolygons && item.slot);
            const boxColor = isOccupiedSlot ? '#ff3b30' : '#00e5ff';
            const fillColor = isOccupiedSlot ? 'rgba(255, 59, 48, 0.12)' : 'rgba(0, 229, 255, 0.10)';

            context.strokeStyle = boxColor;
            context.lineWidth = Math.max(1.5, Math.min(rect.width, rect.height) / 750);
            context.fillStyle = fillColor;
            context.beginPath();
            if (context.roundRect) {
              context.roundRect(bx, by, bw, bh, 3);
            } else {
              context.rect(bx, by, bw, bh);
            }
            context.fill();
            context.stroke();

            const pct = Math.round((Number(item.confidence) || 0) * 100);
            const labelText = isOccupiedSlot ? `Car · ${item.slot} (${pct}%)` : `Car ${pct}%`;
            const labelFontSize = Math.max(10, Math.min(13, rect.width / 130));
            context.font = `600 ${labelFontSize}px system-ui, -apple-system, sans-serif`;
            const textMetrics = context.measureText(labelText);
            const paddingH = 6;
            const paddingV = 3;
            const badgeWidth = textMetrics.width + paddingH * 2;
            const badgeHeight = labelFontSize + paddingV * 2;

            let badgeX = bx;
            let badgeY = by - badgeHeight - 2;
            if (badgeY < offsetY) {
              badgeY = by + 2;
            }

            context.fillStyle = 'rgba(15, 23, 42, 0.85)';
            context.beginPath();
            if (context.roundRect) {
              context.roundRect(badgeX, badgeY, badgeWidth, badgeHeight, 3);
            } else {
              context.rect(badgeX, badgeY, badgeWidth, badgeHeight);
            }
            context.fill();

            context.fillStyle = boxColor;
            context.textAlign = 'left';
            context.textBaseline = 'top';
            context.fillText(labelText, badgeX + paddingH, badgeY + paddingV);
          });
        }
      }
    }
  }

  function formatTime(value) {
    const safe = Number.isFinite(value) && value >= 0 ? value : 0;
    const minutes = Math.floor(safe / 60);
    const seconds = safe - minutes * 60;
    return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(2).padStart(5, '0')}`;
  }

  function timelineIndexAt(seconds) {
    const timeline = data.timeline;
    const target = Math.max(0, Number(seconds) || 0);
    let low = 0;
    let high = timeline.length - 1;
    let match = 0;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (timeline[middle].time <= target) {
        match = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    return match;
  }

  function clearStatesForManualMode() {
    renderedTimelineIndex = -1;
    currentSnapshot = null;
    if (hasCompatiblePolygons && data?.slot_ids) {
      data.slot_ids.forEach(slotId => {
        const node = slotNodes.get(slotId);
        if (node) {
          node.dataset.state = 'UNKNOWN';
          node.className = 'demo-slot unknown';
          node.querySelector('span').textContent = labels.UNKNOWN;
        }
      });
      if (countNodes.occupied) countNodes.occupied.textContent = '0';
      if (countNodes.empty) countNodes.empty.textContent = '0';
      if (countNodes.unknown) countNodes.unknown.textContent = String(data.total_slots);
    }
    drawOverlay(null);
    syncStatus.textContent = 'Kết quả đã được xóa vì video đổi thời điểm. Bấm “Cập nhật khung hình”.';
  }

  function render(seconds, force = false, manual = false) {
    currentTime.textContent = formatTime(seconds);
    if (!data) return;

    if (!hasCompatiblePolygons) {
      drawOverlay(null);
      syncStatus.textContent = `Nhận diện xe YOLO · Thời gian: ${formatTime(seconds)}`;
      return;
    }

    const index = timelineIndexAt(seconds);
    if (autoToggle && !autoToggle.checked && !manual) {
      clearStatesForManualMode();
      return;
    }
    if (!force && index === renderedTimelineIndex) {
      drawOverlay(currentSnapshot);
      return;
    }
    renderedTimelineIndex = index;
    const snapshot = data.timeline[index];
    currentSnapshot = snapshot;
    if (visionJobId && force) {
      const d13Index = data.slot_ids.indexOf('D13');
      console.debug('[vision timeline]', {
        VISION_JOB_ID: visionJobId,
        currentTime: Number(seconds),
        timelineFrame: snapshot.frame,
        timelineTimestamp: snapshot.time,
        D13: snapshot.states[d13Index],
        D13Color: polygonColors[snapshot.states[d13Index]]?.stroke || polygonColors.UNKNOWN.stroke
      });
    }

    data.slot_ids.forEach((slotId, slotIndex) => {
      const state = snapshot.states[slotIndex];
      const node = slotNodes.get(slotId);
      if (node) {
        node.dataset.state = state;
        node.className = `demo-slot ${state.toLowerCase()}`;
        node.querySelector('span').textContent = labels[state];
      }
    });
    if (countNodes.occupied) countNodes.occupied.textContent = snapshot.counts.occupied;
    if (countNodes.empty) countNodes.empty.textContent = snapshot.counts.empty;
    if (countNodes.unknown) countNodes.unknown.textContent = snapshot.counts.unknown;
    drawOverlay(snapshot);
    syncStatus.textContent = `Đồng bộ khung ${snapshot.frame} · ${snapshot.counts.occupied + snapshot.counts.empty + snapshot.counts.unknown}/24 ô`;
  }

  function stopFrameLoop() {
    if (animationFrame !== null) cancelAnimationFrame(animationFrame);
    animationFrame = null;
  }

  function frameLoop() {
    render(video.currentTime);
    if (!video.paused && !video.ended) {
      animationFrame = requestAnimationFrame(frameLoop);
    } else {
      animationFrame = null;
    }
  }

  function startFrameLoop() {
    stopFrameLoop();
    animationFrame = requestAnimationFrame(frameLoop);
  }

  async function loadStates() {
    try {
      if (root.dataset.detectionsUrl) {
        try {
          const detRes = await fetch(root.dataset.detectionsUrl, {
            headers: {'Accept': 'application/json'},
            credentials: 'same-origin',
            cache: 'no-store'
          });
          if (detRes.ok) {
            detectionsData = await detRes.json();
          }
        } catch (e) {
          console.warn('Could not load detections:', e);
        }
      }

      const response = await fetch(root.dataset.statesUrl, {
        headers: {'Accept': 'application/json'},
        credentials: 'same-origin',
        cache: 'no-store'
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Không thể đọc dữ liệu trạng thái.');

      data = body;
      visionJobId = body.vision_job_id || visionJobId;
      if (root.dataset.visionJobId && visionJobId !== root.dataset.visionJobId) {
        throw new Error('Timeline API không khớp job đang hiển thị. Hãy tải lại trang.');
      }

      hasCompatiblePolygons = body.has_compatible_polygons !== false && root.dataset.hasCompatiblePolygons !== 'false';

      if (hasCompatiblePolygons) {
        if (body.total_slots !== 24 || body.slot_ids.length !== 24 || !body.timeline.length) {
          throw new Error('Dữ liệu trạng thái không đủ 24 ô D01–D24.');
        }
        if (overlayCanvas && (
          body.polygons?.length !== 24 ||
          Number(body.coordinate_space?.width) !== 3840 ||
          Number(body.coordinate_space?.height) !== 2160
        )) {
          throw new Error('Dữ liệu Canvas thiếu 24 Polygon trong hệ tọa độ 3840×2160.');
        }
      }

      duration.textContent = formatTime(video.duration || body.duration_seconds);
      render(video.currentTime, true);
    } catch (error) {
      syncStatus.textContent = error.message;
      syncStatus.classList.add('text-danger');
    }
  }

  video.addEventListener('loadedmetadata', () => {
    duration.textContent = formatTime(video.duration);
    render(video.currentTime, true);
    drawOverlay();
  });
  video.addEventListener('play', startFrameLoop);
  video.addEventListener('pause', () => { stopFrameLoop(); render(video.currentTime, true); });
  video.addEventListener('timeupdate', () => render(video.currentTime));
  video.addEventListener('seeking', () => render(video.currentTime, true));
  video.addEventListener('seeked', () => render(video.currentTime, true));
  video.addEventListener('ended', () => { stopFrameLoop(); render(video.duration, true); });
  video.addEventListener('error', () => {
    const descriptions = {
      1: 'Quá trình tải video đã bị hủy.',
      2: 'Không tải được video từ máy chủ hoặc phiên đăng nhập đã hết hạn.',
      3: 'Browser không hỗ trợ codec video nguồn.',
      4: 'Browser không hỗ trợ codec video nguồn.'
    };
    const code = video.error?.code;
    syncStatus.textContent = descriptions[code] || `Không phát được video demo (mã lỗi ${code || 'không xác định'}).`;
    syncStatus.classList.add('text-danger');
  });
  restart.addEventListener('click', async () => {
    video.pause();
    video.currentTime = 0;
    render(0, true);
    try { await video.play(); } catch (_) { /* Browser may require a second user gesture. */ }
  });
  manual?.addEventListener('click', () => render(video.currentTime, true, true));
  fullscreenButton?.addEventListener('click', async () => {
    if (!overlayWrapper) return;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await overlayWrapper.requestFullscreen();
    } catch (_) {
      syncStatus.textContent = 'Trình duyệt không cho phép mở toàn màn hình.';
    }
  });
  autoToggle?.addEventListener('change', () => {
    if (autoToggle.checked) render(video.currentTime, true);
    else if (data) clearStatesForManualMode();
  });
  const resizeObserver = overlayWrapper && window.ResizeObserver
    ? new ResizeObserver(() => drawOverlay())
    : null;
  if (resizeObserver) resizeObserver.observe(overlayWrapper);
  window.addEventListener('resize', drawOverlay);
  document.addEventListener('fullscreenchange', () => requestAnimationFrame(drawOverlay));
  window.addEventListener('pagehide', () => {
    stopFrameLoop();
    resizeObserver?.disconnect();
  });

  loadStates();
})();
