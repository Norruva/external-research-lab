const encoder = new TextEncoder();
function hex(data) {
    return Array.from(new Uint8Array(data), x => x.toString(16).padStart(2, "0")).join("");
}
function unhex(value) {
    if (!/^[0-9a-f]{64}$/.test(value))
        throw new Error("Enter a 64-character lowercase hex key");
    return Uint8Array.from(value.match(/../g), x => parseInt(x, 16));
}
export class SignedClient {
    constructor(keyId, secret) {
        this.keyId = keyId;
        if (!/^[A-Za-z0-9_-]{1,80}$/.test(keyId))
            throw new Error("Invalid key identifier");
        this.key = crypto.subtle.importKey("raw", unhex(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    }
    async request(method, path, value) {
        if (!["/v1/decisions", "/v1/reviews", "/v1/models", "/v1/releases"].includes(path))
            throw new Error("Unsupported API path");
        const body = value === undefined ? "" : JSON.stringify(value);
        const timestamp = Math.floor(Date.now() / 1000).toString();
        const nonce = hex(crypto.getRandomValues(new Uint8Array(16)).buffer);
        const digest = hex(await crypto.subtle.digest("SHA-256", encoder.encode(body)));
        const message = [this.keyId, timestamp, nonce, method, path, digest].join("\n");
        const mac = hex(await crypto.subtle.sign("HMAC", await this.key, encoder.encode(message)));
        const response = await fetch(path, { method, credentials: "omit", redirect: "error", cache: "no-store",
            headers: { "Content-Type": "application/json", "X-Eidos-Key": this.keyId, "X-Eidos-Time": timestamp,
                "X-Eidos-Nonce": nonce, "X-Eidos-Signature": mac }, ...(method === "POST" ? { body } : {}) });
        const text = await response.text();
        if (text.length > 2000000)
            throw new Error("Response limit exceeded");
        const data = JSON.parse(text);
        if (!response.ok)
            throw new Error("Request failed (" + response.status + ")");
        return data;
    }
    decide(value) { return this.request("POST", "/v1/decisions", value); }
}
