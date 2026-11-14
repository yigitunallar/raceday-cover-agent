const API = "https://raceday-cover-agent.onrender.com";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let tasks = [];
function visibleTasks(items) { return items.filter(task => task.hidden_from_dashboard !== true); }
const fmtMoney = (n, cur) => { try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur || "EUR" }).format(Number(n)); } catch (_) { return `${cur || ""} ${Number(n).toFixed(2)}`; } };
function render() {
  const query = $("search").value.toLowerCase();
  const filtered = tasks.filter(t => [t.horse,t.status,t.race?.race_name,t._id].some(x => String(x || "").toLowerCase().includes(query)));
  $("rows").innerHTML = filtered.map(t => {
    const q = t.quote || {};
    const price = q.offerable && Number.isFinite(Number(q.total)) ? esc(fmtMoney(q.total, q.currency)) : "—";
    return `<tr><td>${esc(t.horse || "Unnamed")}${t.quote_only ? ' <span class="status-chip">Quote request</span>' : ""}<small>${esc(t.horse_id || "")}</small></td><td>${esc(t.race?.race_name || "—")}</td><td><span class="status-chip">${esc((t.status || "unknown").replace(/_/g," ").toUpperCase())}</span></td><td>${price}</td><td><a href="mission.html?task_id=${encodeURIComponent(t._id)}">View task ↗</a></td></tr>`;
  }).join("") || '<tr><td colspan="5">No matching tasks.</td></tr>';
}
async function request(path, method="GET") {
  const response = await fetch(API + path, {method});
  if (!response.ok) throw new Error(`Render returned ${response.status}`);
  return response.json();
}
async function refresh() {
  try {
    const [health, data] = await Promise.all([request("/health"), request("/tasks")]);
    if (!Array.isArray(data)) throw new Error("Unexpected task response");
    tasks = visibleTasks(data);
    $("connection").textContent = "Connected to Render";
    $("workerState").textContent = health.agent_running ? "Agent running" : "Agent stopped";
    $("workerDot").className = "worker-dot" + (health.agent_running ? " running" : "");
    const real = tasks.filter(t => !t.quote_only);
    $("total").textContent = real.length;
    $("active").textContent = real.filter(t => ["new","priced","investigating","running","awaiting_owner"].includes(t.status)).length;
    $("quotes").textContent = real.filter(t => t.quote?.offerable).length;
    $("covered").textContent = real.filter(t => t.status === "covered").length;
    $("updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    render();
  } catch (error) { $("connection").textContent = "Connection issue"; $("notice").textContent = error.message; }
}
$("search").addEventListener("input",render);
for (const [id, path, verb] of [["startAgent","/control/start","Start"],["killAgent","/control/kill","Kill"]]) $(id).addEventListener("click", async () => {
  if (!confirm(`${verb} the shared agent? This affects all tasks.`)) return;
  try { await request(path,"POST"); $("notice").textContent = `${verb} request sent to Render.`; await refresh(); }
  catch (error) { $("notice").textContent = error.message; }
});
refresh(); setInterval(refresh, 3000);
