const {test, expect} = require('@playwright/test');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF9sAAAAASUVORK5CYII=', 'base64');
test('connect button reaches the external OAuth page', async ({page}) => {
  await page.evaluate(async () => {
    await fetch('/__test__/oauth-mode', {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content}, body:JSON.stringify({mode:'live'})});
  });
  await page.route('https://www.threads.net/**', route => route.fulfill({contentType:'text/html', body:'<h1>OAuth destination reached</h1>'}));
  await page.goto('/accounts');
  await page.getByRole('button', {name:'＋ 連結 Threads 帳號',exact:true}).click();
  await expect(page.getByRole('heading', {name:'OAuth destination reached'})).toBeVisible();
  const target = new URL(page.url());
  expect(target.pathname).toBe('/oauth/authorize');
  expect(target.searchParams.get('redirect_uri')).toBe('https://example.test/threads/callback');
  expect(target.searchParams.get('scope')).toBe('threads_basic,threads_content_publish');
  expect(target.searchParams.get('state')).toBeTruthy();
  await page.goto('/accounts');
  await page.evaluate(async () => {
    await fetch('/__test__/oauth-mode', {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content}, body:JSON.stringify({mode:'demo'})});
  });
});
test.beforeEach(async ({page}) => {
  await page.goto('/login');
  await page.getByLabel('帳號', {exact:true}).fill('ui-test');
  await page.getByLabel('密碼', {exact:true}).fill('ui-test-password-only');
  await page.getByRole('button', {name:'登入', exact:true}).click();
  await expect(page.locator('.post-card').first()).toBeVisible();
  await page.evaluate(async () => { await fetch('/api/draft', {method:'DELETE', headers:{'X-CSRFToken':document.querySelector('meta[name="csrf-token"]').content}}); });
  await page.reload();
  await expect(page.locator('.post-card')).toHaveCount(1);
});
test('administrator adds, resets and deletes an internal user', async ({page}) => {
  await page.goto('/accounts');
  await expect(page.locator('#internal-users')).toHaveCount(0);
  await page.getByRole('navigation').getByRole('link', {name:'同事登入帳號', exact:true}).click();
  await expect(page).toHaveURL(/\/users$/);
  await expect(page.getByRole('button', {name:'＋ 連結 Threads 帳號',exact:true})).toHaveCount(0);
  await page.getByLabel('內部帳號',{exact:true}).fill('new-colleague');
  await page.getByLabel('初始密碼',{exact:true}).fill('colleague-password-one');
  await page.getByRole('button',{name:'新增同事登入帳號',exact:true}).click();
  const row = page.getByRole('row').filter({hasText:'new-colleague'});
  await expect(row).toHaveCount(1);
  await row.getByRole('link',{name:'重設密碼',exact:true}).click();
  await page.getByLabel('新密碼',{exact:true}).fill('colleague-password-two');
  await page.getByLabel('確認新密碼',{exact:true}).fill('colleague-password-two');
  await page.getByRole('button',{name:'確認重設密碼',exact:true}).click();
  await expect(page.getByRole('status').filter({hasText:'舊登入已失效'})).toBeVisible();
  await page.getByRole('row').filter({hasText:'new-colleague'}).getByRole('link',{name:'刪除帳號',exact:true}).click();
  await page.getByLabel('輸入完整登入帳號以確認',{exact:true}).fill('new-colleague');
  await page.getByRole('button',{name:'確認刪除帳號',exact:true}).click();
  await expect(page.getByRole('row').filter({hasText:'new-colleague'})).toHaveCount(0);
  await expect(page.getByRole('row').filter({hasText:'ui-test'})).toHaveCount(1);
});
test('same image to A/B/C, failure retry, and history', async ({page}) => {
  const errors=[]; page.on('pageerror', error=>errors.push(error.message));
  await page.locator('.post-text').fill('新品測試 [模擬失敗]');
  await page.locator('.post-image').setInputFiles({name:'test.png', mimeType:'image/png', buffer:png});
  await expect(page.locator('.image-preview')).toBeVisible();
  for (const letter of ['A','B','C']) await page.getByLabel(`品牌 ${letter}`, {exact:true}).check();
  await page.getByRole('button', {name:'預覽並確認'}).click();
  await expect(page.locator('#preview-content')).toContainText('品牌 A ／ 品牌 B ／ 品牌 C');
  await page.getByRole('button', {name:'確認整批發布'}).click();
  await expect(page.locator('#batch-summary')).toContainText('2 成功', {timeout:15000});
  await expect(page.locator('#batch-summary')).toContainText('1 失敗');
  await page.getByRole('button', {name:'只重試此項目'}).click();
  await expect(page.locator('#batch-summary')).toHaveText('3 成功', {timeout:15000});
  await expect(page.getByText('已嘗試 2 次', {exact:false})).toHaveCount(1);
  expect(errors).toEqual([]);
});
test('different image cards, drafts and narrow screen', async ({page}) => {
  await page.getByRole('radio', {name:/不同篇指定帳號/}).check();
  await page.locator('.post-text').fill('圖文甲測試');
  await page.locator('.post-image').setInputFiles({name:'alpha.png', mimeType:'image/png', buffer:png});
  await expect(page.locator('.image-preview')).toBeVisible();
  await page.getByLabel('品牌 A', {exact:true}).check();
  await page.getByRole('button', {name:'新增一張圖文'}).click();
  await page.locator('.post-card').nth(1).locator('.post-text').fill('圖文乙測試');
  await page.locator('.post-card').nth(1).locator('.post-image').setInputFiles({name:'beta.png', mimeType:'image/png', buffer:png});
  await expect(page.locator('.post-card').nth(1).locator('.image-preview')).toBeVisible();
  await page.locator('.post-card').nth(1).getByLabel('品牌 B', {exact:true}).check();
  await page.getByRole('button', {name:'儲存草稿', exact:true}).click();
  await expect(page.locator('#draft-status')).toHaveText('草稿已儲存');
  await page.reload();
  await expect(page.locator('.post-card')).toHaveCount(2);
  await expect(page.locator('.post-text').nth(1)).toHaveValue('圖文乙測試');
  await page.screenshot({path:'test-results/compose-desktop.png', fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:'test-results/compose-mobile.png', fullPage:true});
  await page.getByRole('button', {name:'預覽並確認'}).click();
  await page.getByRole('button', {name:'確認整批發布'}).click();
  await expect(page.locator('#batch-summary')).toHaveText('2 成功', {timeout:15000});
  await expect(page.locator('#job-results article').nth(0)).toContainText('圖文甲測試');
  await expect(page.locator('#job-results article').nth(1)).toContainText('圖文乙測試');
});


