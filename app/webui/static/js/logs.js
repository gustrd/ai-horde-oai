/**
 * Logs tab – real-time log table, detail modal, active queue.
 */
function logsTab() {
  return {
    entries: [],
    active: [],
    detail: null,
    activeDetail: null,

    async init() {
      document.addEventListener('tab-activated', (e) => {
        if (e.detail === 'logs') this.load();
      });

      // Real-time updates via WebSocket – reload everything to avoid race duplicates
      wsClient.on('log_entry', () => {
        this.load();
      });
      wsClient.on('active_requests', (data) => {
        this.active = Array.isArray(data) ? data : [];
        // Sync activeDetail if currently viewing one
        if (this.activeDetail) {
          const updated = this.active.find(r => r.job_id === this.activeDetail.job_id);
          this.activeDetail = updated || null;
        }
      });

      await this.load();
    },

    async load() {
      try {
        const r = await fetch('/ui/api/logs');
        this.entries = await r.json();
      } catch (e) {
        console.error('Logs load error', e);
      }
    },

    async toggleCheck(idx) {
      try {
        const r = await fetch(`/ui/api/logs/${idx}/check`, { method: 'PATCH' });
        const data = await r.json();
        this.entries[idx] = { ...this.entries[idx], checked: data.checked };
      } catch {}
    },

    async clearLogs() {
      if (!confirm('Clear all logs?')) return;
      await fetch('/ui/api/logs', { method: 'DELETE' });
      this.entries = [];
    },

    openDetail(idx) {
      this.detail = this.entries[idx] || null;
    },

    openActiveDetail(req) {
      this.activeDetail = req || null;
    },

    cancelJob(jobId) {
      wsClient.send({ type: 'cancel_job', job_id: jobId });
      if (this.activeDetail && this.activeDetail.job_id === jobId) {
        this.activeDetail = null;
      }
    },

    fmtTime(ts) {
      if (!ts) return '—';
      try { return new Date(ts).toLocaleTimeString(); } catch { return ts; }
    },

    statusClass(status) {
      if (typeof status === 'number') {
        if (status >= 500) return 'error';
        if (status >= 400) return 'warn';
        if (status >= 200) return 'ok';
      }
      if (typeof status === 'string' && status.toLowerCase().includes('error')) return 'error';
      return '';
    },

    preview(e) {
      if (e.response_text) return e.response_text.slice(0, 80);
      if (e.error) return '⚠ ' + e.error.slice(0, 80);
      if (e.messages && e.messages.length) {
        const last = e.messages[e.messages.length - 1];
        const c = typeof last.content === 'string' ? last.content : JSON.stringify(last.content);
        return c.slice(0, 80);
      }
      if (e.prompt) return e.prompt.slice(0, 80);
      return '—';
    },
  };
}
