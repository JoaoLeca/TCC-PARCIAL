// Executa os cenários do roteiro de aceitação (eval/roteiro_automacao.md) no site, canal web.
//
// Pré-requisitos: Node.js 22.5 ou superior (node:sqlite), Google Chrome e uma instalação de AVALIAÇÃO,
// com banco separado e criado do zero, API em http://127.0.0.1:8765 e site em http://127.0.0.1:8766,
// ambos apontando para o mesmo DATABASE_PATH, e uma conta de dono criada nesse banco.
// O script cria reservas, bloqueios e chamados fictícios e altera temporariamente o preço do corte:
// nunca o execute contra o banco real da barbearia.
//
// Uso: AVAL_STAFF_PASSWORD=... node --no-warnings eval/aceitacao_site.mjs <pasta-de-saida> <banco-de-avaliacao.db>
// Variáveis opcionais: AVAL_STAFF_USER (padrão: dono), CHROME_PATH.
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

const OUT = process.argv[2];
const DB_PATH = process.argv[3];
const API = "http://127.0.0.1:8765";
const UI = "http://127.0.0.1:8766";
mkdirSync(`${OUT}/evidencias`, { recursive: true });

// ------------------------------------------------------------------ banco
const db = new DatabaseSync(DB_PATH);
const one = (sql, ...params) => db.prepare(sql).get(...params);
const all = (sql, ...params) => db.prepare(sql).all(...params);
const counts = () => ({
  reservas: one("SELECT count(*) n FROM appointments").n,
  confirmadas: one("SELECT count(*) n FROM appointments WHERE status='confirmed'").n,
  chamados: one("SELECT count(*) n FROM support_tickets").n,
  ev_reserva: one("SELECT count(*) n FROM interaction_logs WHERE outcome='booking_confirmed'").n,
  ev_escalado: one("SELECT count(*) n FROM interaction_logs WHERE outcome='escalated'").n,
  ev_oferta: one("SELECT count(*) n FROM interaction_logs WHERE outcome='offered'").n,
  ev_indisponivel: one("SELECT count(*) n FROM interaction_logs WHERE outcome='unavailable'").n,
  recuperacoes: one("SELECT count(*) n FROM customer_recovery").n,
});

// ------------------------------------------------------------------ resultados
const results = [];
let current;
function scenario(id, title, expected) {
  current = { id, title, expected, steps: [], checks: [], evidence: [], counts: {}, error: null };
  results.push(current);
  console.log(`\n=== ${id} – ${title}`);
}
const step = (text) => { current.steps.push(text); console.log("  •", text); };
function check(ok, text) {
  current.checks.push({ ok: !!ok, text });
  console.log(`  ${ok ? "OK " : "FALHOU"} ${text}`);
  return ok;
}
const snapshot = (label) => { current.counts[label] = counts(); return current.counts[label]; };

// ------------------------------------------------------------------ API
async function api(method, path, body, token) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  return { status: response.status, data };
}
const chatApi = (sessionId, messages) => api("POST", "/chat", { session_id: sessionId, messages });
const availability = async (serviceId, day, barberId) => (await api("GET",
  `/availability?service_id=${serviceId}&appointment_date=${day}${barberId ? `&barber_id=${barberId}` : ""}`)).data;

