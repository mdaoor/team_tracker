# 9. Repositories: product vs internal planning

The work is split over two repositories:

| Repository | Visibility | Contains |
| --- | --- | --- |
| **`team_tracker`** (this one) | team / customers | the application code, `scripts/`, deployment files, and **how to use** documentation: this guide, user guides, UAT, operations, security, API, client and integration docs, architecture decision records (`docs/decisions/`) and the normative product spec (`docs/spec/`, which the tests check the code against) |
| **`team_tracker-internal`** | **private**, protected `main` | everything the team plans and implements from: `plans/` (phase plans and appendices), `docs/reviews/` (implementation, technical and UX reviews with screenshots), `docs/handover/` (what is left after each pass), `tools/handover.py` (fills the handover prompts) and its test |

The list of internal paths is [`scripts/repo/internal-paths.txt`](../../scripts/repo/internal-paths.txt).

## Creating the private repository (one time)

Needs the GitHub CLI signed in with rights to create repositories in the owner account or organisation:
```bash
gh auth login
```
Preview what will be published (nothing is created or pushed):
```bash
scripts/ttctl repo split --repo mdaoor/team_tracker-internal --dry-run
```
Create it, keeping the full git history of those files (recommended; needs `pip install git-filter-repo`):
```bash
scripts/ttctl repo split --repo mdaoor/team_tracker-internal --history
```
Or as a single snapshot commit:
```bash
scripts/ttctl repo split --repo mdaoor/team_tracker-internal
```
The script:
1. finds a commit that still contains the internal files (`HEAD`, or automatically the last commit before they were removed;
   `--from REF` to choose);
2. builds the private repository in `.run/internal-repo` (with `--history`: only those paths, all their commits);
3. creates `OWNER/NAME` as **private** if it does not exist - and refuses to push to an existing repository that is not private;
4. pushes `main` and **protects** it: pull request with one approval, no force-push, no deletion. (On a free GitHub plan,
   branch protection on private repositories is not available; the script says so and the repository stays private.)

Give the team access:
```bash
gh api -X PUT repos/mdaoor/team_tracker-internal/collaborators/USERNAME -f permission=push
```

## Working with both
* Plan, review and write handover prompts in **team_tracker-internal**. Implement in **team_tracker** and link the plan
  section in the pull request description (the plan files name the tests that prove each acceptance criterion).
* Handover prompts: `python tools/handover.py kickoff 11` inside the internal repository.
* Keep user-facing documentation in this repository and update it in the same pull request as the code it describes.

## Old history in this repository
Removing the folders here does **not** remove them from this repository's git history or from older branches: anyone who
can read this repository can still check out an old commit. That is fine while this repository is private to the team.
If it becomes public, or the plans must not be readable by its readers, rewrite its history after the private copy exists
(this rewrites every commit id; coordinate with everyone who has a clone and re-clone afterwards):
```bash
git filter-repo --invert-paths --paths-from-file scripts/repo/internal-paths.txt
```
Then force-push the rewritten branches and delete the old branches that still carry the plans.
