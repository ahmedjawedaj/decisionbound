/* Research-demo fixture only. No model calls, repository analysis or actual cost claims. */
'use strict';
const scenarios = Object.freeze({
  compatible: {
    state:'TRANSLATE', className:'apply',
    title:'Transfer the intent. Not the implementation.',
    description:'An existing engineering preference can help, but a Django service-class layout is not a Go package design.',
    lesson:'Keep transport handlers thin; isolate domain rules',
    evidence:'Go package conventions; payment audit ADR',
    authority:'Owner ADR > compatible personal preference',
    action:'Keep domain boundaries; reject Python file-layout rules',
    explanation:'Scope match: design intent only. Specific service-class syntax is excluded. Target audit requirements remain protected.',
    required:900, optional:650, agent:'One worker'
  },
  conflict: {
    state:'ABSTAIN', className:'warn',
    title:'A successful habit is not a universal rule.',
    description:'The current owner-approved ADR prohibits introducing another layer for this subsystem.',
    lesson:'Always create a dedicated service class',
    evidence:'Owner ADR: no extra service layer for refunds',
    authority:'Current project policy > personal coding taste',
    action:'Reject transfer; follow target package pattern',
    explanation:'The memory can be relevant and still be inapplicable. Its original evidence survives; the transfer is declined.',
    required:1000, optional:540, agent:'One worker'
  },
  risky: {
    state:'ESCALATE', className:'stop',
    title:'A cheap shortcut cannot override a hard control.',
    description:'The task asks to skip an audit event on an authorization change to save time; policy forbids that omission.',
    lesson:'Keep implementation changes minimal',
    evidence:'Refund policy: immutable audit events required',
    authority:'Enforced security policy > task shortcut > taste',
    action:'Block unsafe plan; request authorized clarification',
    explanation:'Safety is enforced outside the prompt. No model or delegated worker receives a capability to bypass the audit policy.',
    required:1100, optional:430, agent:'No delegation'
  }
});
const $=id=>document.getElementById(id);
let active='compatible';
function update(){
  const c=scenarios[active], budget=Number($('budget').value);
  const enough=budget>=c.required;
  const optional=enough?Math.min(c.optional,Math.max(0,budget-c.required)):0;
  const used=enough?c.required+optional:0;
  $('budgetValue').textContent=budget.toLocaleString()+' tokens';
  $('decisionStatus').textContent=enough?c.state:'INSUFFICIENT';
  $('decisionStatus').className='status-pill '+(enough?c.className:'stop');
  $('caseTitle').textContent=enough?c.title:'Insufficient context for required obligations.';
  $('caseDesc').textContent=enough?c.description:'The protected task, policy and source pointers need '+c.required.toLocaleString()+' illustrative tokens. The system must fail closed instead of trimming constraints.';
  $('lesson').textContent=c.lesson;
  $('targetEvidence').textContent=c.evidence;
  $('authority').textContent=c.authority;
  $('action').textContent=enough?c.action:'No execution plan can be compiled';
  $('contextSelected').textContent=used.toLocaleString()+' / '+budget.toLocaleString();
  $('delegation').textContent=enough?c.agent:'Blocked';
  $('verification').textContent='Not executed';
  $('barProtected').style.width=(enough?(c.required/budget*100):0)+'%';
  $('barOptional').style.width=(optional/budget*100)+'%';
  $('barFree').style.width=((budget-used)/budget*100)+'%';
  $('decisionExplanation').textContent=enough?c.explanation:'This simulation never discards security requirements or owner policy to fit a context budget.';
  $('cli').textContent='dbound transfer --scenario '+active+' --budget-tokens '+budget+' --dry-run';
}
document.querySelectorAll('.scenario').forEach(button=>button.addEventListener('click',()=>{
  active=button.dataset.case;
  document.querySelectorAll('.scenario').forEach(b=>{const selected=b===button;b.classList.toggle('active',selected);b.setAttribute('aria-pressed',selected?'true':'false')});
  update();
}));
$('budget').addEventListener('input',update);
$('copyCli').addEventListener('click',async()=>{
  const b=$('copyCli');
  try{await navigator.clipboard.writeText($('cli').textContent);b.textContent='Copied';}catch(error){b.textContent='Select command to copy';const selection=window.getSelection();selection.removeAllRanges();const range=document.createRange();range.selectNodeContents($('cli'));selection.addRange(range);}
  setTimeout(()=>{b.textContent='Copy command'},1800);
});
update();
