'use strict';
const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => Array.from(root.querySelectorAll(q));
const money = value => new Intl.NumberFormat('vi-VN').format(value) + ' đ';
async function api(path, payload, method='POST', timeout=60000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = {'X-CSRFToken': $('meta[name="csrf-token"]')?.content || '', 'Accept':'application/json'};
  const options = {method, headers, credentials:'same-origin', signal:controller.signal};
  if (payload instanceof FormData) options.body=payload;
  else if (payload !== undefined) { headers['Content-Type']='application/json'; options.body=JSON.stringify(payload); }
  try {
    const response = await fetch(path, options);
    if (response.redirected || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Phiên làm việc đã hết hạn. Tải lại trang và đăng nhập.');
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'Không thể hoàn tất thao tác.');
    return body;
  } catch(error) {
    if (error.name==='AbortError') throw new Error('Yêu cầu mất quá lâu. Kiểm tra lại trạng thái trước khi thực hiện lần nữa.');
    throw error;
  } finally { clearTimeout(timer); }
}
function showError(form,error) { const box=$('.form-error',form); if(box){box.textContent=error.message;box.hidden=false;} }
function notice(message) { const el=$('#toast'); el.textContent=message;el.hidden=false;setTimeout(()=>el.hidden=true,5000); }
function formData(form) { return Object.fromEntries(new FormData(form).entries()); }
function textElement(tag,text,className='') { const node=document.createElement(tag);node.textContent=text;node.className=className;return node; }
$('#menu-toggle')?.addEventListener('click',()=>$('#sidebar').classList.toggle('open'));
$('#print-receipt')?.addEventListener('click',()=>window.print());

if($('[data-live-map]')){
  let polling=false;
  const timer=setInterval(async()=>{
    if(polling || document.hidden)return;polling=true;
    try{
      const state=await api('/api/slots',undefined,'GET');
      for(const slot of state.slots){
        const el=$(`[data-slot-code="${slot.code}"]`);if(!el)continue;
        el.classList.toggle('occupied',slot.state==='OCCUPIED');el.classList.toggle('empty',slot.state==='EMPTY');el.classList.toggle('review',slot.needs_review);
        el.href=slot.visit_id?`/visits/${slot.visit_id}`:`/slots#confirm-${slot.code}`;
        el.title=`${slot.code} · ${slot.state==='EMPTY'?'Trống':'Có xe'}${slot.needs_review?' · Cần kiểm tra':''}`;
        $('.slot-status',el).textContent=slot.needs_review?'Kiểm tra':slot.plate||(slot.state==='EMPTY'?'Trống':'Có xe');
        const car=$('.slot-vehicle',el);
        if(slot.state==='OCCUPIED'&&!$('svg',car))car.replaceChildren($('.car-template').content.cloneNode(true));
        if(slot.state==='EMPTY'&&!$('.slot-p',car))car.replaceChildren(textElement('span','P','slot-p'));
      }
      $$('[data-area-count]').forEach(el=>{
        const areaSlots=state.slots.filter(s=>s.area_code===el.dataset.areaCount);
        const empty=areaSlots.filter(s=>s.state==='EMPTY').length;
        el.textContent=`${empty} / ${areaSlots.length} ô trống`;
      });
      const cards=$$('.kpi-grid>.kpi');
      if(location.pathname==='/'&&cards.length>=3){
        $('strong',cards[0]).replaceChildren(document.createTextNode(state.summary.occupied),textElement('small',` / ${state.summary.capacity}`));
        $('p',cards[0]).textContent=state.summary.occupancy_percent+'% vị trí có xe';$('.kpi-bar i',cards[0]).style.width=state.summary.occupancy_percent+'%';
        $('strong',cards[1]).textContent=state.summary.empty;$('p',cards[1]).textContent=state.summary.available+' ô sẵn sàng nhận xe';
        $('strong',cards[2]).textContent=state.summary.review;$('strong',cards[2]).classList.toggle('amber-text',state.summary.review>0);
      }
    }catch(error){notice(error.message);}finally{polling=false;}
  },5000);
  window.addEventListener('pagehide',()=>clearInterval(timer));
}