// ------------------------------------------------------------------ Chrome (CDP)
const PORT = 9334;
const chrome = spawn(process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${OUT}/chrome-profile`,
  "--hide-scrollbars", "--window-size=1200,1400", "--lang=pt-BR", "about:blank",
], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let targets = [];
for (let i = 0; i < 60 && !targets.length; i++) {
  try { targets = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).filter((t) => t.type === "page"); } catch {}
  if (!targets.length) await sleep(300);
}
const ws = new WebSocket(targets[0].webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let seq = 0; const pending = new Map();
ws.addEventListener("message", (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const n = ++seq; pending.set(n, (m) => (m.error ? reject(new Error(`${method}: ${m.error.message}`)) : resolve(m.result)));
  ws.send(JSON.stringify({ id: n, method, params }));
});
const evaluate = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result.value;
async function waitFor(expr, label, timeout = 25000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await evaluate(expr)) return; await sleep(300); }
  throw new Error("Tempo esgotado esperando: " + label);
}
const waitText = (text, timeout) => waitFor(`document.body && document.body.innerText.includes(${JSON.stringify(text)})`, text, timeout);
const idle = async () => {
  await sleep(700);
  await waitFor(`!document.querySelector('[data-testid="stStatusWidget"] [data-testid="stStatusWidgetRunningIcon"], [data-testid="stSpinner"]')`, "fim da execução", 30000);
  await sleep(500);
};
async function click(selector, text, { contains = false } = {}) {
  const ok = await evaluate(`(() => {
    const els = [...document.querySelectorAll(${JSON.stringify(selector)})];
    const el = els.find(e => ${contains ? `e.innerText.includes(${JSON.stringify(text)})` : `e.innerText.trim() === ${JSON.stringify(text)}`} && !e.disabled);
    if (!el) return false; el.scrollIntoView({block: "center"}); el.click(); return true; })()`);
  if (!ok) throw new Error(`Não encontrei ${selector} com texto ${text}`);
  await idle();
}
const tab = (name) => click('[role="tab"]', name);
async function type(selector, text, { enter = true } = {}) {
  const ok = await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return false; el.scrollIntoView({block: "center"}); el.focus(); return true; })()`);
  if (!ok) throw new Error("Não encontrei " + selector);
  await send("Input.insertText", { text });
  if (enter) {
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  }
  await idle();
}
const textOf = (selector) => evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); return el ? el.innerText : ""; })()`);
async function newSession() {
  await send("Page.navigate", { url: UI + "/" });
  await waitText("Meus horários");
  await waitFor(`!!document.querySelector('.st-key-booking_times, .st-key-booking_calendar')`, "agenda carregada", 30000);
  await idle();
}
const USER_COUNT = `[...document.querySelectorAll('.st-key-tab_chat_history [data-testid="stChatMessage"]')].filter(m => m.innerText.trim().startsWith("Você")).length`;
async function chat(message) {
  const before = await evaluate(USER_COUNT);
  await type('.st-key-tab_chat_input textarea', message);
  await waitFor(`(() => { const ms = [...document.querySelectorAll('.st-key-tab_chat_history [data-testid="stChatMessage"]')];
    const last = ms[ms.length - 1]; return ${USER_COUNT} === ${before + 1} && last && last.innerText.trim().startsWith("Assistente virtual")
      && !document.querySelector('[data-testid="stSpinner"]'); })()`, "resposta do chat", 30000);
  await idle();
  const answer = await evaluate(`(() => { const m = [...document.querySelectorAll('.st-key-tab_chat_history [data-testid="stChatMessage"]')].pop(); return m ? m.innerText.replace(/^Assistente virtual\\s*/, "") : ""; })()`);
  step(`Cliente: “${message}” → Assistente: “${answer.replace(/\s+/g, " ").slice(0, 220)}”`);
  return answer;
}
async function shot(name, maxHeight = 2600) {
  await evaluate(`(() => { let st = document.getElementById('tcc-hide'); if (!st) { st = document.createElement('style'); st.id = 'tcc-hide';
    st.textContent = 'header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none !important; }';
    document.head.appendChild(st); } return true; })()`);
  await sleep(600);
  const h = await evaluate(`Math.min(Math.max(document.querySelector('[data-testid="stMain"]')?.scrollHeight || 0, document.body.scrollHeight), ${maxHeight})`);
  await send("Emulation.setDeviceMetricsOverride", { width: 1100, height: Math.ceil(h), deviceScaleFactor: 1.5, mobile: false });
  await sleep(1200);
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  const file = `evidencias/${name}.png`;
  writeFileSync(`${OUT}/${file}`, Buffer.from(data, "base64"));
  await send("Emulation.setDeviceMetricsOverride", { width: 1100, height: 1400, deviceScaleFactor: 1.5, mobile: false });
  current.evidence.push(file);
}

// ------------------------------------------------------------------ datas
const fmt = (d) => `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const today = new Date(new Date().toLocaleString("en-US", { timeZone: "America/Sao_Paulo" }));
const plus = (n) => { const d = new Date(today); d.setDate(d.getDate() + n); return d; };
const window7 = Array.from({ length: 7 }, (_, i) => plus(i + 1));
const findDay = (weekday) => window7.find((d) => d.getDay() === weekday); // 0=domingo
const D_TUE = findDay(2), D_WED = findDay(3), D_SAT = findDay(6), D_FIRST = window7.find((d) => d.getDay() !== 0);
const D_SECOND = window7.filter((d) => d.getDay() !== 0)[1];
const minutes = (s) => { const d = new Date(s); return d.getHours() * 60 + d.getMinutes(); };
const hhmm = (s) => new Date(s).toTimeString().slice(0, 5);

// ------------------------------------------------------------------ execução
await send("Page.enable"); await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1100, height: 1400, deviceScaleFactor: 1.5, mobile: false });
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
if (!process.env.AVAL_STAFF_PASSWORD) throw new Error("Defina AVAL_STAFF_PASSWORD com a senha da conta de dono do banco de avaliação.");
const staff = (await api("POST", "/staff/login", { username: process.env.AVAL_STAFF_USER || "dono", password: process.env.AVAL_STAFF_PASSWORD })).data.token;
const barbers = (await api("GET", "/barbers")).data;
const barberId = (name) => barbers.find((b) => b.name === name).id;
const meta = { inicio: new Date().toISOString(), datas: { primeira: iso(D_FIRST), segunda: iso(D_SECOND), terca: iso(D_TUE), quarta: iso(D_WED), sabado: iso(D_SAT) } };

async function run(id, title, expected, body) {
  scenario(id, title, expected);
  try { await body(); } catch (error) {
    current.error = String(error.message || error);
    console.log("  ERRO", current.error);
    try { await shot(`${id}_erro`); } catch {}
  }
}

