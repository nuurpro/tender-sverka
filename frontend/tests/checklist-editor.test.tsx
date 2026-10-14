import assert from "node:assert/strict";
import { afterEach,test } from "node:test";
import { cleanup,configure,fireEvent,render,screen,waitFor } from "@testing-library/react";
import { ChecklistEditor } from "../components/workspace/checklist-editor";
import { WorkspaceApiError,type WorkspaceApi } from "../lib/workspace-api";
import type { ChecklistDetail,ChecklistSave } from "../lib/analysis-types";

configure({ getElementError: message => new Error(message || "Element not found") });
afterEach(cleanup);
const checklist: ChecklistDetail = { id:"checklist-a",organization_id:"org-a",project_id:"project-a",version:1,revision:2,status:"draft",parent_version_id:null,approved_by:null,approved_at:null,created_at:"2026-10-14T08:00:00Z",requirements:[{ id:"row-a",organization_id:"org-a",checklist_id:"checklist-a",logical_id:"logical-a",display_id:"T-1",scope:"product",item:"Насос",category:"Технические",parameter:"Мощность",condition:"Не менее 25 Вт",importance:"mandatory",predicate:{kind:"numeric",operator:"gte",value:"25",unit:"W",upper:null,tolerance:null},citations:[],origin:{kind:"document",analysis_run_id:"analysis-a",review_flags:[]},created_at:"2026-10-14T08:00:00Z"}] };
const props={organizationId:"org-a",projectId:"project-a",canEdit:true,onExpired:()=>{}};
function api(overrides:Record<string,unknown>={}) {
  return {checklists:async()=>[checklist],checklist:async()=>checklist,checklistReportUrl:(_o:string,_p:string,id:string,rev:number,format:string)=>`/api/report/${id}.${format}?revision=${rev}`,...overrides} as unknown as WorkspaceApi;
}

test("editor saves a reasoned revision and derives a fresh predicate from edited condition",async()=>{
  let input:ChecklistSave|undefined;
  const client=api({saveChecklist:async(_o:string,_p:string,_id:string,value:ChecklistSave)=>{input=value;return {...checklist,revision:3,requirements:value.requirements.map(row=>({...checklist.requirements[0],...row}))};}});
  render(<ChecklistEditor {...props} api={client}/>);
  fireEvent.change(await screen.findByLabelText("Условие T-1"),{target:{value:"Не менее 30 Вт"}});
  assert.equal((screen.getByRole("button",{name:"Сохранить изменения"}) as HTMLButtonElement).disabled,true);
  fireEvent.change(screen.getByLabelText("Причина изменения"),{target:{value:"Уточнение эксперта"}});
  fireEvent.click(screen.getByRole("button",{name:"Сохранить изменения"}));
  await waitFor(()=>assert.ok(input));
  assert.equal(input!.expected_revision,2);assert.equal(input!.reason,"Уточнение эксперта");
  assert.equal(input!.requirements[0].condition,"Не менее 30 Вт");
  assert.deepEqual(input!.requirements[0].predicate,{kind:"semantic",text:"Не менее 30 Вт"});
  assert.deepEqual(input!.requirements[0].origin,checklist.requirements[0].origin);
  assert.ok(!("id" in input!.requirements[0]));
  await waitFor(()=>assert.ok(screen.getByRole("link",{name:"PDF"}).getAttribute("href")?.includes("revision=3")));
});

test("revision conflict keeps unsaved changes and cannot silently approve them",async()=>{
  let approvals=0;
  const client=api({saveChecklist:async()=>{throw new WorkspaceApiError("Конфликт изменений.",409);},approveChecklist:async()=>{approvals++;}});
  render(<ChecklistEditor {...props} api={client}/>);
  fireEvent.change(await screen.findByLabelText("Условие T-1"),{target:{value:"Не менее 50 Вт"}});
  fireEvent.change(screen.getByLabelText("Причина изменения"),{target:{value:"Изменение"}});
  fireEvent.click(screen.getByRole("button",{name:"Сохранить изменения"}));
  await screen.findByText("Конфликт изменений.");
  assert.equal((screen.getByLabelText("Условие T-1") as HTMLInputElement).value,"Не менее 50 Вт");
  assert.equal((screen.getByRole("button",{name:"Подтвердить чек-лист"}) as HTMLButtonElement).disabled,true);
  assert.equal(screen.queryByRole("link",{name:"PDF"}),null);
  assert.equal(approvals,0);
});

test("approval makes editing immutable and creates a new draft through fork",async()=>{
  const approved={...checklist,status:"approved" as const,revision:3,approved_by:"user-a",approved_at:checklist.created_at};
  let forked=0;
  const client=api({approveChecklist:async(_o:string,_p:string,_id:string,revision:number)=>{assert.equal(revision,2);return approved;},forkChecklist:async()=>{forked++;return {...checklist,id:"checklist-b",version:2,revision:1,parent_version_id:checklist.id};}});
  render(<ChecklistEditor {...props} api={client}/>);
  fireEvent.click(await screen.findByRole("button",{name:"Подтвердить чек-лист"}));
  await waitFor(()=>assert.equal((screen.getByLabelText("Условие T-1") as HTMLInputElement).disabled,true));
  fireEvent.click(screen.getByRole("button",{name:"Создать новую версию"}));
  await waitFor(()=>assert.equal(forked,1));
  await waitFor(()=>assert.equal((screen.getByLabelText("Условие T-1") as HTMLInputElement).disabled,false));
});

test("viewer can download saved reports but cannot edit, approve or fork",async()=>{
  render(<ChecklistEditor {...props} canEdit={false} api={api()}/>);
  assert.ok(await screen.findByRole("link",{name:"XLSX"}));
  assert.equal((screen.getByLabelText("Условие T-1") as HTMLInputElement).disabled,true);
  assert.equal(screen.queryByRole("button",{name:"Сохранить изменения"}),null);
  assert.equal(screen.queryByRole("button",{name:"Подтвердить чек-лист"}),null);
});
