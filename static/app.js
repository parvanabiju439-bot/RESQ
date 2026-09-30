const $ = (sel, root=document) => root.querySelector(sel);
const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];

const state = {
  userId: localStorage.getItem('resq_user_id') || crypto.randomUUID(),
  location: null,
  recorder: null,
  chunks: [],
  voiceBlob: null,
  voiceMime: 'audio/webm',
  recognition: null,
  recognitionBaseText: '',
  recording: false,
  config: null,
};
localStorage.setItem('resq_user_id', state.userId);

function esc(value='') {
  return String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}
function priorityChip(p) {
  const cls = String(p||'').toLowerCase();
  return `<span class="chip ${cls}">${esc(p || 'UNKNOWN')}</span>`;
}
function callStatusChip(status) {
  const map = {
    CALL_REQUESTED: ['success','Control-station call requested'],
    CALL_FAILED: ['critical','Control-station call failed'],
    NOT_CONFIGURED: ['high','Phone call not configured'],
    DISABLED: ['info','Control callback disabled'],
    NOT_REQUESTED: ['info','No control call'],
  };
  const [cls, label] = map[status] || ['info', status || 'Unknown'];
  return `<span class="chip ${cls}">${esc(label)}</span>`;
}
function formatDistance(km) { return `${Number(km||0).toFixed(2)} km`; }
function formatEta(min) { return `${Math.max(1, Number(min||0))} min`; }
function formatTime(value) {
  try { return new Date(value).toLocaleString(); } catch { return value || ''; }
}
async function api(url, options={}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}
async function loadConfig() {
  if (!state.config) state.config = await api('/api/config');
  return state.config;
}
function setLocation(lat, lon, source='') {
  state.location = {latitude:Number(lat), longitude:Number(lon), source};
  $('#lat')?.setAttribute('value', state.location.latitude);
  $('#lon')?.setAttribute('value', state.location.longitude);
  const out = $('#locationStatus');
  if (out) out.textContent = `Location ready: ${state.location.latitude.toFixed(5)}, ${state.location.longitude.toFixed(5)}${source ? ` (${source})` : ''}`;
}
function useDemoLocation() {
  const cfg = state.config;
  if (cfg?.demo_location) setLocation(cfg.demo_location.latitude, cfg.demo_location.longitude, 'demo');
}
function requestLocation() {
  if (!navigator.geolocation) { useDemoLocation(); $('#locationStatus').textContent = 'Browser location is unavailable; using demo location.'; return; }
  $('#locationStatus').textContent = 'Requesting device location…';
  navigator.geolocation.getCurrentPosition(
    pos => setLocation(pos.coords.latitude, pos.coords.longitude, 'device'),
    () => { useDemoLocation(); $('#locationStatus').textContent = 'Device location unavailable; using demo location.'; },
    {enableHighAccuracy:true, timeout:7000, maximumAge:30000}
  );
}

function stopRecognition() {
  try { state.recognition?.stop(); } catch {}
  state.recognition = null;
}
function beginSpeechRecognition() {
  const box = $('#description');
  state.recognitionBaseText = box ? box.value.trim() : '';
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) return false;
  const recognition = new Recognition();
  recognition.lang = 'en-IN';
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.onresult = event => {
    let transcript = '';
    for (let i=0; i<event.results.length; i++) transcript += event.results[i][0].transcript + ' ';
    const box = $('#description');
    if (box) {
      const base = state.recognitionBaseText.replace(/\s+$/,'');
      box.value = `${base}${base && transcript.trim() ? ' ' : ''}${transcript.trim()}`.trim();
    }
  };
  recognition.onerror = () => {};
  recognition.onend = () => { if (state.recording) { try { recognition.start(); } catch {} } };
  try { recognition.start(); state.recognition = recognition; return true; } catch { return false; }
}

