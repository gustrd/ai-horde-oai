/**
 * Config tab – form + save.
 */
function configTab() {
  return {
    cfg: null,
    status: null,   // { type: 'ok'|'error', msg }
    loading: false,

    async init() {
      document.addEventListener('tab-activated', (e) => {
        if (e.detail === 'config') this.load();
      });
      await this.load();
    },

    async load() {
      try {
        const r = await fetch('/ui/api/config');
        this.cfg = await r.json();
        // Normalize list fields to newline-separated strings for textarea
        for (const k of ['cors_origins','model_whitelist','model_blocklist','worker_whitelist','worker_blocklist']) {
          if (Array.isArray(this.cfg[k])) {
            this.cfg[k] = this.cfg[k].join('\n');
          }
        }
        // Normalize model_aliases: dict -> "key: value\n..."
        if (this.cfg.model_aliases && typeof this.cfg.model_aliases === 'object') {
          this.cfg.model_aliases = Object.entries(this.cfg.model_aliases)
            .map(([k,v]) => `${k}: ${v}`)
            .join('\n');
        }
      } catch (e) {
        this.status = { type: 'error', msg: 'Failed to load config: ' + e };
      }
    },

    async save() {
      this.loading = true;
      this.status = null;
      try {
        const payload = { ...this.cfg };
        // Convert newline-separated strings back to arrays
        for (const k of ['cors_origins','model_whitelist','model_blocklist','worker_whitelist','worker_blocklist']) {
          if (typeof payload[k] === 'string') {
            payload[k] = payload[k].split('\n').map(s => s.trim()).filter(Boolean);
          }
        }
        // Convert model_aliases back to dict
        if (typeof payload.model_aliases === 'string') {
          const dict = {};
          payload.model_aliases.split('\n').forEach(line => {
            const idx = line.indexOf(':');
            if (idx > 0) {
              dict[line.slice(0,idx).trim()] = line.slice(idx+1).trim();
            }
          });
          payload.model_aliases = dict;
        }
        // Convert numeric fields
        for (const k of ['port','max_concurrent_requests','model_min_context','model_min_max_length',
                         'default_max_tokens','model_cache_ttl','stream_stall_timeout',
                         'global_min_request_delay']) {
          if (payload[k] !== undefined) payload[k] = Number(payload[k]);
        }
        // Nested retry
        if (payload.retry) {
          for (const k of Object.keys(payload.retry)) {
            payload.retry[k] = Number(payload.retry[k]);
          }
          payload.retry.broaden_on_retry = Boolean(this.cfg.retry?.broaden_on_retry);
        }
        payload.trusted_workers = Boolean(payload.trusted_workers);

        const r = await fetch('/ui/api/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({ detail: r.statusText }));
          throw new Error(err.detail || r.statusText);
        }
        this.status = { type: 'ok', msg: 'Saved.' };
      } catch (e) {
        this.status = { type: 'error', msg: String(e) };
      } finally {
        this.loading = false;
      }
    },

    onKeydown(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        this.save();
      }
    },

    // ── Template rendered inline via Alpine x-html ─────────────
    get html() {
      return ''; // not used – template is inline in index.html
    },
  };
}
