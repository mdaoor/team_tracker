# Usability checklists

## Governance: a non-technical manager creates, tests and activates a template policy (Phase 9)
Run as a user with `policy.write` but not `policy.admin`; stop and note the step if any of these fail.

1. Policies → **New policy**. A gallery of templates is shown with plain names. No CEL appears anywhere on the page.
2. Pick **Item stuck in a stage**. A description and one field ("days") appear with its help text and limits.
3. Enter 3 days. Give a name; the key fills in. The **Create policy** button enables only while there are no problems.
4. Create. The next screen offers **Run dry run**; **Activate** is disabled and says why.
5. Run the dry run. A count and a sample list appear; a click on a sample opens that item.
6. **Activate** is now enabled. Activate. The policy shows as Active.
7. Violations lists the hits after the next sweep. Waive one: the reason box is required.