async function toggleRecording() {
  const button = $('#recordButton');
  const status = $('#recordStatus');
  if (state.recording) {
    state.recording = false;
    try { state.recorder?.stop(); } catch {}
    stopRecognition();
    button.textContent = '🎙 Record voice report';
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    status.innerHTML = '<div class="alert danger">This browser cannot record audio. Use Chrome or Edge for voice reports.</div>';
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    state.chunks = [];
    state.voiceBlob = null;
    const preferred = 'audio/webm;codecs=opus';
    const mime = MediaRecorder.isTypeSupported(preferred) ? preferred : 'audio/webm';
    state.voiceMime = mime;
    const recorder = new MediaRecorder(stream, {mimeType:mime});
    state.recorder = recorder;
    recorder.ondataavailable = e => { if (e.data.size) state.chunks.push(e.data); };
    recorder.onstop = () => {
      stream.getTracks().forEach(t => t.stop());
      state.voiceBlob = new Blob(state.chunks, {type:state.voiceMime});
      $('#voicePreview').src = URL.createObjectURL(state.voiceBlob);
      $('#voicePreviewWrap').hidden = false;
      status.innerHTML = '<div class="alert success">Voice report captured. Review the transcript or emergency type, then press SOS.</div>';
    };
    recorder.start(250);
    state.recording = true;
    const speech = beginSpeechRecognition();
    button.textContent = '⏹ Stop recording';
    status.innerHTML = `<div class="recording"><span class="dot"></span> Recording… ${speech ? 'speech-to-text is running too.' : 'speech-to-text is unavailable; choose an emergency type below.'}</div>`;
    setTimeout(() => {
      if (state.recording) toggleRecording();
    }, 90000);
  } catch (error) {
    status.innerHTML = `<div class="alert danger">Microphone access failed: ${esc(error.message)}</div>`;
  }
}

function currentLocation() {
  return state.location || (state.config?.demo_location ? state.config.demo_location : null);
}

async function submitSOS(lowBandwidth=false) {
  const loc = currentLocation();
  if (!loc) return showAlert('formAlert','Get your location first.', 'danger');
  if (state.recording) await toggleRecording();
  const name = $('#name')?.value.trim() || 'Anonymous User';
  const phone = $('#phone')?.value.trim() || '';
  const emergencyType = $('#emergencyType')?.value || 'GENERAL';
  const description = $('#description')?.value.trim() || '';

  if (!phone) return showAlert('formAlert','Enter the phone number that should receive the control-station callback.', 'danger');
  if (!lowBandwidth && !description && !state.voiceBlob) {
    return showAlert('formAlert','Type the emergency or record a voice report before sending SOS.', 'danger');
  }
  if (!lowBandwidth && !description && state.voiceBlob && emergencyType === 'GENERAL') {
    return showAlert('formAlert','For a voice-only report, select the emergency type so responders still receive a category if transcription is unavailable.', 'danger');
  }

  const button = lowBandwidth ? $('#lowBandwidthButton') : $('#sosButton');
  button.disabled = true;
  button.textContent = lowBandwidth ? 'Sending essentials…' : 'Sending SOS…';
  try {
    let result;
    if (lowBandwidth) {
      result = await api('/api/sos/low-bandwidth', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({sos:true, latitude:loc.latitude, longitude:loc.longitude, emergency_type:emergencyType, user_id:state.userId, phone_number:phone, name})
      });
    } else {
      const form = new FormData();
      form.append('name', name);
      form.append('phone_number', phone);
      form.append('reporter_id', state.userId);
      form.append('latitude', loc.latitude);
      form.append('longitude', loc.longitude);
      form.append('emergency_type', emergencyType);
      form.append('description', description);
      form.append('low_bandwidth', 'false');
      if (state.voiceBlob) {
        const ext = state.voiceMime.includes('ogg') ? 'ogg' : state.voiceMime.includes('mp4') ? 'm4a' : 'webm';
        form.append('voice_note', state.voiceBlob, `resq_voice.${ext}`);
      }
      result = await api('/api/incidents', {method:'POST', body:form});
    }
    showResult(result);
  } catch (error) {
    showAlert('formAlert', error.message, 'danger');
  } finally {
    button.disabled = false;
    button.textContent = lowBandwidth ? '📡 Send essentials (low-bandwidth)' : '🚨 SEND SOS NOW';
  }
}

