// Transport is supplied by the authenticated host application; no implicit network or auth.
export async function decide(request, transport) { return transport(request); }
