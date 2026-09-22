"use strict";
const oauthLink = document.querySelector('#oauth-redirect #oauth-link');
if (oauthLink) window.location.replace(oauthLink.href);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
async function api(path, options = {}) {
  const headers = {"X-CSRFToken": csrf, ...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers});
  let data;
  try { data = await response.json(); } catch { throw new Error("服務回應異常，請稍後再試。"); }
  if (!response.ok) throw new Error(data.error || "操作未完成，請確認登入與操作權限。");
  return data;
}
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function mediaItems(card) {
  return card.media || (card.image_url ? [{id:card.asset_id, url:card.image_url, media_type:card.media_type || 'IMAGE'}] : []);
}
function mediaElement(item, className = 'post-photo') {
  const video = item.media_type === 'VIDEO';
  const node = el(video ? 'video' : 'img', undefined, className);
  node.src = item.url;
  if (video) { node.controls = true; node.preload = 'metadata'; }
  else node.alt = '貼文圖片';
  return node;
}
let toastTimer;
function toast(message) {
  const node = document.getElementById("toast");
  node.textContent = message; node.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => node.hidden = true, 5000);
}
document.querySelectorAll("time[data-timestamp]").forEach(node => {
  node.textContent = new Date(Number(node.dataset.timestamp) * 1000).toLocaleString("zh-TW", {timeZone: "Asia/Taipei", hour12: false}) + "（台北）";
});
document.querySelectorAll("[data-confirm]").forEach(node => node.addEventListener("click", event => {
  if (!confirm(node.dataset.confirm)) event.preventDefault();
}));

