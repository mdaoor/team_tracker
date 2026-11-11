/** English catalogue (Phase 5 §5: i18n foundations). Keys are stable; add a locale by adding another table. */
const en = {
  "nav.setup": "Setup",
  "nav.review": "Review queue",
  "nav.views": "Views",
  "nav.plan": "Plans",
  "nav.present": "Present",
  "nav.policies": "Policies",
  "nav.weights": "Weights",
  "nav.violations": "Violations",
  "nav.habits": "Habits",
  "nav.performance": "Performance",
  "nav.digest": "Digest",
  "nav.insights": "Insights",
  "review.empty": "You're all caught up",
  "review.confirmAll": "Confirm all suggested",
  "common.coming": "Coming soon",
  "login.title": "Sign in",
} as const;

export type MsgKey = keyof typeof en;
export const t = (k: MsgKey): string => en[k];
export const dir = (): "ltr" | "rtl" => (document.documentElement.dir === "rtl" ? "rtl" : "ltr");
