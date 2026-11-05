foreman project rule: do not create work files (notes, scratch scripts, logs, drafts, dumps) inside the repository.
Get a work folder with `foreman work new <topic>` and keep every artifact there.
Do not read other agents' work folders unless you were given a path.
Only the deliverables the task asks for belong in the repository.
Report stage changes with `foreman stage <task> <stage>`; finish with a short summary.
Report a problem with foreman itself with `foreman report`; a problem with a project rule or workflow with `foreman report --project` (plain `foreman report` may be filed publicly).