$$('form[data-api]').forEach(form=>form.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=$('button:not([type]),button[type="submit"]',form);
  if(button)button.disabled=true;
  $('.form-error',form).hidden=true;
  try {
    const result=await api(form.action,formData(form));
    if(result.redirect)location.assign(result.redirect);else notice(result.message);
  } catch(error){showError(form,error);}finally{if(button)button.disabled=false;}
}));
$('#check-plate')?.addEventListener('click',async()=>{
  const box=$('#plate-result');box.hidden=false;
  try{const result=await api('/api/plate-check?plate='+encodeURIComponent($('#plate').value),undefined,'GET');$('#plate').value=result.plate;box.textContent=result.active_visit?'Xe đang có lượt Đang gửi. Không thể nhận thêm.':result.message;}catch(e){box.textContent=e.message;}
});
$('#plate')?.addEventListener('input',()=>{const c=$('[name="plate_confirmed"]');if(c)c.checked=false;const box=$('#plate-result');if(box)box.hidden=true;});
$$('[data-checkout]').forEach(form=>{
  $('.quote-button',form).addEventListener('click',async()=>{
    const button=$('.quote-button',form);button.disabled=true;
    try{
      const result=await api(`/api/visits/${form.dataset.checkout}/quote`,undefined,'GET');
      $('.quote-total',form).textContent=money(result.fees.total);
      $('.quote-detail',form).textContent=result.fees.nights.length||result.fees.turns?`${result.fees.turns} lượt + ${result.fees.nights.length} đêm. ${result.fees.note}`:result.fees.note;
      $('[name="quoted_amount"]',form).value=result.fees.total;
      const paid=$('[name="paid_confirmed"]',form);if(paid)paid.checked=false;
      $('.checkout-submit',form).disabled=false;
    }catch(e){showError(form,e);}finally{button.disabled=false;}
  });
});

const streams=new Set();
window.addEventListener('pagehide',()=>streams.forEach(stream=>stream.getTracks().forEach(track=>track.stop())));
async function openCamera(video){
  if(!navigator.mediaDevices?.getUserMedia)throw new Error('Trình duyệt không hỗ trợ camera. Mở địa chỉ http://127.0.0.1:5000 hoặc dùng ảnh.');
  const stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1280},height:{ideal:720}},audio:false});
  streams.add(stream);video.srcObject=stream;video.hidden=false;await video.play();return stream;
}
function stopCamera(video){if(video.srcObject){video.srcObject.getTracks().forEach(t=>t.stop());streams.delete(video.srcObject);video.srcObject=null;}video.pause();}
async function frameBlob(video,type='image/jpeg',quality=0.95){
  if(!video.videoWidth)throw new Error('Chưa có khung hình. Hãy mở camera hoặc phát video.');
  const canvas=document.createElement('canvas');canvas.width=video.videoWidth;canvas.height=video.videoHeight;canvas.getContext('2d').drawImage(video,0,0);
  return await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('Không chụp được khung hình.')),type,quality));
}
$$('.ocr-widget').forEach(widget=>{
  const video=$('video',widget), message=$('.ocr-message',widget), buttons=$$('button',widget);
  const fileInput=$('.ocr-file',widget), sourcePreview=$('.ocr-source-preview',widget);
  const suggestion=$('.ocr-suggestion',widget), proposed=$('.ocr-proposed-plate',widget);
  const rawText=$('.ocr-raw-text',widget), cropPreview=$('.ocr-crop-preview',widget), edit=$('.ocr-edit',widget);
  let sourceUrl=null;
  function preview(blob){
    if(sourceUrl)URL.revokeObjectURL(sourceUrl);
    sourceUrl=URL.createObjectURL(blob);sourcePreview.src=sourceUrl;sourcePreview.hidden=false;
  }
  async function read(blob){
    if(!blob){message.textContent='Hãy chọn ảnh hoặc mở camera.';return;}
    preview(blob);suggestion.hidden=true;
    buttons.forEach(b=>b.disabled=true);message.textContent='Đang đọc ảnh…';
    try{
      const upload=new FormData();upload.append('image',blob,'plate.jpg');const result=await api('/api/vision/plate',upload);
      message.textContent=result.message;
      if(result.ok&&result.plate){
        proposed.textContent=result.plate;edit.value=result.plate;
        rawText.textContent=`OCR thô: ${result.raw_text||'—'} · điểm ${result.confidence===null?'—':Math.round(result.confidence*100)+'%'}`;
        if(result.crop_image){cropPreview.src=result.crop_image;cropPreview.hidden=false;}else{cropPreview.removeAttribute('src');cropPreview.hidden=true;}
        suggestion.hidden=false;
      }
    }catch(e){message.textContent=e.message+' Bạn có thể nhập thủ công.';}finally{buttons.forEach(b=>b.disabled=false);}
  }
  fileInput.addEventListener('change',()=>{if(fileInput.files[0]){preview(fileInput.files[0]);suggestion.hidden=true;message.textContent='Ảnh đã chọn. Bấm “Nhận diện biển số” để xử lý.';}});
  $('.ocr-read',widget).addEventListener('click',()=>read(fileInput.files[0]));
  $('.ocr-camera',widget).addEventListener('click',async()=>{try{stopCamera(video);await openCamera(video);$('.ocr-capture',widget).hidden=false;$('.ocr-stop',widget).hidden=false;}catch(e){message.textContent='Không mở được camera: '+e.message;}});
  $('.ocr-capture',widget).addEventListener('click',async()=>{try{await read(await frameBlob(video));}catch(e){message.textContent=e.message;}});
  $('.ocr-stop',widget).addEventListener('click',()=>{stopCamera(video);video.hidden=true;$('.ocr-capture',widget).hidden=true;$('.ocr-stop',widget).hidden=true;});
  $('.ocr-confirm',widget).addEventListener('click',()=>{
    const value=edit.value.trim().toUpperCase();
    if(!value){message.textContent='Hãy nhập hoặc sửa biển số trước khi xác nhận.';return;}
    const target=document.getElementById(widget.dataset.target);target.value=value;target.dispatchEvent(new Event('input'));
    message.textContent=`Đã chuyển ${value} vào biểu mẫu. Hãy tiếp tục đối chiếu và xác nhận nghiệp vụ.`;
  });
  window.addEventListener('pagehide',()=>{if(sourceUrl)URL.revokeObjectURL(sourceUrl);});
});