// A05 – IA desativada ------------------------------------------------------
await run("A05", "IA desativada", "Consultas locais respondem; pergunta dependente da IA informa indisponibilidade sem resposta fabricada e registra unavailable.", async () => {
  const health = (await api("GET", "/health")).data;
  check(health.gpt_configured === false, "API sem chave da OpenAI (gpt_configured = false)");
  await newSession(); await tab("Conversar");
  const before = snapshot("antes");
  const pay = await chat("Quais formas de pagamento vocês aceitam?");
  check(/Pix/i.test(pay) && /cart/i.test(pay) && /dinheiro/i.test(pay), "Formas de pagamento vêm do cadastro (dinheiro, Pix e cartão)");
  const price = await chat("Quanto custa o corte?");
  check(price.includes("R$ 50,00"), "Preço do corte (R$ 50,00) vem do catálogo");
  const slots = await chat(`Tem horário para corte dia ${fmt(D_TUE)}?`);
  check(/Encontrei estas opções/.test(slots), "Consulta de vagas responde com opções reais sem IA");
  const weather = await chat("Qual a previsão do tempo para amanhã?");
  check(/indispon/i.test(weather), "Na mesma conversa, logo após a consulta de vagas, pergunta fora das respostas locais informa que a IA está indisponível");
  const mid = snapshot("apos_conversa");
  check(mid.ev_indisponivel === before.ev_indisponivel + 1, "Telemetria registra um evento unavailable para essa pergunta");
  await shot("A05_contexto_anterior");
  // Mesma pergunta em uma conversa nova, sem consulta de vagas antes.
  await newSession(); await tab("Conversar");
  const fresh = await chat("Qual a previsão do tempo para amanhã?");
  check(/indispon/i.test(fresh), "Em conversa nova, a pergunta fora do escopo informa que a IA está indisponível, sem resposta fabricada");
  const after = snapshot("depois");
  check(after.ev_indisponivel === mid.ev_indisponivel + 1, "Telemetria registra o evento unavailable da conversa nova");
  check(after.reservas === before.reservas, "Nenhuma reserva criada");
  await shot("A05_ia_desativada");
});

// A06 – Restrições de horário sem IA (rota /chat usada pelo site) -----------
await run("A06", "Restrições de horário sem IA", "Cada expressão segue a semântica definida, considerando duração, expediente e bloqueios; nenhuma vaga inventada.", async () => {
  const day = iso(D_TUE), dd = fmt(D_TUE);
  const real = new Set((await availability(1, day)).map((s) => `${s.starts_at}|${s.barber_id}`));
  const before = snapshot("antes");
  const cases = [
    ["depois das 15h", (a) => a > 900],
    ["a partir das 15h", (a) => a >= 900],
    ["antes das 15h", (a, b) => b <= 900],
    ["entre 10h e 12h", (a, b) => a >= 600 && b <= 720],
    ["às 15h", (a) => a === 900],
  ];
  for (const [expr, rule] of cases) {
    const msg = `Quero corte dia ${dd} ${expr}`;
    const r = (await chatApi(`aceitacao-a06-${expr}`, [{ role: "user", content: msg }])).data;
    const slots = r.booking_options?.slots || [];
    const ok = slots.length > 0 && slots.every((s) => rule(minutes(s.starts_at), minutes(s.ends_at))
      && real.has(`${s.starts_at}|${s.barber_id}`) && s.starts_at.startsWith(day));
    step(`“${msg}” → ${slots.map((s) => `${hhmm(s.starts_at)}–${hhmm(s.ends_at)} ${s.barber}`).join("; ") || r.answer.slice(0, 120)}`);
    check(ok, `“${expr}”: opções reais na data pedida e dentro da restrição`);
  }
  // Serviço mais longo: pintura (120 min) antes das 15h deve terminar até 15h e não atravessar o almoço.
  const dayP = iso(D_TUE);
  const realP = new Set((await availability(4, dayP)).map((s) => `${s.starts_at}|${s.barber_id}`));
  const rp = (await chatApi("aceitacao-a06-pintura", [{ role: "user", content: `Quero pintar o cabelo dia ${dd} antes das 15h` }])).data;
  const sp = rp.booking_options?.slots || [];
  step(`“Quero pintar o cabelo dia ${dd} antes das 15h” → ${sp.map((s) => `${hhmm(s.starts_at)}–${hhmm(s.ends_at)} ${s.barber}`).join("; ")}`);
  check(sp.length > 0 && sp.every((s) => minutes(s.ends_at) <= 720 && realP.has(`${s.starts_at}|${s.barber_id}`)),
    "Pintura (120 min) antes das 15h só oferece horários que terminam até 12h, sem atravessar o almoço");
  // Último horário de sábado, com o fim do expediente bloqueado para os barbeiros que trabalham no sábado.
  const sat = iso(D_SAT);
  const lastFree = async () => (await chatApi(`aceitacao-a06-sabado-${Date.now()}`, [{ role: "user", content: "Quero corte no último horário de sábado" }])).data;
  const r1 = await lastFree();
  const s1 = r1.booking_options?.slots || [];
  step(`“Quero corte no último horário de sábado” → ${s1.map((s) => `${s.starts_at.slice(8, 10)}/${s.starts_at.slice(5, 7)} ${hhmm(s.starts_at)} ${s.barber}`).join("; ")}`);
  const starts1 = s1.map((s) => minutes(s.starts_at));
  const latestReal = Math.max(...(await availability(1, sat)).map((s) => minutes(s.starts_at)));
  step(`Maior início real no sábado, segundo /availability: ${String(Math.floor(latestReal / 60)).padStart(2, "0")}:${String(latestReal % 60).padStart(2, "0")}`);
  check(s1.length > 0 && s1.every((s) => s.starts_at.startsWith(sat)) && starts1.every((v, i) => i === 0 || v < starts1[i - 1]) && starts1[0] === latestReal,
    "Último horário de sábado: opções do sábado, começando pelo maior início real e em ordem decrescente");
  const blocks = [];
  for (const b of barbers) {
    const res = await api("POST", "/staff/blocks", { barber_id: b.id, starts_at: `${sat}T18:00:00-03:00`, ends_at: `${sat}T20:00:00-03:00`, reason: "Ensaio A06" }, staff);
    if (res.status === 201) blocks.push(res.data.id);
  }
  step(`Bloqueio das 18h às 20h de sábado para todos os barbeiros (${blocks.length} bloqueios criados pela API da equipe)`);
  const r2 = await lastFree();
  const s2 = r2.booking_options?.slots || [];
  step(`Repetição com o bloqueio → ${s2.map((s) => `${hhmm(s.starts_at)}–${hhmm(s.ends_at)} ${s.barber}`).join("; ")}`);
  check(s2.length > 0 && s2.every((s) => minutes(s.ends_at) <= 18 * 60 && s.starts_at.startsWith(sat)), "Com o bloqueio, o último horário termina até 18h");
  for (const id of blocks) await api("DELETE", `/staff/blocks/${id}`, null, staff);
  const after = snapshot("depois");
  check(after.reservas === before.reservas, "Nenhuma reserva criada pelas consultas");
});

