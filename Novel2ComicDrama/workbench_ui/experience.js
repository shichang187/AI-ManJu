/* Three-step author workflow; all advanced controls remain available. */
window.experienceUI = (() => {
  const $ = id => document.getElementById(id), h = escapeHtml;
  const input = $('storyInputSection'), library = $('promptLibrary');
  const header=document.querySelector('header');
  const sizeHeader=()=>document.documentElement.style.setProperty('--workbench-header-height',header.getBoundingClientRect().height+'px');
  sizeHeader();new ResizeObserver(sizeHeader).observe(header);
  // Modal editors must remain visible when the library tab itself is hidden.
  document.body.append($('plDialog'));
  const wrap = (parent, title, nodes) => {
    const d = document.createElement('details'); d.className='advanced';
    const s=document.createElement('summary');s.textContent=title;d.append(s);
    nodes.forEach(n=>d.append(n));parent.append(d);return d;
  };
  input.querySelector('h2').textContent='01  输入故事';
  $('storyInput').setAttribute('aria-label','故事内容');
  $('storyInput').placeholder='写下你的故事。人物、冲突和结尾都可以直接写在这里。';
  const inputActions=input.querySelector('.actions');
  wrap(input,'角色、风格与镜头设置',[input.querySelector('.grid')]);
  const tools=wrap(input,'高级：访谈协助与分镜工具',[inputActions,input.querySelector('.chat')]);
  wrap(input,'打开已有项目 · 恢复历史版本',[$('projectManager')]);
  const step=document.createElement('section');step.id='modelStep';
  step.innerHTML=`<h2>02  模型扩写分镜</h2><div class="simple-grid">
    <label>生成方式<select id="generationMode"><option value="api">真实模型调用</option><option value="local">本地规则分段（离线）</option></select></label>
    <label>分镜指令<select id="instructionSelect" aria-label="分镜指令"><option value="">内置分镜指令</option></select></label></div>
    <div class="actions"><button id="generateStory">扩写并生成分镜</button><button id="browseInstructions" class="secondary">选择 / 编辑指令</button></div>
    <p id="modelStatus" class="call-status" role="status" aria-live="polite">读取模型设置…</p>
    <nav class="provider-links" aria-label="硅基流动入口"><a href="https://siliconflow.cn/" target="_blank" rel="noopener noreferrer">硅基流动官网</a><a href="https://cloud.siliconflow.cn/account/ak" target="_blank" rel="noopener noreferrer">创建 API Key</a><a href="https://cloud.siliconflow.cn/models" target="_blank" rel="noopener noreferrer">查看模型</a></nav>
    <details id="modelSettings" class="advanced" open><summary>模型设置</summary>
    <label>供应商<select id="modelProvider"><option value="siliconflow">硅基流动</option><option value="custom">自定义兼容接口</option></select></label>
    <label>API Key<input id="modelApiKey" type="password" autocomplete="off" spellcheck="false" aria-describedby="keyHelp" placeholder="粘贴 API Key；留空保留已保存的 Key"></label>
    <p id="keyHelp">去官网登录 → 创建并复制 API Key → 在这里粘贴 → 保存并获取模型。Key 保存后不回显。</p><p id="keyState" role="status"></p>
    <label>对话模型<select id="modelChoice"><option value="">先获取模型，再选择</option></select></label>
    <div class="actions"><button id="saveAndFetch">保存并获取模型</button><button id="saveModelSettings" class="secondary">保存设置</button><button id="replaceModelKey" class="secondary">更换 Key</button><button id="clearModelKey" class="secondary">清除当前供应商 Key</button></div>
    <p id="configStatus" role="status" aria-live="polite"></p>
    <details class="advanced"><summary>高级：自定义接口、模型 ID 与旧凭据</summary>
    <label>接口地址<input id="modelBaseUrl" type="url" autocomplete="off" placeholder="https://服务商域名/v1"></label>
    <label>手动模型 ID<input id="modelName" autocomplete="off" placeholder="服务商提供的对话模型 ID"></label>
    <label>等待时限（秒）<input id="modelTimeout" type="number" min="5" max="180" value="60"></label>
    <label>凭据方式<select id="modelCredentialMode"><option value="saved">页面输入，安全保存</option><option value="legacy">兼容旧凭据文件 / 环境变量</option></select></label>
    <label>旧凭据文件完整路径<input id="modelKeyFile" autocomplete="off" placeholder="仅在旧凭据方式下填写"></label>
    <p>不复制或删除旧凭据文件。文件路径留空时仅在旧模式读取原环境配置。</p>
    <div class="actions"><button id="refreshModelStatus" class="secondary">重新检查配置</button><button id="testModel" class="secondary">发送测试请求（可能消耗文本额度）</button></div></details></details>`;
  input.after(step);
  $('modelHandoff').querySelector('h2').textContent='编辑指令 · 手动粘贴模型结果';
  wrap(step,'高级：编辑指令与手动导入',[$('modelHandoff')]);
  const finish=document.createElement('section');finish.id='finishStep';
  finish.innerHTML='<h2>03  编辑并生成素材</h2><p id="generationBadge" class="call-status" role="status">分镜将在这里显示。</p><p>检查每个镜头，复制提示词到素材平台生成。下载后放入当前项目，再合成成片。</p>';
  step.after(finish);
  finish.append($('storyboardSection'),$('projectSection'),$('composeSection'));
  $('projectSection').querySelector('h2').textContent='保存项目与收集素材';
  $('projectSection').querySelector('button').textContent='保存为新项目';
  $('projectSection').insertAdjacentHTML('afterbegin','<p class="step-note">先保存项目，生成的视频按镜头 ID 命名并下载到项目文件夹。素材平台账号能力和效果未验证。</p>');
  $('projectSection').querySelector('.actions').insertAdjacentHTML('beforeend','<button id="materialPlatform" class="secondary">打开豆包生成素材</button>');
  $('materialPlatform').onclick=()=>openDoubao();
  $('materialPlatform').insertAdjacentHTML('afterend','<a class="platform-link" href="https://jimeng.jianying.com/ai-tool/home/" target="_blank" rel="noopener noreferrer">打开即梦</a>');
  $('composeSection').querySelector('h2').textContent='收集完成后合成成片';
  const bindings=$('assetBindings');
  wrap(bindings,'高级：手动素材绑定与输出设置',[
    ...[...bindings.children].filter(n=>n.id!=='taskHistory' && !(n.tagName==='H3'&&n.textContent==='导出历史与任务'))
  ]);
  // Keep export status above its optional configuration.
  $('storyboardSection').querySelector('h2').hidden=true;
  const baseInputs=collectInputs;
  collectInputs=()=>({...baseInputs(),generation_mode:$('generationMode').value,
    selected_instruction_id:$('instructionSelect').value});
  let instructionLoading=false, busy=false, lastStatus=null, poll=null, selectRequest=0;
  function payload() {
    const a=state.interview.answers || {},sb=state.storyboard || {};
    return {...collectInputs(),...a,characters:sb.characters || [],scenes:sb.scenes || []};
  }
  function instruction(inputs) {
    const values={'故事':inputs.text,'角色':inputs.character_desc||inputs.subject||'按故事确定并保持一致',
      '风格':inputs.style,'场景':inputs.location||'按故事确定并保持一致','时长':`${inputs.min_sec}–${inputs.max_sec}`,
      '动作':'按故事事件顺序编排','镜头':'按每镜画面需要选择运镜'};
    return $('directorInstruction').value.replace(/\{\{([^{}\n]{1,40})\}\}/g,(token,name)=>{
      if(!(name in values))throw Error('指令中“'+name+'”需要填写，请在高级入口编辑指令');return values[name];
    });
  }
  async function refreshStatus(fill=false) {
    const s=await api('/api/model-status');lastStatus=s;
    applyModelStatus(s,fill);
    showStatus();return s;
  }
  function showStatus() {
    if(!lastStatus)return;
    const mode=$('generationMode').value,c=lastStatus.call;
    const last=c.status!=='idle'?` 上次调用：${c.message}`:'';
    $('modelStatus').textContent=mode==='local'?'本地规则分段：不会调用模型。适合离线起稿，内容需要编辑。':
      `模型：${lastStatus.settings.model||'未设置'} · ${lastStatus.message}${last}`;
    $('modelStatus').dataset.status=mode==='local'?'idle':c.status;
    if(c.status==='running')$('modelStatus').textContent=`${c.model} 正在扩写分镜…`;
    $('generateStory').disabled=busy||instructionLoading||c.status==='running';
    $('generateStory').textContent=mode==='local'?'用本地规则分段':'扩写并生成分镜';
    $('instructionSelect').disabled=mode==='local'||busy;
  }
  async function loadInstructions() {
    const opts=[];let page=1,pages=1;
    do{const d=await api('/api/prompts?collection=curated&purpose=instruction&page='+page);pages=d.pages;opts.push(...d.items);page++;}while(page<=pages);
    const selected=state.inputs?.selected_instruction_id||'';
    $('instructionSelect').innerHTML='<option value="">内置分镜指令</option>'+opts.map(e=>`<option value="${h(e.id)}">${h(e.title)}${e.stage==='分镜'?' · 分镜':''}</option>`).join('');
    $('instructionSelect').value=selected;
  }
  $('instructionSelect').onchange=async()=>{
    const id=$('instructionSelect').value,seq=++selectRequest;instructionLoading=true;showStatus();
    try{const text=id?(await api('/api/prompts/detail?id='+encodeURIComponent(id))).text:
      '把故事扩写为可拍摄的漫剧分镜，保留人物、事件顺序、对白归属和结尾，保持场景与角色一致。';
      if(seq!==selectRequest)return;$('directorInstruction').value=text;await saveState();toast('分镜指令已选择，可直接生成');
    }catch(e){toast(e.message);}finally{if(seq===selectRequest){instructionLoading=false;showStatus();}}
  };
  $('browseInstructions').onclick=()=>promptLibrary.browse(0,'instruction');
  const modelButtons=['saveModelSettings','saveAndFetch','replaceModelKey','clearModelKey','testModel'];
  let settingBusy=false;
  function settingsPayload(action='keep') {
    return {provider:$('modelProvider').value,base_url:$('modelBaseUrl').value,model:$('modelName').value,
      credential_mode:$('modelCredentialMode').value,key_file:$('modelKeyFile').value,
      timeout:Number($('modelTimeout').value),api_key:$('modelApiKey').value,key_action:action};
  }
  function modelControls(disabled){modelButtons.forEach(id=>$(id).disabled=disabled);$('modelProvider').disabled=disabled;$('modelChoice').disabled=disabled;}
  async function saveSettings(action='keep', fetchList=false) {
    if(settingBusy)return;settingBusy=true;modelControls(true);
    $('configStatus').textContent=fetchList?'正在安全保存并获取模型…':'正在安全保存…';
    try {
      lastStatus=await api('/api/model-settings',settingsPayload(action));
      $('modelApiKey').value='';
      if(fetchList)lastStatus=await api('/api/model-list',{});
      applyModelStatus(lastStatus,false);showStatus();return true;
    }catch(e){$('configStatus').textContent='操作失败：'+e.message;}
    finally{$('modelApiKey').value='';settingBusy=false;modelControls(false);}
  }
  function applyModelStatus(s,fill) {
    if(fill){$('modelProvider').value=s.settings.provider;$('modelBaseUrl').value=s.settings.base_url;
      $('modelName').value=s.settings.model;$('modelKeyFile').value=s.settings.key_file;
      $('modelTimeout').value=s.settings.timeout;$('modelCredentialMode').value=s.settings.credential_mode;}
    $('modelBaseUrl').disabled=$('modelProvider').value==='siliconflow';
    $('keyState').textContent=(s.has_key?'已保存 API Key':'未保存可用的 API Key')+' · '+s.credential_storage;
    const ids=[...s.model_list.models];const chosen=$('modelName').value;if(chosen&&!ids.includes(chosen))ids.unshift(chosen);
    $('modelChoice').innerHTML='<option value="">选择模型 / 在高级入口手动填写</option>'+ids.map(id=>`<option value="${h(id)}">${h(id)}</option>`).join('');
    $('modelChoice').value=$('modelName').value;
    $('configStatus').textContent=s.message+' '+s.model_list.message;
  }
  $('saveModelSettings').onclick=()=>saveSettings();
  $('saveAndFetch').onclick=()=>saveSettings('keep',true);
  $('replaceModelKey').onclick=()=>saveSettings('replace');
  $('clearModelKey').onclick=()=>{if(window.confirm('清除当前供应商的 API Key？其他供应商和已有项目将保留。'))saveSettings('clear');};
  $('modelChoice').onchange=()=>{$('modelName').value=$('modelChoice').value;saveSettings();};
  const uiEndpoints={};
  $('modelProvider').onchange=async()=>{
    uiEndpoints[lastStatus?.settings.provider||'siliconflow']=$('modelBaseUrl').value;
    $('modelApiKey').value='';$('modelKeyFile').value='';$('modelName').value='';$('modelChoice').innerHTML='<option value="">请选择模型</option>';
    $('modelCredentialMode').value='saved';
    const provider=$('modelProvider').value;
    $('modelBaseUrl').value=provider==='siliconflow'?'https://api.siliconflow.cn/v1':uiEndpoints.custom||'';
    $('modelBaseUrl').disabled=provider==='siliconflow';
    $('keyState').textContent='请保存所选供应商；Key 按供应商和接口分别保存。';
    if(!$('modelBaseUrl').value)return;
    try{lastStatus=await api('/api/model-settings',{provider,base_url:$('modelBaseUrl').value});applyModelStatus(lastStatus,true);showStatus();}
    catch(e){$('configStatus').textContent=e.message;}
  };
  $('modelSettings').addEventListener('toggle',()=>{if(!$('modelSettings').open)$('modelApiKey').value='';});
  window.addEventListener('pagehide',()=>{$('modelApiKey').value='';});
  $('testModel').onclick=async()=>{
    if(settingBusy||!window.confirm('发送原创 JSON 连接测试，可能消耗服务商文本额度。继续？'))return;
    if(!await saveSettings()||!lastStatus?.ready)return;
    settingBusy=true;modelControls(true);
    try{lastStatus=await api('/api/model-test',{});showStatus();$('configStatus').textContent=lastStatus.call.message;}
    catch(e){$('configStatus').textContent=e.message;}
    finally{settingBusy=false;modelControls(false);}
  };

  $('refreshModelStatus').onclick=()=>refreshStatus().catch(e=>{$('configStatus').textContent=e.message;});
  $('generationMode').onchange=()=>{showStatus();saveState();};
  expandStory=async()=>{
    if(busy)return;if(instructionLoading)return toast('正在加载分镜指令，请稍候');const args=payload();
    if(!args.text)return toast('请输入故事内容');
    let failure='';try{
      await refreshStatus();
      if(args.generation_mode==='api'&&!lastStatus.ready){$('modelSettings').open=true;throw Error(lastStatus.message);}
      args.director_instruction=instruction(args);
      // Preserve the previous draft as an independent project before replacing it.
      if(state.storyboard){if(state.projectId){if(!await saveStoryboard())return;}else{await createProject();if(!state.projectId)return;}}
      await saveState();busy=true;showStatus();
      $('modelStatus').textContent=args.generation_mode==='api'?'正在连接模型并扩写分镜…':'正在用本地规则分段…';
      poll=setInterval(()=>refreshStatus().catch(()=>{}),750);
      await api('/api/expand',args);
      await upgradeUI.adopt((await api('/api/state')).state);
      finish.scrollIntoView({behavior:'smooth',block:'start'});toast(args.generation_mode==='api'?'模型分镜已返回，请检查内容':'本地分段已完成，未调用模型');
    }catch(e){failure='生成失败：'+e.message;$('modelStatus').textContent=failure;toast(e.message);}
    finally{clearInterval(poll);busy=false;try{await refreshStatus();}catch(e){failure='状态读取失败：'+e.message;}
      if(failure){$('modelStatus').textContent=failure;$('modelStatus').dataset.status='error';}}
  };
  $('generateStory').onclick=()=>expandStory();
  $('copyDirector').onclick=async()=>{try{const args=payload();args.director_instruction=instruction(args);
    const d=await api('/api/model-request',args);await navigator.clipboard.writeText(d.request);toast('已复制与直接调用一致的指令和故事');}
    catch(e){toast(e.message);}};
  function render() {
    const sb=state.storyboard;
    $('generationBadge').textContent=sb?(sb.generation_info?.message||sb.generation||'旧项目：生成来源未验证')+' · '+sb.shots.length+' 镜':'分镜将在这里显示。';
    $('projectSection').querySelector('button').disabled=!sb;
    $('projectSection').hidden=!sb;$('composeSection').hidden=!sb;
    $('projectSection').querySelector('button').textContent=state.projectId?'保存当前项目':'保存为新项目';
    $('shotList').querySelectorAll('.shot-card').forEach((card,index)=>{
      if(card.dataset.simple)return;card.dataset.simple='true';
      const body=card.querySelector('.shot-body'),details=document.createElement('details');details.className='advanced';
      const browse=card.querySelector('[data-act=library]'),choose=document.createElement('button');
      choose.className='secondary';choose.textContent='选择镜头模板';choose.onclick=()=>quickTemplates(index);browse.replaceWith(choose);
      details.innerHTML='<summary>高级：对白、运镜与字幕</summary>';
      [...body.querySelectorAll(':scope > label')].filter(l=>!['description','prompt','duration'].includes(l.querySelector('[data-field]')?.dataset.field)).forEach(l=>details.append(l));
      const cues=body.querySelector(':scope > details');if(cues)details.append(cues);body.append(details);
      const actions=document.createElement('div');actions.className='actions';
      const copy=document.createElement('button');copy.className='secondary';copy.textContent='复制镜头提示词';
      copy.onclick=async()=>{try{await navigator.clipboard.writeText(state.storyboard.shots[index].prompt||'');toast('已复制，到素材平台生成并下载');}catch{toast('复制失败，请从提示词框手动复制');}};
      actions.append(copy);body.append(actions);
    });
  }
  const oldCreate=createProject;
  createProject=async()=>{if(state.projectId){await saveStoryboard();render();}else{await oldCreate();render();}};
  const originalRestoreInputs=restoreInputs;
  restoreInputs=values=>{originalRestoreInputs(values);sync();};
  function sync(){
    const inputs=state.inputs||{};$('generationMode').value=inputs.generation_mode||'api';
    $('instructionSelect').value=inputs.selected_instruction_id||'';render();showStatus();
  }
  resetAll=async()=>{
    if(busy)return toast('请等待生成完成后重置');
    if(!window.confirm('开始新草稿？已有项目和素材都会保留。'))return;
    try{await saveState();if(state.storyboard){if(state.projectId){if(!await saveStoryboard())return;}else{await createProject();if(!state.projectId)return;}}
      await api('/api/reset',{});await upgradeUI.adopt((await api('/api/state')).state);
      $('directorInstruction').value='把故事扩写为可拍摄的漫剧分镜，保留人物、事件顺序、对白归属和结尾，保持场景与角色一致。';
      $('modelResult').value='';$('modelFeedback').textContent='';$('importModel').disabled=true;
      $('bgmPath').value='';$('projectInfo').textContent='';$('videoList').innerHTML='';$('bindingRows').innerHTML='';
      $('composeResult').style.display='none';$('progressWrap').style.display='none';toast('已开始新草稿');
    }catch(e){toast('重置失败：'+e.message);}
  };
  // Return to the active workflow without losing the selected shot.
  $('tabCreate').textContent='三步创作';$('tabLibrary').textContent='精选与高级参考库';
  $('plDialog').querySelector('.pl-dialog-footer').insertAdjacentHTML('beforeend','<button id="plBackCreate" class="secondary">回到创作</button>');
  $('plBackCreate').onclick=()=>{$('plDialog').close();upgradeUI.tab(false);finish.scrollIntoView();};
  const quick=document.createElement('dialog');quick.id='quickTemplates';quick.setAttribute('aria-labelledby','quickTitle');
  quick.innerHTML='<h2 id="quickTitle">选择镜头模板</h2><p>精选已通过内容审核；实际生成效果未验证。先填入设定，再预览使用。</p><input id="quickQuery" type="search" aria-label="搜索镜头模板" placeholder="搜索运镜、风格或画面"><div id="quickList"></div><div class="actions"><button id="quickPrev" class="secondary">上一页</button><button id="quickNext" class="secondary">下一页</button><button id="quickClose" class="secondary">关闭</button></div>';
  document.body.append(quick);
  let quickIndex=0,quickPage=1,quickSeq=0,quickTimer;
  async function loadQuick(){const seq=++quickSeq;
    try{const d=await api('/api/prompts?collection=curated&purpose=shot&page='+quickPage+'&q='+encodeURIComponent($('quickQuery').value));
      if(seq!==quickSeq)return;$('quickList').innerHTML=d.items.map(e=>`<article><h3>${h(e.title)}</h3><p>${h(e.preview)}</p><p>${h(e.input_mode||'')} · ${h(e.scope||'')} · ${h(e.review_status||'')} · ${h(e.test_status||'未实测')}</p><button data-template="${h(e.id)}" class="secondary">编辑并应用</button></article>`).join('')||'<p>未找到模板。</p>';
      $('quickPrev').disabled=d.page<=1;$('quickNext').disabled=d.page>=d.pages;
      $('quickList').querySelectorAll('button').forEach(b=>b.onclick=()=>{quick.close();promptLibrary.open(b.dataset.template,quickIndex);});
    }catch(e){$('quickList').textContent='读取失败：'+e.message;}}
  function quickTemplates(index){quickIndex=index;quickPage=1;$('quickQuery').value='';quick.showModal();loadQuick();}
  $('quickClose').onclick=()=>quick.close();$('quickPrev').onclick=()=>{quickPage--;loadQuick();};$('quickNext').onclick=()=>{quickPage++;loadQuick();};
  $('quickQuery').oninput=()=>{clearTimeout(quickTimer);quickTimer=setTimeout(()=>{quickPage=1;loadQuick();},200);};
  refreshStatus(true).catch(e=>{$('modelStatus').textContent='模型状态读取失败：'+e.message;});
  loadInstructions().catch(e=>{toast('指令读取失败：'+e.message);});sync();
  return {sync,render,selectInstruction(id){$('instructionSelect').value=id;state.inputs.selected_instruction_id=id;saveState();}};
})();
