"use strict";
const $=id=>document.getElementById(id);
const token=document.querySelector('meta[name="lab-token"]').content;
let session=null, running=false, starting=false, stopping=false, outputMayBeOn=false, saving=false, epoch=0, frameId=null, lastFrame=null;
const fmt=(x,n=5)=>x==null?"—":Number(x).toLocaleString("ru-RU",{maximumSignificantDigits:n});
function msg(text,error=false){$("message").textContent=text;$("message").className=error?"error":"";}
function buttons(){ $("start").disabled=starting||stopping||saving;$("start").textContent=starting?"Запускаем…":running?"Применить и пуск":"▶ Пуск";$("stop").disabled=stopping||(!outputMayBeOn&&!starting);$("save").disabled=!frameId||saving;$("save").textContent=saving?"Сохраняем…":"Сохранить кадр"; }
async function api(path,data){const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Lab-Token":token},body:JSON.stringify(data)});const value=await r.json();if(!r.ok)throw new Error(value.error||"Не удалось выполнить действие");return value;}
function num(id){const v=$(id).value.trim().replace(",",".");if(!v||!Number.isFinite(Number(v)))throw new Error("Проверьте числовые параметры сигнала");return Number(v);}
function signal(){return{channel:1,waveform:$("waveform").value,frequency_hz:num("frequency")*num("unit"),amplitude_vpp:num("amplitude"),offset_v:num("offset"),phase_deg:0,duty_percent:num("duty"),symmetry_percent:num("symmetry")};}
function form(){const kind=$("waveform").value;$("frequency-row").hidden=kind==="DC";$("amplitude-row").hidden=kind==="DC";$("duty-row").hidden=!["SQUARE","PULSE"].includes(kind);$("symmetry-row").hidden=kind!=="RAMP";if(running)msg("Параметры изменены — нажмите «Применить и пуск».");}
function emptyPlot(text){if($("plot").classList.contains("js-plotly-plot"))Plotly.purge("plot");$("plot").replaceChildren();const box=document.createElement("div");box.className="empty";box.textContent=text;$("plot").append(box);}
async function showFrame(frame){
  if(!$("plot").classList.contains("js-plotly-plot"))$("plot").replaceChildren();
  const maxTime=frame.time_s[frame.time_s.length-1];const scale=maxTime<.001?1e6:maxTime<1?1e3:1;const unit=scale===1e6?"мкс":scale===1e3?"мс":"с";
  await Plotly.react("plot",[{x:frame.time_s.map(x=>x*scale),y:frame.voltage_v,mode:"lines",line:{color:"#006d77",width:1.5},name:"CH1"}],
    {margin:{l:58,r:20,t:18,b:50},paper_bgcolor:"transparent",plot_bgcolor:"transparent",hovermode:"x unified",uirevision:session,
     xaxis:{title:{text:`Время, ${unit}`},gridcolor:"#e6ecef",zerolinecolor:"#b9cdd3"},yaxis:{title:{text:"Напряжение, В"},gridcolor:"#e6ecef",zerolinecolor:"#b9cdd3"},font:{family:"-apple-system, sans-serif",size:12}},
    {responsive:true,displaylogo:false,modeBarButtonsToRemove:["lasso2d","select2d"]});
  frameId=frame.frame_id;lastFrame=frame;$("save-area").hidden=false;$("stats").hidden=false;$("stats").replaceChildren();
  const stats=frame.statistics;
  for(const [name,value] of [["Размах",fmt(stats.vpp_v)+" В"],["RMS",fmt(stats.rms_v)+" В"],["Частота, оценка",stats.estimated_frequency_hz==null?"—":fmt(stats.estimated_frequency_hz)+" Гц"]]){const d=document.createElement("div");d.className="stat";const small=document.createElement("small");small.textContent=name;const strong=document.createElement("strong");strong.textContent=value;d.append(small,strong);$("stats").append(d);}
  $("frame-time").textContent=(frame.demo?"ДЕМО · ":"")+new Date(frame.captured_at).toLocaleTimeString("ru-RU")+" · только в памяти";
  $("warnings").textContent=frame.warnings.join(" ");buttons();
}
async function poll(version){
  if(!running||version!==epoch)return;
  try{const frame=await api("/api/preview",{session});if(version!==epoch)return;await showFrame(frame);if(running)msg("");}
  catch(error){if(version===epoch){running=false;$("state").textContent="Ошибка чтения";$("state").className="state";msg(error.message+" Нажмите «Стоп», чтобы выключить выход.",true);$("stop").disabled=false;return;}}
  if(running&&version===epoch)setTimeout(()=>poll(version),200);
}
$("start").addEventListener("click",async()=>{
  let values;try{values=signal();}catch(e){msg(e.message,true);return;}
  const version=++epoch;running=false;starting=true;frameId=null;lastFrame=null;$("save-area").hidden=true;$("stats").hidden=true;$("applied").hidden=true;$("warnings").textContent="";$("frame-time").textContent="Ожидаем новый кадр";emptyPlot("Получаем сигнал…");buttons();msg("Запуск…");
  try{const result=await api("/api/start",values);if(version!==epoch)return;session=result.session;running=true;outputMayBeOn=true;$("state").textContent="Сигнал подаётся";$("state").className="state on";$("applied").hidden=false;const s=result.signal;$("applied").textContent=`Подано: ${s.waveform} · ${fmt(s.frequency_hz)} Гц · ${fmt(s.amplitude_vpp)} Вpp · смещение ${fmt(s.offset_v)} В`;msg("Получаем кадр…");poll(version);}
  catch(e){if(version===epoch){outputMayBeOn=true;msg(e.message,true);emptyPlot("Сигнал не запущен. Повторите «Пуск».");$("state").textContent="Не запущено";$("state").className="state";}}
  finally{starting=false;buttons();}
});
$("stop").addEventListener("click",async()=>{++epoch;running=false;stopping=true;buttons();msg("Выключаем выход генератора…");try{await api("/api/stop",{});session=null;outputMayBeOn=false;$("state").textContent="Остановлено";$("state").className="state";msg("Выход выключен.");}catch(e){msg(e.message,true);}finally{stopping=false;buttons();}});
$("save").addEventListener("click",async()=>{
  if(!frameId||saving)return;const selectedId=frameId;saving=true;buttons();msg("Сохраняем показанный кадр…");
  try{const result=await api("/api/save-frame",{frame_id:selectedId,title:$("title").value,note:$("note").value});$("saved").replaceChildren();const line=document.createElement("div");line.textContent="Сохранено: "+result.folder;$("saved").append(line);for(const [file,label]of[["Читать.html","Открыть запись"],["Данные.csv","Таблица данных"],["plot.png","График PNG"]]){const a=document.createElement("a");a.href=file==="Читать.html"?`/records/${result.id}/`:`/files/${result.id}/${encodeURIComponent(file)}`;if(file==="Читать.html"){a.target="_blank";a.rel="noopener";}a.textContent=label;a.style.marginRight="16px";$("saved").append(a);}msg("Кадр сохранён.");}
  catch(e){msg(e.message,true);}finally{saving=false;buttons();}
});
for(const id of["waveform","frequency","unit","amplitude","offset","duty","symmetry"])$(id).addEventListener("input",form);
window.addEventListener("resize",()=>{if($("plot").classList.contains("js-plotly-plot"))Plotly.Plots.resize("plot");});
form();buttons();
fetch('/api/health').then(r=>r.json()).then(state=>{
  if(state.running&&state.session&&!starting&&!running){session=state.session;running=true;outputMayBeOn=true;const version=++epoch;$("state").textContent="Сигнал подаётся";$("state").className="state on";const s=state.active_generator?.channels?.['1']?.signal;if(s){const unit=s.frequency_hz>=1000?1000:1;$("waveform").value=s.waveform;$("unit").value=String(unit);$("frequency").value=s.frequency_hz/unit;$("amplitude").value=s.amplitude_vpp;$("offset").value=s.offset_v;$("duty").value=s.duty_percent;$("symmetry").value=s.symmetry_percent;form();}buttons();poll(version);}
}).catch(()=>{});
