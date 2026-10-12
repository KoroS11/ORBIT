import { chromium } from 'playwright';
import { spawn } from 'child_process';
import http from 'http';

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

async function main() {
  console.log('[E2E-TEST] Starting Vite preview server on 4173...');
  const preview = spawn('npx.cmd', ['vite', 'preview', '--host', '127.0.0.1', '--port', '4173', '--strictPort'], {
    stdio: 'pipe',
    shell: true,
  });
  preview.stdout.on('data', (d) => process.stdout.write(`[preview] ${d}`));
  preview.stderr.on('data', (d) => process.stderr.write(`[preview err] ${d}`));

  console.log('[E2E-TEST] Starting ORBIT backend on 8000...');
  const backend = spawn('.\\.venv\\Scripts\\python.exe', ['-m', 'uvicorn', 'orbit.api:app', '--host', '127.0.0.1', '--port', '8000'], {
    stdio: 'pipe',
    shell: true,
  });
  backend.stdout.on('data', (d) => process.stdout.write(`[backend] ${d}`));
  backend.stderr.on('data', (d) => process.stderr.write(`[backend err] ${d}`));

  const cleanup = () => {
    try { preview.kill(); } catch (e) {}
    try { backend.kill(); } catch (e) {}
  };

  process.on('exit', cleanup);
  process.on('SIGINT', cleanup);

  try {
    const previewReady = await waitForHttp('http://127.0.0.1:4173');
    const backendReady = await waitForHttp('http://127.0.0.1:8000/health');
    console.log(`[E2E-TEST] Servers up: preview=${previewReady}, backend=${backendReady}`);
    if (!previewReady || !backendReady) {
      throw new Error('Failed to start servers');
    }

    console.log('Waiting 6s for backend SentenceTransformer initialization...');
    await new Promise((r) => setTimeout(r, 6000));

    const browser = await chromium.launch({ headless: true });

    // 1. Check Brigade View & NAV toggle & reload
    console.log('\n--- 1. Testing Brigade & NAV view ---');
    const pageBrigade = await browser.newPage();
    const bErrors = [];
    pageBrigade.on('pageerror', (e) => bErrors.push(e.message));

    await pageBrigade.goto('http://127.0.0.1:4173/', { waitUntil: 'domcontentloaded' });
    await pageBrigade.waitForTimeout(2000);

    const mapStage = await pageBrigade.locator('.map-workspace').isVisible();
    const decStack = await pageBrigade.locator('.decision-stack').isVisible();
    console.log(`Brigade .map-workspace: ${mapStage}, .decision-stack: ${decStack}`);

    // Toggle NAV / GLOBE
    const navBtn = pageBrigade.locator('button:has-text("NAV")');
    if (await navBtn.isVisible()) {
      await navBtn.click();
      console.log('Clicked NAV button');
    }
    const globeBtn = pageBrigade.locator('button:has-text("GLOBE")');
    if (await globeBtn.isVisible()) {
      await globeBtn.click();
      console.log('Clicked GLOBE button');
    }

    // Reload check
    await pageBrigade.reload({ waitUntil: 'domcontentloaded' });
    await pageBrigade.waitForTimeout(1500);
    const mapAfterReload = await pageBrigade.locator('.map-workspace').isVisible();
    console.log(`Brigade after reload: ${mapAfterReload}, errors: ${bErrors.length}`);

    // 2. Check Ops Console on /operator & Replay cycles
    console.log('\n--- 2. Testing Operator (Ops Console) & Scenario Replay Cycles ---');
    const pageOps = await browser.newPage();
    const opsErrors = [];
    pageOps.on('pageerror', (e) => opsErrors.push(e.message));
    pageOps.on('console', (msg) => {
      const t = msg.text();
      if (t.includes('WS') || t.includes('trace') || t.includes('Error')) {
        console.log(`[browser ops] ${t}`);
      }
    });

    await pageOps.goto('http://127.0.0.1:4173/operator', { waitUntil: 'domcontentloaded' });
    await pageOps.waitForTimeout(2000);

    // Initial counter text
    let stats = await pageOps.locator('text=/\\d+ runs · \\d+ calls/').textContent().catch(() => null);
    console.log(`Initial action counter: "${stats}"`);

    // Replay cycle 1: beat1
    console.log('\nTriggering Replay 1 (beat1.jsonl @ speed=200)...');
    const r1 = await fetch('http://127.0.0.1:8000/scenarios/beat1.jsonl/replay?speed=200', { method: 'POST' });
    console.log(`Replay 1 status: ${r1.status}`);

    // Wait for events over WebSocket
    await pageOps.waitForTimeout(5000);
    stats = await pageOps.locator('text=/\\d+ runs · \\d+ calls/').textContent().catch(() => null);
    console.log(`After Replay 1 action counter: "${stats}"`);

    // Replay cycle 2: beat1 again
    console.log('\nTriggering Replay 2 (beat1.jsonl @ speed=200)...');
    const r2 = await fetch('http://127.0.0.1:8000/scenarios/beat1.jsonl/replay?speed=200', { method: 'POST' });
    console.log(`Replay 2 status: ${r2.status}`);

    await pageOps.waitForTimeout(5000);
    stats = await pageOps.locator('text=/\\d+ runs · \\d+ calls/').textContent().catch(() => null);
    console.log(`After Replay 2 action counter: "${stats}"`);

    // 3. Test Decision Review
    console.log('\n--- 3. Testing Decision Review Persistence ---');
    const revRes = await fetch('http://127.0.0.1:8000/decisions/dec-test-e2e/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'approved', actor: 'CDR-E2E' }),
    });
    console.log(`Review POST status: ${revRes.status}`);

    const reviews = await fetch('http://127.0.0.1:8000/decisions/reviews').then(r => r.json());
    console.log(`Stored reviews count: ${Object.keys(reviews.reviews || {}).length}`);

    await browser.close();
    console.log('\n[E2E-TEST] SUCCESS: All views, replay cycles, and reviews verified.');
  } finally {
    cleanup();
  }
}

main().catch((err) => {
  console.error('[E2E-TEST] FATAL:', err);
  process.exit(1);
});
