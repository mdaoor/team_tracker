import { t } from "../lib/i18n";

export function ComingSoon({ name }: { name: string }) {
  return (
    <section>
      <h1>{name}</h1>
      <p>{t("common.coming")}</p>
    </section>
  );
}
