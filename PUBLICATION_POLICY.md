# Public content policy

The public distribution contains reviewed application source, runtime tests, synthetic fixtures, examples, schemas, package configuration, user/API documentation, license/citation and contributor/security guidance, a changelog and a concise roadmap.

Operational notes, chat context, execution instructions, unpublished research, article drafts, raw logs, private datasets, credentials and runtime state belong outside this public distribution. An approved source change does not automatically authorize publication of adjacent documents.

PUBLIC_CONTENT.json is an exact reviewed-file allowlist. CI checks tracked files against it, and source packaging selects only listed files plus the matching built wheel and generated integrity manifest. New files and public claims need explicit review. Generated API/site output must be rebuilt from this reviewed tree; never copy a private workspace or its Git history into a public repository. A passing content check is not proof that every source file is safe: review diffs and use secret scanning as well.
