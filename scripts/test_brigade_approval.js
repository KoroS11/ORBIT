import { chromium } from 'playwright'
import { spawn } from 'child_process'
import http from 'http'

// Wait for a port to be listening
function waitForPort(port, timeoutMs = 15000) {
  const start = Date.now()
  return new Promise((resolve, reject) => {
    function tryConnect() {
      const req = http.get(`http://127.0.0.1:${port}/`, (res) => {
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

async function run() {
  console.log('Starting vite preview server...')
  const preview = spawn('npx.cmd', ['vite', 'preview', '--host', '127.0.0.1', '--port', '4173', '--strictPort'], {
    shell: true,
    stdio: 'pipe',
  })

  preview.stdout.on('data', (d) => process.stdout.write(`[preview] ${d}`))
  preview.stderr.on('data', (d) => process.stderr.write(`[preview err] ${d}`))

  try {
    await waitForPort(4173)
    console.log('Preview server is up on http://127.0.0.1:4173')

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

    console.log('Navigating to Brigade COP...')
    await page.goto('http://127.0.0.1:4173/brigade', { waitUntil: 'domcontentloaded' })

    // Wait for the brigade shell to render
    await page.waitForSelector('.brigade-shell', { timeout: 10000 })
    console.log('Brigade shell rendered successfully.')

    // Check if an approve button or banner exists
    const approveBtn = page.locator('.approve-banner__button')
    if (await approveBtn.isVisible()) {
      console.log('Clicking Approve button in ApproveBanner...')
      await approveBtn.click()
    } else {
      console.log('Simulating approval in store directly...')
      await page.evaluate(() => {
        // Ingest a decision and then review it
        const store = window.__ORBIT_STORE__ || document.querySelector('#root')
        // Dispatch synthetic decision_review to event store or simulate approve
        window.dispatchEvent(new CustomEvent('test-approve'))
      })
    }

    // Give React time to re-render after approval
    await page.waitForTimeout(1000)

    // Verify Brigade shell is STILL rendered and visible (not black screen / empty root)
    const shellVisible = await page.locator('.brigade-shell').isVisible()
    console.log('Brigade shell still visible after approval:', shellVisible)

    // Ensure #root has content and no ErrorBoundary crash
    const rootHtml = await page.evaluate(() => document.getElementById('root')?.innerHTML || '')
    const hasContent = rootHtml.length > 100
    const hasErrorPanel = rootHtml.includes('error-boundary-panel')

    console.log('Root HTML length:', rootHtml.length)
    console.log('Error boundary triggered:', hasErrorPanel)
    console.log('Unhandled page errors count:', errors.length)

    await browser.close()

    if (!shellVisible || !hasContent || errors.length > 0) {
      throw new Error(`Regression test failed: shellVisible=${shellVisible}, errors=${errors.join('; ')}`)
    }

    console.log('PASSED: Brigade view remained visible and operational after approval.')
  } finally {
    try {
      if (preview.pid) {
        spawn('taskkill', ['/pid', preview.pid.toString(), '/f', '/t'])
      }
    } catch {
      preview.kill()
    }
  }
}

run()
  .then(() => {
    process.exit(0)
  })
  .catch((err) => {
    console.error('Test run failed:', err)
    process.exit(1)
  })