function showAlert(id, message, type='info') {
  const el = $('#'+id); if (el) el.innerHTML = `<div class="alert ${type}">${esc(message)}</div>`;
}
function showResult(result) {
  const analysis = result.analysis || {};
  const call = result.control_call || {};
  const matches = result.matches || [];
  const hospitals = result.hospital_routes || [];
  $('#resultCard').hidden = false;
  $('#resultCard').scrollIntoView({behavior:'smooth', block:'start'});
  $('#resultContent').innerHTML = `
    <div class="call-banner"><div class="call-icon">📞</div><div><strong>${esc(state.config?.control_station_name || 'RESQ Control Station')}</strong><div class="small">${call.status === 'CALL_REQUESTED' ? 'A phone call was requested immediately after SOS.' : 'See call status below.'}</div></div><div style="margin-left:auto">${callStatusChip(call.status)}</div></div>
    <div class="grid-3" style="margin-top:12px">
      <div class="stat"><div class="value">#${esc(result.incident_id)}</div><div class="label">Incident ID</div></div>
      <div class="stat"><div class="value">${esc(analysis.category)}</div><div class="label">AI-style category</div></div>
      <div class="stat"><div class="value">${priorityChip(analysis.priority)}</div><div class="label">Priority</div></div>
    </div>
    <div class="item" style="margin-top:12px"><strong>Reported condition</strong><div class="meta">${esc(analysis.reported_condition)}</div><div class="small" style="margin-top:7px">${esc(analysis.disclaimer)}</div></div>
    <div class="item" style="margin-top:10px"><strong>Dynamic Help Radius</strong><div class="meta">${esc((result.help_radius_m||800))} m • ${esc(result.responders_in_radius||0)} available responder(s) currently inside the radius.</div></div>
    ${result.voice_note_url ? `<div class="item" style="margin-top:10px"><strong>Voice report</strong><audio class="audio" controls src="${esc(result.voice_note_url)}"></audio></div>` : ''}
    ${call.error ? `<div class="alert danger" style="margin-top:10px">${esc(call.error)}</div>` : ''}
    <div style="margin-top:14px"><h3>Nearby responders</h3>${matches.length ? `<div class="list">${matches.map(r => `<div class="item"><div class="top"><strong>${esc(r.name)}</strong><span class="chip info">Match ${esc(r.match_score)}</span></div><div class="meta">${esc(r.skills)} • ${formatDistance(r.distance_km)} • ETA ${formatEta(r.eta_min)}</div></div>`).join('')}</div>` : '<div class="empty">No available responder found within the demo radius limit.</div>'}</div>
    <div style="margin-top:14px"><h3>Open hospitals</h3>${hospitals.length ? `<div class="list">${hospitals.map(h => `<div class="item"><div class="top"><strong>🏥 ${esc(h.name)}</strong><span class="chip success">OPEN</span></div><div class="meta">${esc(h.type)} • ${formatDistance(h.distance_km)} • ETA ${formatEta(h.eta_min)} • ${esc(h.source)}</div></div>`).join('')}</div>` : '<div class="empty">No open hospital records in the demo database.</div>'}</div>
  `;
}

let leafletMaps = {};
function setupMap(id, center, zoom=14) {
  if (!window.L || !document.getElementById(id)) return null;
  if (leafletMaps[id]) leafletMaps[id].remove();
  const map = L.map(id).setView(center, zoom);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'© OpenStreetMap contributors'}).addTo(map);
  leafletMaps[id] = map;
  return map;
}

async function startReporter() {
  await loadConfig();
  useDemoLocation();
  $('#requestLocation')?.addEventListener('click', requestLocation);
  $('#recordButton')?.addEventListener('click', toggleRecording);
  $('#sosButton')?.addEventListener('click', () => submitSOS(false));
  $('#lowBandwidthButton')?.addEventListener('click', () => submitSOS(true));
  $('#emergencyType')?.addEventListener('change', e => {
    const box = $('#voiceHint');
    if (box) box.textContent = e.target.value === 'GENERAL' ? 'For voice-only reporting, select Accident, Medical, Fire, or Personal Safety so responders have an immediate category.' : `Selected category: ${e.target.options[e.target.selectedIndex].text}`;
  });
  const cfg = await loadConfig();
  $('#callConfig').innerHTML = cfg.control_call_configured
    ? `<div class="alert success">📞 Real control-station calling is configured. After SOS, RESQ requests a phone call using the configured control-station number.</div>`
    : `<div class="alert info">📞 Live phone callback is optional: add Twilio credentials in <span class="mono">.env</span>. The rest of RESQ works without them.</div>`;
}

