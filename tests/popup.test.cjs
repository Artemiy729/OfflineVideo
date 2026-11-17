const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('@playwright/test');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const os = require('node:os');

test('responsive form has no history; chooses quality and folder, reports completion transiently', async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 420, height: 850}});
    await page.addInitScript(() => {
      window.requests = []; window.offline = false; window.taskState = 'downloading';
      window.chrome = {
        tabs: {getCurrent: async () => ({id: 1}), query: async () => [{id: 1, url: 'https://www.youtube.com/watch?v=fixture'}]},
        scripting: {executeScript: async () => [{result: ['https://example.com/video.mp4']}]},
        storage: {local: {get: async () => ({quality: '720'}), set: async () => {}}},
        runtime: {sendMessage: async message => {
          window.requests.push(message);
          if (window.offline) return {ok: false, error: 'Helper disconnected'};
          if (message.action === 'status') return {ok: true, capabilities: {downloader: true, ffmpeg: true, folder: '/Users/test/Very long directory name for offline learning materials', picker: {}}};
          if (message.action === 'task_status') return {ok: true, state: window.taskState};
          return {ok: true};
        }},
      };
    });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(pathToFileURL(path.resolve(__dirname, '../extension/popup.html')).href);
    await page.waitForFunction(() => !document.getElementById('download').disabled);
    assert.equal(await page.locator('#jobs, .job, #downloads-title').count(), 0);
    assert.equal(await page.locator('#url').inputValue(), 'https://www.youtube.com/watch?v=fixture');
    assert.equal(await page.locator('input[name="quality"]:checked').inputValue(), '720');
    for (const width of [280, 320, 375, 420, 640, 900]) {
      await page.setViewportSize({width, height: 900});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `horizontal overflow at ${width}px`);
      const button = await page.locator('#download').boundingBox();
      assert.ok(button.x >= 0 && button.x + button.width <= width, `button outside ${width}px`);
    }
    await page.setViewportSize({width: 280, height: 850});
    await page.evaluate(() => document.documentElement.classList.remove('full-page'));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 400);
    await page.setViewportSize({width:400,height:600});
    assert.ok(await page.evaluate(() => document.body.scrollHeight <= 600), 'Default popup should fit without vertical scrolling');
    await page.locator('body').screenshot({path:path.join(os.tmpdir(), 'offlinevideo-balanced.png')});
    await page.evaluate(() => document.documentElement.classList.add('full-page'));
    await page.setViewportSize({width: 420, height: 850});
    await page.locator('label').filter({has: page.locator('input[value="1080"]')}).click();
    assert.equal(await page.evaluate(() => window.requests.filter(r => r.action === 'download').length), 0);
    await page.locator('#download').click();
    await page.waitForFunction(() => window.requests.some(r => r.action === 'download'));
    const download = await page.evaluate(() => window.requests.find(r => r.action === 'download'));
    assert.equal(download.quality, '1080'); assert.match(download.id, /^[a-f0-9-]{36}$/);
    await page.evaluate(() => { window.taskState = 'done'; });
    await page.waitForFunction(() => !document.getElementById('download').disabled);
    assert.match(await page.locator('#notice').textContent(), /Video saved/);
    assert.equal(await page.locator('#sources, .candidate').count(), 0);
    await page.locator('#clear-url').click(); assert.equal(await page.locator('#url').inputValue(), '');
    await page.locator('#use-tab').click(); assert.match(await page.locator('#url').inputValue(), /youtube/);
    await page.locator('#choose-folder').click();
    assert.ok(await page.evaluate(() => window.requests.some(r => r.action === 'choose_folder')));
    await page.locator('#folder').click();
    assert.ok(await page.evaluate(() => window.requests.some(r => r.action === 'open_folder')));
    await page.locator('#notice').evaluate(element => { element.hidden = true; });
    await page.locator('#clear-url').click();
    await page.locator('#destination').evaluate(element => { element.textContent = 'OfflineVideo'; element.title = 'Example download folder'; });
    await page.locator('body').screenshot({path: process.env.OFFLINEVIDEO_PREVIEW_PATH || path.join(os.tmpdir(), 'offlinevideo-redesign.png')});
    await page.evaluate(() => { window.offline = true; });
    await page.waitForFunction(() => document.getElementById('download').disabled);
    assert.match(await page.locator('#connection').textContent(), /Disconnected/);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
