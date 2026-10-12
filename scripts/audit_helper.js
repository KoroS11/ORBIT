import fs from 'fs';
import path from 'path';

export class AuditRecorder {
  constructor(auditDir = 'frontend/audit') {
    this.auditDir = auditDir;
    this.screenshotsDir = path.join(auditDir, 'screenshots');
    this.logFile = path.join(auditDir, 'audit_log.json');
    this.events = [];
    this.interactionIndex = 0;

    if (!fs.existsSync(this.screenshotsDir)) {
      fs.mkdirSync(this.screenshotsDir, { recursive: true });
    }
  }

  attachToPage(page) {
    this.page = page;

    // 1. Console errors and warnings
    page.on('console', (msg) => {
      const type = msg.type();
      if (type === 'error' || type === 'warning') {
        this.logEvent('console', {
          level: type,
          text: msg.text(),
          location: msg.location(),
        });
      }
    });

    // 2. Uncaught page exceptions
    page.on('pageerror', (err) => {
      this.logEvent('pageerror', {
        message: err.message,
        stack: err.stack,
      });
    });

    // 3. Failed network requests
    page.on('requestfailed', (req) => {
      this.logEvent('requestfailed', {
        url: req.url(),
        method: req.method(),
        failure: req.failure()?.errorText || 'Unknown failure',
      });
    });

    // 4. HTTP 4xx / 5xx responses
    page.on('response', (res) => {
      const status = res.status();
      if (status >= 400) {
        this.logEvent('http_error', {
          url: res.url(),
          status,
          statusText: res.statusText(),
        });
      }
    });

    // 5. WebSocket instrumentation in the browser context
    page.addInitScript(() => {
      const OrigWebSocket = window.WebSocket;
      window.__ORBIT_WS_LOGS = [];
      window.WebSocket = function (url, protocols) {
        const ws = new OrigWebSocket(url, protocols);
        window.__ORBIT_WS_LOGS.push({ event: 'open_attempt', url: String(url), ts: Date.now() });

        ws.addEventListener('open', () => {
          window.__ORBIT_WS_LOGS.push({ event: 'open', url: String(url), ts: Date.now() });
        });
        ws.addEventListener('close', (e) => {
          window.__ORBIT_WS_LOGS.push({ event: 'close', code: e.code, reason: e.reason, ts: Date.now() });
        });
        ws.addEventListener('error', () => {
          window.__ORBIT_WS_LOGS.push({ event: 'error', url: String(url), ts: Date.now() });
        });
        ws.addEventListener('message', (e) => {
          try {
            JSON.parse(e.data);
          } catch (err) {
            window.__ORBIT_WS_LOGS.push({ event: 'unparsed_message', data: String(e.data).slice(0, 200), ts: Date.now() });
          }
        });
        return ws;
      };
    });
  }

  logEvent(type, payload) {
    const entry = {
      timestamp: new Date().toISOString(),
      interactionIndex: this.interactionIndex,
      type,
      ...payload,
    };
    this.events.push(entry);
    this.flush();
  }

  async captureInteraction(label) {
    this.interactionIndex += 1;
    const filename = `${String(this.interactionIndex).padStart(3, '0')}_${label.replace(/[^a-zA-Z0-9_-]/g, '_')}.png`;
    const screenshotPath = path.join(this.screenshotsDir, filename);

    // Capture screenshot
    try {
      await this.page.screenshot({ path: screenshotPath, fullPage: false });
    } catch (err) {
      console.warn(`[AuditRecorder] Screenshot failed for ${label}:`, err.message);
    }

    // Sanity check: Ensure view is not blank or black
    const sanity = await this.page.evaluate(() => {
      const root = document.getElementById('root');
      if (!root) return { ok: false, reason: 'No #root element found' };

      const rect = root.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) {
        return { ok: false, reason: `Root element has zero dimension (${rect.width}x${rect.height})` };
      }

      if (root.children.length === 0) {
        return { ok: false, reason: 'Root element has no children' };
      }

      // Check for ErrorBoundary display
      const errorBoundary = document.querySelector('.error-boundary-panel');
      if (errorBoundary) {
        return { ok: false, reason: 'ErrorBoundary panel is active', isErrorPanel: true };
      }

      // Check canvas if present
      const canvas = document.querySelector('canvas');
      let canvasDetails = null;
      if (canvas) {
        const cRect = canvas.getBoundingClientRect();
        canvasDetails = { width: cRect.width, height: cRect.height };
      }

      return {
        ok: true,
        rootWidth: rect.width,
        rootHeight: rect.height,
        childrenCount: root.children.length,
        canvas: canvasDetails,
      };
    }).catch((err) => ({ ok: false, reason: err.message }));

    // Harvest browser WS logs
    const wsLogs = await this.page.evaluate(() => {
      const logs = window.__ORBIT_WS_LOGS || [];
      window.__ORBIT_WS_LOGS = [];
      return logs;
    }).catch(() => []);

    for (const w of wsLogs) {
      this.logEvent(`ws_${w.event}`, w);
    }

    this.logEvent('interaction', {
      label,
      screenshot: filename,
      sanity,
    });

    return sanity;
  }

  flush() {
    try {
      fs.writeFileSync(this.logFile, JSON.stringify(this.events, null, 2), 'utf-8');
    } catch (err) {
      console.error('[AuditRecorder] Error writing log file:', err);
    }
  }

  getSummary() {
    const counts = {};
    for (const ev of this.events) {
      counts[ev.type] = (counts[ev.type] || 0) + 1;
    }
    return {
      totalEvents: this.events.length,
      counts,
      errors: this.events.filter((e) => ['pageerror', 'http_error', 'requestfailed'].includes(e.type)),
    };
  }
}
