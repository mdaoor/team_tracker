import type { Row } from "../api/http";
import { Button } from "./ui";

/** UX §3.4: someone else changed the value while you were editing. */
export function ConflictDialog({
  field,
  mine,
  theirs,
  changedBy,
  onKeepMine,
  onTakeTheirs,
  onRevert,
}: {
  field: string;
  mine: unknown;
  theirs: unknown;
  changedBy?: string;
  onKeepMine: () => void;
  onTakeTheirs: () => void;
  onRevert: () => void;
}) {
  return (
    <div role="alertdialog" aria-modal="true" aria-label="Edit conflict" className="dialog">
      <h3>Changed by {changedBy ?? "someone else"}</h3>
      <p>
        <b>{field}</b>: theirs is <code data-testid="theirs">{String(theirs)}</code>, yours is{" "}
        <code data-testid="mine">{String(mine)}</code>.
      </p>
      <div className="row">
        <Button variant="primary" onClick={onKeepMine}>
          Keep mine
        </Button>
        <Button onClick={onTakeTheirs}>Take theirs</Button>
        <Button variant="ghost" onClick={onRevert}>
          Revert
        </Button>
      </div>
    </div>
  );
}

export const conflictFields = (current: Row): string[] => Object.keys(current);
