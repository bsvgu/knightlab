import {pieceSVG, pieceNames, parseFEN, miniBoard} from './pieces.js';
import {BoardMotion} from './board-motion.js';

const $ = (id) => document.getElementById(id);
const all = (selector) => [...document.querySelectorAll(selector)];
const MODE = {
 mate:{name:'Mate Sprint',eyebrow:'TIMED CHECKMATE PUZZLES',title:'Mate in 2',description:'Solve mate-in-2 puzzles in a 3- or 5-minute session. Solved puzzles advance automatically.',number:'02',numberLabel:'MOVES TO MATE',setup:'Select category, duration, lives, and starting rating.',guide:'Move input and puzzle rules',guideText:"Drag a piece to a highlighted square, or click the piece and destination. Play all solution moves. Opponent replies and the next puzzle load automatically.",detail:'An incorrect legal move costs one life. Illegal moves do not.',icon:'♜',footnote:'Solved puzzles increase the target rating by 100, up to 4,000.'},
 tactics:{name:'Tactics Sprint',eyebrow:'TIMED TACTICS PUZZLES',title:'Tactics Sprint',description:'Solve puzzles from a selected category in a 3- or 5-minute session.',number:'3/5',numberLabel:'MINUTES',setup:'Select category, duration, lives, and starting rating.',guide:'Puzzle rules',guideText:'Play all solution moves. Opponent replies are automatic. The next puzzle loads after a complete solution.',detail:'An incorrect legal move costs one life. Illegal moves do not.',icon:'♝',footnote:'Solved puzzles increase the target rating by 100, up to 4,000.'},
 intuition:{name:'Intuition',eyebrow:'ENGINE MOVE EVALUATION',title:'Intuition training',description:'Play one move in a real-game position. Stockfish evaluates the move and calculates a training score.',number:'01',numberLabel:'MOVE PER POSITION',setup:'Select the number of positions to evaluate.',guide:'Move evaluation',guideText:'Each position has a +1.0 to +3.0 evaluation for the player to move. Play one legal move to see its centipawn loss and training score. Select Next to continue.',detail:'The engine evaluation is shown after the move.',icon:'✦',footnote:'Training score: 0–100, derived from centipawn loss.'},
 free:{name:'Free Practice',eyebrow:'UNTIMED PUZZLES',title:'Free Practice',description:'Solve puzzles without a time limit or life limit. Select Next after each solution.',number:'∞',numberLabel:'TIME LIMIT',setup:'Select category and starting rating.',guide:'Puzzle rules',guideText:'Play all solution moves. Opponent replies are automatic. Incorrect moves leave the position unchanged. Select Next after solving the puzzle.',detail:'Incorrect moves leave the position unchanged.',icon:'♙',footnote:'Select a puzzle in the library to practice a specific position.'}
};
const preferences = (() => {try{return JSON.parse(localStorage.getItem('knightlab-preferences') || '{}');}catch{return {};}})();
const state = {view:'mate',session:null,status:null,preview:null,flipped:false,selected:null,busy:false,pollBusy:false,pollTimer:null,pollEpoch:0,initialLives:3,duration:[180,300].includes(preferences.duration)?preferences.duration:180,positions:[10,20].includes(preferences.positions)?preferences.positions:10,library:{page:1,total:0,items:[],busy:false,request:0},importBusy:false,searchTimer:null};

