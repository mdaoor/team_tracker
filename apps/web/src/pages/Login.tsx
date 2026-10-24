import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, type Row } from "../api/http";
import { t } from "../lib/i18n";
import { Button, Field } from "../components/ui";

export function Login() {
  const [workspace, setWorkspace] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const nav = useNavigate();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.post<Row>("/auth/ldap/login", { workspace, username, password });
      nav(`/w/${workspace}/review`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };
  return (
    <main className="login">
      <h1>{t("login.title")}</h1>
      <form onSubmit={submit}>
        <Field label="Workspace">
          <input
            value={workspace}
            onChange={(e) => setWorkspace(e.target.value)}
            required
            autoFocus
          />
        </Field>
        <Field label="Username (DOMAIN\user or user@domain)">
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoComplete="username"
          />
        </Field>
        <Field label="Password">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
        </Field>
        {error && (
          <p role="alert" className="err">
            {error}
          </p>
        )}
        <Button variant="primary" type="submit">
          Sign in
        </Button>
      </form>
      <p>
        <a href={`/auth/login?workspace=${encodeURIComponent(workspace || "")}`}>
          Sign in with single sign-on
        </a>
      </p>
    </main>
  );
}
