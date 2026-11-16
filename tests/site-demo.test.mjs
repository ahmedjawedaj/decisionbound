// Verifies the static hypothetical decision visualization, NOT agent-runtime correctness.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../docs/demo.js',import.meta.url),'utf8');
function makeFixture(){
 const nodes=new Map();
 for(const id of ['budget','budgetValue','decisionStatus','caseTitle','caseDesc','lesson','targetEvidence','authority','action','contextSelected','delegation','verification','barProtected','barOptional','barFree','decisionExplanation','cli','copyCli']){
  nodes.set(id,{textContent:'',className:'',style:{},value:id==='budget'?'1800':undefined,listeners:{},addEventListener(type,cb){this.listeners[type]=cb}});
 }
 const buttons=['compatible','conflict','risky'].map(key=>({dataset:{case:key},listeners:{},addEventListener(type,cb){this.listeners[type]=cb},setAttribute(){},classList:{toggle(){}}}));
 const document={getElementById:id=>nodes.get(id),querySelectorAll:()=>buttons};
 vm.runInNewContext(source,{document,navigator:{clipboard:{}},setTimeout});
 return {get:id=>nodes.get(id),click:key=>buttons.find(b=>b.dataset.case===key).listeners.click(),budget:n=>{nodes.get('budget').value=String(n);nodes.get('budget').listeners.input()}};
}
test('compatible case translates intent, not specific framework structure',()=>{
 const f=makeFixture();
 assert.equal(f.get('decisionStatus').textContent,'TRANSLATE');
 assert.match(f.get('action').textContent,/reject Python/i);
 assert.match(f.get('authority').textContent,/Owner ADR/);
});
test('conflicting project rule triggers abstention rather than personal taste',()=>{
 const f=makeFixture();f.click('conflict');
 assert.equal(f.get('decisionStatus').textContent,'ABSTAIN');
 assert.match(f.get('authority').textContent,/project policy/);
});
test('security conflict escalates and does not delegate',()=>{
 const f=makeFixture();f.click('risky');
 assert.equal(f.get('decisionStatus').textContent,'ESCALATE');
 assert.equal(f.get('delegation').textContent,'No delegation');
});
test('protected context never silently trimmed for low budget',()=>{
 const f=makeFixture();f.budget(700);
 assert.equal(f.get('decisionStatus').textContent,'INSUFFICIENT');
 assert.equal(f.get('contextSelected').textContent,'0 / 700');
 assert.match(f.get('action').textContent,/No execution plan/);
});
test('site does not claim runtime verification',()=>{
 const f=makeFixture(); for(const s of ['compatible','conflict','risky']){
  f.click(s);assert.equal(f.get('verification').textContent,'Not executed');
 }
});
