'use strict';

(() => {
  const root = document.querySelector('[data-chatbot]');
  if (!root) return;
  const form = root.querySelector('[data-chatbot-form]');
  const input = form.querySelector('[name="message"]');
  const submit = form.querySelector('button[type="submit"]');
  const historyNode = root.querySelector('[data-chatbot-history]');
  const loading = root.querySelector('[data-chatbot-loading]');
  const clearBtn = root.querySelector('[data-chatbot-clear]');
  const confirmDialog = root.querySelector('[data-chatbot-clear-dialog]');
  const cancelBtn = confirmDialog ? confirmDialog.querySelector('[data-chatbot-cancel]') : null;
  const confirmBtn = confirmDialog ? confirmDialog.querySelector('[data-chatbot-confirm]') : null;

  // Account-isolated storage key
  const userId = (root.dataset.userId || '').trim();
  const username = (root.dataset.username || '').trim().toLowerCase();
  const userRole = (root.dataset.userRole || '').trim().toUpperCase();
  const userLabel = userRole === 'CUSTOMER' ? 'Khách hàng' : (userRole === 'STAFF' ? 'Nhân viên' : 'Quản lý');
  const storageKey = userId && username
    ? `parking_ai_chat_u${userId}_${username}_v1`
    : 'parking_ai_chat_anonymous_v1';
  let history = [];

  try {
    const stored = JSON.parse(localStorage.getItem(storageKey) || '[]');
    if (Array.isArray(stored) && stored.length > 0) {
      history = stored.slice(-40);
    }
  } catch (_) {
    history = [];
  }

  function persist() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(history.slice(-40)));
    } catch (_) {
      /* storage full or disabled in private browsing */
    }
  }

  function extractContextWindow() {
    // Only send the last 6 messages (3-4 recent turns) to avoid bloated payloads
    const context = [];
    const candidates = history.slice(-6);
    for (const item of candidates) {
      if (item && item.role && item.text && typeof item.text === 'string') {
        context.push({
          role: item.role === 'assistant' ? 'assistant' : 'user',
          text: item.text.slice(0, 500),
          intent: item.intent || undefined
        });
      }
    }
    return context;
  }

  function messageNode(item) {
    const article = document.createElement('article');
    article.className = `chatbot-message ${item.role}`;
    const label = document.createElement('small');
    label.textContent = item.role === 'user' ? userLabel : 'Parking AI';
    const body = document.createElement('p');
    body.textContent = item.text;
    article.append(label, body);
    if (item.intent) {
      const meta = document.createElement('span');
      meta.className = 'chatbot-meta';
      let sourceLabel = 'Fallback an toàn';
      if (item.classificationSource === 'qwen') {
        sourceLabel = 'Qwen NLU';
      } else if (item.classificationSource === 'gemini') {
        sourceLabel = 'Gemini NLU';
      } else if (item.classificationSource === 'gemini_context') {
        sourceLabel = 'Gemini Context';
      }
      meta.textContent = `${item.intent} · ${sourceLabel}`;
      article.append(meta);
    }
    return article;
  }

  function renderHistory() {
    historyNode.replaceChildren();
    if (!history.length) {
      const empty = document.createElement('div');
      empty.className = 'chatbot-empty';
      empty.textContent = 'Nhập câu hỏi tự nhiên về dữ liệu và hoạt động của bãi xe.';
      historyNode.append(empty);
      return;
    }
    history.forEach(item => historyNode.append(messageNode(item)));
    historyNode.scrollTop = historyNode.scrollHeight;
  }

  function add(item) {
    history.push(item);
    history = history.slice(-40);
    persist();
    renderHistory();
  }

  async function ask(message) {
    const historyWindow = extractContextWindow();
    add({role: 'user', text: message});
    submit.disabled = true;
    input.disabled = true;
    loading.hidden = false;
    try {
      const result = await api(root.dataset.chatbotUrl, {message, history: historyWindow}, 'POST', 30000);
      add({
        role: 'assistant',
        text: result.answer,
        intent: result.intent,
        aiUsed: Boolean(result.ai_used),
        classificationSource: result.classification_source,
        answerSource: result.answer_source,
        model: result.model
      });
    } catch (error) {
      add({role: 'assistant', text: error.message, intent: 'ERROR', aiUsed: false});
    } finally {
      submit.disabled = false;
      input.disabled = false;
      loading.hidden = true;
      input.focus();
    }
  }

  form.addEventListener('submit', event => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || submit.disabled) return;
    input.value = '';
    ask(message);
  });

  function doClearHistory() {
    history = [];
    try {
      localStorage.removeItem(storageKey);
    } catch (_) {}
    renderHistory();
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      if (confirmDialog && typeof confirmDialog.showModal === 'function') {
        confirmDialog.showModal();
      } else {
        const confirmed = window.confirm(
          'Bạn có chắc chắn muốn xóa toàn bộ lịch sử trò chuyện của tài khoản này không? Thao tác này không thể hoàn tác.'
        );
        if (confirmed) {
          doClearHistory();
        }
      }
    });
  }

  if (cancelBtn) {
    cancelBtn.addEventListener('click', () => {
      if (confirmDialog && typeof confirmDialog.close === 'function') {
        confirmDialog.close();
      }
    });
  }

  if (confirmBtn) {
    confirmBtn.addEventListener('click', () => {
      if (confirmDialog && typeof confirmDialog.close === 'function') {
        confirmDialog.close();
      }
      doClearHistory();
    });
  }

  renderHistory();
})();
