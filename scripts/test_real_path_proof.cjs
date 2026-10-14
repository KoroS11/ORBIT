const { spawn, execSync } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

const API_PORT = 8000;
const FRONTEND_PORT = 5173;
const ARTIFACT_DIR = path.resolve('C:/Users/harsh/.gemini/antigravity-ide/brain/d2ac1cd6-6dea-4251-96f6-71dc15ab5a12');

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
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  console.log('=== STARTING REAL-PATH PROOF TEST (Node + Playwright) ===');

  const pyExe = path.resolve('.venv/Scripts/python.exe');
  const tmpSeedPath = path.resolve('data/seed/.tmp_mock_gemini_cache_seed.json');

  console.log(`Generating MOCK-recorded seed under NEW windowing at ${tmpSeedPath}...`);
  execSync(`"${pyExe}" scripts/generate_mock_seed.py --output "${tmpSeedPath}"`, { stdio: 'inherit' });

  const env = {
    ...process.env,
    ORBIT_LLM: 'gemini',
    ORBIT_GEMINI_SEED_CACHE: tmpSeedPath,
    // Unroutable proxy to guarantee any outbound Gemini call fails
    HTTPS_PROXY: 'http://127.0.0.1:9',
    HTTP_PROXY: 'http://127.0.0.1:9',
    HF_HUB_OFFLINE: '1',
    TRANSFORMERS_OFFLINE: '1',
    ORBIT_LOG_LEVEL: 'INFO',
  };

  const apiProc = spawn(pyExe, ['-m', 'uvicorn', 'orbit.api:app', '--host', '127.0.0.1', '--port', String(API_PORT)], {
    env,
    shell: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  apiProc.stdout.on('data', (d) => process.stdout.write(`[API] ${d}`));
  apiProc.stderr.on('data', (d) => process.stderr.write(`[API] ${d}`));

  let viteProc = null;
  const isViteUp = await new Promise((resolve) => {
    http.get(`http://127.0.0.1:${FRONTEND_PORT}`, (res) => {
      resolve(res.statusCode < 400);
    }).on('error', () => resolve(false));
  });

  if (isViteUp) {
    console.log(`Vite dev server already running on port ${FRONTEND_PORT}`);
  } else {
    console.log(`Starting Vite dev server on 127.0.0.1:${FRONTEND_PORT}...`);
    viteProc = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', String(FRONTEND_PORT)], { shell: true, stdio: 'inherit' });
  }

  // Poll Vite frontend
  console.log('Waiting for frontend...');
  for (let i = 0; i < 40; i++) {
    try {
      await new Promise((resolve, reject) => {
        http.get(`http://127.0.0.1:${FRONTEND_PORT}`, (res) => {
          if (res.statusCode < 400) resolve(); else reject();
        }).on('error', reject);
      });
      console.log('Frontend is ready!');
      break;
    } catch {
      await sleep(500);
    }
  }

  console.log('Waiting for backend /health...');
  let apiReady = false;
  for (let i = 0; i < 120; i++) {
    try {
      const h = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
      if (h.status === 'ok') {
        console.log('API is ready! Initial health:', JSON.stringify(h));
        apiReady = true;
        break;
      }
    } catch {
      await sleep(500);
    }
  }

  if (!apiReady) {
    console.error('API failed to start!');
    apiProc.kill();
    if (viteProc) viteProc.kill();
    process.exit(1);
  }

  const initialH = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
  console.log('\nInitial Counters:', {
    seed_hits: initialH.seed_hits,
    cache_hits: initialH.cache_hits,
    live_calls: initialH.live_calls,
    stub_fallbacks: initialH.stub_fallbacks,
    active_provider: initialH.active_provider,
  });

  const scenarioReports = [];
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  try {
    // Helper to find and click scenario button in left rail
    async function clickScenarioInRail(namePattern) {
      const btn = page.locator('.scenario-list button').filter({ hasText: namePattern }).first();
      await btn.waitFor({ state: 'visible', timeout: 10000 });
      const text = await btn.innerText();
      console.log(`Found rail button: ${JSON.stringify(text)}`);
      await btn.click();
    }

    // 1. Initial Load & Default Scenario: Counter-C5ISR (Run 1)
    console.log('\n--- 1. Testing Default Scenario: Counter-C5ISR (Run 1) ---');
    const h1_before = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
    await page.goto(`http://127.0.0.1:${FRONTEND_PORT}/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForSelector('.scenario-list', { timeout: 30000 });
    // The frontend auto-triggers Counter-C5ISR on initial socket connection
    console.log('Waiting 14s for Counter-C5ISR (Run 1) replay...');
    await sleep(14000);
    const h1_after = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
    const s1_hits = (h1_after.seed_hits || 0) - (h1_before.seed_hits || 0);
    const s1_stub = (h1_after.stub_fallbacks || 0) - (h1_before.stub_fallbacks || 0);
    const s1_live = (h1_after.live_calls || 0) - (h1_before.live_calls || 0);
    scenarioReports.push({
      scenario: 'Counter-C5ISR (Run 1)',
      seedHits: s1_hits,
      stubFallbacks: s1_stub,
      liveCalls: s1_live,
      activeProvider: h1_after.active_provider,
    });
    console.log(`Counter-C5ISR (Run 1) -> seed_hits: ${s1_hits}, stub_fallbacks: ${s1_stub}, live_calls: ${s1_live}, provider: ${h1_after.active_provider}`);

    // 2. Repeat Counter-C5ISR (Run 2)
    console.log('\n--- 2. Repeating Default Scenario: Counter-C5ISR (Run 2) ---');
    const h2_before = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
    await clickScenarioInRail('Counter-C5ISR');
    console.log('Waiting 12s for Counter-C5ISR (Run 2) replay...');
    await sleep(12000);
    const h2_after = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
    const s2_hits = (h2_after.seed_hits || 0) - (h2_before.seed_hits || 0);
    const s2_stub = (h2_after.stub_fallbacks || 0) - (h2_before.stub_fallbacks || 0);
    const s2_live = (h2_after.live_calls || 0) - (h2_before.live_calls || 0);
    scenarioReports.push({
      scenario: 'Counter-C5ISR (Run 2)',
      seedHits: s2_hits,
      stubFallbacks: s2_stub,
      liveCalls: s2_live,
      activeProvider: h2_after.active_provider,
    });
    console.log(`Counter-C5ISR (Run 2) -> seed_hits: ${s2_hits}, stub_fallbacks: ${s2_stub}, live_calls: ${s2_live}, provider: ${h2_after.active_provider}`);

    // 3. Beat47 (SATCOM gateway)
    console.log('\n--- 3. Testing beat47 (SATCOM gateway) ---');
    const h3_before = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
    await clickScenarioInRail('SATCOM gateway');
    console.log('Waiting 12s for beat47 replay...');
    await sleep(12000);
    const h3_after = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
    const s3_hits = (h3_after.seed_hits || 0) - (h3_before.seed_hits || 0);
    const s3_stub = (h3_after.stub_fallbacks || 0) - (h3_before.stub_fallbacks || 0);
    const s3_live = (h3_after.live_calls || 0) - (h3_before.live_calls || 0);
    scenarioReports.push({
      scenario: 'beat47',
      seedHits: s3_hits,
      stubFallbacks: s3_stub,
      liveCalls: s3_live,
      activeProvider: h3_after.active_provider,
    });
    console.log(`beat47 -> seed_hits: ${s3_hits}, stub_fallbacks: ${s3_stub}, live_calls: ${s3_live}, provider: ${h3_after.active_provider}`);

    // 4. Army Multi-Domain Attack Chain
    console.log('\n--- 4. Testing army_multidomain_attack_chain (Attack chain) ---');
    const h4_before = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
    await clickScenarioInRail('Attack chain');
    console.log('Waiting 12s for army_multidomain_attack_chain replay...');
    await sleep(12000);
    const h4_after = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
    const s4_hits = (h4_after.seed_hits || 0) - (h4_before.seed_hits || 0);
    const s4_stub = (h4_after.stub_fallbacks || 0) - (h4_before.stub_fallbacks || 0);
    const s4_live = (h4_after.live_calls || 0) - (h4_before.live_calls || 0);
    scenarioReports.push({
      scenario: 'army_multidomain_attack_chain',
      seedHits: s4_hits,
      stubFallbacks: s4_stub,
      liveCalls: s4_live,
      activeProvider: h4_after.active_provider,
    });
    console.log(`army-chain -> seed_hits: ${s4_hits}, stub_fallbacks: ${s4_stub}, live_calls: ${s4_live}, provider: ${h4_after.active_provider}`);

    // Wait for the badge to poll /health (up to 30s) or check for badge content
    console.log('\nWaiting for DataProvenanceBadge to refresh...');
    const badgeEl = page.locator('.data-provenance-badge');
    await badgeEl.waitFor({ state: 'visible', timeout: 10000 });
    // Let's wait up to 31s so the interval fires
    await sleep(31000);

    const badgePng = path.join(ARTIFACT_DIR, 'provenance_badge.png');
    await badgeEl.screenshot({ path: badgePng });
    console.log(`Saved provenance badge screenshot to ${badgePng}`);

    const badgeText = await badgeEl.innerText();
    console.log(`Badge rendered text: ${JSON.stringify(badgeText)}`);
  } finally {
    await browser.close();
  }

  const finalH = await fetchJson(`http://127.0.0.1:${API_PORT}/health`);
  console.log('\n============================================================');
  console.log('              REAL-PATH PROOF SUMMARY REPORT');
  console.log('============================================================');
  console.log(`Total Seed Hits:       ${finalH.seed_hits}`);
  console.log(`Total Cache Hits:      ${finalH.cache_hits}`);
  console.log(`Total Live Calls:      ${finalH.live_calls} (Outbound blocked)`);
  console.log(`Total Stub Fallbacks:  ${finalH.stub_fallbacks}`);
  console.log(`Final Active Provider: ${finalH.active_provider}`);
  console.log('------------------------------------------------------------');
  console.log(`Scenario                         Seed Hits  Stub Fallbacks  Live Calls  Provider`);
  console.log('------------------------------------------------------------');
  for (const r of scenarioReports) {
    const scn = r.scenario.padEnd(32);
    const hits = String(r.seedHits).padEnd(10);
    const stubs = String(r.stubFallbacks).padEnd(16);
    const live = String(r.liveCalls).padEnd(12);
    console.log(`${scn} ${hits} ${stubs} ${live} ${r.activeProvider}`);
  }
  console.log('============================================================\n');

  try {
    if (process.platform === 'win32') {
      const { execSync } = require('child_process');
      if (apiProc && apiProc.pid) execSync(`taskkill /pid ${apiProc.pid} /T /F`, { stdio: 'ignore' });
      if (viteProc && viteProc.pid) execSync(`taskkill /pid ${viteProc.pid} /T /F`, { stdio: 'ignore' });
    } else {
      apiProc.kill();
      if (viteProc) viteProc.kill();
    }
  } catch {}

  if (fs.existsSync(tmpSeedPath)) {
    try {
      fs.unlinkSync(tmpSeedPath);
      console.log(`Cleaned up temporary mock seed file: ${tmpSeedPath}`);
    } catch (e) {
      console.warn('Could not clean up temporary mock seed file:', e);
    }
  }
}

main().catch((e) => {
  console.error('Fatal error:', e);
  process.exit(1);
});