// A07 – Mudança de contexto temporal --------------------------------------
await run("A07", "Mudança de contexto temporal", "A preferência mais recente substitui a anterior; trocar só o profissional mantém a restrição; horários inválidos pedem esclarecimento.", async () => {
  const dd = fmt(D_TUE);
  async function conversation(name, messages) {
    const history = [];
    let last;
    for (const content of messages) {
      history.push({ role: "user", content });
      last = (await chatApi(`aceitacao-a07-${name}`, history)).data;
      history.push({ role: "assistant", content: last.answer });
    }
    const slots = last.booking_options?.slots || [];
    step(`${messages.map((m) => `“${m}”`).join(" → ")} → ${slots.map((s) => `${hhmm(s.starts_at)} ${s.barber}`).join("; ") || last.answer.slice(0, 110)}`);
    return { slots, answer: last.answer };
  }
  const before = snapshot("antes");
  let r = await conversation("manha-depois", [`Quero corte dia ${dd} pela manhã`, "depois das 15h"]);
  check(r.slots.length && r.slots.every((s) => minutes(s.starts_at) > 900), "“pela manhã” seguido de “depois das 15h”: vale a restrição mais recente");
  r = await conversation("depois-manha", [`Quero corte dia ${dd} depois das 15h`, "pela manhã"]);
  check(r.slots.length && r.slots.every((s) => minutes(s.starts_at) < 720), "Ordem inversa: vale o período da manhã");
  r = await conversation("as15-tarde", [`Quero corte dia ${dd} às 15h`, "à tarde"]);
  check(r.slots.length && r.slots.every((s) => minutes(s.starts_at) >= 720 && minutes(s.starts_at) < 1080) && r.slots.some((s) => minutes(s.starts_at) !== 900),
    "“às 15h” trocado por “à tarde”: opções da tarde, não apenas 15h");
  r = await conversation("as15-diego", [`Quero corte dia ${dd} às 15h`, "com o Diego"]);
  check(r.slots.length && r.slots.every((s) => minutes(s.starts_at) === 900 && s.barber === "Diego"), "Trocar só o profissional mantém “às 15h”");
  for (const expr of ["às 25h", "entre 16h e 10h", "por volta das 15h"]) {
    r = await conversation(`invalido-${expr}`, [`Quero corte dia ${dd} ${expr}`]);
    check(!r.slots.length && /interpretar/i.test(r.answer), `“${expr}”: pede esclarecimento sem oferecer vagas`);
  }
  const after = snapshot("depois");
  check(after.reservas === before.reservas, "Nenhuma reserva criada pelas consultas");
});