const compose = document.getElementById("compose");
if (compose) {
  const accounts = JSON.parse(compose.dataset.accounts);
  const cardsNode = document.getElementById("cards");
  const dialog = document.getElementById("preview-dialog");
  let mode = "same", cards = [], pendingPayload, uploading = 0, dirty = false;
  const emptyCard = () => ({text: "", media: [], first_reply: "", accounts: []});
  function changed() { dirty = true; document.getElementById("draft-status").textContent = "有尚未儲存的變更"; updateCount(); }
  function updateCount() {
    document.getElementById("target-count").textContent = cards.reduce((n, card) => n + card.accounts.length, 0);
  }
  function renderCards() {
    cardsNode.replaceChildren();
    document.getElementById("add-card").hidden = mode === "same";
    cards.forEach((card, index) => {
      const node = document.getElementById("card-template").content.cloneNode(true);
      node.querySelector(".card-title").textContent = `圖文 ${index + 1}`;
      const remove = node.querySelector(".remove-card");
      remove.hidden = cards.length === 1;
      remove.addEventListener("click", () => {
        if (uploading) return toast("請等素材上傳完成。");
        if (!confirm("確定移除這張圖文？")) return;
        cards.splice(index, 1); changed(); renderCards();
      });
      const text = node.querySelector(".post-text"), counter = node.querySelector(".char-count");
      text.value = card.text;
      function count() { const length = Array.from(text.value).length; counter.textContent = `${length} / 500 字元`; counter.classList.toggle("danger", length > 500); }
      count(); text.addEventListener("input", () => { card.text = text.value; count(); changed(); });
      card.media = mediaItems(card);
      const reply = node.querySelector('.first-reply'), replyCount = node.querySelector('.reply-count');
      reply.value = card.first_reply || '';
      const countReply = () => { replyCount.textContent = `${Array.from(reply.value).length} / 500 字元`; };
      countReply(); reply.addEventListener('input', () => {card.first_reply = reply.value; countReply(); changed();});
      const list = node.querySelector('.media-list'), file = node.querySelector('.post-image');
      card.media.forEach((item, itemIndex) => {
        const box = el('div', undefined, 'media-item');
        box.append(mediaElement(item, item.media_type === 'VIDEO' ? 'video-preview post-photo' : 'image-preview'));
        const controls = el('div', undefined, 'flex gap-3 mt-2');
        controls.append(el('span', `${itemIndex + 1}`, 'fineprint'));
        const remove = el('button', '移除', 'linkbutton'); remove.type = 'button';
        remove.addEventListener('click', () => { if (uploading) return toast('請等素材上傳完成。'); card.media.splice(itemIndex, 1); changed(); renderCards(); });
        const earlier = el('button', '往前', 'linkbutton'); earlier.type = 'button'; earlier.disabled = itemIndex === 0;
        earlier.addEventListener('click', () => { if (uploading) return toast('請等素材上傳完成。'); [card.media[itemIndex-1],card.media[itemIndex]] = [card.media[itemIndex],card.media[itemIndex-1]]; changed(); renderCards(); });
        controls.append(earlier, remove); box.append(controls); list.append(box);
      });
      file.addEventListener('change', async () => {
        const files = [...file.files]; if (!files.length) return;
        if (card.media.length + files.length > 20) { file.value = ''; return toast('每篇最多 20 張圖片。'); }
        const hasVideo = card.media.some(m => m.media_type === 'VIDEO') || files.some(f => /\.(mp4|mov)$/i.test(f.name));
        if (hasVideo && card.media.length + files.length > 1) { file.value = ''; return toast('影片請單獨發布，不可與圖片混用。'); }
        uploading++; file.disabled = true;
        try {
          for (let i=0; i<files.length; i++) {
            toast(`素材上傳中 ${i+1} / ${files.length}……`);
            const body = new FormData(); body.append('image', files[i]);
            const data = await api('/api/assets', {method:'POST',body});
            card.media.push({id:data.id,url:data.url,media_type:data.media_type}); changed();
          }
          toast('素材已上傳');
        } catch (error) { toast(error.message + ' 已成功上傳的素材會保留。'); }
        finally { uploading--; renderCards(); }
      });
      const choices = node.querySelector(".account-options");
      accounts.forEach(account => {
        const label = el("label", undefined, "checklabel");
        const input = document.createElement("input"); input.type = mode === "same" ? "checkbox" : "radio";
        input.name = `account-${index}`; input.value = account.id; input.checked = card.accounts.includes(account.id);
        input.disabled = account.status !== "connected" || account.expires_at * 1000 <= Date.now();
        input.addEventListener("change", () => {
          card.accounts = mode === "different" ? [account.id] : input.checked ? [...card.accounts, account.id] : card.accounts.filter(id => id !== account.id);
          changed();
        });
        label.append(input, el("span", account.label + (input.disabled ? " · 需重新連結" : ""))); choices.append(label);
      });
      cardsNode.append(node);
    }); updateCount();
  }
  document.querySelectorAll('input[name="mode"]').forEach(input => input.addEventListener("change", () => {
    if (uploading || (cards.length > 1 && input.value === "same" && !confirm("切換後只保留第一張圖文，確定繼續？"))) {
      document.querySelector(`input[name="mode"][value="${mode}"]`).checked = true; return;
    }
    mode = input.value;
    if (mode === "same") cards = [cards[0]];
    else cards.forEach(card => card.accounts = card.accounts.slice(0, 1));
    changed(); renderCards();
  }));
  document.getElementById("add-card").addEventListener("click", () => {
    if (uploading) return toast("請等素材上傳完成。");
    if (cards.length >= 20) return toast("每批最多 20 張圖文。");
    cards.push(emptyCard()); changed(); renderCards();
  });
  const payload = () => ({mode, cards: cards.map(card => ({text: card.text, asset_ids: mediaItems(card).map(m => m.id), first_reply: card.first_reply || "", accounts: [...card.accounts]}))});
  document.getElementById("save-draft").addEventListener("click", async () => {
    if (uploading) return toast("請等素材上傳完成。");
    try { await api("/api/draft", {method: "PUT", body: JSON.stringify(payload())}); dirty = false; document.getElementById("draft-status").textContent = "草稿已儲存"; toast("草稿已儲存"); }
    catch (error) { toast(error.message); }
  });
  document.getElementById("new-draft").addEventListener("click", async () => {
    if (uploading || !confirm("確定清空目前草稿？")) return;
    try {
      await api("/api/draft", {method: "DELETE"}); cards = [emptyCard()]; mode = "same"; dirty = false;
      document.querySelector('input[name="mode"][value="same"]').checked = true;
      document.getElementById("draft-status").textContent = "新的草稿"; renderCards();
    } catch (error) { toast(error.message); }
  });
  document.getElementById("preview").addEventListener("click", () => {
    if (uploading) return toast("請等素材上傳完成。");
    if (cards.some(card => !card.accounts.length || (!card.text.trim() && !mediaItems(card).length) || Array.from(card.text.trim()).length > 500 || Array.from((card.first_reply || "").trim()).length > 500)) {
      return toast("請為每張圖文填入內容、選擇帳號，並確認文字不超過 500 字元。");
    }
    pendingPayload = payload();
    const content = document.getElementById("preview-content"); content.replaceChildren();
    cards.forEach((card, index) => {
      const item = el("article", undefined, "panel");
      const names = card.accounts.map(id => accounts.find(account => account.id === id)?.label || "無法使用的帳號");
      item.append(el("h3", `圖文 ${index + 1} → ${names.join(" ／ ")}`, "font-semibold"), el("p", card.text, "post-copy my-3"));
      mediaItems(card).forEach(m => item.append(mediaElement(m)));
      if (card.first_reply) item.append(el('p', '第一則回覆：' + card.first_reply, 'post-copy my-3'));
      content.append(item);
    });
    document.getElementById("publish-error").hidden = true; dialog.showModal();
  });
  document.getElementById("close-preview").addEventListener("click", () => dialog.close());
  document.getElementById("publish").addEventListener("click", async event => {
    const button = event.currentTarget;
    if (button.disabled) return;
    button.disabled = true;
    try {
      const encoded = JSON.stringify(pendingPayload);
      let pending = JSON.parse(sessionStorage.getItem("pending-publication") || "null");
      if (!pending || pending.payload !== encoded) pending = {payload: encoded, key: crypto.randomUUID()};
      sessionStorage.setItem("pending-publication", JSON.stringify(pending));
      const data = await api("/api/batches", {method: "POST", body: JSON.stringify({...pendingPayload, request_key: pending.key})});
      sessionStorage.removeItem("pending-publication"); dirty = false;
      location.href = `/batches/${data.id}`;
    } catch (error) {
      const node = document.getElementById("publish-error"); node.textContent = error.message + " 可重按確認，系統會避免重複提交。"; node.hidden = false;
      button.disabled = false;
    }
  });
  window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  (async () => {
    try {
      const draft = await api("/api/draft");
      if (draft) { mode = draft.mode; cards = draft.cards; document.querySelector(`input[name="mode"][value="${mode}"]`).checked = true; document.getElementById("draft-status").textContent = "已載入上次儲存的草稿"; }
    } catch (error) { toast(error.message); }
    if (!cards.length) cards = [emptyCard()]; renderCards();
  })();
}