$$('.plate-detection-widget').forEach(widget=>{
  const fileInput=$('.plate-media-file',widget), stage=$('.plate-media-stage',widget);
  const image=$('.plate-source-image',widget), video=$('.plate-source-video',widget);
  const message=$('.plate-message',widget), results=$('.plate-detection-results',widget);
  const cropGrid=$('.plate-crop-grid',widget);
  const buttons=$$('button',widget), analyze=$('.plate-analyze',widget), stop=$('.plate-stop',widget);
  const ocrCard=$('.ocr-plate-card',widget), ocrBadge=$('.ocr-recognized-badge',widget);
  const ocrInput=$('.ocr-plate-input',widget), ocrStatusPill=$('.ocr-status-pill',widget);
  const ocrApplyBtn=$('.ocr-apply-btn',widget), ocrLookupBtn=$('.ocr-lookup-btn',widget);
  const ocrFeedback=$('.ocr-lookup-feedback',widget);
  let sourceBlob=null, sourceUrl=null, sourceKind=null;

  async function seek(time){
    if(Math.abs(video.currentTime-time)<0.0005)return;
    await new Promise((resolve,reject)=>{
      const done=()=>{cleanup();resolve();},failed=()=>{cleanup();reject(new Error('Không đọc được frame lân cận.'));};
      const cleanup=()=>{video.removeEventListener('seeked',done);video.removeEventListener('error',failed);};
      video.addEventListener('seeked',done,{once:true});video.addEventListener('error',failed,{once:true});video.currentTime=time;
    });
  }
  async function temporalFrames(radius=5){
    const originalTime=video.currentTime,resume=!video.paused,frames=[],seen=new Set();let currentIndex=0;
    video.pause();
    try{
      for(let offset=-radius;offset<=radius;offset++){
        const maximum=Math.max(0,video.duration-0.001);
        const time=Math.max(0,Math.min(maximum,originalTime+offset/30));
        const key=time.toFixed(6);if(seen.has(key))continue;seen.add(key);
        await seek(time);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        if(offset===0)currentIndex=frames.length;
        frames.push(await frameBlob(video,'image/jpeg',0.98));
      }
    }finally{
      await seek(originalTime);
      if(resume)video.play().catch(()=>{});
    }
    return {frames,currentIndex};
  }

  function showResults(result){
    cropGrid.replaceChildren();
    if(result.vehicles && result.vehicles.length){
      result.vehicles.forEach(item=>{
        const enhanced=item.plate_crop_enhanced||item.plate_crop;
        if(enhanced){const crop=document.createElement('img');crop.src=enhanced;crop.alt='';cropGrid.append(crop);}
      });
    }

    if(ocrCard){
      if(ocrFeedback){ocrFeedback.hidden=true;ocrFeedback.innerHTML='';}
      const detectedPlate=result.plate||(result.vehicles&&result.vehicles.find(v=>v.plate)?.plate)||'';
      if(detectedPlate){
        if(ocrBadge)ocrBadge.textContent=detectedPlate;
        if(ocrInput)ocrInput.value=detectedPlate;
        if(ocrStatusPill){
          ocrStatusPill.textContent='OCR Thành công';
          ocrStatusPill.className='status-pill ocr-status-pill green';
        }
        ocrCard.hidden=false;
      }else if(cropGrid.children.length>0||(result.vehicles&&result.vehicles.length>0)){
        if(ocrBadge)ocrBadge.textContent='Chưa đọc rõ ký tự';
        if(ocrInput)ocrInput.value='';
        if(ocrStatusPill){
          ocrStatusPill.textContent='Cần nhập tay';
          ocrStatusPill.className='status-pill ocr-status-pill amber';
        }
        ocrCard.hidden=false;
      }else{
        if(ocrBadge)ocrBadge.textContent='Không phát hiện biển số';
        if(ocrInput)ocrInput.value='';
        if(ocrStatusPill){
          ocrStatusPill.textContent='Không phát hiện';
          ocrStatusPill.className='status-pill ocr-status-pill neutral';
        }
        ocrCard.hidden=false;
      }
    }

    results.hidden=!cropGrid.children.length && !(ocrCard && !ocrCard.hidden);
  }

  ocrApplyBtn?.addEventListener('click',()=>{
    const val=(ocrInput?.value||'').trim().toUpperCase();
    if(!val){
      if(ocrFeedback){
        ocrFeedback.hidden=false;
        ocrFeedback.innerHTML='<div class="alert alert-warning py-2 px-3 small mb-0">Vui lòng nhập hoặc kiểm tra biển số trước khi điền vào biểu mẫu.</div>';
      }
      return;
    }
    const targetId=widget.dataset.target;
    const targetInput=targetId?document.getElementById(targetId):null;
    if(targetInput){
      targetInput.value=val;
      targetInput.dispatchEvent(new Event('input',{bubbles:true}));
      targetInput.dispatchEvent(new Event('change',{bubbles:true}));
      if(ocrFeedback){
        ocrFeedback.hidden=false;
        ocrFeedback.innerHTML=`<div class="alert alert-success py-2 px-3 small mb-0">✓ Đã điền biển số <strong>${val}</strong> vào biểu mẫu.</div>`;
      }
    }
  });

  ocrLookupBtn?.addEventListener('click',async()=>{
    const val=(ocrInput?.value||'').trim();
    if(!val){
      if(ocrFeedback){
        ocrFeedback.hidden=false;
        ocrFeedback.innerHTML='<div class="alert alert-warning py-2 px-3 small mb-0">Vui lòng nhập biển số để tra cứu lượt gửi.</div>';
      }
      return;
    }
    if(ocrFeedback){
      ocrFeedback.hidden=false;
      ocrFeedback.innerHTML='<div class="text-muted small py-2">Đang tra cứu lượt gửi trong cơ sở dữ liệu…</div>';
    }
    ocrLookupBtn.disabled=true;
    try{
      const resp=await api('/api/visits/lookup?plate='+encodeURIComponent(val),undefined,'GET');
      if(resp.found&&resp.visit){
        const enteredDate=resp.visit.entered_at?new Date(resp.visit.entered_at).toLocaleString('vi-VN'):'—';
        ocrFeedback.innerHTML=`
          <div class="alert alert-success py-2 px-3 small mb-0">
            <div class="fw-bold mb-1">✓ ${resp.message}</div>
            <div class="mb-2">Vị trí đỗ: <strong>${resp.visit.slot_code}</strong> · Mã vé: <strong>${resp.visit.ticket_code}</strong> · Giờ vào: ${enteredDate}</div>
            <div class="d-flex gap-2 flex-wrap">
              <a href="${resp.visit.detail_url}" class="btn btn-sm btn-outline-success">Chi tiết lượt gửi ↗</a>
              <a href="${resp.visit.exit_url}" class="btn btn-sm btn-primary">Làm thủ tục xe ra →</a>
            </div>
          </div>
        `;
      }else{
        ocrFeedback.innerHTML=`
          <div class="alert alert-warning py-2 px-3 small mb-0">
            ${resp.message||'Không tìm thấy lượt gửi đang hoạt động cho biển số này.'}
          </div>
        `;
      }
    }catch(err){
      ocrFeedback.innerHTML=`
        <div class="alert alert-danger py-2 px-3 small mb-0">
          Lỗi tra cứu: ${err.message}
        </div>
      `;
    }finally{
      ocrLookupBtn.disabled=false;
    }
  });

  function setMedia(blob,kind){
    if(sourceUrl)URL.revokeObjectURL(sourceUrl);sourceBlob=blob;sourceKind=kind;sourceUrl=URL.createObjectURL(blob);
    stopCamera(video);video.removeAttribute('src');image.removeAttribute('src');
    image.hidden=kind!=='image';video.hidden=kind==='image';video.controls=kind==='video';stage.hidden=false;
    results.hidden=true;
    if(kind==='image'){image.src=sourceUrl;}
    else{video.src=sourceUrl;video.load();}
  }
  async function run(){
    let blob=sourceBlob;if(!blob&&sourceKind!=='video'&&sourceKind!=='camera'){message.textContent='Hãy chọn ảnh/video hoặc mở camera.';return;}
    buttons.forEach(button=>button.disabled=true);message.hidden=false;message.textContent='Đang phát hiện xe và vùng biển số…';
    try{
      const upload=new FormData();
      if(sourceKind==='video'){
        const window=await temporalFrames(5);window.frames.forEach((frame,index)=>upload.append('frames',frame,`frame_${index}.jpg`));
        upload.append('current_index',String(window.currentIndex));
      }else{
        if(sourceKind==='camera')blob=await frameBlob(video,'image/jpeg',0.98);
        upload.append('image',blob,sourceKind==='image'&&blob.name?blob.name:'frame.jpg');
      }
      const result=await api('/api/vision/plate',upload,'POST',300000);showResults(result);message.textContent='';message.hidden=true;
    }catch(error){results.hidden=true;message.hidden=false;message.textContent=error.message;}
    finally{buttons.forEach(button=>button.disabled=false);}
  }
  fileInput.addEventListener('change',()=>{
    const file=fileInput.files[0];if(!file)return;
    const kind=file.type.startsWith('video/')?'video':'image';setMedia(file,kind);
    message.hidden=false;
    message.textContent=kind==='video'?'Phát hoặc tua video, sau đó phân tích frame hiện tại.':'Ảnh đã chọn. Bấm phân tích để chạy AI.';
  });
  analyze.addEventListener('click',()=>run().catch(error=>{message.textContent=error.message;}));
  $('.plate-camera',widget).addEventListener('click',async()=>{
    try{
      if(sourceUrl){URL.revokeObjectURL(sourceUrl);sourceUrl=null;}sourceBlob=null;sourceKind='camera';
      image.hidden=true;video.removeAttribute('src');video.controls=false;stage.hidden=false;
      await openCamera(video);stop.hidden=false;results.hidden=true;message.hidden=false;message.textContent='Camera đã mở. Bấm phân tích frame hiện tại.';
    }catch(error){message.textContent='Không mở được camera: '+error.message;}
  });
  stop.addEventListener('click',()=>{stopCamera(video);video.hidden=true;stage.hidden=true;stop.hidden=true;sourceKind=null;results.hidden=true;});
  video.addEventListener('seeked',()=>{results.hidden=true;message.hidden=false;message.textContent='Frame đã thay đổi. Bấm phân tích để cập nhật kết quả.';});
  window.addEventListener('pagehide',()=>{if(sourceUrl)URL.revokeObjectURL(sourceUrl);stopCamera(video);});
});

