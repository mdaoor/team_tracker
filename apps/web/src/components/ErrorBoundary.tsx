import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "./ui";

type Props = { children: ReactNode; label?: string; resetKey?: string };
type State = { error: Error | null };

/** A render error in one screen shows this instead of a blank app (review T12). Navigating elsewhere clears it. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("render error", error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <section role="alert" className="denied" aria-labelledby="eb-h">
        <h1 id="eb-h">Something went wrong{this.props.label ? ` in ${this.props.label}` : ""}</h1>
        <p>The rest of the app still works. You can reload this screen or go back.</p>
        <p>
          <small>{this.state.error.message}</small>
        </p>
        <div className="row">
          <Button variant="primary" onClick={() => location.reload()}>
            Reload
          </Button>
          <Button onClick={() => this.setState({ error: null })}>Try again</Button>
        </div>
      </section>
    );
  }
}
