/**
 * Chat tab – non-streaming POST, markdown rendering.
 */
function chatTab() {
  return {
    messages: [],        // {role, content, meta, reasoning, _showReasoning}
    inputText: '',
    systemPrompt: '',
    selectedModel: 'default',
    modelAliases: [],
    sending: false,
    statusText: '',
    statusClass: '',
    _history: [],        // raw message list sent to API

    async init() {
      document.addEventListener('tab-activated', (e) => {
        if (e.detail === 'chat') this.scrollBottom();
      });
      // Listen for model selection from Models tab
      document.addEventListener('switch-to-chat', (e) => {
        this.selectedModel = e.detail;
        document.dispatchEvent(new CustomEvent('tab-activated-request', { detail: 'chat' }));
      });
      await this.loadAliases();
    },

    async loadAliases() {
      try {
        const r = await fetch('/ui/api/config');
        const cfg = await r.json();
        const aliases = cfg.model_aliases || {};
        this.modelAliases = Object.keys(aliases);
      } catch {}
    },

    renderMsg(msg) {
      if (!msg.content) return '';
      if (msg.role === 'user') return escHtml(msg.content);
      try {
        return marked.parse(msg.content);
      } catch {
        return escHtml(msg.content);
      }
    },

    async send() {
      const text = this.inputText.trim();
      if (!text || this.sending) return;

      this.inputText = '';
      this.sending = true;
      this.statusText = 'Sending…';
      this.statusClass = 'sending';

      // Add user message
      this.messages.push({ role: 'user', content: text });
      this._history.push({ role: 'user', content: text });
      this.scrollBottom();

      const startTime = Date.now();

      try {
        const apiMessages = [];
        if (this.systemPrompt.trim()) {
          apiMessages.push({ role: 'system', content: this.systemPrompt.trim() });
        }
        apiMessages.push(...this._history);

        const r = await fetch('/ui/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model: this.selectedModel,
            messages: apiMessages,
            stream: false,
          }),
        });

        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

        if (!r.ok) {
          const err = await r.json().catch(() => ({ detail: r.statusText }));
          throw new Error(err.detail || r.statusText);
        }

        const data = await r.json();
        const choice = data.choices?.[0];
        const content = choice?.message?.content || '';
        const reasoning = choice?.message?.reasoning_content || '';
        const model = data.model || '';
        const usage = data.usage || {};
        const worker = data._worker || '';
        const kudos = data._kudos || '';

        const meta = [
          elapsed + 's',
          model && `model: ${model}`,
          worker && `worker: ${worker}`,
          kudos && `kudos: ${kudos}`,
          usage.total_tokens && `tokens: ${usage.total_tokens}`,
        ].filter(Boolean).join(' · ');

        this.messages.push({ role: 'assistant', content, reasoning, meta, _showReasoning: false });
        this._history.push({ role: 'assistant', content });

        this.statusText = `Done in ${elapsed}s`;
        this.statusClass = 'done';
        this.scrollBottom();
      } catch (e) {
        this.messages.push({ role: 'assistant', content: '❌ ' + String(e), meta: '' });
        this.statusText = 'Error: ' + String(e);
        this.statusClass = 'error';
        this.scrollBottom();
      } finally {
        this.sending = false;
      }
    },

    clearChat() {
      this.messages = [];
      this._history = [];
      this.statusText = '';
      this.statusClass = '';
    },

    scrollBottom() {
      this.$nextTick(() => {
        const el = this.$refs.msgContainer;
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    onKeydown(e) {
      // Ctrl+Enter to send
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        this.send();
      }
    },
  };
}

function escHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>');
}
