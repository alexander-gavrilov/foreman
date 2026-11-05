foreman project rule: do not create work files (notes, scratch scripts, logs, drafts, dumps) inside the repository.
Get a work folder with `foreman work new <topic>` and keep every artifact there.
Do not read other agents' work folders unless you were given a path.
Only the deliverables the task asks for belong in the repository.
Report stage changes with `foreman stage <task> <stage>`; finish with a short summary.
Report problems with foreman or with a project rule using `foreman report`.
