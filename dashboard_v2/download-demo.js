/* UI-only link validation. No requests, downloader, or production API contract. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.BFDownload=api;
})(typeof window==='undefined'?this:window,function(){
  'use strict';
  /* Any site, as the real downloader: the probe decides later. A host starting with "chua-ho-tro." plays a
     page yt-dlp cannot read. */
  const UNSUPPORTED_PREFIX='chua-ho-tro.';
  function validate(link){
    const value=String(link||'').trim();
    if(!value)throw new Error('Nhập liên kết video cần tải.');
    if(value.length>2048)throw new Error('Liên kết quá dài. Vui lòng kiểm tra lại.');
    let url;try{url=new URL(value);}catch(_){throw new Error('Liên kết chưa hợp lệ. Hãy nhập đầy đủ https://…');}
    if(url.protocol!=='https:'||url.username||url.password||(url.port&&url.port!=='443'))throw new Error('Dùng liên kết HTTPS, không chứa tài khoản hoặc cổng tùy chỉnh.');
    if(!url.hostname.includes('.')||/^[\d.]+$|^\[/.test(url.hostname))throw new Error('Link phải có tên miền đầy đủ, không dùng địa chỉ IP.');
    if(url.pathname==='/')throw new Error('Nhập link của một video cụ thể, không phải trang chủ.');
    return {host:url.hostname,url:url.href};
  }
  const active=t=>['DOWNLOADING','VERIFYING'].includes(t.state);
  function createQueue(){return {items:[],serial:0,parallel:2,paused:false};}
  function log(t,message){t.logs.push(message);}
  function schedule(q){
    if(q.paused)return;
    let slots=Math.max(0,q.parallel-q.items.filter(active).length);
    for(const t of q.items){if(!slots)break;if(t.state!=='QUEUED')continue;t.state='DOWNLOADING';log(t,'[Mẫu] Worker nhận lượt tải.');slots--;}
  }
  function enqueue(q,text){
    const lines=String(text||'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
    if(!lines.length)throw new Error('Nhập ít nhất một liên kết video.');
    if(lines.length>20||q.items.length+lines.length>100)throw new Error('Tối đa 20 link mỗi lần và 100 lượt trong demo.');
    const entries=lines.map((line,i)=>{try{return validate(line);}catch(e){throw new Error('Dòng '+(i+1)+': '+e.message);}});
    const seen=new Set(q.items.map(t=>t.url));
    for(const entry of entries){if(seen.has(entry.url))throw new Error('Có link trùng trong danh sách hoặc đã thêm trước đó. Dùng Thử lại cho lượt đã dừng/lỗi.');seen.add(entry.url);}
    for(const entry of entries){
      const id=++q.serial,item={...entry,id,name:'Video mẫu '+id+' · '+entry.host,state:'QUEUED',progress:0,totalBytes:320000000,logs:['[Mẫu] Đã thêm vào danh sách tải.'],error:''};
      if(entry.host.startsWith(UNSUPPORTED_PREFIX)){item.state='FAILED';item.code='UNSUPPORTED';item.error='Trang này chưa được hỗ trợ: không tìm thấy video đọc được trong trang (mô phỏng).';log(item,'[Mẫu] Thăm dò: trang không có video.');}
      q.items.push(item);
    }
    schedule(q);return entries.length;
  }
  function tick(q){
    if(q.paused)return;
    for(const t of q.items.filter(active)){
      if(t.state==='VERIFYING'){t.progress=100;t.state='COMPLETED';log(t,'[Mẫu] Kiểm tra hoàn tất. Không tạo tệp thật.');}
      else{const before=t.progress;t.progress=Math.min(96,t.progress+12);if(before<48&&t.progress>=48)log(t,'[Mẫu] Đã nhận khoảng một nửa dữ liệu.');if(t.progress===96){t.state='VERIFYING';log(t,'[Mẫu] Đang kiểm tra tệp tải về.');}}
    }
    schedule(q);
  }
  function action(q,id,op){
    const t=q.items.find(t=>t.id===Number(id));if(!t)return false;
    if(op==='pause'&&active(t)){t.resumeState=t.state;t.state='PAUSED';log(t,'[Mẫu] Tạm dừng lượt tải.');}
    else if(op==='resume'&&t.state==='PAUSED'){
      if(q.paused||q.items.filter(active).length>=q.parallel)return false;
      t.state=t.resumeState||'DOWNLOADING';log(t,'[Mẫu] Tiếp tục lượt tải.');
    }
    else if(op==='cancel'&&['QUEUED','DOWNLOADING','VERIFYING','PAUSED'].includes(t.state)){t.state='CANCELLED';log(t,'[Mẫu] Đã hủy lượt tải.');}
    else if(op==='retry'&&['FAILED','CANCELLED'].includes(t.state)&&t.code!=='UNSUPPORTED'){t.state='QUEUED';t.progress=0;t.error='';log(t,'[Mẫu] Thử lại từ đầu, xếp cuối hàng đợi.');q.items=q.items.filter(x=>x!==t).concat(t);}
    else if(op==='fail'&&active(t)){t.state='FAILED';t.error='Lỗi kết nối giả lập. Bạn có thể thử lại.';log(t,'[Mẫu] Worker báo lỗi kết nối.');}
    else return false;
    schedule(q);return true;
  }
  function setParallel(q,value){if(![1,2,3].includes(Number(value)))throw new Error('Chọn từ 1 đến 3 lượt tải đồng thời.');q.parallel=Number(value);schedule(q);}
  function togglePause(q){q.paused=!q.paused;schedule(q);}
  function stats(q){return {active:q.items.filter(active).length,queued:q.items.filter(t=>t.state==='QUEUED').length,completed:q.items.filter(t=>t.state==='COMPLETED').length,failed:q.items.filter(t=>t.state==='FAILED').length,paused:q.items.filter(t=>t.state==='PAUSED').length};}
  return {UNSUPPORTED_PREFIX,validate,createQueue,enqueue,tick,action,setParallel,togglePause,stats,active};
});
