import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/http";
import { devLogin, getAuthProviders, ldapLogin } from "../api/session";
import { t } from "../lib/i18n";
import { Button, Field } from "../components/ui";

export function Login() {
  const [workspace, setWorkspace] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const nav = useNavigate();
  const [dev, setDev] = useState(false);
  useEffect(() => {
    getAuthProviders()
      .then((p) => setDev(!!p.dev))
      .catch(() => setDev(false));
  }, []);
  const devSignIn = async () => {
    setError(null);
    try {
      await devLogin({ workspace: workspace || "acme", handle: username || "@mahmoud" });
      nav(`/w/${workspace || "acme"}/review`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await ldapLogin({ workspace, username, password });
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
      {dev && (
        <p>
          <Button onClick={() => void devSignIn()}>
            Development sign-in as {username || "@mahmoud"} in {workspace || "acme"}
          </Button>
          <br />
          <small>
            Local installs only (TT_DEV_AUTH). Type a handle above, or accept the default.
          </small>
        </p>
      )}
      <p>
        <a href={`/auth/login?workspace=${encodeURIComponent(workspace || "")}`}>
          Sign in with single sign-on
        </a>
      </p>
    </main>
  );
}