// A02 – Sem vagas e alternativas -------------------------------------------
await run("A02", "Sem vagas e alternativas", "Não promete nem grava horário ocupado; oferece outra data ou profissional existente; com a janela cheia, informa a indisponibilidade sem inventar vaga.", async () => {
  const day = iso(D_WED), dd = fmt(D_WED);
  const rafael = barberId("Rafael");
  const before = snapshot("antes");
  const block = await api("POST", "/staff/blocks", { barber_id: rafael, starts_at: `${day}T00:00:00-03:00`, ends_at: `${day}T23:59:00-03:00`, reason: "Ensaio A02" }, staff);
  check(block.status === 201, `Dia ${dd} inteiro bloqueado para o Rafael pela API da equipe`);
  const rafaelSlots = await availability(1, day, rafael);
  check(rafaelSlots.length === 0, "A disponibilidade do Rafael na data ficou vazia");
  await newSession(); await tab("Conversar");
  const answer = await chat(`Quero corte com o Rafael dia ${dd} à tarde`);
  check(/Não encontrei a combinação pedida/.test(answer), "O assistente informa que a combinação pedida não tem vagas");
  const suggestions = await evaluate(`[...document.querySelectorAll('.st-key-tab_chat_history button')].map(b => b.innerText.trim()).filter(t => /\\d{2}\\/\\d{2} · \\d{2}:\\d{2} · /.test(t))`);
  step(`Alternativas exibidas: ${suggestions.join("; ")}`);
  const altOk = [];
  for (const label of suggestions) {
    const [, d, m, h, barber] = label.match(/(\d{2})\/(\d{2}) · (\d{2}:\d{2}) · (.+)/);
    const date = `${D_WED.getFullYear()}-${m}-${d}`;
    const slots = await availability(1, date, barberId(barber));
    altOk.push(barber !== "Rafael" || date !== day ? slots.some((s) => hhmm(s.starts_at) === h) : false);
  }
  check(suggestions.length > 0 && altOk.every(Boolean), "Cada alternativa existe de fato na disponibilidade (outro profissional ou outro dia)");
  await shot("A02_alternativas");
  check(snapshot("apos_alternativas").reservas === before.reservas, "Consultar alternativas não cria reserva");
  await api("DELETE", `/staff/blocks/${block.data.id}`, null, staff);
  // Janela inteira sem vagas
  const ids = [];
  const start = iso(window7[0]), endDay = plus(8);
  for (const b of barbers) {
    const res = await api("POST", "/staff/blocks", { barber_id: b.id, starts_at: `${start}T00:00:00-03:00`, ends_at: `${iso(endDay)}T00:00:00-03:00`, reason: "Ensaio A02 janela cheia" }, staff);
    if (res.status === 201) ids.push(res.data.id);
  }
  check(ids.length === barbers.length, `Janela de ${fmt(window7[0])} a ${fmt(window7[6])} bloqueada para os ${barbers.length} barbeiros`);
  const full = await chat("Quero corte amanhã");
  check(/Não encontrei vagas/.test(full) && !/\d{2}\/\d{2} às/.test(full), "Com a janela cheia, informa a indisponibilidade sem inventar vaga");
  await shot("A02_janela_cheia");
  await tab("Agendar");
  await click("button", "Atualizar disponibilidade").catch(() => {});
  const agenda = await textOf('[data-testid="stMain"]');
  check(!(await evaluate(`!!document.querySelector('.st-key-booking_times button')`)), "A aba Agendar não exibe horários com a janela bloqueada");
  step(`Aba Agendar: ${(agenda.match(/Não há[^\n]*/) || ["(sem mensagem específica)"])[0]}`);
  for (const id of ids) await api("DELETE", `/staff/blocks/${id}`, null, staff);
  const after = snapshot("depois");
  check(after.reservas === before.reservas, "Nenhuma reserva criada no cenário");
});

// A03 – Solicitação de retorno da equipe ------------------------------------
await run("A03", "Solicitação de retorno da equipe", "A frase só oferece ajuda; abrir ou abandonar o formulário não cria protocolo; campos vazios impedem o envio; só o envio válido cria o protocolo e registra escalated.", async () => {
  await newSession(); await tab("Conversar");
  const before = snapshot("antes");
  const answer = await chat("Quero falar com uma pessoa da equipe sobre uma reclamação");
  check(/Falar com a equipe/.test(answer), "O assistente orienta a usar Falar com a equipe");
  let now = snapshot("apos_frase");
  check(now.chamados === before.chamados && now.ev_escalado === before.ev_escalado && now.ev_oferta === before.ev_oferta + 1,
    "A frase registra apenas a oferta (offered), sem chamado nem escalated");
  await click(".st-key-tab_human_request button", "Falar com a equipe");
  await click("button", "Voltar");
  now = snapshot("apos_abandonar");
  check(now.chamados === before.chamados, "Abrir e abandonar o formulário não cria chamado");
  await click(".st-key-tab_human_request button", "Falar com a equipe");
  await click("button", "Solicitar atendimento");
  const err = await textOf('[data-testid="stMain"]');
  check(/Preencha seu nome e um telefone válido/.test(err), "Envio sem contato é recusado com orientação");
  check(snapshot("apos_vazio").chamados === before.chamados, "Envio inválido não cria chamado");
  await type('.st-key-tab_support_name input', "Cliente Aceitação A03", { enter: false });
  await type('.st-key-tab_support_phone input', "16900000303", { enter: false });
  await click("button", "Solicitar atendimento");
  await waitText("enviado à equipe");
  const msg = await textOf(".st-key-tab_chat_history");
  check(/O retorno será pelo telefone informado/.test(msg) && /assistente virtual/i.test(msg), "A interface informa o retorno por telefone e que o assistente continua virtual");
  const after = snapshot("depois");
  check(after.chamados === before.chamados + 1 && after.ev_escalado === before.ev_escalado + 1, "O envio válido cria um chamado e um evento escalated");
  const ticket = one("SELECT status, channel FROM support_tickets ORDER BY id DESC LIMIT 1");
  check(ticket.status === "open" && ticket.channel === "web", "O chamado fica aberto na fila da equipe, canal web");
  await shot("A03_retorno_equipe");
});

