import { chromium } from 'playwright';
import { spawn } from 'child_process';
import http from 'http';
import { AuditRecorder } from './audit_helper.js';

function checkHttp(url) {
  return new Promise((resolve) => {
    http.get(url, (res) => {
      resolve(res.statusCode === 200);
    }).on('error', () => resolve(false));
  });
}

async function waitForHttp(url, timeoutMs = 25000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await checkHttp(url)) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

async function runAudit() {
  const recorder = new AuditRecorder();
  console.log('[AUDIT] Starting backend process in stub mode on port 8000...');
  const backend = spawn('.\\.venv\\Scripts\\python.exe', ['-m', 'uvicorn', 'orbit.api:app', '--host', '127.0.0.1', '--port', '8000'], {
    env: { ...process.env, ORBIT_LLM: 'stub' },
    stdio: 'pipe',
    shell: true,
  });

  console.log('[AUDIT] Starting Vite preview server on port 4173...');
  const preview = spawn('npx.cmd', ['vite', 'preview', '--host', '127.0.0.1', '--port', '4173', '--strictPort'], {
    stdio: 'pipe',
    shell: true,
  });

  const cleanup = () => {
    try { preview.kill(); } catch (e) {}
    try { backend.kill(); } catch (e) {}
  };

  process.on('exit', cleanup);
  process.on('SIGINT', cleanup);

  try {
    const previewUp = await waitForHttp('http://127.0.0.1:4173');
    const backendUp = await waitForHttp('http://127.0.0.1:8000/health');
    console.log(`[AUDIT] Preview up: ${previewUp}, Backend up: ${backendUp}`);
    if (!previewUp || !backendUp) throw new Error('Failed to start test servers');

    // Wait 5s for SBERT/sentence-transformers
    console.log('[AUDIT] Waiting 5s for backend sentence-transformers initialization...');
    await new Promise((r) => setTimeout(r, 5000));

    const browser = await chromium.launch({ headless: true });

    // ==========================================
    // TEST SUITE 1: BRIGADE COP & CORE CONTROLS
    // ==========================================
    console.log('\n=== SUITE 1: BRIGADE COP & CORE CONTROLS ===');
    const page = await browser.newPage();
    recorder.attachToPage(page);

    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto('http://127.0.0.1:4173/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    await recorder.captureInteraction('01_brigade_initial_load');

    // 1.1 Left Rail Toggle (SCENARIOS)
    console.log('[AUDIT] Testing Left Rail SCENARIOS toggle...');
    const leftTab = page.locator('.panel-tab--left-edge');
    if (await leftTab.isVisible()) {
      await leftTab.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('02_brigade_left_rail_collapsed');
      await leftTab.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('03_brigade_left_rail_expanded');
    }

    // 1.2 Bottom Rail Toggle (SIGNAL STREAM)
    console.log('[AUDIT] Testing Bottom Rail SIGNAL STREAM toggle...');
    const bottomTab = page.locator('.panel-tab--bottom-edge');
    if (await bottomTab.isVisible()) {
      await bottomTab.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('04_brigade_bottom_rail_collapsed');
      await bottomTab.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('05_brigade_bottom_rail_expanded');
    }

    // 1.3 Right Rail Toggle (COMMANDER PANEL)
    console.log('[AUDIT] Testing Right Rail COMMANDER PANEL toggle...');
    const rightTab = page.locator('.panel-tab--right-edge');
    if (await rightTab.isVisible()) {
      await rightTab.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('06_brigade_right_rail_collapsed');
      await rightTab.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('07_brigade_right_rail_expanded');
    }

    // 1.4 Map Stage: NAV / GLOBE toggle
    console.log('[AUDIT] Testing MapStage NAV/GLOBE toggles...');
    const navBtn = page.locator('button:has-text("NAV")');
    const globeBtn = page.locator('button:has-text("GLOBE")');
    if (await navBtn.isVisible()) {
      await navBtn.click();
      await page.waitForTimeout(1000);
      await recorder.captureInteraction('08_mapstage_nav_active');
    }

    // In NAV view (AorMap), test Basemap toggles
    const imageryBtn = page.locator('button:has-text("Imagery")');
    const mutedBtn = page.locator('button:has-text("Muted")');
    if (await imageryBtn.isVisible()) {
      await imageryBtn.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('09_aormap_imagery_basemap');
    }
    if (await mutedBtn.isVisible()) {
      await mutedBtn.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('10_aormap_muted_basemap');
    }

    // Switch back to GLOBE
    if (await globeBtn.isVisible()) {
      await globeBtn.click();
      await page.waitForTimeout(1000);
      await recorder.captureInteraction('11_mapstage_globe_active');
    }

    // Cesium layer buttons
    const realSatBtn = page.locator('button:has-text("Real Satellites"), button:has-text("Satellites (cached)")');
    if (await realSatBtn.isVisible()) {
      await realSatBtn.click();
      await page.waitForTimeout(1000);
      await recorder.captureInteraction('12_cesium_real_satellites');
    }

    // 1.5 EventFeed Tabs: RAW vs FLOW & AutoFocus
    console.log('[AUDIT] Testing EventFeed controls...');
    const rawTab = page.locator('button:has-text("RAW")');
    const flowTab = page.locator('button:has-text("FLOW")');
    if (await rawTab.isVisible()) {
      await rawTab.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('13_eventfeed_raw');
    }
    if (await flowTab.isVisible()) {
      await flowTab.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('14_eventfeed_flow');
    }
    const autoFocusBtn = page.locator('button[title*="auto-focus"], button:has-text("TRACK"), button:has-text("FREE")').first();
    if (await autoFocusBtn.isVisible()) {
      await autoFocusBtn.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('15_eventfeed_autofocus_toggled');
    }

    // 1.6 Scenario Selection in Rail
    console.log('[AUDIT] Testing Scenario Rail item click...');
    const scenarioItems = page.locator('.scenario-list__item');
    const count = await scenarioItems.count();
    console.log(`[AUDIT] Found ${count} scenario items in rail`);
    if (count > 1) {
      await scenarioItems.nth(1).click();
      await page.waitForTimeout(2000);
      await recorder.captureInteraction('16_scenario_selection_changed');
    }

    // 1.7 Accordions in Commander Stack
    console.log('[AUDIT] Testing Accordion sections...');
    const accordions = page.locator('.stack-section__trigger');
    const accCount = await accordions.count();
    console.log(`[AUDIT] Found ${accCount} accordions`);
    for (let i = 0; i < Math.min(accCount, 3); i++) {
      await accordions.nth(i).click();
      await page.waitForTimeout(300);
    }
    await recorder.captureInteraction('17_accordions_toggled');

    // 1.8 Stress Mode Controls
    console.log('[AUDIT] Testing Stress Mode controls...');
    const stressCheckbox = page.locator('.stress-mode input[type="checkbox"]').first();
    if (await stressCheckbox.isVisible()) {
      await stressCheckbox.click();
      await page.waitForTimeout(300);
      const applyBtn = page.locator('.stress-mode button:has-text("Apply")');
      if (await applyBtn.isVisible()) {
        await applyBtn.click();
        await page.waitForTimeout(500);
      }
      await recorder.captureInteraction('18_stress_mode_applied');
    }

    // 1.9 Navigation Link to Ops Console
    console.log('[AUDIT] Testing Navigation link Ops...');
    await page.click('a[href="/operator"]');
    await page.waitForTimeout(2000);
    await recorder.captureInteraction('19_navigated_to_ops');

    // ==========================================
    // TEST SUITE 2: OPS CONSOLE & DECISION RAIL
    // ==========================================
    console.log('\n=== SUITE 2: OPS CONSOLE & DECISION RAIL ===');
    // Ensure on /operator
    if (!page.url().includes('/operator')) {
      await page.goto('http://127.0.0.1:4173/operator', { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(2000);
    }

    // Trigger replay of beat1
    console.log('[AUDIT] Triggering Scenario Replay (beat1.jsonl)...');
    await fetch('http://127.0.0.1:8000/scenarios/beat1.jsonl/replay?speed=200', { method: 'POST' });
    await page.waitForTimeout(4000);
    await recorder.captureInteraction('20_ops_after_replay');

    // 2.1 ActionLog / System Actions tool card expansion
    console.log('[AUDIT] Testing ActionLog tool card expand/collapse...');
    const toolCards = page.locator('.tool-call-card__header');
    const toolCount = await toolCards.count();
    console.log(`[AUDIT] Found ${toolCount} tool cards in ActionLog`);
    if (toolCount > 0) {
      await toolCards.first().click();
      await page.waitForTimeout(400);
      await recorder.captureInteraction('21_tool_card_expanded');
      await toolCards.first().click();
      await page.waitForTimeout(400);
      await recorder.captureInteraction('22_tool_card_collapsed');
    }

    // 2.2 Decision Rail: Approve, Reject, View Trace, Double click
    console.log('[AUDIT] Testing Decision Rail Approve/Reject/Trace...');
    const approveBtn = page.locator('.decision-card__btn--approve').first();
    const rejectBtn = page.locator('.decision-card__btn--reject').first();
    const traceBtn = page.locator('.decision-card__btn--trace').first();

    if (await traceBtn.isVisible()) {
      await traceBtn.click();
      await page.waitForTimeout(500);
      await recorder.captureInteraction('23_view_trace_clicked');
    }

    if (await approveBtn.isVisible()) {
      // Test rapid click / double click
      console.log('[AUDIT] Testing double click on Approve...');
      await approveBtn.dblclick();
      await page.waitForTimeout(1000);
      await recorder.captureInteraction('24_decision_approved_double_click');

      // Test reconsider button
      const reconsiderBtn = page.locator('.decision-card__btn-reconsider').first();
      if (await reconsiderBtn.isVisible()) {
        await reconsiderBtn.click();
        await page.waitForTimeout(500);
        await recorder.captureInteraction('25_decision_reconsidered');
      }
    }

    // Test Reject
    if (await rejectBtn.isVisible()) {
      await rejectBtn.click();
      await page.waitForTimeout(1000);
      await recorder.captureInteraction('26_decision_rejected');
    }

    // 2.3 Reasoning panel clear button
    const clearReasoningBtn = page.locator('.reasoning-panel button:has-text("Clear")');
    if (await clearReasoningBtn.isVisible()) {
      await clearReasoningBtn.click();
      await page.waitForTimeout(400);
      await recorder.captureInteraction('27_reasoning_cleared');
    }

    // 2.4 Nav link back to Brigade
    console.log('[AUDIT] Testing Nav link to Brigade...');
    await page.click('a[href="/brigade"]');
    await page.waitForTimeout(2000);
    await recorder.captureInteraction('28_navigated_back_to_brigade');

    // ==========================================
    // TEST SUITE 3: RESPONSIVE & KEYBOARD AUDIT
    // ==========================================
    console.log('\n=== SUITE 3: RESPONSIVE & KEYBOARD AUDIT ===');
    // 3.1 Viewport 1366x768
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.waitForTimeout(1000);
    await recorder.captureInteraction('29_responsive_1366x768');

    // 3.2 Viewport 1024x768
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.waitForTimeout(1000);
    await recorder.captureInteraction('30_responsive_1024x768');

    // Restore 1920x1080
    await page.setViewportSize({ width: 1920, height: 1080 });

    // 3.3 Keyboard navigation: Tab, Enter, Space, Escape
    console.log('[AUDIT] Testing Keyboard Tab, Enter, Space...');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Space');
    await page.waitForTimeout(400);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
    await recorder.captureInteraction('31_keyboard_interaction');

    // ==========================================
    // TEST SUITE 4: ERROR & EDGE-CASE SCENARIOS
    // ==========================================
    console.log('\n=== SUITE 4: ERROR & EDGE-CASE SCENARIOS ===');

    // 4.1 Malformed WS payload resilience
    console.log('[AUDIT] Injecting malformed WebSocket payloads into event store...');
    await page.evaluate(() => {
      // Simulate synthetic corrupt events
      window.dispatchEvent(new MessageEvent('message', { data: '{"garbage": true, null: null}' }));
      window.dispatchEvent(new MessageEvent('message', { data: 'invalid json {' }));
    });
    await page.waitForTimeout(500);
    await recorder.captureInteraction('32_malformed_ws_payload_handled');

    // 4.2 4xx/5xx REST failure resilience
    console.log('[AUDIT] Calling invalid review endpoint for non-existent decision...');
    const errRes = await fetch('http://127.0.0.1:8000/decisions/non-existent-id-404/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'approved' }),
    }).catch((e) => ({ status: 500, error: e.message }));
    console.log(`[AUDIT] 404/Error response handled: status ${errRes.status}`);
    await recorder.captureInteraction('33_rest_error_handled');

    // 4.3 Page reload with persisted review state
    console.log('[AUDIT] Reloading page to test state persistence and restoration...');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    await recorder.captureInteraction('34_reload_persisted_state');

    await browser.close();
    console.log('\n[AUDIT] Complete test run finished successfully.');

    const summary = recorder.getSummary();
    console.log('\n[AUDIT] === EVENT SUMMARY ===');
    console.log(`Total events logged: ${summary.totalEvents}`);
    console.log('Counts by event type:', summary.counts);
    console.log(`Errors count: ${summary.errors.length}`);
    if (summary.errors.length > 0) {
      console.log('Captured errors:');
      for (const err of summary.errors) {
        console.log(`  - [${err.type}] ${err.message || err.url || JSON.stringify(err)}`);
      }
    }
    process.exit(0);
  } finally {
    cleanup();
  }
}

runAudit().catch((err) => {
  console.error('[AUDIT] FATAL ERROR:', err);
  process.exit(1);
});
