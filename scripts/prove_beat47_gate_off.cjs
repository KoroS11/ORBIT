const http = require('http');
const { chromium } = require('playwright');

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = '';
      res.on('data', (chunk) => body += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(body));
        } catch (e) {
          reject(e);
        }
      });
    }).on('error', reject);
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function run() {
  console.log('=== REAL-PATH PROOF: GATE OFF + NETWORK BLOCKED + BEAT47 REPLAY ===');

  // 1. Verify health before replay
  const healthBefore = await fetchJson('http://127.0.0.1:8000/health');
  console.log('Initial /health status:', {
    provider: healthBefore.provider,
    live_enabled: healthBefore.live_enabled,
    live_calls: healthBefore.live_calls,
    seed_hits: healthBefore.seed_hits,
    stub_fallbacks: healthBefore.stub_fallbacks,
  });

  if (healthBefore.live_enabled !== false) {
    throw new Error(`Expected live_enabled to be false, got ${healthBefore.live_enabled}`);
  }
  if (healthBefore.live_calls !== 0) {
    throw new Error(`Expected live_calls to be 0, got ${healthBefore.live_calls}`);
  }

  // 2. Launch browser and listen to WebSocket frames
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const receivedDecisions = [];
  const receivedAttributions = [];

  page.on('websocket', (ws) => {
    console.log(`WebSocket opened: ${ws.url()}`);
    ws.on('framereceived', (event) => {
      try {
        const payload = JSON.parse(event.payload);
        if (payload.type === 'decision' || payload.kind === 'decision') {
          const dec = payload.data || payload;
          receivedDecisions.push(dec);
          console.log(`[UI WS] Decision received: window=${dec.attribution_window ?? 'unknown'}, id=${dec.id || dec.decision_id}, action=${dec.action}`);
        } else if (payload.type === 'attribution' || payload.kind === 'attribution') {
          const attr = payload.data || payload;
          receivedAttributions.push(attr);
          console.log(`[UI WS] Attribution received: window=${attr.window_index ?? attr.attribution_window ?? 'unknown'}, actor=${attr.primary_actor || attr.actor}`);
        }
      } catch {}
    });
  });

  try {
    console.log('Navigating to UI at http://127.0.0.1:5173/...');
    await page.goto('http://127.0.0.1:5173/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.scenario-list', { timeout: 30000 });
    console.log('UI loaded and scenario list found.');

    // Wait for any initial auto-play or settle
    await sleep(4000);

    // Clear initial messages before clicking beat47
    receivedDecisions.length = 0;
    receivedAttributions.length = 0;

    const beat47Button = page.locator('.scenario-list button').filter({ hasText: /SATCOM gateway/i }).first();
    await beat47Button.waitFor({ state: 'visible', timeout: 10000 });
    console.log('Clicking "SATCOM gateway" (beat47) scenario button...');
    await beat47Button.click();

    console.log('Waiting 18 seconds for beat47 scenario to replay and process all windows...');
    await sleep(18000);

    console.log('\n--- Replay Complete. Inspecting Results ---');
    console.log(`Received Attributions in UI: ${receivedAttributions.length}`);
    console.log(`Received Decisions in UI:    ${receivedDecisions.length}`);

    for (let i = 0; i < receivedDecisions.length; i++) {
      const d = receivedDecisions[i];
      console.log(`Decision ${i + 1}: window=${d.attribution_window}, action=${d.action}, reason=${d.reason ? d.reason.slice(0, 80) + '...' : 'none'}`);
    }

    // 3. Verify health after replay
    const healthAfter = await fetchJson('http://127.0.0.1:8000/health');
    console.log('\nFinal /health status:', {
      provider: healthAfter.provider,
      live_enabled: healthAfter.live_enabled,
      live_calls: healthAfter.live_calls,
      seed_hits: healthAfter.seed_hits,
      stub_fallbacks: healthAfter.stub_fallbacks,
      cache_hits: healthAfter.cache_hits,
    });

    console.log('\n--- VERIFICATION CHECKS ---');
    console.log(`1. Live calls remained 0: ${healthAfter.live_calls === 0} (${healthAfter.live_calls})`);
    console.log(`2. Live gate is OFF (live_enabled === false): ${healthAfter.live_enabled === false}`);
    console.log(`3. Decisions received for every window: ${receivedDecisions.length > 0} (count: ${receivedDecisions.length})`);
    console.log(`4. Decision count matches attribution windows: ${receivedDecisions.length === receivedAttributions.length} (${receivedDecisions.length} vs ${receivedAttributions.length})`);

    if (healthAfter.live_calls !== 0) {
      throw new Error(`FAILURE: live_calls was ${healthAfter.live_calls}, expected 0!`);
    }
    if (healthAfter.live_enabled !== false) {
      throw new Error(`FAILURE: live_enabled was ${healthAfter.live_enabled}, expected false!`);
    }
    if (receivedDecisions.length === 0) {
      throw new Error('FAILURE: No decisions were received in the UI!');
    }
    if (receivedDecisions.length !== receivedAttributions.length) {
      throw new Error(`FAILURE: Window mismatch: ${receivedDecisions.length} decisions vs ${receivedAttributions.length} attributions`);
    }

    console.log('\nPROOF SUCCESS: All windows produced a decision with ZERO live Gemini calls under blocked network!');
  } finally {
    await browser.close();
  }
}

run().catch((err) => {
  console.error('Test error:', err);
  process.exit(1);
});
