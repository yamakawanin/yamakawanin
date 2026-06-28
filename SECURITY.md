# Security

## Private repository names

This profile repository is public. If private repositories are enabled, only
their names are written to `README.md`; links, descriptions, languages, topics,
and source code are never written. Publishing a name still reveals that name.

Set `"include_private": false` in `.project-index.json` if repository names are
sensitive.

## Token requirements

Use a fine-grained personal access token named `PROFILE_TOKEN`:

- Resource owner: `yamakawanin`
- Repository access: the repositories that should appear in the index
- Repository permissions: Metadata — Read-only
- Expiration: the shortest practical period

Do not use a classic token or grant Contents, Administration, Actions, Secrets,
or write permissions. Store the token only as a GitHub Actions repository
secret. Never put it in this repository, `.project-index.json`, shell history,
or README.

The workflow exposes the secret only to the project-collection step. It is not
available to the commit step. The workflow is triggered only by a schedule or a
manual dispatch, not by pull requests.
