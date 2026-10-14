"use client";

import { useEffect,useRef,useState } from "react";
import { Check,Download,Loader2,Plus,RefreshCw,Save,Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { WorkspaceApiError,type WorkspaceApi } from "@/lib/workspace-api";
import type { ChecklistDetail,ChecklistSummary,RequirementInput } from "@/lib/analysis-types";

type Props={api:WorkspaceApi;organizationId:string;projectId:string;canEdit:boolean;onExpired:()=>void};
const editableKeys=["logical_id","display_id","scope","item","category","parameter","condition","importance","predicate","citations","origin"] as const;
function inputs(value:ChecklistDetail):RequirementInput[]{return value.requirements.map(row=>Object.fromEntries(editableKeys.map(key=>[key,structuredClone(row[key])])) as RequirementInput);}

export function ChecklistEditor({api,organizationId,projectId,canEdit,onExpired}:Props){
  const [history,setHistory]=useState<ChecklistSummary[]>([]);
  const [detail,setDetail]=useState<ChecklistDetail|null>(null);
  const [rows,setRows]=useState<RequirementInput[]>([]);
  const [reason,setReason]=useState("");
  const [dirty,setDirty]=useState(false);
  const [busy,setBusy]=useState(false);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState("");
  const [refresh,setRefresh]=useState(0);
  const scope=useRef<object|null>(null);
  const read=useRef(0);
  const editable=canEdit && detail?.status==="draft" && !busy && !loading;
  function failed(value:unknown){if(value instanceof WorkspaceApiError && value.status===401)onExpired();else setError(value instanceof Error?value.message:"Не удалось сохранить чек-лист.");}
  function install(value:ChecklistDetail){setDetail(value);setRows(inputs(value));setDirty(false);setReason("");setHistory(items=>[...items.filter(item=>item.id!==value.id),value].sort((a,b)=>b.version-a.version));}
  useEffect(()=>{
    const identity={};scope.current=identity;read.current++;
    setLoading(true);setBusy(false);setError("");setDetail(null);setRows([]);setHistory([]);setDirty(false);setReason("");
    async function load(){
      try{
        const versions=await api.checklists(organizationId,projectId);
        if(scope.current!==identity)return;
        const sorted=[...versions].sort((a,b)=>b.version-a.version);setHistory(sorted);
        if(sorted[0]){const value=await api.checklist(organizationId,projectId,sorted[0].id);if(scope.current===identity)install(value);}
      }catch(value){if(scope.current===identity)failed(value);}
      finally{if(scope.current===identity)setLoading(false);}
    }
    void load();return()=>{if(scope.current===identity)scope.current=null;read.current++;};
  },[api,organizationId,projectId,onExpired,refresh]);
  async function openVersion(id:string){
    if(busy || dirty || loading)return;
    const identity=scope.current;const sequence=++read.current;setLoading(true);setError("");
    try{const value=await api.checklist(organizationId,projectId,id);if(scope.current===identity && read.current===sequence)install(value);}
    catch(value){if(scope.current===identity && read.current===sequence)failed(value);}
    finally{if(scope.current===identity && read.current===sequence)setLoading(false);}
  }
  function change(index:number,patch:Partial<RequirementInput>){if(!editable)return;setRows(items=>items.map((row,i)=>i===index?{...row,...patch}:row));setDirty(true);}
  function add(){
    if(!editable || rows.length>=2000)return;
    const next=Math.max(0,...rows.filter(row=>row.scope==="product").map(row=>Number(row.display_id.slice(2))))+1;
    setRows(items=>[...items,{logical_id:crypto.randomUUID(),display_id:`T-${next}`,scope:"product",item:null,category:"Прочее",parameter:"",condition:"",importance:"scored",predicate:{kind:"semantic",text:""},citations:[],origin:{}}]);setDirty(true);
  }
  async function mutate(action:"save"|"approve"|"fork"){
    if(!canEdit || !detail || busy || loading || (action!=="save" && dirty))return;
    if(action==="save" && (!dirty || !reason.trim() || rows.some(row=>!row.parameter?.trim() || !row.condition?.trim()))){setError("Заполните параметры, условия и причину изменения.");return;}
    const identity=scope.current;setBusy(true);setError("");
    try{
      const value=action==="save"?await api.saveChecklist(organizationId,projectId,detail.id,{requirements:rows,expected_revision:detail.revision,reason:reason.trim()}):action==="approve"?await api.approveChecklist(organizationId,projectId,detail.id,detail.revision):await api.forkChecklist(organizationId,projectId,detail.id);
      if(scope.current===identity)install(value);
    }catch(value){if(scope.current===identity)failed(value);}
    finally{if(scope.current===identity)setBusy(false);}
  }
  return <section aria-label="Редактор чек-листа" className="mt-7 space-y-5 rounded-xl border bg-white p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">Чек-лист требований</h2><p className="mt-1 text-xs text-muted-foreground">Сохранённые версии, правки с основанием и отчёты с исходными цитатами.</p></div><Button variant="outline" size="sm" disabled={busy || dirty || loading} onClick={()=>setRefresh(value=>value+1)}><RefreshCw/>Обновить версии</Button></div>
    {loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin"/>Открываем чек-лист…</p>}
    {!loading && !detail && <p className="text-sm text-muted-foreground">Извлеките требования из документации, затем обновите версии чек-листа.</p>}
    {detail && <><div className="flex flex-wrap items-center justify-between gap-3"><div><label htmlFor="checklist-version" className="sr-only">Версия чек-листа</label><select id="checklist-version" value={detail.id} disabled={busy || dirty || loading} onChange={event=>openVersion(event.target.value)} className="rounded-lg border bg-white px-3 py-2 text-sm">{history.map(value=><option key={value.id} value={value.id}>Версия {value.version} · {value.status==="approved"?"Подтверждён":"Черновик"}</option>)}</select><span className="ml-3 text-xs text-muted-foreground">Редакция {detail.revision}</span></div>{!dirty && !busy && <div className="flex gap-3">{(["pdf","xlsx","json"] as const).map(format=><a key={format} href={api.checklistReportUrl(organizationId,projectId,detail.id,detail.revision,format)} className="inline-flex items-center gap-1 rounded-md border px-3 py-2 text-xs font-medium text-primary hover:bg-muted"><Download className="size-3"/>{format.toUpperCase()}</a>)}</div>}</div>
      <div className="space-y-4">{rows.map((row,index)=><article key={row.logical_id || row.display_id} className="rounded-lg border p-4"><div className="mb-3 flex items-center justify-between gap-3"><span className="text-xs font-semibold text-primary">{row.display_id} · {row.scope==="product"?"Товар":"Участник"}</span>{editable && <Button variant="ghost" size="sm" aria-label={`Удалить ${row.display_id}`} onClick={()=>{setRows(items=>items.filter((_,i)=>i!==index));setDirty(true);}}><Trash2/></Button>}</div><div className="grid gap-4 sm:grid-cols-[1fr_180px]"><div><label htmlFor={`parameter-${row.display_id}`} className="mb-2 block text-xs font-medium">Параметр {row.display_id}</label><Input id={`parameter-${row.display_id}`} value={row.parameter || ""} disabled={!editable} onChange={event=>change(index,{parameter:event.target.value,predicate:{kind:"semantic",text:row.condition || ""}})}/></div><div><label htmlFor={`importance-${row.display_id}`} className="mb-2 block text-xs font-medium">Важность {row.display_id}</label><select id={`importance-${row.display_id}`} value={row.importance} disabled={!editable} onChange={event=>change(index,{importance:event.target.value as RequirementInput["importance"]})} className="h-10 w-full rounded-lg border bg-white px-3 text-sm"><option value="mandatory">Обязательное</option><option value="scored">Оценочное</option><option value="desirable">Желательное</option></select></div></div><label htmlFor={`condition-${row.display_id}`} className="mb-2 mt-4 block text-xs font-medium">Условие {row.display_id}</label><Input id={`condition-${row.display_id}`} value={row.condition || ""} disabled={!editable} onChange={event=>change(index,{condition:event.target.value,predicate:{kind:"semantic",text:event.target.value}})}/>{Array.isArray(row.origin.review_flags) && row.origin.review_flags.length>0 && <p className="mt-3 text-xs text-amber-900">Требует проверки источника</p>}</article>)}</div>
      {canEdit && detail.status==="draft" && <><Button variant="outline" disabled={!editable || rows.length>=2000} onClick={add}><Plus/>Добавить требование</Button>{dirty && <div><label htmlFor="checklist-edit-reason" className="mb-2 block text-sm font-medium">Причина изменения</label><Input id="checklist-edit-reason" maxLength={2000} value={reason} disabled={busy} onChange={event=>setReason(event.target.value)} placeholder="Основание правок для журнала изменений"/></div>}<div className="flex flex-wrap gap-3"><Button disabled={busy || !dirty || !reason.trim()} onClick={()=>mutate("save")}><Save/>Сохранить изменения</Button><Button variant="outline" disabled={busy || dirty || !rows.length || rows.some(row=>!row.parameter?.trim() || !row.condition?.trim())} onClick={()=>mutate("approve")}><Check/>Подтвердить чек-лист</Button></div></>}
      {canEdit && detail.status==="approved" && <Button variant="outline" disabled={busy} onClick={()=>mutate("fork")}><Plus/>Создать новую версию</Button>}
      {dirty && <p className="text-xs text-muted-foreground">Сохраните изменения перед подтверждением, переключением версии и выгрузкой отчётов.</p>}
    </>}
    {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
  </section>;
}
