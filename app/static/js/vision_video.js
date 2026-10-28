'use strict';

(() => {
  const root = document.querySelector('[data-vision-video-workflow]');
  if (!root) return;

  const fileInput = document.querySelector('#parking-file');
  const analyzeButton = document.querySelector('#parking-analyze');
  const cameraButton = document.querySelector('#parking-camera');
  const stopButton = document.querySelector('#parking-stop');
  const autoToggle = document.querySelector('#parking-auto');
  const cameraVideo = document.querySelector('#parking-video');
  const processedVideo = document.querySelector('#demo-ai-video');
  const imagePreview = document.querySelector('#parking-image');
  const message = document.querySelector('#parking-message');
  const progress = document.querySelector('#vision-progress');
  const progressBar = document.querySelector('#vision-progress-bar');
  const progressLabel = document.querySelector('#vision-progress-label');
  const progressValue = document.querySelector('#vision-progress-value');
  let chosen = null;
  let previewUrl = null;
  let busy = false;
  let pollingTimer = null;

  function canAnalyzeSelection() {
    if (isVideoFile(chosen)) return root.dataset.ready === 'true';
    if (chosen || cameraVideo.srcObject) return root.dataset.legacyReady === 'true';
    return Boolean(processedVideo);
  }

  function syncAnalyzeButton() {
    analyzeButton.disabled = busy || !canAnalyzeSelection();
  }

  function isVideoFile(file) {
    return Boolean(file) && (
      file.type.startsWith('video/') || /\.(mp4|webm|mov|m4v)$/i.test(file.name)
    );
  }

  function say(text, isError = false) {
    message.textContent = text;
    message.hidden = false;
    message.classList.toggle('vision-error', isError);
  }

  function setProgress(job) {
    progress.hidden = false;
    const total = Math.max(1, Number(job.total_frames) || 1);
    const frames = Math.max(0, Number(job.progress_frames) || 0);
    let percent = Math.min(100, Math.round(frames * 100 / total));
    let label = `Đang phân tích YOLO + Polygon: ${frames}/${total} frame`;
    if (job.status === 'queued') {
      percent = 0;
      label = 'Đang chờ GPU…';
    }
    progressBar.value = percent;
    progressValue.textContent = `${percent}%`;
    progressLabel.textContent = label;
  }

  async function pollJob() {
    clearTimeout(pollingTimer);
    try {
      const body = await api(root.dataset.statusUrl, undefined, 'GET', 30000);
      const job = body.job;
      setProgress(job);
      if (job.status === 'complete') {
        say('Đã phân tích xong. Đang mở video gốc với Canvas trạng thái…');
        window.location.reload();
        return;
      }
      if (job.status === 'failed') {
        progress.hidden = true;
        syncAnalyzeButton();
        say(job.error || 'Xử lý video thất bại.', true);
        return;
      }
      pollingTimer = setTimeout(pollJob, 1200);
    } catch (error) {
      progress.hidden = true;
      syncAnalyzeButton();
      say(error.message, true);
    }
  }

  async function uploadVideo(file) {
    if (busy) return;
    busy = true;
    analyzeButton.disabled = true;
    const form = new FormData();
    form.append('video', file, file.name);
    say('Đang tải video lên máy chủ…');
    progress.hidden = false;
    progressBar.value = 0;
    progressLabel.textContent = 'Đang tải video…';
    progressValue.textContent = '0%';
    try {
      const body = await api(root.dataset.uploadUrl, form, 'POST', 600000);
      setProgress(body.job);
      chosen = null;
      fileInput.value = '';
      pollingTimer = setTimeout(pollJob, 300);
    } catch (error) {
      progress.hidden = true;
      analyzeButton.disabled = false;
      say(error.message, true);
    } finally {
      busy = false;
    }
  }

  async function analyzeLegacyFrame() {
    if (busy) return;
    if (root.dataset.legacyReady !== 'true') {
      say('CNN ảnh/camera 20 ROI A/B đã bị khóa trên layout D01–D24 để không ghi sai trạng thái.', true);
      return;
    }
    busy = true;
    analyzeButton.disabled = true;
    try {
      const blob = cameraVideo.srcObject ? await frameBlob(cameraVideo) : chosen;
      if (!blob) throw new Error('Chọn ảnh hoặc mở camera trước.');
      const form = new FormData();
      form.append('image', blob, 'parking.jpg');
      say('Đang nhận diện ảnh bằng pipeline CNN 20 ROI cũ…');
      const result = await api('/api/vision/parking', form);
      const overlay = document.querySelector('#parking-overlay');
      overlay.src = 'data:image/jpeg;base64,' + result.overlay;
      overlay.hidden = false;
      const grid = textElement('div', '', 'prediction-grid');
      result.predictions.forEach(item => {
        const card = textElement('div', '', `prediction-item ${item.accepted ? '' : 'warn'}`);
        card.title = item.reason;
        card.append(
          textElement('strong', item.slot),
          textElement('div', `${item.state === 'EMPTY' ? 'Trống' : 'Có xe'} · ${(item.confidence * 100).toFixed(1)}%`),
          textElement('div', item.accepted ? 'Khớp dữ liệu' : 'Cần kiểm tra / bỏ qua')
        );
        grid.append(card);
      });
      document.querySelector('#parking-predictions').replaceChildren(grid);
      say('Đã nhận diện ảnh. Kết quả độ tin cậy thấp không thay đổi trạng thái đã lưu.');
    } catch (error) {
      autoToggle.checked = false;
      say(error.message, true);
    } finally {
      busy = false;
      syncAnalyzeButton();
    }
  }

  fileInput.addEventListener('change', event => {
    chosen = event.target.files[0] || null;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    imagePreview.hidden = true;
    if (!chosen) {
      syncAnalyzeButton();
      return;
    }
    if (isVideoFile(chosen)) {
      say(`Đã chọn ${chosen.name}. Bấm “Phân tích bằng AI” để chạy inference YOLO + Polygon.`);
    } else {
      previewUrl = URL.createObjectURL(chosen);
      imagePreview.src = previewUrl;
      imagePreview.hidden = false;
      if (root.dataset.legacyReady === 'true') {
        say('Bấm “Nhận diện ảnh” để dùng pipeline CNN 20 ROI cũ.');
      } else {
        say('CNN ảnh 20 ROI A/B không tương thích layout D01–D24 nên chức năng ghi trạng thái đã bị khóa.', true);
      }
    }
    syncAnalyzeButton();
  });

  analyzeButton.addEventListener('click', async () => {
    if (isVideoFile(chosen)) {
      await uploadVideo(chosen);
    } else if (chosen || cameraVideo.srcObject) {
      await analyzeLegacyFrame();
    } else if (processedVideo) {
      document.querySelector('[data-demo-manual]')?.click();
      say(`Đã cập nhật trạng thái tại ${processedVideo.currentTime.toFixed(2)} giây.`);
    } else {
      say('Hãy chọn video, ảnh hoặc mở camera trước.', true);
    }
  });

  cameraButton.addEventListener('click', async () => {
    if (root.dataset.legacyReady !== 'true') {
      say('CNN camera 20 ROI A/B đã bị khóa trên layout D01–D24.', true);
      return;
    }
    try {
      chosen = null;
      fileInput.value = '';
      imagePreview.hidden = true;
      await openCamera(cameraVideo);
      say(`Camera ${cameraVideo.videoWidth} × ${cameraVideo.videoHeight}. Nhận diện camera tiếp tục dùng CNN 20 ROI cũ.`);
    } catch (error) {
      say('Không mở được camera: ' + error.message, true);
    }
  });

  stopButton.addEventListener('click', () => {
    stopCamera(cameraVideo);
    cameraVideo.hidden = true;
    processedVideo?.pause();
    autoToggle.checked = false;
    say('Đã dừng camera / video.');
  });

  const cameraTimer = setInterval(() => {
    if (autoToggle.checked && cameraVideo.srcObject && !cameraVideo.paused) {
      analyzeLegacyFrame();
    }
  }, 3000);

  if (['queued', 'processing'].includes(root.dataset.jobStatus)) {
    pollingTimer = setTimeout(pollJob, 200);
  } else if (root.dataset.jobStatus === 'failed') {
    pollJob();
  }

  syncAnalyzeButton();

  window.addEventListener('pagehide', () => {
    clearInterval(cameraTimer);
    clearTimeout(pollingTimer);
    stopCamera(cameraVideo);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  });
})();
