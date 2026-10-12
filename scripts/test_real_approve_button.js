import { chromium } from 'playwright'
import { spawn } from 'child_process'
import http from 'http'

function waitForPort(port, timeoutMs = 20000) {
  const start = Date.now()
  return new Promise((resolve, reject) => {
    function tryConnect() {
      const req = http.get(`http://127.0.0.1:${port}/health`, (res) => {
        resolve()
      })
      req.on('error', () => {
        if (Date.now() - start > timeoutMs) {
          reject(new Error(`Timeout waiting for port ${port}`))
        } else {
          setTimeout(tryConnect, 300)
        }
      })
    }
    tryConnect()
  })
}

function waitForHttp(url, timeoutMs = 20000) {
  const start = Date.now()
  return new Promise((resolve, reject) => {
    function tryConnect() {
      const req = http.get(url, (res) => {
        resolve()
      })
      req.on('error', () => {
        if (Date.now() - start > timeoutMs) {
          reject(new Error(`Timeout waiting for ${url}`))
        } else {
          setTimeout(tryConnect, 300)
        }
      })
    }
    tryConnect()
  })
}

async function postJson(url, data = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url)
    const body = JSON.stringify(data)
    const req = http.request(
      {
        hostname: u.hostname,
        port: u.port,
        path: u.pathname + u.search,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        let respData = ''
        res.on('data', (c) => (respData += c))
        res.on('end', () => resolve({ status: res.statusCode, body: respData }))
      }
    )
    req.on('error', reject)
    req.write(body)
    req.end()
  })
}

async function run() {
  console.log('1. Starting ORBIT backend on port 8000 (provider=stub)...')
  const backend = spawn('uv', ['run', 'uvicorn', 'orbit.api:app', '--port', '8000'], {
    shell: true,
    stdio: 'pipe',
    env: { ...process.env, ORBIT_LLM: 'stub', ORBIT_LIVE: '' },
  })
  backend.stdout.on('data', (d) => process.stdout.write(`[backend] ${d}`))
  backend.stderr.on('data', (d) => process.stderr.write(`[backend err] ${d}`))

  console.log('2. Starting Vite preview on port 4173...')
  const preview = spawn('npx.cmd', ['vite', 'preview', '--host', '127.0.0.1', '--port', '4173', '--strictPort'], {
    shell: true,
    stdio: 'pipe',
  })
  preview.stdout.on('data', (d) => process.stdout.write(`[preview] ${d}`))
  preview.stderr.on('data', (d) => process.stderr.write(`[preview err] ${d}`))

  try {
    await waitForPort(8000)
    console.log('Backend is ready on http://127.0.0.1:8000')

    await waitForHttp('http://127.0.0.1:4173/')
    console.log('Preview is ready on http://127.0.0.1:4173')

    const browser = await chromium.launch({ headless: true })
    const page = await browser.newPage()

    const errors = []
    page.on('pageerror', (err) => {
      console.error('[Page Error]:', err.message)
      errors.push(err.message)
    })
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        console.error('[Console Error]:', msg.text())
      }
    })

    console.log('3. Navigating to Operator view in real browser...')
    await page.goto('http://127.0.0.1:4173/operator', { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.operator-shell', { timeout: 10000 })

    console.log('4. Triggering live scenario replay (beat47.jsonl)...')
    const replayRes = await postJson('http://127.0.0.1:8000/scenarios/beat47.jsonl/replay?speed=1000')
    console.log('Replay trigger response:', replayRes.status)

    console.log('5. Waiting for real decision card Approve button in DOM (up to 45s)...')
    const approveBtn = page.locator('.decision-card__btn--approve').first()
    await approveBtn.waitFor({ state: 'visible', timeout: 45000 })

    const btnText = await approveBtn.textContent()
    console.log(`Found real Approve button with text: "${btnText?.trim()}"`)

    console.log('6. Clicking real Approve button with Playwright (NO store injection)...')
    await approveBtn.click()

    // Wait for card to reflect approved state
    console.log('7. Verifying card reflects approved state...')
    const approvedTag = page.locator('.decision-card__status-tag--approved').first()
    await approvedTag.waitFor({ state: 'visible', timeout: 5000 })
    console.log('Approved tag visible:', await approvedTag.textContent())

    // Verify UI didn't crash
    const rootHtml = await page.evaluate(() => document.getElementById('root')?.innerHTML || '')
    const hasError = rootHtml.includes('error-boundary-panel')
    console.log('Error boundary triggered:', hasError)
    console.log('Page error count:', errors.length)

    await browser.close()

    if (hasError || errors.length > 0) {
      throw new Error(`Real approve test failed: errors=${errors.join('; ')}`)
    }

    console.log('PASSED: Real Approve-button Playwright test executed and verified successfully with zero store injection.')
  } finally {
    try {
      if (backend.pid) spawn('taskkill', ['/pid', backend.pid.toString(), '/f', '/t'])
      if (preview.pid) spawn('taskkill', ['/pid', preview.pid.toString(), '/f', '/t'])
    } catch {
      backend.kill()
      preview.kill()
    }
  }
}

run()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error('Test run failed:', err)
    process.exit(1)
  })
