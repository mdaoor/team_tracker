import type { Story } from "@ladle/react";
import { Sparkline } from "../components/Sparkline";
import {
  Button,
  Card,
  Chip,
  ConfidenceCue,
  Empty,
  Field,
  Pill,
  ProvenanceChip,
} from "../components/ui";
import { emptyAuto, emptyNotify } from "../lib/governance";
import { AutoActionEditor, NotifyEditor, VersionHistory } from "../pages/governance/PolicyEditors";

export const Buttons: Story = () => (
  <div className="row">
    <Button>Default</Button>
    <Button variant="primary">Primary</Button>
    <Button variant="ghost">Ghost</Button>
    <Button disabled>Disabled</Button>
  </div>
);

export const Pills: Story = () => (
  <div className="row">
    {(["info", "warning", "serious", "critical"] as const).map((s) => (
      <Pill key={s} severity={s}>
        {s}
      </Pill>
    ))}
  </div>
);

export const ChipsAndCues: Story = () => (
  <div className="row">
    <Chip onRemove={() => undefined}>project WEB</Chip>
    <ConfidenceCue confidence={0.92} />
    <ConfidenceCue confidence={0.55} />
    <ConfidenceCue confirmed />
    <ProvenanceChip source="manual" setBy="@sara" setAt="2026-10-01" />
    <ProvenanceChip source="inherited" inheritedFrom="WEB-1" />
  </div>
);

export const CardWithField: Story = () => (
  <Card title="Details" actions={<Button variant="ghost">Edit</Button>}>
    <Field label="Name">
      <input defaultValue="Late finish" />
    </Field>
    <Empty>Nothing else to show.</Empty>
  </Card>
);

export const Sparklines: Story = () => (
  <ul className="plain">
    <li>
      Improving <Sparkline label="Late finish" values={[0.5, 0.4, 0.4, 0.3, 0.2, 0.2, 0.1, 0.1]} />
    </li>
    <li>
      Gap in the middle <Sparkline label="Evidence" values={[0.2, 0.3, null, null, 0.6, 0.7]} />
    </li>
    <li>
      Single point <Sparkline label="New habit" values={[null, null, 0.4]} />
    </li>
    <li>
      No data <Sparkline label="Empty" values={[null, null]} />
    </li>
  </ul>
);

export const PolicyEditors: Story = () => (
  <>
    <NotifyEditor
      value={{ ...emptyNotify(), reporting_line: ["line"] }}
      onChange={() => undefined}
    />
    <NotifyEditor value={emptyNotify()} onChange={() => undefined} wellbeing />
    <AutoActionEditor
      value={emptyAuto()}
      onChange={() => undefined}
      stages={["build", "test"]}
      projects={["WEB", "DATA"]}
    />
    <VersionHistory
      canWrite
      onRestore={() => undefined}
      versions={[
        {
          event_id: "2",
          kind: "updated",
          at: "2026-10-20T10:00:00Z",
          changed: ["severity"],
          definition: {},
        },
        { event_id: "1", kind: "created", at: "2026-10-01T10:00:00Z", changed: [], definition: {} },
      ]}
    />
  </>
);
