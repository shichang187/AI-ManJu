/* Additive catalog UI; existing storyboard and composition stay in index.html. */
(() => {
  const root = document.getElementById('promptLibrary');
  const html = escapeHtml;
  let page = 1, stage = '漫剧', current = null, sequence = 0, selectedShot = 0;
  let favorites = new Set();
  root.innerHTML = `
    <div class="pl-heading"><div><span class="pl-eyebrow">PROMPT ATELIER · 提示词工坊</span>
      <h2>把灵感，变成可以开拍的画面</h2><p>从角色到镜头，挑选、修改，再放进你的分镜。</p></div>
      <span class="pl-local">本地资料库</span></div>
    <div class="pl-content">
      <div class="pl-topline"><p id="plStats" role="status">正在读取提示词…</p>
        <button class="secondary" id="plFavorites" aria-pressed="false">只看收藏</button></div>
      <div class="pl-search"><label for="plQuery" class="pl-sr">搜索提示词</label>
        <input id="plQuery" type="search" placeholder="搜索角色、场景、运镜，或原文关键词…">
        <button id="plSearchButton">搜索</button></div>
      <div id="plStages" class="pl-stages" aria-label="创作阶段"></div>
      <div id="plCards" class="pl-cards" aria-live="polite"></div>
      <div class="pl-paging"><button class="secondary" id="plPrev">上一页</button>
        <span id="plPage"></span><button class="secondary" id="plNext">下一页</button></div>
      <details class="pl-import"><summary>导入我的资料 · 查看导入记录</summary>
        <p>选择已下载、已解压的提示词文件夹。支持 TXT、MD、DOCX、CSV、XLSX；PDF 需可选依赖，旧 DOC 需先提取文本。资料保存在本机。</p>
        <label for="plFolder">本地文件夹的完整路径</label>
        <div class="pl-search"><input id="plFolder" placeholder="例如 D:\\提示词资料">
          <button id="plImport">导入 / 更新</button><button id="plUndoImport" class="secondary">撤销上次导入</button></div>
        <p id="plImportStatus" role="status"></p><div id="plReport"></div>
      </details>
    </div>
    <dialog id="plDialog" aria-labelledby="plTitle">
      <div class="pl-dialog-head"><div><span id="plMeta" class="pl-eyebrow"></span><h2 id="plTitle"></h2></div>
        <button class="secondary" id="plClose" aria-label="关闭提示词编辑器">关闭</button></div>
      <p id="plSource" class="pl-source"></p>
      <p id="plUsage" class="pl-note"></p>
      <div id="plVariables" class="pl-variables"></div>
      <div class="pl-editor-actions"><button class="secondary" id="plFill">填入当前角色与镜头</button>
        <button class="secondary" id="plOriginal">恢复资料原文</button>
        <button class="secondary" id="plContext">附上当前故事</button></div>
      <label for="plEditor">使用前预览并修改</label><textarea id="plEditor" rows="13"></textarea>
      <div class="pl-dialog-footer"><button id="plCopy">复制提示词</button>
        <label for="plShot">应用到</label><select id="plShot"></select><button id="plApply">替换镜头提示词</button>
        <button class="secondary" id="plUndo">撤销上次替换</button>
        <button class="secondary" id="plDirector">用作导演指令</button></div>
      <details><summary>个人版本与效果记录</summary><label>阶段<select id="plPersonalStage">
        <option>角色</option><option>场景</option><option>剧本</option><option>分镜</option><option>运镜</option><option>风格</option><option>扩展</option></select></label>
        <label>用途类别<input id="plPersonalKind" value="个人模板"></label><label>实际输入<textarea id="plActualInput" rows="2"></textarea></label>
        <label>实际结果<textarea id="plActualResult" rows="2"></textarea></label><label>人工评语<textarea id="plEvaluation" rows="2" placeholder="未验证"></textarea></label>
        <button class="secondary" id="plPersonalSave">保存个人版本</button></details>
      <p id="plFeedback" role="status"></p>
    </dialog>`;
  const $ = id => document.getElementById(id);
  let favoriteOnly = false, timer;
  function error(message) { $('plFeedback').textContent = message; toast(message); }
  function report(data) {
    if (!data) { $('plReport').textContent = '尚未导入本地资料。可以先试用原创入门模板。'; return; }
    $('plReport').innerHTML = `<p>${html(data.time)} · 读取 ${data.read_files} 个文件，去重后 ${data.entries} 条，
      漫剧分类 ${data.primary} 条；跳过 ${data.ignored_media} 个媒体等文件。</p>
      <details><summary>待处理 ${data.issues.length} 个文件</summary><ul>${data.issues.map(i =>
        `<li><span>${html(i.file)}</span> — ${html(i.reason)}</li>`).join('')}</ul></details>`;
  }
  async function load() {
    const request = ++sequence;
    const params = new URLSearchParams({page, stage, q: $('plQuery').value});
    if (favoriteOnly) params.set('favorites', '1');
    try {
      const data = await api('/api/prompts?' + params);
      if (request !== sequence) return;
      favorites = new Set(data.favorites);
      page = data.page;
      $('plStats').textContent = `${data.private_count} 条私有资料 · ${data.builtin_count} 条原创入门模板 · 当前 ${data.total} 条`;
      const stages = ['漫剧', ...Object.keys(data.counts), '全部'];
      $('plStages').innerHTML = stages.map(s => `<button class="pl-stage ${stage === (s === '全部' ? '' : s) ? 'active' : ''}"
        data-stage="${html(s)}" aria-pressed="${stage === (s === '全部' ? '' : s)}">${html(s)}${s in data.counts ? ' ' + data.counts[s] : ''}</button>`).join('');
      $('plStages').querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
        stage = b.dataset.stage === '全部' ? '' : b.dataset.stage; page = 1; load();
      }));
      $('plCards').innerHTML = data.items.length ? data.items.map(e => `<article class="pl-card">
        <div class="pl-card-top"><span>${html(e.stage)} · ${html(e.kind)}</span>
          <button class="pl-star" data-star="${e.id}" aria-label="${favorites.has(e.id) ? '取消收藏' : '收藏'} ${html(e.title)}"
          aria-pressed="${favorites.has(e.id)}">${favorites.has(e.id) ? '★' : '☆'}</button></div>
        <h3>${html(e.title)}</h3><p>${html(e.preview)}</p><div class="pl-card-bottom">
          <span>${html(e.usage)}${e.private ? ' · 私有' : ''}</span><button class="secondary" data-open="${e.id}">查看 / 使用</button></div></article>`).join('') :
        '<div class="pl-empty">没有找到提示词。试试其他关键词、切换分类，或导入本地资料。</div>';
      $('plCards').querySelectorAll('[data-open]').forEach(b => b.addEventListener('click', () => open(b.dataset.open)));
      $('plCards').querySelectorAll('[data-star]').forEach(b => b.addEventListener('click', async () => {
        b.disabled = true;
        const updated = new Set(favorites);
        updated.has(b.dataset.star) ? updated.delete(b.dataset.star) : updated.add(b.dataset.star);
        try { await api('/api/prompts/favorites', {favorites: [...updated]}); await load(); }
        catch (e) { b.disabled = false; toast('收藏保存失败：' + e.message); }
      }));
      $('plPage').textContent = `${page} / ${data.pages}`;
      $('plPrev').disabled = page <= 1; $('plNext').disabled = page >= data.pages;
      if (!$('plFolder').value) $('plFolder').value = data.folder;
      report(data.report);
    } catch (e) { if (request === sequence) $('plCards').textContent = '读取失败：' + e.message; }
  }
  function shots() { return state.storyboard?.shots || []; }
  function shotOptions() {
    $('plShot').innerHTML = shots().length ? shots().map((s, i) => `<option value="${i}">镜头 ${i + 1} · ${html((s.description || '').slice(0, 28))}</option>`).join('') :
      '<option value="">先生成分镜</option>';
    $('plShot').value = shots().length ? String(Math.min(selectedShot, shots().length - 1)) : '';
    $('plApply').disabled = !shots().length;
    $('plUndo').disabled = !shots()[Number($('plShot').value)]?.prompt_before_library;
  }
  function renderVariables() {
    const names = [...new Set([...current.text.matchAll(/\{\{([^{}\n]{1,40})\}\}/g)].map(m => m[1]))];
    $('plVariables').innerHTML = names.map((name, i) => `<label>${html(name)}<input data-variable="${html(name)}" id="plVar${i}" placeholder="填写${html(name)}"></label>`).join('');
    $('plVariables').querySelectorAll('input').forEach(input => input.addEventListener('input', renderText));
  }
  function renderText() {
    const values = {};
    $('plVariables').querySelectorAll('input').forEach(i => { if (i.value.trim()) values[i.dataset.variable] = i.value.trim(); });
    $('plEditor').value = current.text.replace(/\{\{([^{}\n]{1,40})\}\}/g, (token, name) => values[name] ?? token);
  }
  async function open(id, index = selectedShot) {
    try {
      selectedShot = index; current = await api('/api/prompts/detail?id=' + encodeURIComponent(id));
      $('plTitle').textContent = current.title;
      $('plMeta').textContent = current.stage + ' · ' + current.kind + (current.private ? ' · 私有资料' : ' · 原创入门模板');
      $('plSource').textContent = current.sources.map(s => s.label + ' / ' + s.file).join('\n');
      $('plUsage').textContent = (current.usage === '先给对话模型' ?
        '这是完整创作指令。建议复制给对话模型，拿到输出后再用作镜头提示词；请先编辑，再应用到分镜。' :
        current.usage === '参考链接' ? '这条资料只有参考链接。工作台未读取链接内容。' :
        '根据你的角色、场景和镜头修改后使用。替换只改变选定镜头的提示词。') +
        ' 模型适用性与成片效果未验证；平台功能以实际页面为准。';
      $('plFeedback').textContent = ''; $('plPersonalStage').value=current.stage;
      $('plPersonalKind').value=current.kind; renderVariables(); renderText(); shotOptions();
      if (!$('plDialog').open) $('plDialog').showModal();
    } catch (e) { toast(e.message); }
  }
  $('plFill').addEventListener('click', () => {
    const inputs = collectInputs(), sb = state.storyboard, shot = shots()[Number($('plShot').value)];
    const values = {'角色': sb?.character_desc || inputs.character_desc, '场景': sb?.environment_anchor,
      '动作': shot?.description, '风格': inputs.style, '时长': shot?.duration, '故事': inputs.text,
      '镜头': shot?.camera_movement};
    $('plVariables').querySelectorAll('input').forEach(i => { if (values[i.dataset.variable]) i.value = values[i.dataset.variable]; });
    if ($('plVariables').querySelector('input')) renderText();
    else {
      const marker = '\n\n【当前镜头设定】';
      $('plEditor').value = $('plEditor').value.split(marker)[0] + marker + '\n角色：' + (values['角色'] || '请填写') +
        '\n环境：' + (values['场景'] || '请填写') + '\n动作：' + (values['动作'] || '请填写') +
        '\n运镜：' + (values['镜头'] || '请填写') + '\n时长：' + (values['时长'] || '请填写') + ' 秒';
    }
  });
  $('plOriginal').addEventListener('click', () => { renderVariables(); renderText(); $('plFeedback').textContent = '已恢复资料原文。'; });
  $('plContext').addEventListener('click', () => {
    const sb = state.storyboard, inputs = collectInputs();
    $('plEditor').value += '\n\n【我的创作上下文】\n故事：' + inputs.text + '\n角色：' +
      (sb?.character_desc || inputs.character_desc) + '\n环境：' + (sb?.environment_anchor || '请根据故事设定');
  });
  $('plCopy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('plEditor').value); $('plFeedback').textContent = '已复制，可粘贴到你使用的模型。'; }
    catch { error('复制失败，请选中编辑框文字手动复制。'); }
  });
  async function persistChange(undo) {
    const shot = shots()[Number($('plShot').value)];
    if (!shot) return error('请先生成分镜。');
    const prompt = undo ? shot.prompt_before_library : $('plEditor').value.trim();
    if (!prompt || /\{\{[^{}]+\}\}/.test(prompt)) return error('请填写所有模板变量，或编辑后再应用。');
    const before = JSON.parse(JSON.stringify(shot));
    if (undo) { shot.prompt = prompt; delete shot.prompt_before_library; delete shot.prompt_library_id; }
    else { shot.prompt_before_library = shot.prompt; shot.prompt = prompt; shot.prompt_library_id = current.id; }
    $('plApply').disabled = true; $('plUndo').disabled = true;
    try {
      await api('/api/save-storyboard', state.storyboard);
      renderStoryboard(); saveState(); shotOptions();
      $('plFeedback').textContent = undo ? '已恢复上次替换前的提示词。' : '已保存到选定镜头。可以撤销上次替换。';
    } catch (e) { Object.keys(shot).forEach(k => delete shot[k]); Object.assign(shot, before); shotOptions(); error('保存失败：' + e.message); }
  }
  $('plApply').addEventListener('click', () => persistChange(false));
  $('plDirector').addEventListener('click', () => { $('plDialog').close(); upgradeUI.director($('plEditor').value, current.id); });
  $('plPersonalSave').addEventListener('click', async () => {
    try { await api('/api/prompts/personal', {source_id:current.id,title:current.title+' · 个人版本',text:$('plEditor').value,
      stage:$('plPersonalStage').value,kind:$('plPersonalKind').value,input:$('plActualInput').value,
      result:$('plActualResult').value,evaluation:$('plEvaluation').value || '未验证'});
      $('plFeedback').textContent='个人版本已保存，来源原文保留。'; await load(); }
    catch(e) { error('保存失败：'+e.message); }
  });
  $('plUndo').addEventListener('click', () => persistChange(true));
  $('plShot').addEventListener('change', () => { selectedShot = Number($('plShot').value); $('plUndo').disabled = !shots()[selectedShot]?.prompt_before_library; });
  $('plClose').addEventListener('click', () => $('plDialog').close());
  $('plSearchButton').addEventListener('click', () => { page = 1; load(); });
  $('plQuery').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { page = 1; load(); }, 250); });
  $('plQuery').addEventListener('keydown', e => { if (e.key === 'Enter') { clearTimeout(timer); page = 1; load(); } });
  $('plFavorites').addEventListener('click', () => { favoriteOnly = !favoriteOnly; $('plFavorites').setAttribute('aria-pressed', favoriteOnly); page = 1; load(); });
  $('plPrev').addEventListener('click', () => { page--; load(); });
  $('plNext').addEventListener('click', () => { page++; load(); });
  $('plImport').addEventListener('click', async () => {
    $('plImport').disabled = true; $('plImportStatus').textContent = '正在读取本地资料，请稍候…';
    try {
      await api('/api/prompts/import', {folder: $('plFolder').value.trim()});
      while(true) {
        await new Promise(resolve=>setTimeout(resolve,500));
        const job=await api('/api/prompts/import-progress');
        $('plImportStatus').textContent=`正在导入 ${job.progress}%；旧库仍可搜索。`;
        if(job.status==='error') throw Error(job.error);
        if(job.status==='done') { $('plImportStatus').textContent=`导入完成：${job.report.read_files} 文件，${job.report.entries} 条；未提取或待处理 ${job.report.issues.length} 项。`; break; }
      }
      page = 1; await load();
    } catch (e) { $('plImportStatus').textContent = '导入失败：' + e.message; }
    finally { $('plImport').disabled = false; }
  });
  $('plUndoImport').addEventListener('click', async () => {
    try { await api('/api/prompts/undo-import', {}); $('plImportStatus').textContent='已恢复上次导入前的资料库。'; await load(); }
    catch(e) { $('plImportStatus').textContent='撤销失败：'+e.message; }
  });
  window.promptLibrary = {browse(index = 0) {
    selectedShot = index; upgradeUI.tab(true); root.scrollIntoView({behavior: 'smooth', block: 'start'}); $('plQuery').focus({preventScroll: true});
  }};
  load();
})();