const batch = document.getElementById("batch");
if (batch) {
  const labels = {pending: "待發布", processing: "處理中", success: "成功", failed: "失敗", uncertain: "待確認"};
  let timer, busy = false;
  async function loadBatch() {
    if (busy) return;
    busy = true; clearTimeout(timer);
    try {
      const data = await api(`/api/batches/${batch.dataset.id}`);
      document.getElementById("batch-error").hidden = true;
      const counts = {};
      data.jobs.forEach(job => counts[job.status] = (counts[job.status] || 0) + 1);
      document.getElementById("batch-summary").textContent = Object.entries(counts).map(([state, n]) => `${n} ${labels[state]}`).join(" · ");
      document.getElementById("worker-notice").hidden = data.worker_online || !counts.pending;
      const node = document.getElementById("job-results"); node.replaceChildren();
      data.jobs.forEach(job => {
        const card = el("article", undefined, "panel");
        const heading = el("div", undefined, "flex justify-between items-center gap-3");
        heading.append(el("h2", job.account_label), el("span", labels[job.status], `badge ${job.status === "failed" ? "bad" : job.status === "uncertain" ? "warn" : ""}`));
        card.append(heading, el("p", `圖文 ${job.card_index + 1} · 已嘗試 ${job.attempts} 次`, "fineprint"), el("p", job.text, "post-copy my-4"));
        (job.media?.length ? job.media : mediaItems(job)).forEach(m => card.append(mediaElement(m)));
        if (job.first_reply) {
          const state = job.status !== 'success' && job.reply_status === 'pending' ? '等待主貼文成功' : labels[job.reply_status] || job.reply_status;
          card.append(el('p', `第一則回覆：${state}`, 'font-semibold mt-4'), el('p', job.first_reply, 'post-copy'));
          if (job.reply_error) card.append(el('p', job.reply_error, 'notice'));
          if (job.status === 'success' && ['failed','uncertain'].includes(job.reply_status)) {
            const action = job.reply_status === 'failed' ? 'retry' : 'reconcile';
            const button = el('button', action === 'retry' ? '只重試第一則回覆' : '查核第一則回覆', 'secondary mt-3');
            button.addEventListener('click', async () => { button.disabled = true; try { await api(`/api/jobs/${job.id}/reply/${action}`, {method:'POST'}); await loadBatch(); } catch(error) {toast(error.message); button.disabled=false;} });
            card.append(button);
          }
        }
        if (job.error) card.append(el("p", job.error, "notice mt-4"));
        if (job.permalink && (job.permalink.startsWith("https://") || job.permalink.startsWith("/demo/posts/"))) {
          const link = el("a", job.permalink.startsWith("/demo/") ? "查看模擬貼文 ↗" : "查看 Threads 貼文 ↗", "secondary mt-4");
          link.href = job.permalink; link.target = "_blank"; link.rel = "noopener noreferrer"; card.append(link);
        } else if (job.post_id) card.append(el("p", `貼文 ID：${job.post_id}`, "fineprint"));
        if (["failed", "uncertain"].includes(job.status)) {
          const action = job.status === "failed" ? "retry" : "reconcile";
          const button = el("button", action === "retry" ? "只重試此項目" : "查核結果", "secondary mt-3");
          button.addEventListener("click", async () => {
            button.disabled = true;
            try { await api(`/api/jobs/${job.id}/${action}`, {method: "POST"}); await loadBatch(); }
            catch (error) { toast(error.message); button.disabled = false; }
          }); card.append(button);
        }
        node.append(card);
      });
      if (counts.pending || counts.processing || data.jobs.some(j => j.status === 'success' && ['pending','processing'].includes(j.reply_status))) timer = setTimeout(loadBatch, 2500);
    } catch (error) {
      const node = document.getElementById("batch-error"); node.textContent = error.message; node.hidden = false;
      timer = setTimeout(loadBatch, 10000);
    } finally { busy = false; }
  }
  loadBatch();
}
