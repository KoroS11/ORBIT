const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

// Read keys to redact from .env
const envLines = fs.existsSync('.env') ? fs.readFileSync('.env', 'utf-8').split('\n') : [];
const sensitiveValues = [];
for (const line of envLines) {
  const match = line.match(/^([^#=]+)=(.*)$/);
  if (match) {
    const val = match[2].trim();
    if (val.length > 5) {
      sensitiveValues.push(val);
      try {
        sensitiveValues.push(encodeURIComponent(val));
      } catch {}
    }
  }
}

function redact(str) {
  if (!str) return str;
  let res = String(str);
  // Strip any query parameter tokens
  res = res.replace(/([?&](?:token|access_token|key)=)[^&]+/gi, '$1<redacted>');
  for (const s of sensitiveValues) {
    if (s && res.includes(s)) {
      res = res.split(s).join('<redacted>');
    }
  }
  return res;
}

const ARTIFACT_DIR = path.resolve('C:/Users/harsh/.gemini/antigravity-ide/brain/d2ac1cd6-6dea-4251-96f6-71dc15ab5a12/scratch');
if (!fs.existsSync(ARTIFACT_DIR)) {
  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });
}

async function main() {
  console.log('=== PLAYWRIGHT VERIFICATION: MAP IMAGERY (NAV & GLOBE) ===');
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  const navTileResponses = [];
  const globeTileResponses = [];
  let currentMode = 'nav';

  page.on('response', (res) => {
    const url = res.url();
    if (
      url.includes('arcgisonline.com') ||
      url.includes('virtualearth.net') ||
      url.includes('cesium.com') ||
      url.includes('/tile/')
    ) {
      const record = {
        status: res.status(),
        url: redact(url),
        contentType: res.headers()['content-type'] || 'unknown',
      };
      if (currentMode === 'nav') {
        navTileResponses.push(record);
      } else {
        globeTileResponses.push(record);
      }
    }
  });

  try {
    // 1. Verify NAV View
    console.log('\n--- 1. Testing NAV Basemap Imagery (http://127.0.0.1:5173/) ---');
    await page.goto('http://127.0.0.1:5173/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.aor-map__canvas canvas', { timeout: 15000 });
    console.log('MapLibre canvas mounted. Waiting for tiles...');
    await page.waitForTimeout(5000);

    const nav200Tiles = navTileResponses.filter((t) => t.status === 200);
    console.log(`NAV View: Total tile responses: ${navTileResponses.length}, 200 OK tiles: ${nav200Tiles.length}`);
    for (let i = 0; i < Math.min(5, nav200Tiles.length); i++) {
      console.log(`  Sample 200 Tile [${i + 1}]: ${nav200Tiles[i].url} (${nav200Tiles[i].contentType})`);
    }

    if (nav200Tiles.length < 5) {
      throw new Error(`Expected at least 5 tile responses with status 200 for NAV, got ${nav200Tiles.length}`);
    }

    const navAfterPath = path.join(ARTIFACT_DIR, 'nav_view_after.png');
    await page.screenshot({ path: navAfterPath });
    console.log(`Saved NAV after screenshot: ${navAfterPath}`);

    // 2. Switch to GLOBE View
    console.log('\n--- 2. Testing GLOBE View ---');
    currentMode = 'globe';
    const globeBtn = page.locator('.map-view-toggle button').filter({ hasText: /GLOBE/i }).first();
    await globeBtn.waitFor({ state: 'visible', timeout: 5000 });
    console.log('Clicking GLOBE toggle...');
    await globeBtn.click();

    await page.waitForSelector('.cesium-widget canvas', { timeout: 20000 });
    console.log('Cesium widget canvas mounted. Waiting for imagery render...');
    await page.waitForTimeout(6000);

    const globe200Tiles = globeTileResponses.filter((t) => t.status === 200);
    console.log(`GLOBE View: Total imagery responses: ${globeTileResponses.length}, 200 OK: ${globe200Tiles.length}`);
    for (let i = 0; i < Math.min(5, globe200Tiles.length); i++) {
      console.log(`  Sample 200 Asset [${i + 1}]: ${globe200Tiles[i].url}`);
    }

    const globeAfterPath = path.join(ARTIFACT_DIR, 'globe_view_after.png');
    await page.screenshot({ path: globeAfterPath });
    console.log(`Saved GLOBE after screenshot: ${globeAfterPath}`);

    console.log('\n=== VERIFICATION PASSED ===');
    console.log(`NAV Map: ${nav200Tiles.length} status 200 tiles (>= 5 required: PASSED)`);
    console.log(`Globe: Canvas rendered with ${globe200Tiles.length} successful imagery assets (PASSED)`);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error('Test failed:', err);
  process.exit(1);
});