// A01 – Reserva confirmada (site) ------------------------------------------
let bookingSession;
await run("A01", "Reserva confirmada (site)", "Antes da confirmação não existe reserva nova; depois existe exatamente uma, com preço e duração do resumo, e um evento booking_confirmed.", async () => {
  await newSession();
  const before = snapshot("antes");
  const dayLabel = await textOf('[data-testid="stMain"]');
  step(`Agendar: serviço Corte de Cabelo, barbeiro Sem preferência, primeiro dia aberto (${(dayLabel.match(/Horários disponíveis · (\d{2}\/\d{2})/) || [])[1]})`);
  await click(".st-key-booking_times button", "15:00");
  if (await evaluate(`!!document.querySelector('.st-key-booking_professionals button')`)) {
    const prof = await evaluate(`document.querySelector('.st-key-booking_professionals button').innerText.trim()`);
    await click(".st-key-booking_professionals button", prof);
    step(`Horário 15:00 com ${prof}`);
  }
  await type('input[aria-label="Seu nome"]', "Cliente Aceitação A01");
  await type('input[aria-label="Telefone com DDD"]', "16900000101");
  const review = await textOf(".st-key-booking_review");
  step(`Resumo: ${review.replace(/\s+/g, " ").slice(0, 160)}`);
  await shot("A01_revisao");
  check(snapshot("antes_de_confirmar").reservas === before.reservas, "Antes de confirmar não existe reserva nova");
  await click("button", "Confirmar agendamento");
  await waitText("Agendamento confirmado!");
  const after = snapshot("depois");
  check(after.reservas === before.reservas + 1 && after.ev_reserva === before.ev_reserva + 1, "Depois da confirmação existe exatamente uma reserva nova e um evento booking_confirmed");
  const row = one("SELECT a.*, s.name service FROM appointments a JOIN services s ON s.id=a.service_id ORDER BY a.id DESC LIMIT 1");
  const dur = (new Date(row.ends_at) - new Date(row.starts_at)) / 60000;
  check(row.service === "Corte de Cabelo" && hhmm(row.starts_at) === "15:00" && row.price_at_booking === 50 && dur === 40,
    `Reserva #${row.id} gravada com Corte de Cabelo, 15:00, R$ 50,00 e 40 min, como no resumo`);
  const owner = one("SELECT channel FROM booking_owners WHERE appointment_id=?", row.id);
  check(owner && owner.channel === "web", "A reserva ficou vinculada à sessão do site (booking_owners, canal web)");
  await shot("A01_comprovante");
  await tab("Meus horários");
  await waitText(`#${row.id}`);
  check(true, "Meus horários mostra a reserva na mesma sessão, sem pedir telefone ou código");
  await shot("A01_meus_horarios");
  bookingSession = { first: row.id };
  step("Parte do Telegram (/agendar): não executada nesta rodada, pois depende do token do bot");
});

