import { NavLink, Navigate, useParams } from "react-router-dom";
import { Dimensions } from "./Dimensions";
import { Glossary } from "./Glossary";
import { Identity } from "./Identity";
import { Jira } from "./Jira";
import { Manual } from "./Manual";
import { People } from "./People";
import { Planning } from "./Planning";
import { Process } from "./Process";
import { Projects } from "./Projects";
import { Risks } from "./Risks";
import { Rules } from "./Rules";

const SECTIONS = [
  ["projects", "Projects & products", Projects],
  ["process", "Process & stages", Process],
  ["dimensions", "Dimensions", Dimensions],
  ["people", "People & teams", People],
  ["identity", "Identity provider", Identity],
  ["jira", "Jira", Jira],
  ["planning", "Milestones & iterations", Planning],
  ["risks", "Risks", Risks],
  ["glossary", "Tags & glossary", Glossary],
  ["rules", "Translator rules", Rules],
  ["manual", "Manual mode & context pack", Manual],
] as const;

export function Setup() {
  const { ws, section } = useParams();
  const current = SECTIONS.find((s) => s[0] === section);
  if (!current) return <Navigate to={`/w/${ws}/setup/projects`} replace />;
  const Page = current[2];
  return (
    <div className="setup">
      <nav aria-label="Setup sections" className="subnav">
        {SECTIONS.map(([k, label]) => (
          <NavLink key={k} to={`/w/${ws}/setup/${k}`}>
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="setup-body">
        <h1>{current[1]}</h1>
        <Page />
      </div>
    </div>
  );
}