async function loadControlData() {
  const [incidents, responders, hospitals, notifications] = await Promise.all([
    api('/api/incidents'), api('/api/responders'), api('/api/hospitals'), api('/api/notifications')
  ]);
  renderControl(incidents, responders, hospitals, notifications);
}
function renderControl(incidents, responders, hospitals, notifications) {
  $('#incCount').textContent = incidents.length;
  $('#resCount').textContent = responders.filter(r => r.is_available).length;
  $('#hosCount').textContent = hospitals.filter(h => h.status === 'OPEN').length;
  const latest = incidents[0];
  const cfg = state.config;
  const center = latest ? [latest.latitude, latest.longitude] : [cfg.demo_location.latitude, cfg.demo_location.longitude];
  const map = setupMap('controlMap', center, 14);
  if (map) {
    incidents.forEach(i => {
      const radius = Number(i.help_radius_m || 800);
      L.circle([i.latitude, i.longitude], {radius, fillOpacity:.08, weight:1}).addTo(map).bindPopup(`<strong>#${i.id} ${esc(i.emergency_type)}</strong><br>${esc(i.priority)}<br>Help radius: ${radius} m`);
      L.marker([i.latitude, i.longitude]).addTo(map).bindPopup(`<strong>Incident #${i.id}</strong><br>${esc(i.description)}<br>Status: ${esc(i.status)}`);
    });
    responders.filter(r => r.is_available).forEach(r => L.marker([r.latitude,r.longitude]).addTo(map).bindTooltip(`Responder: ${esc(r.name)}`));
    hospitals.filter(h => h.status==='OPEN').forEach(h => L.marker([h.latitude,h.longitude]).addTo(map).bindTooltip(`Hospital: ${esc(h.name)}`));
  }
  $('#incidentList').innerHTML = incidents.length ? incidents.map(i => `
    <div class="item">
      <div class="top"><strong>#${esc(i.id)} — ${esc(i.emergency_type || 'GENERAL')}</strong>${priorityChip(i.priority)}</div>
      <div class="meta">${formatTime(i.created_at)} • ${esc(i.status)} • help radius ${esc(i.help_radius_m)} m • ${esc(i.responders_in_radius)} responder(s)</div>
      <div class="meta">${esc(i.description)}</div>
      ${i.voice_note_available ? `<audio class="audio" controls src="/api/incidents/${i.id}/voice"></audio>` : ''}
      ${i.responder_name ? `<div class="alert success" style="margin-top:9px">✅ ${esc(i.responder_name)} is assigned and on the way.</div>` : ''}
      <div class="row" style="margin-top:9px">${callStatusChip(i.control_call_status)}${i.control_call_error ? `<span class="small">${esc(i.control_call_error)}</span>` : ''}${i.phone_number && i.control_call_status !== 'CALL_REQUESTED' ? `<button class="btn ghost" onclick="retryControlCall(${i.id})">📞 Retry callback</button>` : ''}</div>
      ${i.hospital_name ? `<div class="meta">Nearest demo hospital: ${esc(i.hospital_name)}</div>` : ''}
    </div>`).join('') : '<div class="empty">No incidents yet.</div>';
  $('#notificationList').innerHTML = notifications.length ? notifications.slice(0,20).map(n => `<div class="item"><div>${esc(n.message)}</div><div class="meta">${formatTime(n.created_at)}</div></div>`).join('') : '<div class="empty">No notifications yet.</div>';
}
async function startControl() {
  await loadConfig();
  await loadControlData();
  setInterval(loadControlData, 5000);
}

