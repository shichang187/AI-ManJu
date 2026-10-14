/* Native HTML editors, shared project persistence and offline model handoff. */
const upgradeUI = (() => {
  const $ = id => document.getElementById(id), h = escapeHtml;
  let dirty = false, savingState = Promise.resolve(), inventory = null, directorSource = '', handoffTimer;
  const main = document.querySelector('main');
  main.insertAdjacentHTML('afterbegin', `<nav class="work-tabs" aria-label="工作台页签">
    <button id="tabCreate" aria-pressed="true">创作工作台</button><button id="tabLibrary" aria-pressed="false">提示词资料库</button></nav>
    <section id="projectManager"><h2>我的项目</h2><div class="actions"><select id="projects" aria-label="项目列表"></select>
    <button id="selectProject">打开项目</button><button id="listProjects" class="secondary">刷新列表</button>
    <select id="versions" aria-label="历史版本"></select><button id="restoreVersion" class="secondary">恢复版本</button></div>
    <p id="saveFeedback" role="status">修改分镜后，请保存到当前项目。</p></section>`);
  $('storyboardSection').insertAdjacentHTML('beforeend', `<div class="actions"><button id="addShot">增加镜头</button>
    <button onclick="saveStoryboard()">保存分镜</button></div><details><summary>角色与场景档案</summary>
    <p>支持多个角色、参考图路径、服装版本和场景。角色使用 CharacterProfile 字段；每镜可编辑出场名单和场景 ID。</p>
    <label for="characterProfiles">角色档案 JSON</label><textarea id="characterProfiles" rows="8"></textarea>
    <label for="sceneProfiles">场景档案 JSON</label><textarea id="sceneProfiles" rows="8"></textarea></details>`);
  const flow = document.createElement('section');
  flow.id = 'modelHandoff';
  flow.innerHTML = `<h2>用对话模型继续创作</h2><p>复制导演指令与故事，粘贴到你使用的模型；将返回的 JSON 放入下面，校验后预览，再导入编辑。模型效果未验证。</p>
    <label for="directorInstruction">可编辑导演指令</label><textarea id="directorInstruction" rows="6">把故事整理为分镜，保持角色、地点、时间和结尾。只返回 JSON 对象，包含 title、shots；每镜包含 shot_id（唯一）、description、dialogue、narration、duration、camera_movement、characters、scene_id、prompt。</textarea>
    <button id="copyDirector">复制导演指令＋我的故事</button><label for="modelResult">粘贴模型生成结果</label>
    <textarea id="modelResult" rows="8" placeholder='{"title":"我的故事","shots":[{"shot_id":1,"description":"动作","duration":4}]}'></textarea>
    <div class="actions"><button id="checkModel">校验并预览</button><button id="importModel" disabled>导入分镜</button></div>
    <pre id="modelFeedback" role="status"></pre>`;
  $('storyboardSection').before(flow);
  const baseInputs=collectInputs;
  collectInputs=()=>({...baseInputs(),director_instruction:$('directorInstruction').value,
    model_result_draft:$('modelResult').value,director_source_id:directorSource});
  ['directorInstruction','modelResult'].forEach(id=>$(id).addEventListener('input',()=>{
    clearTimeout(handoffTimer); handoffTimer=setTimeout(()=>saveState(),400);
  }));
  $('videoList').after(Object.assign(document.createElement('div'), {id: 'assetBindings'}));
  $('assetBindings').innerHTML = `<h3>镜头与素材对应</h3><p>编号按稳定镜头 ID 对应。缺失或重复备选会阻止导出。</p>
    <div id="bindingRows"></div><button id="saveBindings">保存对应关系</button>
    <details><summary>多镜合一对应关系</summary><p>一个素材可对应连续镜头，shot_ids 按顺序填写；句级字幕时间以该素材起点为零。</p>
    <textarea id="groupBindings" rows="5" placeholder='[{"shot_ids":[1,2],"filename":"combined.mp4"}]'></textarea>
    <button id="saveGroups">校验并保存多镜关系</button></details><p id="assetFeedback" role="status"></p>
    <div class="grid"><label>输出宽<input id="exportWidth" type="number" value="1280"></label>
    <label>输出高<input id="exportHeight" type="number" value="720"></label><label>帧率<input id="exportFps" type="number" value="30"></label>
    <label>画幅策略<select id="exportFit"><option value="pad">完整保留并留边</option><option value="crop">居中裁剪填满</option></select></label></div>
    <label><input id="plannedSubtitles" type="checkbox">使用计划对白作为整镜字幕（尚未与实际声音对齐）</label>
    <h3>导出历史与任务</h3><div id="taskHistory" role="status"></div>`;
  const editable = ['description', 'dialogue', 'narration', 'speaker', 'camera_movement', 'prompt', 'scene_id'];
  const labels = {description:'动作与画面', dialogue:'对白', narration:'旁白', speaker:'说话人', camera_movement:'运镜', prompt:'镜头提示词', scene_id:'场景 ID'};
  function markDirty() { dirty = true; $('saveFeedback').textContent = '有未保存修改，请保存到当前项目。'; }
  ['characterProfiles','sceneProfiles'].forEach((id)=>$(id).addEventListener('input',()=>{
    try { const value=JSON.parse($(id).value); if(!Array.isArray(value)) throw Error();
      state.storyboard[id==='characterProfiles'?'characters':'scenes']=value; $(id).setCustomValidity(''); }
    catch { $(id).setCustomValidity('请填写 JSON 数组'); } markDirty();
  }));
  renderStoryboard = function () {
    const sb = state.storyboard;
    if (!sb) { $('storyboardSection').style.display = 'none'; return; }
    $('storyboardSection').style.display = '';
    $('expandedText').textContent = `环境：${sb.environment_anchor || ''}\n${sb.expanded_text || ''}\n${(sb.warnings || []).join('\n')}`;
    $('characterProfiles').value = JSON.stringify(sb.characters || [], null, 2);
    $('sceneProfiles').value = JSON.stringify(sb.scenes || [], null, 2);
    $('shotList').innerHTML = '';
    sb.shots.forEach((shot, index) => {
      const card = document.createElement('article'); card.className = 'shot-card';
      card.innerHTML = `<div class="shot-head"><strong>镜头 ${h(shot.shot_id)}</strong><span>顺序 ${index+1}</span>
        <button data-act="up" class="secondary">上移</button><button data-act="down" class="secondary">下移</button>
        <button data-act="delete" class="secondary">删除</button><button data-act="library" class="secondary">选提示词</button></div>
        <div class="shot-body">${editable.map(k => `<label>${labels[k]}<textarea data-field="${k}" rows="${k==='prompt'?5:2}">${h(shot[k] || '')}</textarea></label>`).join('')}
        <label>计划时长（秒）<input type="number" min="0.1" max="600" step="0.1" data-field="duration" value="${h(shot.duration)}"></label>
        <label>出场角色（逗号分隔）<input data-field="characters" value="${h((shot.characters || []).join(','))}"></label>
        <details><summary>句级字幕时间</summary><p>相对素材秒数；多镜合一也使用同一素材起点。人工填写时间，不代表语音自动对齐。</p>
        <textarea data-field="subtitle_cues" rows="4">${h(JSON.stringify(shot.subtitle_cues || [],null,2))}</textarea></details></div>`;
      card.querySelectorAll('[data-field]').forEach(el => el.addEventListener('input', () => {
        try { shot[el.dataset.field] = el.dataset.field === 'duration' ? Number(el.value) :
          el.dataset.field === 'characters' ? el.value.split(/[,，]/).map(s=>s.trim()).filter(Boolean) :
          el.dataset.field === 'subtitle_cues' ? JSON.parse(el.value) : el.value; el.setCustomValidity(''); markDirty(); }
        catch { el.setCustomValidity('请填写有效字幕 JSON'); markDirty(); }
      }));
      card.querySelectorAll('[data-act]').forEach(b=>b.addEventListener('click', ()=>{
        const act=b.dataset.act;
        if(act==='library') return promptLibrary.browse(index);
        if(act==='delete') { if(sb.shots.length===1) return toast('至少保留一个镜头'); sb.shots.splice(index,1); }
        if(act==='up' && index>0) [sb.shots[index-1],sb.shots[index]]=[sb.shots[index],sb.shots[index-1]];
        if(act==='down' && index<sb.shots.length-1) [sb.shots[index+1],sb.shots[index]]=[sb.shots[index],sb.shots[index+1]];
        markDirty(); renderStoryboard();
      })); $('shotList').append(card);
    });
    window.experienceUI?.render();
  };
  saveState = function () {
    clearTimeout(handoffTimer);
    state.inputs = collectInputs();
    const pid=state.projectId, body={state:{inputs:state.inputs, interview:state.interview}, project_id:pid};
    if(!pid) delete body.project_id;
    savingState = savingState.catch(()=>{}).then(()=>api('/api/state', body));
    savingState.catch(e=>{ $('saveFeedback').textContent='保存失败：'+e.message; toast('保存失败：'+e.message); });
    return savingState;
  };
  async function adopt(s) {
    state.storyboard=s.storyboard || null; state.projectId=s.project_id || ''; state.revision=s.revision;
    state.projectFolder=s.project_folder || ''; state.projectOpened=!!s.project_opened;
    state.inputs=s.inputs || {}; state.interview=s.interview || {answers:{},done:false};
    if(s.inputs?.director_instruction!==undefined) $('directorInstruction').value=s.inputs.director_instruction;
    $('modelResult').value=s.inputs?.model_result_draft || ''; directorSource=s.inputs?.director_source_id || '';
    restoreInputs(state.inputs); renderProject(); renderStoryboard(); dirty=false;
    window.experienceUI?.sync();
    $('saveFeedback').textContent=state.projectId ? `当前项目 ${state.storyboard?.title || ''} · 版本 ${state.revision}` : '当前是草稿，确认后创建项目。';
    await refreshProjects(); await refreshVideos(); await refreshTasks(); updateComposeBtn();
  }
  restoreState = async function () { try { await adopt((await api('/api/state')).state); }
    catch(e) { toast('恢复失败：'+e.message); $('saveFeedback').textContent='恢复失败：'+e.message; } };
  saveStoryboard = async function () {
    if(!state.storyboard) return toast('请先生成或导入分镜');
    try {
      if(document.querySelector('textarea:invalid')) throw Error('请修正字幕或档案 JSON');
      state.storyboard.characters=JSON.parse($('characterProfiles').value || '[]');
      state.storyboard.scenes=JSON.parse($('sceneProfiles').value || '[]');
      await saveState();
      const res=await api('/api/save-storyboard',state.storyboard); dirty=false;
      $('saveFeedback').textContent='已保存到 '+(state.storyboard.title || '草稿')+' · 版本 '+(res.revision || '草稿');
      await refreshProjects(); return true;
    } catch(e) { $('saveFeedback').textContent='保存失败：'+e.message; toast('保存失败：'+e.message); return false; }
  };
  updateComposeBtn = ()=>{ $('composeBtn').disabled=!state.projectId; };
  const oldCreate = createProject;
  createProject=async()=>{ if(!await saveStoryboard()) return; await oldCreate(); await savingState;
    $('saveFeedback').textContent='已创建项目 '+(state.storyboard.title || '')+' · 版本 '+state.revision;
    await refreshProjects(); await refreshVideos(); };
  async function refreshProjects() {
    const {projects}=await api('/api/projects');
    $('projects').innerHTML='<option value="">选择已有项目</option>'+projects.filter(p=>!p.invalid).map(p=>
      `<option value="${h(p.project_id || 'legacy:'+p.legacy_folder)}">${h(p.title)} · ${h(p.project_id?.slice(-8) || '旧项目')}</option>`).join('');
    $('projects').value=state.projectId || '';
    if(state.projectId) { const v=await api('/api/project-history'); $('versions').innerHTML=v.revisions.map(r=>`<option>${r}</option>`).join(''); }
    else $('versions').innerHTML='';
  }
  const oldVideos=refreshVideos;
  refreshVideos=async()=>{ await oldVideos(); if(!state.projectId) return;
    inventory=await api('/api/videos?probe=1');
    $('bindingRows').innerHTML=(state.storyboard?.shots || []).map(s=>`<label>镜头 ${h(s.shot_id)}<select data-shot="${h(s.shot_id)}"><option value="">选择素材</option>${inventory.videos.map(v=>`<option value="${h(v.filename)}">${h(v.filename)}${v.error?'（预检失败）':v.media?` · ${v.media.width}×${v.media.height} · ${v.media.duration.toFixed(2)}秒 · ${v.media.audio?'有声':'静音'}`:''}</option>`).join('')}</select></label>`).join('');
    $('bindingRows').querySelectorAll('select').forEach(el=>{ el.value=inventory.bindings.find(b=>b.shot_ids.map(String).includes(el.dataset.shot))?.filename || ''; });
    $('groupBindings').value=JSON.stringify(inventory.bindings,null,2);
    $('assetFeedback').textContent=inventory.issues.map(i=>`镜头 ${i.shot_id}：${i.reason}`).join('\n') || '素材对应完整。';
  };
  async function bind(groups) { try { await api('/api/bind-assets',{bindings:groups}); await refreshVideos(); toast('对应关系已保存'); }
    catch(e){ $('assetFeedback').textContent='保存失败：'+e.message; toast(e.message); } }
  $('saveBindings').onclick=()=>bind([...$('bindingRows').querySelectorAll('select')].map(el=>({shot_ids:[el.dataset.shot],filename:el.value})));
  $('saveGroups').onclick=()=>{try{bind(JSON.parse($('groupBindings').value));}catch(e){toast(e.message);}};
  composeVideo=async()=>{ if(!state.projectId) return toast('请先创建项目'); if(!await saveStoryboard()) return;
    try { const res=await api('/api/compose',{bgm_path:$('bgmPath').value.trim(),bgm_volume:.15,
      width:Number($('exportWidth').value),height:Number($('exportHeight').value),fps:Number($('exportFps').value),
      fit:$('exportFit').value,planned_subtitles:$('plannedSubtitles').checked});
      $('progressWrap').style.display='block'; pollProgress(res.task_id); await refreshTasks(); }
    catch(e){toast('合成失败：'+e.message);} };
  async function refreshTasks() {
    const {tasks}=await api('/api/tasks'); const current=tasks.filter(t=>t.project_id===state.projectId);
    $('taskHistory').innerHTML=current.reverse().map(t=>`<p>${h(t.status)} · 输入版本 ${t.revision} · ${h(t.step)} ${h(t.error || '')}
      ${t.final_path?`<button class="secondary" data-final="${h(t.final_path)}">打开这版成片</button>`:''}</p>`).join('') || '尚无导出。';
    $('taskHistory').querySelectorAll('button').forEach(b=>b.onclick=()=>api('/api/open-file',{path:b.dataset.final}).catch(e=>toast(e.message)));
    return tasks;
  }
  setInterval(()=>{if(state.projectId) refreshTasks().catch(e=>{$('taskHistory').textContent='任务状态读取失败：'+e.message;});},2000);
  $('addShot').onclick=()=>{ if(!state.storyboard)return; state.storyboard.shots.push({shot_id:'shot_'+crypto.randomUUID().replaceAll('-',''),duration:4,description:'',dialogue:'',narration:'',prompt:'',characters:[],scene_id:'scene_1',shot_type:'action'});markDirty();renderStoryboard(); };
  $('selectProject').onclick=async()=>{ if(dirty && !await saveStoryboard())return; const id=$('projects').value;if(!id)return;
    try { await saveState(); const d=id.startsWith('legacy:')?{legacy_folder:id.slice(7)}:{project_id:id};await adopt((await api('/api/select-project',d)).state); }
    catch(e){toast(e.message);} };
  $('listProjects').onclick=()=>refreshProjects().catch(e=>toast(e.message));
  $('restoreVersion').onclick=async()=>{if(!$('versions').value)return;try{await adopt((await api('/api/restore-project',{revision:Number($('versions').value)})).state);}catch(e){toast(e.message);}};
  let modelCandidate=null;
  $('checkModel').onclick=()=>{try { let s=$('modelResult').value.trim().replace(/^```(?:json)?\s*|\s*```$/g,'');let v=JSON.parse(s);if(Array.isArray(v))v={shots:v};
    if(!Array.isArray(v.shots)||!v.shots.length)throw Error('需要非空 shots 数组');const ids=new Set();v.shots.forEach((s,i)=>{s.shot_id??=i+1;if(ids.has(String(s.shot_id)))throw Error('镜头 ID 重复');ids.add(String(s.shot_id));s.duration??=4;if(!(s.duration>0&&s.duration<=600))throw Error('镜头时长无效');});
    modelCandidate=v;$('modelFeedback').textContent=JSON.stringify(v,null,2);$('importModel').disabled=false;
    }catch(e){modelCandidate=null;$('importModel').disabled=true;$('modelFeedback').textContent='校验失败：'+e.message;}};
  $('modelResult').oninput=()=>{modelCandidate=null;$('importModel').disabled=true;};
  $('importModel').onclick=async()=>{if(!modelCandidate)return false;
    const before=state.storyboard;state.storyboard={...(state.storyboard || {}),...modelCandidate,
      generation:'手动粘贴结果',generation_info:{mode:'manual',message:'手动粘贴，来源和模型效果未验证'}};
    renderStoryboard();markDirty();if(await saveStoryboard())return true;
    state.storyboard=before;renderStoryboard();return false;};
  $('copyDirector').onclick=async()=>{const sb=state.storyboard || {},inputs=collectInputs();const context={story:inputs.text,style:inputs.style,interview:state.interview.answers,characters:sb.characters,scenes:sb.scenes,constraints:sb.constraints};
    try{await navigator.clipboard.writeText($('directorInstruction').value+'\n\n我的创作约束：\n'+JSON.stringify(context,null,2));toast('已复制指令与故事');}catch{toast('复制失败，请手动复制文本');}};
  function tab(library){ main.querySelectorAll(':scope > section').forEach(s=>s.hidden=library?s.id!=='promptLibrary':s.id==='promptLibrary');
    $('tabCreate').setAttribute('aria-pressed',!library);$('tabLibrary').setAttribute('aria-pressed',library); }
  $('tabCreate').onclick=()=>tab(false);$('tabLibrary').onclick=()=>tab(true);tab(false);
  return {adopt,refreshProjects,tab,director(text,id){$('directorInstruction').value=text;
    directorSource=id || '';saveState();tab(false);window.experienceUI?.selectInstruction(id);flow.closest('details')?.setAttribute('open','');flow.scrollIntoView();}};
})();
restoreState();
