/** Where the e2e API listens (TT_E2E_API_PORT overrides 8010 so parallel runs do not collide). */
export const ORIGIN = `http://localhost:${process.env.TT_E2E_API_PORT ?? "8010"}`;
export const API = `${ORIGIN}/api/v1/w/acme`;