test('video preview, draft restore and batch result', async ({page}) => {
  const {execFileSync} = require('node:child_process');
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'multithreader-video-'));
  const filename = path.join(directory, 'sample.mp4');
  try {
    execFileSync('ffmpeg', ['-v','error','-f','lavfi','-i','color=c=black:s=320x240:d=1','-c:v','libx264','-pix_fmt','yuv420p',filename]);
    await page.locator('.post-text').fill('影片測試');
    await page.locator('.post-image').setInputFiles(filename);
    await expect(page.locator('.video-preview')).toBeVisible();
    await expect.poll(() => page.locator('.video-preview').evaluate(v => v.readyState)).toBeGreaterThan(0);
    await page.getByLabel('品牌 A', {exact:true}).check();
    await page.getByRole('button', {name:'儲存草稿',exact:true}).click();
    await expect(page.locator('#draft-status')).toHaveText('草稿已儲存');
    await page.reload();
    await expect(page.locator('.video-preview')).toBeVisible();
    await page.getByRole('button', {name:'預覽並確認'}).click();
    await expect(page.locator('#preview-content video')).toBeVisible();
    await page.getByRole('button', {name:'確認整批發布'}).click();
    await expect(page.locator('#batch-summary')).toHaveText('1 成功', {timeout:15000});
    await expect(page.locator('#job-results video')).toBeVisible();
  } finally { fs.unlinkSync(filename); fs.rmdirSync(directory); }
});


test('multiple images reorder and first reply survive draft and publish', async ({page}) => {
  await page.locator('.post-text').fill('多張圖片');
  await page.locator('.first-reply').fill('這是補充資訊');
  await page.locator('.post-image').setInputFiles([
    {name:'one.png',mimeType:'image/png',buffer:png},
    {name:'two.png',mimeType:'image/png',buffer:png}
  ]);
  await expect(page.locator('.image-preview')).toHaveCount(2);
  const second = await page.locator('.image-preview').nth(1).getAttribute('src');
  await page.getByRole('button',{name:'往前',exact:true}).nth(1).click();
  await expect(page.locator('.image-preview').first()).toHaveAttribute('src', second);
  await page.getByLabel('品牌 A',{exact:true}).check();
  await page.getByRole('button',{name:'儲存草稿',exact:true}).click();
  await expect(page.locator('#draft-status')).toHaveText('草稿已儲存');
  await page.reload();
  await expect(page.locator('.image-preview')).toHaveCount(2);
  await expect(page.locator('.first-reply')).toHaveValue('這是補充資訊');
  await page.getByRole('button',{name:'預覽並確認'}).click();
  await expect(page.locator('#preview-content img')).toHaveCount(2);
  await page.getByRole('button',{name:'確認整批發布'}).click();
  await expect(page.locator('#batch-summary')).toHaveText('1 成功',{timeout:15000});
  await expect(page.locator('#job-results')).toContainText('第一則回覆：成功');
  await expect(page.locator('#job-results img')).toHaveCount(2);
});