const statsNode=$('#statistics-data');
if(statsNode && window.Chart){
  const stats=JSON.parse(statsNode.textContent);
  Chart.defaults.font.family='Segoe UI, system-ui, sans-serif';Chart.defaults.color='#829491';
  new Chart($('#flow-chart'),{type:'bar',data:{labels:stats.hours.map(h=>String(h.hour).padStart(2,'0')),datasets:[{label:'Xe vào',data:stats.hours.map(h=>h.arrivals),backgroundColor:'#328f76',borderRadius:3},{label:'Xe ra',data:stats.hours.map(h=>h.departures),backgroundColor:'#b7d6c7',borderRadius:3}]},options:{responsive:true,maintainAspectRatio:false,scales:{y:{beginAtZero:true,ticks:{precision:0},grid:{color:'#edf2ef'}},x:{grid:{display:false}}},plugins:{legend:{position:'bottom'}}}});
  new Chart($('#revenue-chart'),{type:'doughnut',data:{labels:['Vé lượt','Vé tháng'],datasets:[{data:[stats.turn_revenue,stats.monthly_revenue],backgroundColor:['#328f76','#c9dbce'],borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,cutout:'72%',plugins:{legend:{position:'bottom'},tooltip:{callbacks:{label:c=>c.label+': '+money(c.raw)}}}}});
}
$('#backup-form')?.addEventListener('submit',async e=>{
  e.preventDefault();const form=e.target,button=$('button',form);button.disabled=true;$('.form-error',form).hidden=true;
  try{const result=await api('/api/backups',formData(form),'POST',180000);const out=$('#backup-result');out.textContent=`${result.message} Tệp: ${result.filename}. Thư mục: ${result.directory}`;out.hidden=false;notice('Đã tạo bản sao lưu được xác minh.');}catch(error){showError(form,error);}finally{button.disabled=false;}
});

if($('#parking-file') && !document.querySelector('[data-vision-video-workflow]')){
  const video=$('#parking-video'), preview=$('#parking-image'), message=$('#parking-message');let chosen=null,url=null,busy=false;
  function say(text){message.textContent=text;message.hidden=false;}
  function stop(){stopCamera(video);$('#parking-auto').checked=false;}
  $('#parking-file').addEventListener('change',e=>{
    stop();chosen=e.target.files[0];if(url)URL.revokeObjectURL(url);video.hidden=true;preview.hidden=true;
    if(!chosen)return;url=URL.createObjectURL(chosen);
    if(chosen.type.startsWith('video/')){video.src=url;video.hidden=false;say('Bấm phát video rồi nhận diện một khung hình, hoặc bật nhận diện định kỳ.');}
    else{preview.src=url;preview.hidden=false;}
  });
  $('#parking-camera').addEventListener('click',async()=>{try{stop();chosen=null;preview.hidden=true;video.removeAttribute('src');await openCamera(video);say(`Camera ${video.videoWidth} × ${video.videoHeight}. Cấu hình ROI phải khớp độ phân giải này.`);}catch(e){say('Không mở được camera: '+e.message);}});
  $('#parking-stop').addEventListener('click',()=>{stop();say('Đã dừng nguồn video / camera.');});
  async function analyze(){
    if(busy)return;busy=true;$('#parking-analyze').disabled=true;
    try{
      const blob=!video.hidden?await frameBlob(video):chosen;
      if(!blob)throw new Error('Chọn ảnh/video hoặc mở camera trước.');
      const form=new FormData();form.append('image',blob,'parking.jpg');say('Đang nhận diện và đối chiếu dữ liệu…');
      const result=await api('/api/vision/parking',form);
      const overlay=$('#parking-overlay');overlay.src='data:image/jpeg;base64,'+result.overlay;overlay.hidden=false;
      const grid=textElement('div','','prediction-grid');
      result.predictions.forEach(p=>{const card=textElement('div','',`prediction-item ${p.accepted?'':'warn'}`);card.title=p.reason;card.append(textElement('strong',p.slot),textElement('div',`${p.state==='EMPTY'?'Trống':'Có xe'} · ${(p.confidence*100).toFixed(1)}%`),textElement('div',p.accepted?'Khớp dữ liệu':'Cần kiểm tra / bỏ qua'));grid.append(card);});
      $('#parking-predictions').replaceChildren(grid);say('Đã nhận diện. Kết quả mâu thuẫn hoặc độ tin cậy thấp không thay đổi trạng thái đã lưu.');
    }catch(e){say(e.message);$('#parking-auto').checked=false;}finally{busy=false;$('#parking-analyze').disabled=false;}
  }
  $('#parking-analyze').addEventListener('click',analyze);
  const interval=setInterval(()=>{if($('#parking-auto').checked&&!video.hidden&&!video.paused)analyze();},3000);
  window.addEventListener('pagehide',()=>{clearInterval(interval);stop();if(url)URL.revokeObjectURL(url);});
}
