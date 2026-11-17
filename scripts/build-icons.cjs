// Render the vector source into the PNG sizes required by Chromium extensions.
const {chromium} = require('@playwright/test');
const fs = require('node:fs/promises');
const path = require('node:path');
(async () => {
  const directory = path.resolve(__dirname, '../extension/icons');
  const source = await fs.readFile(path.join(directory, 'icon.svg'), 'utf8');
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({deviceScaleFactor: 1});
    for (const size of [16, 24, 32, 48, 128, 256]) {
      await page.setViewportSize({width: size, height: size});
      await page.setContent(`<style>html,body{margin:0;background:transparent}img{display:block;width:${size}px;height:${size}px}</style><img src="data:image/svg+xml;base64,${Buffer.from(source).toString('base64')}">`);
      await page.locator('img').evaluate(image => image.decode());
      await page.screenshot({path: path.join(directory, `icon-${size}.png`), omitBackground: true});
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
