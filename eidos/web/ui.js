import { SignedClient } from "./transport.js";
const element = (id) => document.getElementById(id);
const output = element("result");
let client = null;
let latest = null;
let profiles = [];
let reviewRequestId = null;
function show(value) { output.textContent = JSON.stringify(value, null, 2); }
function error(value) { show({ error: value instanceof Error ? value.message : "Request failed" }); }
element("connect").addEventListener("click", async () => {
    try {
        const secret = element("secret");
        latest = null;
        reviewRequestId = null;
        client = new SignedClient(element("key").value, secret.value);
        secret.value = "";
        const inventory = await client.request("GET", "/v1/models");
        profiles = inventory.profiles;
        const select = element("profile");
        select.replaceChildren();
        for (const profile of profiles) {
            const option = document.createElement("option");
            option.value = profile.model.id;
            option.textContent = profile.domain;
            select.append(option);
        }
        show(inventory);
    }
    catch (e) {
        client = null;
        error(e);
    }
});
element("forget").addEventListener("click", () => { client = null; latest = null; reviewRequestId = null; profiles = []; element("history").replaceChildren(); output.textContent = "Disconnected"; });
element("decide").addEventListener("click", async () => {
    try {
        if (!client)
            throw new Error("Connect with an operator key first");
        const profile = element("profile").value;
        const selected = profiles.find(x => x.model.id === profile);
        if (!selected)
            throw new Error("Select an available classifier");
        const common = { id: "classification", profile, prompt: "Classify the supplied text", state_path: ["text"] };
        const question = profile === "completeness-v1" ? { ...common, kind: "BINARY", positive_class: "complete", negative_class: "incomplete" } :
            profile === "priority-v1" ? { ...common, kind: "SCORE", minimum: 0, maximum: 100, value_type: "number",
                anchors: [{ class_id: "low", value: 0 }, { class_id: "medium", value: 50 }, { class_id: "high", value: 100 }] } :
                { ...common, kind: "CHOICE", choices: selected.labels.map(x => ({ id: x, class_id: x })) };
        const request = { schema_version: "1.0", input_version: "operator-1", request_id: crypto.randomUUID(),
            tenant_id: element("tenant").value, language: "en", state: { text: element("text").value },
            sources: [], questions: [question], budget_ms: 1000 };
        latest = await client.decide(request);
        reviewRequestId = latest.request_id;
        show(latest);
    }
    catch (e) {
        error(e);
    }
});
for (const action of ["accepted", "rejected"]) {
    element(action).addEventListener("click", async () => {
        try {
            if (!client || !reviewRequestId)
                throw new Error("Make or select a decision first");
            show(await client.request("POST", "/v1/reviews", { request_id: reviewRequestId, action }));
        }
        catch (e) {
            error(e);
        }
    });
}
element("refresh").addEventListener("click", async () => {
    try {
        if (!client)
            throw new Error("Connect first");
        const data = await client.request("GET", "/v1/reviews");
        const list = element("history");
        list.replaceChildren();
        for (const item of data.items) {
            const button = document.createElement("button");
            button.className = "secondary";
            button.textContent = item.request_id + " · " + (item.review ?? "awaiting review");
            button.addEventListener("click", () => { reviewRequestId = item.request_id; show(item); });
            list.append(button);
        }
        if (!data.items.length)
            list.textContent = "No retained decisions";
    }
    catch (e) {
        error(e);
    }
});