// A08 – Sugestão clicável e confirmação -------------------------------------
await run("A08", "Sugestão clicável e confirmação", "O clique revalida catálogo e disponibilidade e preenche a revisão; nenhum clique cria reserva; só a confirmação grava.", async () => {
  await tab("Conversar");
  const before = snapshot("antes");
  const dd = fmt(D_SECOND);
  await chat(`Quero agendar corte dia ${dd} à tarde`);
  const labels = await evaluate(`[...document.querySelectorAll('.st-key-tab_chat_history button')].map(b => b.innerText.trim()).filter(t => /\\d{2}\\/\\d{2} · \\d{2}:\\d{2} · /.test(t))`);
  step(`Sugestões: ${labels.join("; ")}`);
  check(labels.length >= 3, "O chat apresenta sugestões clicáveis");
  // 1) Clique simples
  await click(".st-key-tab_chat_history button", labels[0]);
  const review1 = await textOf('[data-testid="stMain"]');
  const [, , , t0, b0] = labels[0].match(/(\d{2})\/(\d{2}) · (\d{2}:\d{2}) · (.+)/);
  check(review1.includes(b0) && review1.includes(t0.replace(":", "h")), `O clique abre Agendar com ${t0} e ${b0} preenchidos para revisão`);
  check(snapshot("apos_clique").reservas === before.reservas, "O clique não cria reserva");
  await shot("A08_sugestao_preenchida");
  // 2) Vaga ocupada por outra sessão antes do clique
  await tab("Conversar");
  const [, d1, m1, t1, b1] = labels[1].match(/(\d{2})\/(\d{2}) · (\d{2}:\d{2}) · (.+)/);
  const date1 = `${D_SECOND.getFullYear()}-${m1}-${d1}`;
  const occ = await api("POST", "/appointments", { customer_name: "Outra Sessão A08", customer_phone: "16900000808", service_id: 1,
    barber_id: barberId(b1), starts_at: `${date1}T${t1}:00-03:00`, confirmed: true });
  check(occ.status === 200, `Outra sessão ocupa a vaga ${t1} com ${b1} pela API`);
  const mid = snapshot("apos_ocupar");
  await click(".st-key-tab_chat_history button", labels[1]);
  const page2 = await textOf('[data-testid="stMain"]');
  check(/mudou ou acabou de ser ocupado|não está mais disponível|Escolha uma opção atualizada/i.test(page2), "A sugestão antiga é revalidada e o site avisa que a vaga não está mais disponível");
  check(snapshot("apos_clique_ocupado").reservas === mid.reservas, "Clicar na sugestão antiga não cria reserva");
  await shot("A08_vaga_ocupada");
  // 3) Preço alterado pelo dono antes do clique
  await tab("Conversar");
  const upd = await api("PATCH", "/staff/services/1", { price: 55, duration_minutes: 40 }, staff);
  check(upd.status === 200, "O dono altera o preço do corte para R$ 55,00");
  const pre3 = snapshot("antes_clique_preco");
  await click(".st-key-tab_chat_history button", labels[2]);
  const page3 = await textOf('[data-testid="stMain"]');
  check(/O serviço foi atualizado. Consulte as opções novamente/.test(page3) && !(await evaluate(`!!document.querySelector('.st-key-booking_review')`)),
    "Com o preço alterado, a sugestão antiga não abre a revisão e o site orienta nova consulta");
  check(snapshot("apos_clique_preco").reservas === pre3.reservas, "Clicar na sugestão com preço desatualizado não cria reserva");
  await shot("A08_preco_alterado");
  await api("PATCH", "/staff/services/1", { price: 50, duration_minutes: 40 }, staff);
  step("Preço do corte restaurado para R$ 50,00");
  // 4) Nova consulta e confirmação única de uma vaga válida
  await tab("Conversar");
  await chat(`Quero agendar corte dia ${dd} à tarde`);
  const fresh = await evaluate(`[...document.querySelectorAll('.st-key-tab_chat_history button')].map(b => b.innerText.trim()).filter(t => /\\d{2}\\/\\d{2} · \\d{2}:\\d{2} · /.test(t)).slice(-3)`);
  step(`Nova consulta: ${fresh.join("; ")}`);
  check(!fresh.some((label) => label.includes(`${t1} · ${b1}`)), `A nova consulta não oferece mais a vaga ocupada (${t1} com ${b1})`);
  await click(".st-key-tab_chat_history button", fresh[0]);
  const [, , , t2, b2] = fresh[0].match(/(\d{2})\/(\d{2}) · (\d{2}:\d{2}) · (.+)/);
  check(await evaluate(`!!document.querySelector('.st-key-booking_review')`), `A sugestão atualizada abre a revisão de ${t2} com ${b2}`);
  const nameFilled = await evaluate(`(document.querySelector('input[aria-label="Seu nome"]')||{}).value || ""`);
  if (!nameFilled) await type('input[aria-label="Seu nome"]', "Cliente Aceitação A01");
  const phoneFilled = await evaluate(`(document.querySelector('input[aria-label="Telefone com DDD"]')||{}).value || ""`);
  if (!phoneFilled) await type('input[aria-label="Telefone com DDD"]', "16900000101");
  const pre = snapshot("antes_de_confirmar");
  check(pre.reservas === pre3.reservas, "Nenhum dos cliques criou reserva");
  await click("button", "Confirmar agendamento");
  await waitText("Agendamento confirmado!");
  const after = snapshot("depois");
  check(after.reservas === pre.reservas + 1, "Somente a confirmação explícita cria a reserva (uma única)");
  const row = one("SELECT a.id, a.starts_at, a.price_at_booking, b.name barber FROM appointments a JOIN barbers b ON b.id=a.barber_id ORDER BY a.id DESC LIMIT 1");
  step(`Reserva #${row.id} criada: ${row.starts_at.slice(8, 10)}/${row.starts_at.slice(5, 7)} ${hhmm(row.starts_at)} com ${row.barber}, R$ ${row.price_at_booking}`);
  bookingSession.second = row.id;
  await shot("A08_confirmada");
});

