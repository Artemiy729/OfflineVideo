// Requires native/install.py to have registered the Chromium host on macOS.
const {chromium} = require('@playwright/test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
(async () => {
  const extension = path.resolve(__dirname, '../extension');
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'offlinevideo-browser-'));
  const target = path.join(profile, 'NativeMessagingHosts');
  fs.mkdirSync(target);
  fs.copyFileSync(path.join(os.homedir(), 'Library/Application Support/Dia/NativeMessagingHosts/local.offlinevideo.json'), path.join(target, 'local.offlinevideo.json'));
  const browserOptions = process.env.OFFLINEVIDEO_BROWSER === 'dia'
    ? {executablePath: '/Applications/Dia.app/Contents/MacOS/Dia'} : {channel: 'chromium'};
  const context = await chromium.launchPersistentContext(profile, {
    ...browserOptions, headless: process.env.OFFLINEVIDEO_SHOW_BROWSER !== '1',
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  try {
    const belongsToExtension = worker => worker.url().startsWith('chrome-extension://plnhfpbpnmmgemohkahbhbjbffmdecac/');
    const worker = context.serviceWorkers().find(belongsToExtension) || await context.waitForEvent('serviceworker', {predicate: belongsToExtension, timeout: 20000});
    const id = new URL(worker.url()).host;
    assert.equal(id, 'plnhfpbpnmmgemohkahbhbjbffmdecac');
    const result = await worker.evaluate(() => chrome.runtime.sendNativeMessage('local.offlinevideo', {action: 'status'}));
    assert.equal(result.ok, true);
    assert.equal(result.capabilities.downloader, true);
    assert.equal(result.capabilities.ffmpeg, true);
    assert.equal(result.capabilities.javascript, true);
    if (process.env.OFFLINEVIDEO_TEST_POPUP === '1') {
      const windowId = await worker.evaluate(async () => {
        const window = await chrome.windows.create({url: 'about:blank', type: 'normal', focused: true, width: 1100, height: 800});
        return window.id;
      });
      await worker.evaluate(windowId => chrome.windows.update(windowId, {focused: true}), windowId);
      await new Promise(resolve => setTimeout(resolve, 300));
      await worker.evaluate(windowId => chrome.action.openPopup({windowId}), windowId);
      const session = await context.newCDPSession(context.pages()[0]);
      const targets = await session.send('Target.getTargets');
      const target = targets.targetInfos.find(target => target.url === `chrome-extension://${id}/popup.html`);
      assert.ok(target, 'Action popup must exist');
      const {sessionId} = await session.send('Target.attachToTarget', {targetId: target.targetId, flatten: false});
      const dimensions = await new Promise(async (resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('Popup measurement timed out')), 10000);
        const receive = event => {
          if (event.sessionId !== sessionId) return;
          const message = JSON.parse(event.message);
          if (message.id !== 1) return;
          clearTimeout(timeout);
          session.off('Target.receivedMessageFromTarget', receive);
          if (message.error) reject(new Error(message.error.message));
          else resolve(message.result.result.value);
        };
        session.on('Target.receivedMessageFromTarget', receive);
        try {
          await session.send('Target.sendMessageToTarget', {sessionId, message: JSON.stringify({id: 1, method: 'Runtime.evaluate', params: {
            expression: '({width:innerWidth,contentWidth:document.documentElement.scrollWidth,fullPage:document.documentElement.classList.contains("full-page")})', returnByValue: true,
          }})});
        } catch (error) { clearTimeout(timeout); reject(error); }
      });
      console.log('Actual action popup dimensions:', JSON.stringify(dimensions));
      assert.equal(dimensions.width, 400);
      assert.equal(dimensions.contentWidth, 400);
      assert.equal(dimensions.fullPage, false);
      await session.send('Target.detachFromTarget', {sessionId});
      await session.detach();
    }
    const page = await context.newPage();
    await page.goto(`chrome-extension://${id}/popup.html`);
    await page.waitForFunction(() => !document.getElementById('download').disabled);
    await page.locator('body').screenshot({path: '/private/tmp/offlinevideo-preview.png'});
    console.log('Loaded unpacked extension; real native messaging, yt-dlp, FFmpeg and JavaScript runtime: OK');
  } finally { await context.close(); fs.rmSync(profile, {recursive: true, force: true}); }
})().catch(error => { console.error(error); process.exitCode = 1; });
