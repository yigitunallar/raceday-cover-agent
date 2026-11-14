/* This page speaks only to the deployed Render API. It does not create local mock events. */
const API = "https://raceday-cover-agent.onrender.com";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const title = value => String(value || "unknown").replace(/^get_/, "").replace(/_/g, " ");
const clock = value => { const date = new Date(value); return value && !isNaN(date) ? date.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}) : "—"; };
const money = (value, currency) => value == null || !Number.isFinite(Number(value)) ? "—" : new Intl.NumberFormat(undefined, {style:"currency",currency:currency || "EUR"}).format(Number(value));
const setText = (id, text) => { $(id).textContent = text; };
let tasks = [], selectedId = new URLSearchParams(location.search).get("task_id") || "", selectedGeneration = 0, quoteRoute = false, medicalRoute = false, intakeRoute = false, busy = false, workerRunning = false;

async function request(path, method = "GET", body) {
  const response = await fetch(API + path, {method, headers:{"Content-Type":"application/json"}, body:body === undefined ? undefined : JSON.stringify(body)});
  let data = null;
  try { data = await response.json(); } catch (_) { /* Some errors have no JSON body. */ }
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Request failed (${response.status})`);
  return data;
}
function notice(message, error = false) {
  const el = $("notice"); el.textContent = message; el.style.color = error ? "#ad584f" : "";
}
function setHtml(id, html) { const node=$(id); if(node.innerHTML!==html) node.innerHTML=html; }

function populateHorses() {
  const task = tasks.find(t => t._id === selectedId);
  setText("selectedHorse", task ? `${task.horse} · ${title(task.status)}` : "No saved task selected");
}

function renderTask(task) {
  if (!task) {
    setText("horseName", "No horse selected"); setText("raceName", "No tasks available from Render");
    setText("taskState", "NO TASK"); setText("taskIdLabel", "Select a horse");
    for (const id of ["raceType","rating","share","deadline","quoteAmount","sumInsured","premium","tax"]) setText(id, "—");
    $("requestQuote").disabled = true; $("declarationControls").hidden = true;
    setText("runHeadline", "Waiting for a task"); setText("runDescription", "Select a horse to see its saved status."); $("progressRing").className = "progress-ring"; return;
  }
  const race=task.race || {}, quote=task.quote || {}, status=task.status || "unknown";
  setText("horseName", task.horse || "Unnamed horse");
  setText("raceName", [race.race_name, race.race_status ? title(race.race_status) : ""].filter(Boolean).join("  ·  "));
  setText("taskShort", task.horse_id || "UNKNOWN HORSE"); setText("taskIdLabel", task._id);
  setText("taskState", title(status).toUpperCase());
  const done = ["covered","quoted","not_eligible","not_confirmed","referred","blocked","quote_unverified"].includes(status);
  setText("runHeadline", `Task: ${title(status)}`);
  setText("runDescription", done ? "Latest status saved by the backend." : workerRunning ? "Shared worker running · waiting for updates." : "Shared worker stopped · saved state retained.");
  $("progressRing").className = "progress-ring" + (["covered","quoted"].includes(status) ? " complete" : done ? " stopped" : workerRunning ? " active" : "");
  $("taskState").className = "status-chip" + (["blocked","referred","not_eligible","not_confirmed","quote_unverified"].includes(status) ? " issue" : "");
  setText("raceType", race.type || "—"); setText("rating", task.runner?.ofr || "—");
  setText("share", task.ownership_share == null ? "—" : `${Math.round(Number(task.ownership_share)*100)}%`);
  setText("deadline", clock(task.deadline));
  setText("quoteLabel", quote.offerable ? (status === "covered" ? "SAVED PRICE · COVERED" : "SAVED QUOTE · COVER NOT CONFIRMED") : "WAITING FOR ASSESSMENT");
  setText("quoteAmount", quote.offerable ? money(quote.total, quote.currency) : "—");
  $("coverageNote").hidden = !(quote.offerable && status !== "covered");
  setText("coverageNote", "A quote is saved; coverage has not been confirmed.");
  const factors = (quote.risk_factors || []).map(f => f.evidence).filter(Boolean);
  const quoteSummary = quote.offerable && status !== "covered" ? (factors.length ? `Recorded price factors: ${factors.join(" · ")}.` : "No additional price factors recorded.") : quote.explanation;
  setText("quoteText", quoteSummary || (quote.reason ? quote.reason : "The quote appears here when the agent finishes pricing this horse."));
  setText("sumInsured", money(quote.sum_insured,quote.currency)); setText("premium", money(quote.premium,quote.currency)); setText("tax", money(quote.tax,quote.currency));
  $("declarationControls").hidden = !(status === "awaiting_owner" && !task.confirmation);
  setText("inputText", task.confirmation ? `Declaration recorded: ${task.confirmation.fit ? "fit to run" : "not fit to run"}.` : status === "awaiting_owner" ? "The owner’s fit-to-run declaration is required for this task." : "When a task is waiting for a fit-to-run declaration, the owner can record it here.");
  const canRequestNew = quoteRoute && String(task._id).startsWith("task_") && !task.quote_only;
  const canRunExisting = !quote.offerable && ["new", "priced", "awaiting_owner"].includes(status);
  $("requestQuote").disabled = !canRequestNew && !canRunExisting;
  $("requestQuote").innerHTML = canRequestNew ? 'Request a quote <span>↗</span>' : canRunExisting ? 'Get quote from this task <span>↗</span>' : quote.offerable ? 'Quote saved <span>✓</span>' : 'Quote unavailable <span>↗</span>';
  setText("quoteCapability", canRequestNew ? "Creates a quote-only request for this fictional horse." : canRunExisting ? "Starts the shared worker, which processes all open tasks." : quote.offerable ? "This task already has a saved quote." : "This task cannot currently produce a quote.");
}

function renderActivity(events, task) {
  let list = Array.isArray(events) ? events : [];
  if (!list.length && Array.isArray(task?.investigation?.trail)) {
    list = task.investigation.trail.map(item => ({kind:"investigate", detail:item, at:task.updated_at}));
  }
  const latestDecision = [...list].reverse().find(event => event.detail?.why || event.detail?.reason);
  setText("decisionLabel", task?.investigation?.source === "template" ? "LATEST SAVED STEP · FALLBACK" : "LATEST SAVED DECISION");
  setText("decisionText", latestDecision ? (latestDecision.detail.why || latestDecision.detail.reason) : "No decision recorded yet.");
  setText("eventCount", `${list.length} EVENT${list.length === 1 ? "" : "S"}`);
  if (!list.length) { setHtml("activity", `<div class="empty">No saved activity for this horse yet.<br>The log updates when the backend records a step.</div>`); return; }
  const html = list.map(event => {
    const detail=event.detail || {};
    const name=event.kind === "investigate" ? title(detail.tool || "investigation") : title(event.kind);
    const reason=detail.why || detail.reason || detail.description || detail.move || "";
    const evidence=detail.finding || (Object.keys(detail).length ? JSON.stringify(detail,null,2) : "");
    return `<article class="activity-item"><div class="activity-icon">${event.kind === "investigate" ? "⌕" : "✳"}</div><div class="activity-main"><strong>${esc(name)}</strong>${reason ? `<p>${esc(reason)}</p>` : ""}${evidence ? `<details><summary>View saved evidence</summary><pre>${esc(evidence)}</pre></details>` : ""}</div><time>${esc(clock(event.at))}</time></article>`;
  }).join("");
  setHtml("activity",html);
}

function renderEvidence(actions) {
  if (!actions.length) { setHtml("evidence",`<div class="empty-light">No outside action records for this task yet.</div>`); return; }
  setHtml("evidence",actions.map(action => `<details class="evidence-detail"><summary class="evidence-row"><span>${esc(title(action.step))}<br><small>${esc(action.system || "")}</small></span><strong class="${action.state === "verified" ? "" : "pending"}">${esc(title(action.state).toUpperCase())} ↓</strong></summary><pre>${esc(JSON.stringify({operation_id:action.operation_id, evidence:action.evidence},null,2))}</pre></details>`).join(""));
}
function renderMedical(data, task) {
  if (!medicalRoute) {
    const finding = task?.investigation?.trail?.find(item => item.tool === "get_health_records")?.finding;
    setHtml("medicalRecords", finding ? `<div class="medical-record"><span class="medical-label">SAVED HEALTH CHECK · DEMO DATA</span><pre>${esc(typeof finding === "string" ? finding : JSON.stringify(finding, null, 2))}</pre><p>Image viewing is not available on the deployed backend.</p></div>` : `<div class="empty-light">No saved health check for this task. Image viewing is not available on the deployed backend.</div>`);return;
  }
  const records=data?.medical_xrays || [];
  if (!records.length) {setHtml("medicalRecords",`<div class="empty-light">No X-ray record on file. This is not a health clearance.</div>`);return;}
  setHtml("medicalRecords",records.map(record=>{
    const path=typeof record.image_url === "string" && record.image_url.startsWith("/") && !record.image_url.startsWith("//") ? record.image_url : null;
    return `<article class="medical-record"><span class="medical-label">${record.synthetic ? "SYNTHETIC · NOT DIAGNOSTIC" : "SOURCE RECORD"}</span>${path ? `<img class="medical-image" src="${esc(API+path)}" alt="${record.synthetic ? "Synthetic" : "Recorded"} horse medical image">` : ""}<p>${esc(record.report || "No report provided.")}</p></article>`;
  }).join(""));
}

async function refresh() {
  if (busy) return;
  busy=true;
  try {
    const [health, allTasks] = await Promise.all([request("/health"),request("/tasks")]);
    tasks = Array.isArray(allTasks) ? allTasks : [];
    populateHorses();
    const task=tasks.find(t=>t._id === selectedId), generation=selectedGeneration;
    workerRunning = !!health.agent_running;
    setText("workerState",workerRunning ? "Agent running" : "Agent stopped");
    $("workerDot").className = "worker-dot" + (health.agent_running ? " running" : "");
    renderTask(task);
    if (task) {
      const [events,actions,medical] = await Promise.all([request(`/timeline?task_id=${encodeURIComponent(task._id)}`),request(`/tasks/${encodeURIComponent(task._id)}/actions`),medicalRoute ? request(`/horse-profiles/${encodeURIComponent(task.horse_id)}/medical-xrays`).catch(()=>null) : null]);
      if (generation === selectedGeneration && task._id === selectedId) {renderActivity(events,task);renderEvidence(actions);if(medicalRoute && !medical) setHtml("medicalRecords",`<div class="empty-light">Medical records could not be loaded.</div>`);else renderMedical(medical,task);}
    } else {renderActivity([],null);renderEvidence([]);renderMedical(null,null);}
  } catch (error) {
    setText("workerState","Connection issue"); $("workerDot").className="worker-dot error";
    notice(`Could not read Render: ${error.message}`,true);
  } finally {busy=false;}
}


async function control(path, verb) {
  if (!confirm(`${verb} the shared agent? This affects all horses in the Render environment.`)) return;
  try {await request(path,"POST",{});notice(`${verb} request sent to the shared worker.`);await refresh();}
  catch(error){notice(error.message,true);}
}
$("startAgent").addEventListener("click",()=>control("/control/start","Start"));
$("killAgent").addEventListener("click",()=>control("/control/kill","Kill"));
async function declare(fit) {
  const task=tasks.find(t=>t._id===selectedId);
  if (!task || !confirm(`Record that the owner declared ${task.horse} ${fit ? "fit" : "not fit"} to run?`)) return;
  try {await request(`/tasks/${encodeURIComponent(task._id)}/confirm`,"POST",{fit,given_by:"owner (demo UI)"});notice("Declaration saved to the selected task.");await refresh();}
  catch(error){notice(error.message,true);}
}
$("answerYes").addEventListener("click",()=>declare(true));
$("answerNo").addEventListener("click",()=>declare(false));
$("requestQuote").addEventListener("click",async()=>{
  const task=tasks.find(t=>t._id===selectedId); if (!task) return;
  if (!quoteRoute) {
    if (!confirm("Start the shared agent to price this existing task? The worker will process all open tasks, not only this horse.")) return;
    try {await request("/control/start","POST",{});notice("Shared worker started. This horse’s saved quote will appear when available.");await refresh();}
    catch(error){notice(error.message,true);}
    return;
  }
  try {const result=await request("/demo/quote-requests","POST",{source_task_id:task._id});selectedId=result.task_id;selectedGeneration++;notice("Quote request created. Start the shared worker if it is stopped.");await refresh();}
  catch(error){notice(error.message,true);}
});
function showTask() {
  const hasTask = Boolean(selectedId);
  $("intake").hidden = hasTask;
  for (const id of ["missionBar", "notice", "missionContent", "sideColumn"]) $(id).hidden = !hasTask;
}
request("/openapi.json").then(doc=>{
  quoteRoute=!!doc.paths?.["/demo/quote-requests"]?.post;
  medicalRoute=!!doc.paths?.["/horse-profiles/{horse_id}/medical-xrays"]?.get;
  intakeRoute=!!doc.paths?.["/intake/requests"]?.post;
  $("intakeSubmit").disabled = !intakeRoute;
  setText("intakeStatus", intakeRoute ? "Submit to start the shared agent." : "New requests are not available on Render yet. Your information will not be sent.");
}).catch(()=>{setText("intakeStatus", "Could not check the Render connection. Please try again later.");});
$("intakeForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (!intakeRoute) return;
  $("intakeSubmit").disabled = true;
  const form = new FormData(event.target);
  try {
    const result = await request("/intake/requests", "POST", Object.fromEntries(form));
    selectedId = result.task_id; selectedGeneration++;
    history.replaceState(null, "", `?task_id=${encodeURIComponent(selectedId)}`);
    showTask();
    try { await request("/control/start", "POST", {}); notice("Your request is saved. The shared agent is running."); }
    catch(error) { notice(`Request saved; start the agent using the button. ${error.message}`, true); }
    await refresh();
  } catch(error) {setText("intakeStatus", error.message); $("intakeSubmit").disabled = false;}
});
showTask(); refresh(); setInterval(refresh,3000);