function escapeHTML(value){return String(value??'').replace(/[&<>"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function numeric(value,fallback=0){const n=Number(value);return Number.isFinite(n)?n:fallback;}
function count(value){return new Intl.NumberFormat('en').format(numeric(value));}
function humanize(theme){return String(theme??'').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/([A-Za-z])(\d)/g,'$1 $2').replace(/^./,x=>x.toUpperCase());}
function timeLabel(value){if(value===null||value===undefined)return '∞';const seconds=Math.max(0,Math.ceil(numeric(value)));return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;}
function cpLabel(cp){if(cp===undefined||cp===null)return '—';const value=numeric(cp)/100;return `${value>=0?'+':''}${value.toFixed(2)}`;}
function isActive(){return state.session&&state.session.state!=='finished';}
function autoAdvance(session){return session?.state==='between'&&['mate','tactics'].includes(session.mode)&&numeric(session.remaining_seconds)>0;}
function showError(message){$('global-error-text').textContent=message;$('global-error').hidden=false;}
function clearError(){$('global-error').hidden=true;}
function announce(message){$('screenreader-status').textContent=message;}
async function api(path,options={}){
 let response;
 try{response=await fetch(path,{...options,headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...options.headers}});}catch{throw new Error('The local server is unreachable. Keep Knightlab running and try again.');}
 let data;try{data=await response.json();}catch{throw new Error(`The server returned an unreadable response (${response.status}).`);}
 if(!response.ok){const detail=data.detail;const message=Array.isArray(detail)?detail.map(x=>x.msg||String(x)).join(' '):typeof detail==='string'?detail:data.message||`Request failed (${response.status}).`;const error=new Error(message);error.status=response.status;throw error;}
 return data;
}
function savePreferences(){try{localStorage.setItem('knightlab-preferences',JSON.stringify({duration:state.duration,positions:state.positions,lives:numeric($('session-lives').value,3),rating:numeric($('session-rating').value,800),theme:$('session-theme').value,dragEnabled:$('drag-pieces').checked}));}catch{/* Storage may be disabled; practice still works. */}}
function saveActiveSession(){try{if(isActive())localStorage.setItem('knightlab-active-session',JSON.stringify({id:state.session.id,initialLives:state.initialLives}));else localStorage.removeItem('knightlab-active-session');}catch{/* Resume is optional when browser storage is disabled. */}}
function safeGameLink(url){try{const parsed=new URL(url);return ['https:','http:'].includes(parsed.protocol)?parsed.href:null;}catch{return null;}}
function categoryLabel(id){return state.status?.categories?.find(c=>c.id===id)?.label||humanize(id);}
function renderMateHeading(){
 if(state.view!=='mate')return;
 const moves=Number($('session-theme').value.match(/\d+/)?.[0]||2);
 $('mode-title').textContent=`Mate in ${moves}`;
 $('heading-number').innerHTML=`${String(moves).padStart(2,'0')}<span>MOVES TO MATE</span>`;
 $('mode-description').textContent=`Solve mate-in-${moves} puzzles in a 3- or 5-minute session. Solved puzzles advance automatically.`;
}

function renderCategories(){
 const categories=state.status?.categories||[];
 const old=$('session-theme').value;
 const libraryOld=$('library-theme').value;
 const mate=state.view==='mate';
 const available=mate?categories.filter(c=>/^mateIn\d+$/.test(c.id)):categories;
 const options=(mate?[{id:'mateIn2',label:'Mate in 2'},...available.filter(c=>c.id!=='mateIn2')]:[{id:'',label:'All categories'},...available]);
 $('session-theme').innerHTML=options.map(c=>`<option value="${escapeHTML(c.id)}">${escapeHTML(c.label||humanize(c.id))}</option>`).join('');
 const selected=mate&&(!/^mateIn\d+$/.test(old))?'mateIn2':old;
 $('session-theme').value=options.some(c=>c.id===selected)?selected:(mate?'mateIn2':'');
 $('library-theme').innerHTML='<option value="">All categories</option>'+categories.map(c=>`<option value="${escapeHTML(c.id)}">${escapeHTML(c.label||humanize(c.id))} (${count(c.count)})</option>`).join('');
 $('library-theme').value=categories.some(c=>c.id===libraryOld)?libraryOld:'';
 renderMateHeading();
}
function renderStatus(){
 if(!state.status)return;
 const {engine,counts,history}=state.status;
 const available=!!engine?.available;
 $('engine-status').classList.toggle('offline',!available);
 $('engine-status').innerHTML=`<span class="status-dot"></span>${escapeHTML(available?`${engine.name||'Stockfish'} ready`:'Engine unavailable')}`;
 $('engine-status').title=available?'Local engine is ready for intuition analysis.':'Puzzle modes work without an engine. Set up Stockfish to enable Intuition.';
 $('library-nav-count').textContent=`${count(counts?.puzzles)} puzzles`;
 $('library-total').innerHTML=`${count(counts?.puzzles)}<span>POSITIONS</span>`;
 $('history-solved').textContent=count(history?.solved);
 $('history-sessions').textContent=count(history?.sessions);
 $('history-extra').textContent=history?.average_accuracy!==null&&history?.average_accuracy!==undefined?`Intuition average · ${numeric(history.average_accuracy).toFixed(1)}% training score`:'Completed sessions are stored locally.';
 $('intuition-engine-warning').hidden=available;
 updateStartButton();
}
async function refreshStatus(){try{state.status=await api('/api/status');renderCategories();renderStatus();}catch(error){showError(error.message);$('engine-status').textContent='Server unavailable';}}

function updateStartButton(){
 const button=$('start-button');
 button.disabled=state.busy||(state.view==='intuition'&&!state.status?.engine?.available);
 button.innerHTML=state.busy?'<span class="spinner"></span>Preparing session…':`Start ${escapeHTML(MODE[state.view]?.name||'practice')} <span>→</span>`;
}
function renderMode(){
 all('[data-mode]').forEach(b=>{const active=b.dataset.mode===state.view;b.classList.toggle('active',active);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
 const library=state.view==='library';
 $('library-view').hidden=!library;$('trainer-view').hidden=library;
 $('breadcrumb-mode').textContent=library?'Puzzle Library':MODE[state.view].name;
 if(library)return;
 const mode=MODE[state.view];
 $('mode-eyebrow').textContent=mode.eyebrow;$('mode-title').textContent=mode.title;$('mode-description').textContent=mode.description;
 $('heading-number').innerHTML=`${mode.number}<span>${mode.numberLabel}</span>`;
 $('setup-description').textContent=mode.setup;$('guide-title').textContent=mode.guide;$('guide-text').textContent=mode.guideText;$('guide-detail').innerHTML=`<span class="small-dot"></span>${escapeHTML(mode.detail)}`;$('guide-icon').textContent=mode.icon;
 $('setup-footnote').innerHTML=`<span>↗</span>${escapeHTML(isActive()&&state.session.mode!==state.view?`${MODE[state.session.mode]?.name||'A session'} is active. Starting a new session ends and saves it.`:mode.footnote)}`;
 $('theme-field').hidden=state.view==='intuition';$('duration-field').hidden=state.view==='free'||state.view==='intuition';$('lives-field').hidden=state.view==='free'||state.view==='intuition';$('rating-field').hidden=state.view==='intuition';$('intuition-settings').hidden=state.view!=='intuition';
 renderCategories();updateStartButton();renderStats();
}
async function navigate(mode){
 if(state.busy)return;
 motion.cancel();
 if(!MODE[mode]&&mode!=='library')mode='mate';
 state.view=mode;renderMode();
 if(mode==='library'){await loadLibrary();return;}
 if(!state.session)await loadPreview();
 renderBoard();
}

function displayedBoard(){
 if(state.boardOverride)return state.boardOverride;
 if(state.session?.board)return state.session.board;
 if(state.preview){const turn=state.preview.fen?.split(' ')[1]||'w';return {fen:state.preview.fen,turn,player_color:turn,legal_moves:[],last_move:null};}
 return {fen:'8/8/8/8/8/8/8/8 w - - 0 1',turn:'w',player_color:'w',legal_moves:[],last_move:null};
}
function boardOrder(){const player=displayedBoard().player_color||displayedBoard().turn;const black=(player==='b')!==state.flipped;return {black,squares:Array.from({length:64},(_,i)=>{const file=black?7-i%8:i%8;const rank=black?1+Math.floor(i/8):8-Math.floor(i/8);return String.fromCharCode(97+file)+rank;})};}
function renderBoard(){
 if(motion.dragging)return;
 const board=displayedBoard();const pieces=parseFEN(board.fen);const {squares}=boardOrder();
 const interactive=state.session?.state==='playing'&&!state.busy;
 const legal=board.legal_moves||[];
 const destinations=new Set(state.selected?legal.filter(m=>m.startsWith(state.selected)).map(m=>m.slice(2,4)):[]);
 const last=board.last_move;const lastSquares=typeof last==='string'?[last.slice(0,2),last.slice(2,4)]:Array.isArray(last)?last:last?.from&&last?.to?[last.from,last.to]:[];
 const priorFocus=document.activeElement?.dataset?.square;
 $('chess-board').innerHTML=squares.map((square,i)=>{
  const file=square.charCodeAt(0)-97,rank=Number(square[1]);const piece=pieces[square];
  const dark=(file+rank)%2===1;const dest=destinations.has(square);
  const classes=['square',dark?'dark':'light',interactive?'interactive':'',state.selected===square?'selected':'',lastSquares.includes(square)?'last-move':'',dest?'legal':'',dest&&piece?'capture':''].filter(Boolean).join(' ');
  const description=`${square}, ${piece?`${piece===piece.toUpperCase()?'white':'black'} ${pieceNames[piece.toLowerCase()]}`:'empty'}${dest?', legal destination':''}`;
  return `<button type="button" class="${classes}" data-square="${square}" aria-label="${description}" aria-pressed="${state.selected===square}" tabindex="${priorFocus===square||(!priorFocus&&i===0)?0:-1}">${piece?pieceSVG(piece):''}${i>=56?`<span class="coordinate file">${square[0]}</span>`:''}${i%8===0?`<span class="coordinate rank">${square[1]}</span>`:''}</button>`;
 }).join('');
 motion.syncSource();
 if(priorFocus)$('chess-board').querySelector(`[data-square="${priorFocus}"]`)?.focus({preventScroll:true});
 const color=board.player_color||board.turn||'w';$('player-dot').classList.toggle('black',color==='b');
 const ended=state.session&&state.session.state!=='playing';
 $('player-label').textContent=ended?`${color==='w'?'White':'Black'} · ${state.session.state==='between'?'position complete':'session complete'}`:`${color==='w'?'White':'Black'} to play`;
 $('empty-board').hidden=!!state.session||!!state.preview;
 const challenge=state.session?.challenge||state.preview;
 $('position-kicker').textContent=state.session?`POSITION ${state.session.current_position||1}${state.session.mode==='intuition'?` OF ${state.session.total_positions||state.positions}`:''}`:'POSITION PREVIEW';
 $('position-meta').textContent=challenge?`${state.session?.mode==='intuition'?'Real game':`Rated ${count(challenge.rating)}`} · ${(challenge.themes||[]).slice(0,2).map(categoryLabel).join(' / ')||challenge.source||'Imported puzzle'}`:'Imported puzzle data';
 const link=safeGameLink(challenge?.game_url);$('game-link').hidden=!link;if(link)$('game-link').href=link;
}
async function loadPreview(){
 if(state.view==='intuition'){state.preview=null;renderBoard();$('empty-board').querySelector('strong').textContent='No active session';$('empty-board').querySelector('p').textContent=state.status?.engine?.available?`${count(state.status?.counts?.intuition)} game positions are available. Start a session to load a position.`:'Install Stockfish to enable Intuition.';return;}
 $('board-loading').hidden=false;
 try{const theme=state.view==='mate'?$('session-theme').value:state.view==='tactics'||state.view==='free'?$('session-theme').value:'';const data=await api(`/api/library?${new URLSearchParams({theme,min_rating:'0',max_rating:'4000',sort:'rating_asc',page:'1',limit:'1'})}`);state.preview=data.items?.[0]||null;$('empty-board').querySelector('strong').textContent='No puzzles available';$('empty-board').querySelector('p').textContent='Import puzzle data, then start a session.';renderBoard();}catch(error){showError(error.message);}finally{$('board-loading').hidden=true;}
}
function renderStats(){
 const s=state.session;const mode=s?.mode||state.view;
 const isIntuition=mode==='intuition';const free=mode==='free';
 $('stat-time').textContent=free||isIntuition?'∞':s?timeLabel(s.remaining_seconds):timeLabel(state.duration);
 $('stat-time').style.color=s?.remaining_seconds!==null&&numeric(s?.remaining_seconds)>0&&numeric(s?.remaining_seconds)<30?'#ac6845':'';
 const lives=s?s.lives:numeric($('session-lives').value,3);
 $('stat-lives').innerHTML=isIntuition||free?'—':Array.from({length:s?state.initialLives:numeric($('session-lives').value,3)},(_,i)=>`<span class="${i>=numeric(lives)?'lost':''}">♥</span>`).join(' ');
 $('score-label').textContent=isIntuition?'POSITIONS':'SOLVED';$('stat-score').textContent=isIntuition?`${s?.results?.length||0}/${s?.total_positions||state.positions}`:String(s?.solved||0).padStart(2,'0');
 $('rating-label').textContent=isIntuition?'TRAINING SCORE':'TARGET RATING';
 const results=s?.results||[];const accuracy=results.length?results.reduce((sum,r)=>sum+numeric(r.accuracy),0)/results.length:null;
 $('stat-rating').innerHTML=isIntuition?(accuracy!==null?`${accuracy.toFixed(1)}<span class="stat-unit">%</span>`:'—'):`${count(s?.current_rating??numeric($('session-rating').value,800))}<span class="stat-unit">+</span>`;
 $('session-state').textContent=s?(s.state==='playing'?'SESSION IN PROGRESS':s.state==='between'?'POSITION COMPLETE':'SESSION COMPLETE'):'NO ACTIVE SESSION';$('session-state').classList.toggle('live',s?.state==='playing');
}
function renderFeedback(){
 const feedback=state.session?.feedback;const panel=$('move-feedback');panel.hidden=!feedback;
 if(!feedback)return;
 panel.className=`move-feedback ${feedback.type||''}`;
 const titles={correct:'Correct move',wrong:'Incorrect move',illegal:'Illegal move',solved:'Puzzle solved',evaluated:'Move evaluation'};
 let html=`<strong>${escapeHTML(titles[feedback.type]||'Position feedback')}</strong>${escapeHTML(feedback.message||'')}`;
 if(feedback.type==='evaluated'){
  html+=`<div class="feedback-eval"><div><strong>${numeric(feedback.accuracy).toFixed(1)}%</strong><span>Training score</span></div><div><strong>${count(feedback.loss_cp)} cp</strong><span>Centipawn loss</span></div><div><strong>${cpLabel(feedback.before_cp)}</strong><span>Best evaluation</span></div><div><strong>${cpLabel(feedback.after_cp)}</strong><span>Your move evaluation</span></div></div>`;
  html+=`<div class="feedback-pv">Your move: ${escapeHTML(feedback.played_move||'—')} · Best move: ${escapeHTML(feedback.best_move||'—')}${feedback.pv_san?.length?`<br />Engine line: ${escapeHTML(feedback.pv_san.join(' '))}`:''}</div>`;
 }
 panel.innerHTML=html;
}
function renderSummary(){
 const s=state.session;const panel=$('summary-panel');panel.hidden=s?.state!=='finished';if(panel.hidden)return;
 const summary=s.summary||{};const reason={time:'Session time limit reached.',timeout:'Session time limit reached.',timer:'Session time limit reached.','Time expired':'Session time limit reached.',lives:'No lives remaining.','No lives remaining':'No lives remaining.',completed:'Configured number of positions completed.','Round complete':'Configured number of positions completed.',stopped:'Session ended manually.',Stopped:'Session ended manually.',pool_exhausted:'No matching puzzles remain.','Library complete':'No matching puzzles remain.'};
 const accuracy=summary.accuracy;
 panel.innerHTML=`<p class="eyebrow">SESSION COMPLETE</p><h2>${s.mode==='intuition'?'Intuition results':'Session results'}</h2><p>${escapeHTML(reason[summary.reason]||humanize(summary.reason)||'Session complete.')} Session results are saved locally.</p><div class="summary-stats"><div><strong>${count(summary.solved??s.solved)}</strong><span>${s.mode==='intuition'?'moves evaluated':'puzzles solved'}</span></div><div><strong>${count(summary.failed??s.failed)}</strong><span>${s.mode==='intuition'?'positions skipped':'wrong moves'}</span></div>${accuracy!==null&&accuracy!==undefined?`<div><strong>${numeric(accuracy).toFixed(1)}%</strong><span>training score</span></div>`:`<div><strong>${timeLabel(summary.duration_seconds)}</strong><span>time practiced</span></div>`}</div><button id="summary-start" class="primary-button">Start new session <span>→</span></button>`;
 $('summary-start').addEventListener('click',()=>{if(state.view==='library')navigate(s.mode);startSession();});
}
function renderRecap(){
 const results=state.session?.mode==='intuition'?state.session.results||[]:[];
 $('recap-section').hidden=!results.length;
 $('recap-list').innerHTML=results.map((r,i)=>`<div class="recap-row"><span class="recap-number">${String(i+1).padStart(2,'0')}</span><div class="recap-moves">Played ${escapeHTML(r.played_move||'—')} <span aria-hidden="true">·</span> Best ${escapeHTML(r.best_move||'—')}<small>${count(r.loss_cp)} cp loss · ${cpLabel(r.before_cp)} → ${cpLabel(r.after_cp)}</small></div><div class="recap-score">${numeric(r.accuracy).toFixed(1)}%<small>TRAINING SCORE</small></div></div>`).join('');
}
function renderSession(){
 renderStats();renderBoard();renderFeedback();renderSummary();renderRecap();
 const active=isActive();$('session-actions').hidden=!active;
 $('next-button').hidden=state.session?.state!=='between'||(state.busy&&autoAdvance(state.session));$('next-button').disabled=state.busy;
 $('next-button').textContent=state.session?.mode==='intuition'&&state.session.current_position>=state.session.total_positions?'Finish session →':'Next position →';
 $('stop-button').disabled=state.busy;
 $('flip-board').disabled=state.busy;$('drag-pieces').disabled=state.busy;
 all('[data-mode]').forEach(button=>button.disabled=state.busy);
 updateStartButton();
}
function releaseMissingSession(){clearInterval(state.pollTimer);state.pollTimer=null;state.session=null;state.selected=null;saveActiveSession();renderSession();}
function setBusy(busy,silent=false){state.busy=busy;if(busy){state.pollEpoch++;state.selected=null;}$('board-loading').hidden=!busy||silent;renderSession();}
function acceptSession(session){
 const old=state.session;state.session=session;if(old?.id!==session.id||old?.board?.fen!==session.board?.fen||session.state!=='playing')state.selected=null;
 saveActiveSession();
 renderSession();
 if(session.feedback&&JSON.stringify(old?.feedback)!==JSON.stringify(session.feedback))announce(session.feedback.message||'Position updated.');
 if(session.state==='finished'){clearInterval(state.pollTimer);state.pollTimer=null;if(old?.state!=='finished')refreshStatus();}
}
async function pollSession(){
 if(!isActive()||state.busy||state.pollBusy||motion.occupied)return;state.pollBusy=true;
 try{const id=state.session.id,epoch=state.pollEpoch;const response=await api(`/api/sessions/${encodeURIComponent(id)}`);if(state.session?.id===id&&state.pollEpoch===epoch&&!state.busy&&!motion.occupied){acceptSession(response);if(autoAdvance(response))await sessionAction('next');}}catch(error){if(error.status===404)releaseMissingSession();showError(error.message);}finally{state.pollBusy=false;}
}
function startPolling(){clearInterval(state.pollTimer);state.pollTimer=setInterval(pollSession,1000);}
async function restoreActiveSession(explicitMode){
 let stored;try{stored=JSON.parse(localStorage.getItem('knightlab-active-session')||'null');}catch{return;}
 if(!stored?.id||typeof stored.id!=='string')return;
 try{
  const session=await api(`/api/sessions/${encodeURIComponent(stored.id)}`);
  state.initialLives=Math.max(numeric(session.lives,3),Math.min(10,Math.max(1,numeric(stored.initialLives,3))));
  if(!explicitMode&&MODE[session.mode]){state.view=session.mode;renderMode();}
  acceptSession(session);
  if(isActive()){startPolling();announce('Active session restored.');}
 }catch(error){
  if(error.status===404){try{localStorage.removeItem('knightlab-active-session');}catch{/* Storage may be unavailable. */}}
  else showError(error.message);
 }
}
function sessionPayload(puzzleId){
 const mode=puzzleId?'free':state.view;
 return {mode,theme:mode==='intuition'?'':puzzleId?'':$('session-theme').value,duration_seconds:mode==='free'||mode==='intuition'?0:state.duration,lives:numeric($('session-lives').value,3),start_rating:numeric($('session-rating').value,800),positions:state.positions,min_eval:100,max_eval:300,...(puzzleId?{puzzle_id:puzzleId}:{})};
}
function confirmReplace(){return new Promise(resolve=>{const dialog=$('replace-dialog');const onCancel=()=>{cleanup();dialog.close();resolve(false);};const onConfirm=()=>{cleanup();dialog.close();resolve(true);};const cleanup=()=>{$('replace-cancel').removeEventListener('click',onCancel);$('replace-confirm').removeEventListener('click',onConfirm);dialog.removeEventListener('cancel',onCancel);};$('replace-cancel').addEventListener('click',onCancel);$('replace-confirm').addEventListener('click',onConfirm);dialog.addEventListener('cancel',onCancel);dialog.showModal();});}
async function startSession(puzzleId){
 if(state.busy)return;
 const payload=sessionPayload(puzzleId);
 if(payload.mode==='intuition'&&!state.status?.engine?.available){showError('Stockfish is required for Intuition. Set up the local engine first.');return;}
 if(isActive()&&!(await confirmReplace()))return;
 motion.cancel();setBusy(true);clearError();savePreferences();
 try{
  if(isActive()){try{acceptSession(await api(`/api/sessions/${encodeURIComponent(state.session.id)}/stop`,{method:'POST',body:'{}'}));}catch(error){if(error.status===404)releaseMissingSession();else throw error;}}
  const session=await api('/api/sessions',{method:'POST',body:JSON.stringify(payload)});
  if(puzzleId){state.view='free';renderMode();history.replaceState(null,'','#free');}
  state.flipped=false;state.initialLives=payload.lives;acceptSession(session);startPolling();announce('Session started. Select a piece and its destination.');
 }catch(error){showError(error.message);}finally{setBusy(false);}
}
async function sessionAction(action,body={}){
 if(!state.session||state.busy)return;
 if(action!=='move')motion.cancel();
 const previousBoard=state.session.board;
 setBusy(true,action==='move'||(action==='next'&&autoAdvance(state.session)));clearError();
 try{
  const response=await api(`/api/sessions/${encodeURIComponent(state.session.id)}/${action}`,{method:'POST',body:JSON.stringify(body)});
  if(action==='move'){
   const frames=response.board?.transition||[];
   let fen=previousBoard.fen;
   for(let i=0;i<frames.length;i++){
    if(i&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches)await new Promise(resolve=>setTimeout(resolve,55));
    await motion.animateMove(frames[i],fen,frame=>{state.boardOverride={...previousBoard,fen:frame.fen,last_move:frame.uci,legal_moves:[]};renderBoard();});
    fen=frames[i].fen;
   }
   if(!frames.length)await motion.revert();
  }
  state.boardOverride=null;acceptSession(response);
  // Finish the accepted move animation, then advance timed sprints without
  // resetting the server-owned clock, lives, or rating progression.
  if(autoAdvance(response))acceptSession(await api(`/api/sessions/${encodeURIComponent(response.id)}/next`,{method:'POST',body:'{}'}));
 }catch(error){await motion.revert();if(error.status===404)releaseMissingSession();showError(error.message);}
 finally{state.boardOverride=null;setBusy(false);}
}
function promotionChoice(candidates,color){return new Promise(resolve=>{const dialog=$('promotion-dialog');$('promotion-options').innerHTML=candidates.map(move=>{const piece=move[4];return `<button type="button" data-promotion="${escapeHTML(move)}">${pieceSVG(color==='w'?piece.toUpperCase():piece)}${escapeHTML(humanize(pieceNames[piece]))}</button>`;}).join('');const choose=event=>{const button=event.target.closest('[data-promotion]');if(!button)return;cleanup();dialog.close();resolve(button.dataset.promotion);};const cancel=()=>{cleanup();dialog.close();resolve(null);};const close=event=>{if(event.target.closest('[data-close-dialog]'))cancel();};const cleanup=()=>{$('promotion-options').removeEventListener('click',choose);dialog.removeEventListener('cancel',cancel);dialog.removeEventListener('click',close);};$('promotion-options').addEventListener('click',choose);dialog.addEventListener('cancel',cancel);dialog.addEventListener('click',close);dialog.showModal();});}
async function clickSquare(square){
 if(state.session?.state!=='playing'||state.busy||motion.occupied)return;
 const board=state.session.board;const legal=board.legal_moves||[];
 if(state.selected){
  const candidates=legal.filter(m=>m.startsWith(state.selected+square));
  if(candidates.length){const move=await chooseMove(candidates,board);if(move)await sessionAction('move',{uci:move});return;}
 }
 if(legal.some(m=>m.startsWith(square))){state.selected=state.selected===square?null:square;renderBoard();if(state.selected)announce(`${square} selected. Choose a highlighted destination.`);}else{state.selected=null;renderBoard();announce('Select a piece with a legal move.');}
}
async function chooseMove(candidates,board){
 if(candidates.length===1)return candidates[0];
 setBusy(true,true);
 try{return await promotionChoice(candidates,board.player_color||board.turn);}
 finally{setBusy(false);}
}
const motion=new BoardMotion($('chess-board'),{
 getBoard:displayedBoard,
 enabled:()=>$('drag-pieces').checked,
 canInteract:()=>state.session?.state==='playing'&&!state.busy,
 onSelect:from=>{state.pollEpoch++;state.selected=from;renderBoard();},
 onDrop:async(from,to)=>{
  const board=state.session.board;
  const candidates=(board.legal_moves||[]).filter(move=>move.startsWith(from+to));
  if(!candidates.length){await motion.revert();return;}
  const move=await chooseMove(candidates,board);
  if(move)await sessionAction('move',{uci:move});else await motion.revert();
 },
 onSettled:()=>renderBoard()
});

async function loadLibrary(){
 const request=++state.library.request;state.library.busy=true;$('library-loading').hidden=false;$('library-items').hidden=true;
 const min=numeric($('library-min-rating').value),max=numeric($('library-max-rating').value,3000);
 if(min>max){showError('The minimum rating must not exceed the maximum.');state.library.busy=false;$('library-loading').hidden=true;$('library-items').hidden=false;return;}
 const params=new URLSearchParams({theme:$('library-theme').value,min_rating:String(min),max_rating:String(max),sort:$('library-sort').value,q:$('library-search').value.trim(),page:String(state.library.page),limit:'12'});
 try{
  const data=await api(`/api/library?${params}`);if(request!==state.library.request)return;
  state.library.items=data.items||[];state.library.total=numeric(data.total);state.library.page=numeric(data.page,1);renderLibrary();
 }catch(error){if(request===state.library.request){showError(error.message);$('library-items').innerHTML='<div class="library-empty"><h2>Could not load the collection.</h2><p>Check that the local server is running, then apply the filters to try again.</p></div>';}}
 finally{if(request===state.library.request){state.library.busy=false;$('library-loading').hidden=true;$('library-items').hidden=false;}}
}
function renderLibrary(){
 const {items,total,page}=state.library;const pages=Math.max(1,Math.ceil(total/12));
 $('library-result-count').textContent=`${count(total)} matching positions`;
 $('library-page-info').textContent=`Page ${page} of ${pages}`;$('library-prev').disabled=page<=1;$('library-next').disabled=page>=pages;
 $('library-items').innerHTML=items.length?items.map(item=>`<article class="puzzle-card"><div class="puzzle-card-top"><span class="puzzle-card-id" title="${escapeHTML(item.id)}"># ${escapeHTML(item.id)}</span><span class="rating-badge">${count(item.rating)}</span></div>${miniBoard(item.fen)}<div class="puzzle-card-themes">${escapeHTML((item.themes||[]).slice(0,3).map(categoryLabel).join(' · '))}</div><div class="puzzle-card-source">${escapeHTML(item.source||'Imported puzzle')} · ${item.fen?.split(' ')[1]==='b'?'Black':'White'} to play</div><button class="secondary-button" data-practice-puzzle="${escapeHTML(item.id)}">Practice position <span>→</span></button></article>`).join(''):'<div class="library-empty"><h2>No matching puzzles</h2><p>No puzzles match these filters. Try a broader rating range, another category, or import puzzle data.</p></div>';
}
function applyLibraryFilters(){state.library.page=1;loadLibrary();}
async function importFile(kind,file){
 if(!file||state.importBusy)return;
 state.importBusy=true;const feedback=$('import-feedback');feedback.hidden=false;feedback.className='import-feedback';
 feedback.innerHTML=`<span class="spinner"></span>${kind==='pgn'?'Analyzing PGN positions with Stockfish…':'Validating and importing puzzle data…'}<br /><small>${escapeHTML(file.name)}</small>`;
 $('import-puzzles-button').disabled=true;$('import-pgn-button').disabled=true;
 const form=new FormData();form.append('file',file);
 try{
  const result=await api(`/api/import/${kind==='pgn'?'pgn':'puzzles'}`,{method:'POST',body:form});
  feedback.innerHTML=`<strong>${count(result.imported)} ${kind==='pgn'?'intuition positions':'puzzles'} imported.</strong> ${count(result.skipped)} skipped.${result.inspected!==undefined?` ${count(result.inspected)} positions inspected.`:''}${result.errors?.length?`<ul>${result.errors.slice(0,8).map(error=>`<li>${escapeHTML(error)}</li>`).join('')}</ul>${result.errors.length>8?`<small>And ${count(result.errors.length-8)} more validation messages.</small>`:''}`:''}`;
  await refreshStatus();await loadLibrary();if(!state.session&&state.view!=='library')await loadPreview();
 }catch(error){feedback.classList.add('error');feedback.textContent=error.message;}
 finally{state.importBusy=false;$('import-puzzles-button').disabled=false;$('import-pgn-button').disabled=false;$('puzzle-file').value='';$('pgn-file').value='';}
}

all('[data-mode]').forEach(button=>button.addEventListener('click',()=>{const mode=button.dataset.mode;if(location.hash===`#${mode}`)navigate(mode);else location.hash=mode;}));
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));
$('dismiss-error').addEventListener('click',clearError);
$('session-form').addEventListener('submit',event=>{event.preventDefault();if($('session-form').reportValidity())startSession();});
all('[data-duration]').forEach(button=>button.addEventListener('click',()=>{state.duration=Number(button.dataset.duration);all('[data-duration]').forEach(b=>b.classList.toggle('selected',b===button));savePreferences();renderStats();}));
all('[data-positions]').forEach(button=>button.addEventListener('click',()=>{state.positions=Number(button.dataset.positions);all('[data-positions]').forEach(b=>b.classList.toggle('selected',b===button));savePreferences();renderStats();}));
['session-lives','session-rating'].forEach(id=>$(id).addEventListener('change',()=>{savePreferences();renderStats();}));
$('session-theme').addEventListener('change',()=>{savePreferences();if(!state.session)loadPreview();renderMateHeading();});
$('chess-board').addEventListener('click',event=>{if(motion.suppressClick(event)){event.preventDefault();return;}const button=event.target.closest('[data-square]');if(button)clickSquare(button.dataset.square);});
$('chess-board').addEventListener('keydown',event=>{const keys={ArrowLeft:-1,ArrowRight:1,ArrowUp:-8,ArrowDown:8};if(!(event.key in keys))return;event.preventDefault();const buttons=all('#chess-board [data-square]');const index=buttons.indexOf(event.target);const target=Math.max(0,Math.min(63,index+keys[event.key]));buttons.forEach((b,i)=>b.tabIndex=i===target?0:-1);buttons[target]?.focus();});
$('flip-board').addEventListener('click',()=>{motion.cancel();state.flipped=!state.flipped;renderBoard();});
$('drag-pieces').addEventListener('change',()=>{motion.cancel();$('chess-board').classList.toggle('drag-enabled',$('drag-pieces').checked);savePreferences();});
$('next-button').addEventListener('click',()=>sessionAction('next'));
$('stop-button').addEventListener('click',()=>sessionAction('stop'));
$('board-help').addEventListener('click',()=>$('about-dialog').showModal());
$('about-button').addEventListener('click',()=>$('about-dialog').showModal());
all('[data-close-dialog]').forEach(button=>{if(button.dataset.closeDialog!=='promotion-dialog')button.addEventListener('click',()=>$(button.dataset.closeDialog).close());});
$('empty-import').addEventListener('click',()=>{location.hash='library';});
$('library-search').addEventListener('input',()=>{clearTimeout(state.searchTimer);state.searchTimer=setTimeout(applyLibraryFilters,280);});
['library-theme','library-sort'].forEach(id=>$(id).addEventListener('change',applyLibraryFilters));
$('library-apply').addEventListener('click',applyLibraryFilters);
['library-min-rating','library-max-rating'].forEach(id=>$(id).addEventListener('keydown',event=>{if(event.key==='Enter')applyLibraryFilters();}));
$('library-prev').addEventListener('click',()=>{if(state.library.busy)return;state.library.page=Math.max(1,state.library.page-1);loadLibrary();});
$('library-next').addEventListener('click',()=>{if(state.library.busy)return;state.library.page++;loadLibrary();});
$('library-items').addEventListener('click',event=>{const button=event.target.closest('[data-practice-puzzle]');if(button)startSession(button.dataset.practicePuzzle);});
$('import-puzzles-button').addEventListener('click',()=>$('puzzle-file').click());
$('import-pgn-button').addEventListener('click',()=>$('pgn-file').click());
$('puzzle-file').addEventListener('change',()=>importFile('puzzles',$('puzzle-file').files[0]));
$('pgn-file').addEventListener('change',()=>importFile('pgn',$('pgn-file').files[0]));

async function init(){
 $('drag-pieces').checked=preferences.dragEnabled!==false;$('chess-board').classList.toggle('drag-enabled',$('drag-pieces').checked);
 $('session-lives').value=[1,3,5].includes(numeric(preferences.lives))?String(preferences.lives):'3';
 $('session-rating').value=String(Math.max(0,Math.min(3500,numeric(preferences.rating,800))));
 all('[data-duration]').forEach(b=>b.classList.toggle('selected',Number(b.dataset.duration)===state.duration));all('[data-positions]').forEach(b=>b.classList.toggle('selected',Number(b.dataset.positions)===state.positions));
 const initial=location.hash.slice(1);const explicitMode=!!(MODE[initial]||initial==='library');state.view=explicitMode?initial:'mate';state.busy=true;renderMode();renderBoard();$('board-loading').hidden=false;
 await refreshStatus();if(preferences.theme&&[...$('session-theme').options].some(o=>o.value===preferences.theme))$('session-theme').value=preferences.theme;
 renderMateHeading();
 await restoreActiveSession(explicitMode);setBusy(false);
 if(autoAdvance(state.session))await sessionAction('next');
 if(state.view==='library')await loadLibrary();else if(!state.session)await loadPreview();else renderSession();
}
init();
