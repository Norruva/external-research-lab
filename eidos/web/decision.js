const encoder = new TextEncoder();
let session = null;
let generation = 0;
const element = id => document.getElementById(id);
const hex = bytes => Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, "0")).join("");
const display = value => { element("result").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2); };
const example = {format: "eidos-decision-request-v2", request_id: "original-browser-example", kind: "CHOICE",
    context: "Mira carries a red notebook. Oren carries a blue folder.", prompt: "What color is Mira's notebook?",
    options: [{id: "answer-a", description: "The notebook is red."}, {id: "answer-b", description: "The notebook is blue."}]};
element("request").value = JSON.stringify(example, null, 2);

function disconnect() {
    generation += 1;
    if (session) for (const controller of session.controllers) controller.abort();
    session = null;
    element("operator-key").value = "";
    element("decide").disabled = true;
    element("session-status").textContent = "Disconnected";
}

async function call(method, path, value) {
    const active = session;
    if (!active || !["/v2/model", "/v2/decisions"].includes(path)) throw new Error("Connect first");
    const body = value === undefined ? "" : (typeof value === "string" ? value : JSON.stringify(value));
    const bytes = encoder.encode(body);
    if (bytes.length > 1048576) throw new Error("Request exceeds the supported byte budget");
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const nonce = hex(crypto.getRandomValues(new Uint8Array(16)));
    const digest = hex(await crypto.subtle.digest("SHA-256", bytes));
    const message = [active.keyId, timestamp, nonce, method, path, digest].join("\n");
    const mac = hex(await crypto.subtle.sign("HMAC", active.key, encoder.encode(message)));
    if (session !== active) throw new Error("Session ended");
    const controller = new AbortController();
    active.controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
        const response = await fetch(path, {method, credentials: "omit", redirect: "error", cache: "no-store", signal: controller.signal,
            headers: {"Content-Type": "application/json", "X-Eidos-Key": active.keyId, "X-Eidos-Time": timestamp,
                "X-Eidos-Nonce": nonce, "X-Eidos-Signature": mac}, ...(method === "POST" ? {body} : {})});
        const text = await response.text();
        if (text.length > 2000000) throw new Error("Response exceeds the supported budget");
        if (!response.ok) throw new Error("Request refused (HTTP " + response.status + ")");
        return JSON.parse(text);
    } finally { clearTimeout(timer); active.controllers.delete(controller); }
}

element("disconnect").addEventListener("click", () => { disconnect(); display("Disconnected"); });
// A generation prevents an older connection or request from replacing a new session.
element("connect").addEventListener("click", async () => {
    const keyId = element("key-id").value;
    const secret = element("operator-key").value;
    disconnect();
    const current = generation;
    try {
        if (!/^[A-Za-z0-9_-]{1,80}$/.test(keyId) || !/^[0-9a-f]{64}$/.test(secret)) throw new Error("Enter a valid key ID and 64-character lowercase hexadecimal key");
        const raw = Uint8Array.from(secret.match(/../g), pair => parseInt(pair, 16));
        const key = await crypto.subtle.importKey("raw", raw, {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
        raw.fill(0);
        if (generation !== current) return;
        session = {keyId, key, controllers: new Set()};
        const model = await call("GET", "/v2/model");
        if (!session || session.key !== key) return;
        element("session-status").textContent = "Connected · Local model · Review required";
        element("decide").disabled = false;
        display(model);
    } catch (error) {
        if (generation === current) { disconnect(); display(error instanceof Error ? error.message : "Connection failed"); }
    }
});
element("decide").addEventListener("click", async () => {
    const current = generation;
    element("decide").disabled = true;
    try {
        const text = element("request").value;
        if (encoder.encode(text).length > 1048576) throw new Error("Request exceeds the supported byte budget");
        const result = await call("POST", "/v2/decisions", text);
        if (generation === current) display(result);
    } catch (error) {
        if (generation === current) display(error instanceof Error ? error.message : "Request failed");
    } finally { if (generation === current) element("decide").disabled = session === null; }
});
window.addEventListener("pagehide", disconnect);