// A09 – Consulta e alteração por mensagem (site) ----------------------------
await run("A09", "Consulta e alteração de reservas por mensagem", "Só reservas da sessão; com mais de uma, pergunta qual; nada muda antes da confirmação final separada; “não” mantém a reserva; “sim” aplica a alteração.", async () => {
  if (!bookingSession || !bookingSession.first || !bookingSession.second) throw new Error("A09 depende das reservas criadas em A01 e A08");
  await tab("Conversar");
  const before = snapshot("antes");
  const list = await chat("meus agendamentos");
  check(list.includes(`#${bookingSession.first}`) && list.includes(`#${bookingSession.second}`), "A consulta lista as duas reservas desta sessão");
  let a = await chat("quero remarcar minha reserva");
  check(/qual/i.test(a), "Com duas reservas, o assistente pergunta qual delas alterar");
  a = await chat(`reserva #${bookingSession.first}`);
  if (/Para qual nova data/i.test(a)) a = await chat(`${fmt(D_TUE)} à tarde`);
  check(/Encontrei estas opções para remarcar/.test(a), "O assistente oferece horários reais para a remarcação");
  const firstBefore = one("SELECT starts_at, status FROM appointments WHERE id=?", bookingSession.first);
  a = await chat("1");
  check(/Confirma a remarcação\? Responda sim ou não/.test(a), "Depois da escolha, pede confirmação final separada");
  check(one("SELECT starts_at FROM appointments WHERE id=?", bookingSession.first).starts_at === firstBefore.starts_at, "Antes do “sim”, a reserva não muda");
  a = await chat("sim");
  const moved = one("SELECT starts_at, status FROM appointments WHERE id=?", bookingSession.first);
  check(moved.starts_at !== firstBefore.starts_at && moved.starts_at.startsWith(iso(D_TUE)) && moved.status === "confirmed",
    `Com o “sim”, a reserva #${bookingSession.first} passa para ${fmt(D_TUE)} às ${hhmm(moved.starts_at)}`);
  await shot("A09_remarcada");
  a = await chat("quero cancelar minha reserva");
  if (/qual/i.test(a)) a = await chat(`reserva #${bookingSession.second}`);
  check(/Confirma o cancelamento\? Responda sim ou não/.test(a), "O cancelamento apresenta a reserva e pede confirmação");
  a = await chat("não");
  check(one("SELECT status FROM appointments WHERE id=?", bookingSession.second).status === "confirmed", "Com o “não”, a reserva continua confirmada");
  a = await chat("quero cancelar minha reserva");
  if (/qual/i.test(a)) a = await chat(`reserva #${bookingSession.second}`);
  a = await chat("sim");
  check(one("SELECT status FROM appointments WHERE id=?", bookingSession.second).status === "cancelled", `Com o “sim”, a reserva #${bookingSession.second} é cancelada`);
  await shot("A09_cancelada");
  await tab("Meus horários");
  await click("button", "Atualizar reservas").catch(() => {});
  const mine = await textOf('[data-testid="stMain"]');
  check(mine.includes(`#${bookingSession.first}`), "Meus horários reflete as alterações feitas por mensagem");
  await shot("A09_meus_horarios");
  const after = snapshot("depois");
  check(after.reservas === before.reservas, "Nenhuma reserva nova foi criada pelas alterações");
});

// A10 – Isolamento e recuperação sem o bot configurado -----------------------
await run("A10", "Isolamento entre sessões e recuperação sem o bot", "Outra sessão não vê nem altera as reservas; a recuperação informa que o Telegram não está configurado, sem simular envio.", async () => {
  await newSession();
  const before = snapshot("antes");
  await tab("Meus horários");
  const page = await textOf('[data-testid="stMain"]');
  check(/Nenhuma reserva vinculada a esta sessão/.test(page), "Uma sessão nova não vê as reservas de outra sessão");
  await tab("Conversar");
  const a = await chat("quero cancelar minha reserva");
  check(/telefone/i.test(a) && /Telegram/i.test(a), "Pedido de cancelamento em outra sessão inicia a verificação pelo telefone no Telegram");
  const b = await chat("16900000101");
  check(/não está configurada|indispon/i.test(b), "Sem o bot configurado, o chat informa que a verificação não está disponível, sem simular envio");
  check(one("SELECT status FROM appointments WHERE id=?", bookingSession.first).status === "confirmed", "A reserva da outra sessão continua confirmada");
  await shot("A10_chat_outra_sessao");
  await tab("Meus horários");
  const opened = await evaluate(`(() => { const s = [...document.querySelectorAll('summary')].find(e => e.innerText.includes('Localizar reservas')); const d = s && s.closest('details'); if (d && !d.open) s.click(); return true; })()`);
  await idle();
  await type('input[aria-label="Telefone usado na reserva"]', "16900000101", { enter: false });
  await click("button", "Verificar pelo Telegram");
  const msg = await textOf('[data-testid="stMain"]');
  check(/Telegram ainda não está configurada/i.test(msg), "Sem o token, o site informa que a verificação pelo Telegram não está configurada");
  const after = snapshot("depois");
  check(after.recuperacoes === before.recuperacoes, "Nenhum desafio ou código de recuperação é criado sem o bot");
  await shot("A10_recuperacao_sem_bot");
});

// A04 – não executável sem o Telegram --------------------------------------
scenario("A04", "Botões antigos do Telegram", "O menu antigo não altera a escolha nem cria reserva; a confirmação repetida não duplica reserva ou evento.");
current.notExecuted = "Não executado: depende do token do bot e de um aplicativo Telegram (canal coberto apenas pelos testes automatizados com transporte simulado).";

meta.fim = new Date().toISOString();
writeFileSync(`${OUT}/resultados.json`, JSON.stringify({ meta, results }, null, 2));
ws.close(); chrome.kill(); db.close();
const summary = results.map((r) => `${r.id}: ${r.notExecuted ? "não executado" : r.error ? "erro" : r.checks.every((c) => c.ok) ? "aprovado" : "reprovado"} (${r.checks.filter((c) => c.ok).length}/${r.checks.length})`);
console.log("\nResumo:\n" + summary.join("\n"));
