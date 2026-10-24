// Drives the web editor in Chromium (Playwright). Usage: node ui_e2e.js <base url> <operator api key>
// Prints a JSON report; exits non-zero on failure.
const { chromium } = require('playwright');

(async () => {
  const [base, key] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const report = { consoleErrors: [], steps: {} };
  page.on('console', m => { if (m.type() === 'error') report.consoleErrors.push(m.text()); });
  page.on('pageerror', e => report.consoleErrors.push(String(e)));
  try {
    const response = await page.goto(`${base}/ui/`);
    report.steps.csp = response.headers()['content-security-policy'] || '';
    await page.waitForSelector('#list .items li');
    report.steps.shells = await page.locator('#list .items li').count();

    await page.click('#tabs button[data-view="submodels"]');
    await page.waitForSelector('#list h2:text("Submodels")');
    await page.locator('#list .items li', { hasText: 'Device' }).first().click();
    await page.locator('.node', { hasText: 'Temperature' }).first().click();
    await page.locator('#detail input[type="number"]').first().fill('33.5');
    await page.locator('#detail button.primary', { hasText: 'Save' }).first().click();
    await page.waitForSelector('#toast div.error');
    report.steps.anonymousSaveRefused = (await page.locator('#toast div.error').first().textContent()).includes('may not');

    await page.fill('#credential', key);
    await page.click('#login button[type="submit"]');
    await page.waitForFunction(() => document.getElementById('who').textContent === 'signed in');
    await page.click('#tabs button[data-view="submodels"]');
    await page.waitForSelector('#list h2:text("Submodels")');
    await page.locator('#list .items li', { hasText: 'Device' }).first().click();
    await page.locator('.node', { hasText: 'Temperature' }).first().click();
    await page.locator('#detail input[type="number"]').first().fill('33.5');
    await page.locator('#detail button.primary', { hasText: 'Save' }).first().click();
    await page.waitForSelector('#toast div:not(.error)');
    report.steps.saved = true;

    await page.click('#tabs button[data-view="templates"]');
    await page.waitForSelector('#list h2:text("Templates")');
    await page.locator('#list .items li', { hasText: 'bundled IDTA template' }).filter({ hasText: 'CarbonFootprint' }).first().click();
    await page.fill('#detail input[aria-label="Submodel id"]', 'urn:example:ui:pcf');
    await page.locator('#detail button', { hasText: 'Create submodel' }).click();
    await page.waitForSelector('#detail h3:text("Created")');
    report.steps.created = await page.locator('#detail p.err, #detail p.ok').first().textContent();

    await page.click('#tabs button[data-view="query"]');
    await page.waitForSelector('#list h2:text("Query")');
    await page.locator('#list button.primary', { hasText: 'Run' }).click();
    await page.waitForSelector('#detail h2');
    report.steps.query = await page.locator('#detail h2').textContent();

    await page.click('#tabs button[data-view="submodels"]');
    await page.waitForSelector('#list h2:text("Submodels")');
    await page.locator('#list .items li', { hasText: 'Device' }).first().click();
    await page.locator('#detail button', { hasText: 'History' }).click();
    await page.waitForSelector('#detail h3:text("History") + ul li');
    report.steps.revisions = await page.locator('#detail h3:text("History") + ul li').count();
    report.ok = true;
  } catch (error) {
    report.ok = false;
    report.error = String(error);
  }
  await browser.close();
  console.log(JSON.stringify(report));
  process.exit(report.ok ? 0 : 1);
})();
