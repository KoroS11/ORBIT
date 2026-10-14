const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const ARTIFACT_DIR = 'C:\\Users\\harsh\\.gemini\\antigravity-ide\\brain\\d2ac1cd6-6dea-4251-96f6-71dc15ab5a12\\scratch';
if (!fs.existsSync(ARTIFACT_DIR)) {
  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });
}

async function run() {
  console.log('Starting Playwright test for headers without provenance bar...');
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  try {
    // 1. Check Brigade Header
    console.log('Navigating to Brigade view (http://127.0.0.1:5173/)...');
    await page.goto('http://127.0.0.1:5173/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.brigade-shell .app-header', { timeout: 10000 });

    const brigadeHeader = page.locator('.brigade-shell .app-header');
    await brigadeHeader.waitFor({ state: 'visible' });

    // Verify provenance bar is completely absent
    const provBadgeInBrigade = await brigadeHeader.locator('.data-provenance-badge').count();
    if (provBadgeInBrigade !== 0) {
      throw new Error(`Expected 0 .data-provenance-badge in Brigade header, found ${provBadgeInBrigade}`);
    }

    const brigadeHeaderText = await brigadeHeader.innerText();
    console.log('Brigade header text:', JSON.stringify(brigadeHeaderText));
    if (brigadeHeaderText.includes('SYNTHETIC SCENARIO DATA')) {
      throw new Error('Found "SYNTHETIC SCENARIO DATA" in Brigade header!');
    }

    // Verify other elements exist
    const opsLink = brigadeHeader.locator('a[href="/operator"]');
    await opsLink.waitFor({ state: 'visible' });
    console.log('Ops button found in Brigade header.');

    // Screenshot Brigade header
    const brigadeShotPath = path.join(ARTIFACT_DIR, 'brigade_header.png');
    await brigadeHeader.screenshot({ path: brigadeShotPath });
    console.log(`Saved Brigade header screenshot to: ${brigadeShotPath}`);

    // 2. Click Ops button to navigate
    console.log('Clicking Ops button...');
    await opsLink.click();
    await page.waitForURL('**/operator', { timeout: 10000 });
    await page.waitForSelector('.operator-shell .app-header', { timeout: 10000 });

    // 3. Check Ops Header
    const opsHeader = page.locator('.operator-shell .app-header');
    await opsHeader.waitFor({ state: 'visible' });

    const provBadgeInOps = await opsHeader.locator('.data-provenance-badge').count();
    if (provBadgeInOps !== 0) {
      throw new Error(`Expected 0 .data-provenance-badge in Ops header, found ${provBadgeInOps}`);
    }

    const opsHeaderText = await opsHeader.innerText();
    console.log('Ops header text:', JSON.stringify(opsHeaderText));
    if (opsHeaderText.includes('SYNTHETIC SCENARIO DATA')) {
      throw new Error('Found "SYNTHETIC SCENARIO DATA" in Ops header!');
    }

    // Verify Brigade View button exists
    const brigadeLink = opsHeader.locator('a[href="/brigade"]');
    await brigadeLink.waitFor({ state: 'visible' });
    console.log('Brigade View button found in Ops header.');

    // Screenshot Ops header
    const opsShotPath = path.join(ARTIFACT_DIR, 'ops_header.png');
    await opsHeader.screenshot({ path: opsShotPath });
    console.log(`Saved Ops header screenshot to: ${opsShotPath}`);

    // Click Brigade View button to verify it navigates back
    console.log('Clicking Brigade View button to verify navigation...');
    await brigadeLink.click();
    await page.waitForURL('**/brigade', { timeout: 10000 });
    await page.waitForSelector('.brigade-shell .app-header', { timeout: 10000 });
    console.log('Successfully navigated back to Brigade view!');

    console.log('\n[PASS] Playwright test for headers without provenance bar completed successfully.');
  } finally {
    await browser.close();
  }
}

run().catch((err) => {
  console.error('[FAIL] Error during Playwright header test:', err);
  process.exit(1);
});
