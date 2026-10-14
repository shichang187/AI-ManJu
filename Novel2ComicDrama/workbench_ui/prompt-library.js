/* Additive catalog UI; existing storyboard and composition stay in index.html. */
(() => {
  const root = document.getElementById('promptLibrary');
  const html = escapeHtml;
  let collection = 'curated', page = 1, stage = '', current = null, sequence = 0, selectedShot = 0, purpose = 'shot';
  let favorites = new Set();
  root.innerHTML = `
    <div class="pl-heading"><div><span class="pl-eyebrow">PROMPT ATELIER · 提示词工坊</span>
      <h2>为这一步选择合适的资料</h2><p>默认精选经过内容审核；先带入设定并预览。实际生成效果未验证。</p></div>
      <span class="pl-local">本地资料库</span></div>
    <div class="pl-content">
      <div class="pl-topline"><p id="plStats" role="status">正在读取提示词…</p>
        <button class="secondary" id="plFavorites" aria-pressed="false">只看收藏</button></div>
      <div class="pl-search"><label for="plQuery" class="pl-sr">搜索提示词</label>
        <input id="plQuery" type="search" placeholder="搜索角色、场景、运镜，或原文关键词…">
        <button id="plSearchButton">搜索</button></div>
      <div class="pl-stages"><button id="plCurated">精选模板</button><button id="plAllReferences" class="secondary">高级：全部旧资料与个人版本</button></div><div id="plPurposes" class="pl-stages" aria-label="资料用途"></div>
      <details id="plReferenceEntry"><summary>高级：参考原文</summary><p>原文、教程、示例与未核实条目。原文不自动发送给模型。</p><button id="plReferences" class="secondary">查看参考原文</button></details>
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
      <details><summary>高级：逐项编辑模板变量</summary><div id="plVariables" class="pl-variables"></div></details>
      <div class="pl-editor-actions"><button class="secondary" id="plFill">填入当前角色与镜头</button>
        <button class="secondary" id="plOriginal">恢复当前模板</button>
        <button class="secondary" id="plContext">附上当前故事</button></div>
      <label>画幅<select id="plAspect"><option>9:16</option><option>16:9</option><option>1:1</option><option>4:3</option><option>3:4</option></select></label><p id="plDependencies" role="status"></p><details><summary>来源、审核说明与原文</summary><p id="plReview"></p><textarea id="plSourceOriginal" rows="5" readonly aria-label="来源可见原文"></textarea></details><label for="plEditor">使用前预览并修改</label><textarea id="plEditor" rows="13"></textarea>
      <div class="pl-dialog-footer"><button id="plCopy">复制提示词</button>
        <label for="plShot">应用到</label><select id="plShot"></select><button id="plApply">替换镜头提示词</button>
        <button class="secondary" id="plUndo">撤销上次替换</button>
        <button class="secondary" id="plDirector">用作导演指令</button></div>
      <details><summary>个人版本与效果记录</summary><label>阶段<select id="plPersonalStage">
        <option>角色</option><option>场景</option><option>剧本</option><option>分镜</option><option>运镜</option><option>风格</option><option>扩展</option></select></label>
        <label>用途<select id="plPersonalPurpose"><option value="instruction">给对话模型的指令</option><option value="shot">直接用于镜头的模板</option><option value="reference">参考原文</option></select></label>
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
    const params = new URLSearchParams({collection, page, stage, purpose, q: $('plQuery').value});
    if (favoriteOnly) params.set('favorites', '1');
    try {
      const data = await api('/api/prompts?' + params);
      if (request !== sequence) return;
      favorites = new Set(data.favorites);
      page = data.page;
      const labels = {instruction:'给对话模型的指令',shot:'镜头模板',...(collection==='reference'?{reference:'参考原文'}:{})};
      $('plPurposes').innerHTML=Object.entries(labels).map(([key,label])=>`<button data-purpose="${key}" aria-pressed="${purpose===key}">${label} · ${data.purpose_counts[key]}</button>`).join('');
      $('plPurposes').querySelectorAll('button').forEach(b=>b.onclick=()=>{purpose=b.dataset.purpose;page=1;load();});
      $('plStats').textContent = collection==='curated'?`${data.curated_count} 条精选 · 内容审核通过 · 实际效果未验证`:`高级资料：${data.private_count} 条旧私有记录与个人版本 · 分类是规则建议，未全库优选`;
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
          <span>${html(e.input_mode||e.usage)} · ${html(e.test_status||'未实测')}${e.private ? ' · 私有' : ''}</span><button class="secondary" data-open="${e.id}">查看 / 使用</button></div></article>`).join('') :
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
      $('plMeta').textContent = current.purpose_label + ' · ' + current.kind + (current.private ? ' · 私有资料' : ' · 原创入门模板');
      $('plSource').replaceChildren();
      current.sources.forEach(source=>{
        const node=document.createElement(/^https:\/\//.test(source.file)?'a':'span');
        node.textContent=source.label+(node.tagName==='A'?' · 作品入口':' / '+source.file);
        if(node.tagName==='A'){node.href=source.file;node.target='_blank';node.rel='noopener noreferrer';}
        $('plSource').append(node,document.createElement('br'));
      });
      $('plReview').textContent=(current.scores?'内容评分 '+current.scores.reduce((a,b)=>a+b,0)+'/10 · ':'')+(current.review_reason||current.purpose_basis)+' · '+(current.license_status||'许可未验证');
      $('plSourceOriginal').value=current.original_text||current.text;
      $('plDependencies').textContent=(current.input_mode||'')+' · '+(current.scope||'')+' · '+(current.test_status||'未实测')+
        ((current.required_assets||[]).length?' · 需要素材：'+current.required_assets.join('、'):' · 无必需参考素材');
      $('plAspect').hidden=current.collection!=='curated'||current.purpose!=='shot';

      $('plUsage').textContent = (current.purpose === 'instruction' ?
        '这是完整创作指令。建议复制给对话模型，拿到输出后再用作镜头提示词；请先编辑，再应用到分镜。' :
        current.purpose === 'reference' ? '这是参考原文，需要阅读并选取；不会自动应用到镜头或发送给模型。' :
        '根据你的角色、场景和镜头修改后使用。替换只改变选定镜头的提示词。') +
        ' 模型适用性与成片效果未验证；平台功能以实际页面为准。';
      $('plFeedback').textContent = ''; $('plPersonalStage').value=current.stage;
      $('plPersonalKind').value=current.kind; renderVariables(); renderText(); shotOptions();
      $('plPersonalPurpose').value=current.purpose;
      $('plApply').hidden=current.purpose!=='shot'||current.scope==='连续多镜'; $('plDirector').hidden=current.purpose!=='instruction';
      $('plFill').hidden=current.purpose==='reference';
      if (!$('plDialog').open) $('plDialog').showModal();
    } catch (e) { toast(e.message); }
  }
  $('plAspect').onchange=()=>{$('plFill').click();};
  $('plFill').addEventListener('click', async () => {
    if(current.collection==='curated' && current.purpose==='shot'){
      const shot=shots()[Number($('plShot').value)],id=current.id;
      if(!shot)return error('请先生成分镜');
      try{
        const data=await api('/api/prompts/adapt',{id,shot_id:shot.shot_id,aspect:$('plAspect').value});
        if(current.id!==id||shots()[Number($('plShot').value)]?.shot_id!==data.shot_id)return;
        $('plVariables').querySelectorAll('input').forEach(i=>{i.value=data.variables[i.dataset.variable]||'';});
        $('plEditor').value=data.text;
        $('plFeedback').textContent=data.missing_variables.length?'需要填写：'+data.missing_variables.join('、'):'当前角色、场景、对白归属与时长已填入；请预览后应用。';
      }catch(e){error(e.message);}return;
    }

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
  $('plOriginal').addEventListener('click', () => { renderVariables(); renderText(); $('plFeedback').textContent = '已恢复当前模板。'; });
  $('plContext').addEventListener('click', () => {
    const sb = state.storyboard, inputs = collectInputs();
    $('plEditor').value += '\n\n【我的创作上下文】\n故事：' + inputs.text + '\n角色：' +
      (sb?.character_desc || inputs.character_desc) + '\n环境：' + (sb?.environment_anchor || '请根据故事设定');
  });
  $('plCopy').addEventListener('click', async () => {
    if(/\{\{[^{}]+\}\}/.test($('plEditor').value))return error('请先填写全部变量再复制');
    if((current.required_assets||[]).length)return error('此参考模板需要素材：'+current.required_assets.join('、')+'；请在平台准备完整素材后使用原文。');
    try { await navigator.clipboard.writeText($('plEditor').value); $('plFeedback').textContent = '已复制，可粘贴到你使用的模型。'; }
    catch { error('复制失败，请选中编辑框文字手动复制。'); }
  });
  async function persistChange(undo) {
    const shot = shots()[Number($('plShot').value)];
    if (!shot) return error('请先生成分镜。');
    if(!undo && current.purpose!=='shot') return error('这条资料需要先阅读或用于模型，不能直接替换镜头。');
    const prompt = undo ? shot.prompt_before_library : $('plEditor').value.trim();
    if (!prompt || /\{\{[^{}]+\}\}/.test(prompt)) return error('请填写所有模板变量，或编辑后再应用。');
    if(!undo && current.scope==='连续多镜')return error('连续多镜模板不能覆盖单镜，请按完整时间轴绑定素材');
    if(!undo && (current.required_assets||[]).length)return error('缺少参考素材：'+current.required_assets.join('、'));
    if(!undo && current.duration_range && (shot.duration<current.duration_range[0]||shot.duration>current.duration_range[1]))return error('镜头时长超出适配范围，请拆分或另选模板');
    const before = JSON.parse(JSON.stringify(shot));
    if (undo) { shot.prompt = prompt; delete shot.prompt_before_library; delete shot.prompt_library_id; }
    else { shot.prompt_before_library = shot.prompt; shot.prompt = prompt; shot.prompt_library_id = current.id; }
    $('plApply').disabled = true; $('plUndo').disabled = true;
    try {
      await api('/api/save-storyboard', state.storyboard);
      renderStoryboard(); await saveState(); shotOptions();
      $('plFeedback').textContent = undo ? '已恢复上次替换前的提示词。' : '已保存到选定镜头。可以撤销上次替换。';
    } catch (e) { Object.keys(shot).forEach(k => delete shot[k]); Object.assign(shot, before); shotOptions(); error('保存失败：' + e.message); }
  }
  $('plApply').addEventListener('click', () => persistChange(false));
  $('plDirector').addEventListener('click', () => { $('plDialog').close(); upgradeUI.director($('plEditor').value, current.id); });
  $('plPersonalSave').addEventListener('click', async () => {
    try { await api('/api/prompts/personal', {source_id:current.id,title:current.title+' · 个人版本',text:$('plEditor').value,
      stage:$('plPersonalStage').value,kind:$('plPersonalKind').value,input:$('plActualInput').value,
      purpose:$('plPersonalPurpose').value,
      result:$('plActualResult').value,evaluation:$('plEvaluation').value || '未验证'});
      $('plFeedback').textContent='个人版本已保存，来源原文保留。'; await load(); }
    catch(e) { error('保存失败：'+e.message); }
  });
  $('plUndo').addEventListener('click', () => persistChange(true));
  $('plShot').addEventListener('change', () => { selectedShot = Number($('plShot').value); $('plUndo').disabled = !shots()[selectedShot]?.prompt_before_library; if(current.collection==='curated'){renderVariables();renderText();$('plFeedback').textContent='镜头已切换，请重新填入当前设定';} });
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
  $('plCurated').onclick=()=>{collection='curated';purpose='shot';stage='';page=1;load();};
  $('plAllReferences').onclick=()=>{collection='reference';purpose='';stage='';page=1;load();};
  $('plReferences').onclick=()=>{collection='reference';purpose='reference';stage='';page=1;load();};
  window.promptLibrary = {open,browse(index = 0, use = 'shot') {
    selectedShot = index;collection='curated';purpose=use;stage='';page=1;$('plQuery').value='';load();upgradeUI.tab(true);
    root.scrollIntoView({behavior: 'smooth', block: 'start'}); $('plQuery').focus({preventScroll: true});
  }};
  load();
})();