let responderId = localStorage.getItem('resq_responder_id') || '';
async function startResponder() {
  await loadConfig();
  const responders = await api('/api/responders');
  const select = $('#responderSelect');
  select.innerHTML = responders.map(r => `<option value="${esc(r.id)}">${esc(r.name)} — ${esc(r.skills)} — ${esc(r.status)}</option>`).join('');
  if (responderId) select.value = responderId;
  responderId = select.value;
  localStorage.setItem('resq_responder_id', responderId);
  select.addEventListener('change', () => { responderId = select.value; localStorage.setItem('resq_responder_id', responderId); loadResponderData(); });
  await loadResponderData();
  setInterval(loadResponderData, 5000);
}
async function loadResponderData() {
  const [incidents,responses,responders] = await Promise.all([
    api('/api/incidents'), api('/api/incident-responses?responder_id='+encodeURIComponent(responderId)), api('/api/responders')
  ]);
  const me = responders.find(r => String(r.id)===String(responderId));
  $('#myStatus').innerHTML = me ? `<span class="chip ${me.is_available ? 'success':'high'}">${esc(me.status)}</span>` : '';
  const responded = new Map(responses.map(x => [String(x.incident_id), x.decision]));
  const nearby = incidents.filter(i => ['REPORTED','ASSIGNED','RESPONDING'].includes(i.status));
  $('#responderIncidents').innerHTML = nearby.length ? nearby.map(i => {
    const match = (i.matches || []).find(x => String(x.id)===String(responderId));
    const distance = haversineFallback(i.latitude,i.longitude,me?.latitude,me?.longitude);
    const decision = responded.get(String(i.id));
    const canAct = me?.is_available && i.status==='REPORTED' && i.assigned_responder_id == null && (match || distance <= (i.help_radius_m||800)/1000);
    return `<div class="item"><div class="top"><strong>🚨 Emergency #${esc(i.id)} ${esc(i.emergency_type)}</strong>${priorityChip(i.priority)}</div><div class="meta">${formatDistance(distance)} away • ${esc(i.status)} • help radius ${esc(i.help_radius_m)} m</div><div class="meta">${esc(i.description)}</div>${i.voice_note_available?`<audio class="audio" controls src="/api/incidents/${i.id}/voice"></audio>`:''}${decision?`<div class="meta" style="margin-top:8px">Your previous response: ${esc(decision)}</div>`:''}<div class="row actions">${canAct?`<button class="btn primary" onclick="responderDecision(${i.id},'ACCEPTED')">ACCEPT</button><button class="btn ghost" onclick="responderDecision(${i.id},'REJECTED')">REJECT</button>`:''}${i.assigned_responder_id==responderId?`<button class="btn info" onclick="updateResponderStatus(${i.id},'RESPONDING')">ON THE WAY</button><button class="btn warning" onclick="updateResponderStatus(${i.id},'ARRIVED')">ARRIVED</button><button class="btn primary" onclick="updateResponderStatus(${i.id},'RESOLVED')">RESOLVED</button>`:''}</div></div>`;
  }).join('') : '<div class="empty">No active incidents.</div>';
}
function haversineFallback(lat1, lon1, lat2, lon2) {
  if ([lat1,lon1,lat2,lon2].some(v => typeof v !== 'number' || Number.isNaN(v))) return 999;
  const R=6371, p=Math.PI/180, a=.5-Math.cos((lat2-lat1)*p)/2+Math.cos(lat1*p)*Math.cos(lat2*p)*(1-Math.cos((lon2-lon1)*p))/2;
  return 2*R*Math.asin(Math.sqrt(a));
}
window.responderDecision = async (incidentId, decision) => {
  try { await api(`/api/incidents/${incidentId}/respond`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({responder_id:Number(responderId),decision})}); await loadResponderData(); } catch(e) { alert(e.message); }
};
window.updateResponderStatus = async (incidentId,status) => {
  try { await api(`/api/incidents/${incidentId}/status`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status,responder_id:Number(responderId)})}); await loadResponderData(); } catch(e) { alert(e.message); }
};

window.retryControlCall = async (incidentId) => {
  try {
    await api(`/api/incidents/${incidentId}/call`, {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    await loadControlData();
  } catch (e) { alert(e.message); }
};

async function startHospital() {
  await loadConfig();
  const [hospitals, incidents] = await Promise.all([api('/api/hospitals'), api('/api/incidents')]);
  const open = hospitals.filter(h=>h.status==='OPEN');
  $('#hospitalCount').textContent = open.length;
  $('#hospitalList').innerHTML = open.map(h => `<div class="item"><div class="top"><strong>🏥 ${esc(h.name)}</strong><span class="chip success">OPEN</span></div><div class="meta">${esc(h.type)} • ${esc(h.phone)}</div></div>`).join('');
  $('#hospitalIncidents').innerHTML = incidents.slice(0,30).map(i => `<div class="item"><div class="top"><strong>#${esc(i.id)} ${esc(i.emergency_type)}</strong>${priorityChip(i.priority)}</div><div class="meta">${esc(i.status)} • ${esc(i.reported_condition)}</div><div class="meta">Assigned hospital: ${esc(i.hospital_name || 'Not assigned')}</div></div>`).join('') || '<div class="empty">No incidents.</div>';
  const map = setupMap('hospitalMap', [state.config.demo_location.latitude,state.config.demo_location.longitude], 13);
  if (map) open.forEach(h => L.marker([h.latitude,h.longitude]).addTo(map).bindPopup(`<strong>${esc(h.name)}</strong><br>${esc(h.type)}<br>${esc(h.phone)}`));
}

function boot() {
  const page = document.body.dataset.page;
  if (page==='reporter') startReporter().catch(e=>showAlert('formAlert',e.message,'danger'));
  if (page==='control') startControl().catch(e=>console.error(e));
  if (page==='responder') startResponder().catch(e=>console.error(e));
  if (page==='hospital') startHospital().catch(e=>console.error(e));
}
window.addEventListener('DOMContentLoaded', boot);
