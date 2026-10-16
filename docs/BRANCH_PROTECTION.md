# Branch protection and merge policy

This file records the intended merge policy and the GitHub configuration that enforces it.
On October 8, 2026 the live ruleset, bypass actor, and repository merge settings were read back
and matched this record. GitHub also returns parameters this record does not set, such as
`require_extra_approval_for_unattributed_changes: true`. Use the read-only inspection commands
at the end to recheck before relying on enforcement; configuration changes require separate
approval.

## Merge policy

- **Squash merge** is the default for ordinary feature and fix pull requests. The branch collapses into
  one commit on `main`, so `main` reads as one commit per reviewed change.
- **Merge commit** is reserved for the cases that genuinely need topology preserved: release branches,
  long-running integration branches, and upstream or fork syncs.
- **Rebase merge** is not used on `main`. It remains enabled at the repository level, so it is still
  available on other branches, but the `main` ruleset refuses it.

The reason squash is the default is that it makes the unit of history match the unit of review. One pull
request becomes one commit that was reviewed and tested as a whole, which keeps `git bisect` meaningful
and makes reverting a change a single-commit operation.

## Recorded enforcement on `main`

| Rule | Effect |
| --- | --- |
| Pull request required | Direct pushes to `main` are rejected; every change arrives through a pull request |
| Required approvals: 0 | A pull request is required, but no second approver is, so a solo maintainer can merge |
| Conversation resolution required | All review threads must be resolved before merging |
| Allowed merge methods | `squash` and `merge` only; rebase is refused on `main` |
| Block force pushes | History on `main` cannot be rewritten |
| Block deletion | `main` cannot be deleted |

## Ruleset definition

The recorded configuration, originally created through the repository rulesets API:

```json
{
  "name": "protect-main",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [
    { "actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always" }
  ],
  "conditions": {
    "ref_name": { "include": ["refs/heads/main"], "exclude": [] }
  },
  "rules": [
    {
      "type": "pull_request",
      "parameters": {
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_approving_review_count": 0,
        "required_review_thread_resolution": true,
        "allowed_merge_methods": ["squash", "merge"]
      }
    },
    { "type": "non_fast_forward" },
    { "type": "deletion" }
  ]
}
```

In this repository the ruleset is id `20806131`. Ruleset ids are per-repository, so treat that value as
an instance detail rather than part of the policy.

## Repository merge settings

| Setting | Value | Why |
| --- | --- | --- |
| `allow_squash_merge` | `true` | Default method for feature and fix pull requests |
| `allow_merge_commit` | `true` | Needed for the topology cases described above |
| `allow_rebase_merge` | `true` | Left enabled for other branches; the `main` ruleset still refuses it |
| `squash_merge_commit_title` | `PR_TITLE` | Makes the squash subject deterministically the pull request title |
| `squash_merge_commit_message` | `COMMIT_MESSAGES` | Keeps the developer-facing detail from the branch commits |

`squash_merge_commit_title` matters for Conventional Commits. `PR_TITLE` makes the subject on
`main` the pull request title regardless of how many commits the branch has, so a Conventional
Commits pull request title is always what lands.

## What is convention rather than enforcement

GitHub scopes `allowed_merge_methods` to a ruleset, and a ruleset's conditions match the **base** branch,
not the source branch. A feature pull request and a release-branch pull request both target `main`, so
GitHub cannot distinguish them.

The split is therefore enforced as a menu and documented as a convention: the ruleset guarantees that
only `squash` or `merge` is available, and this document plus `CONTRIBUTING.md` state which to pick.
Choosing a merge commit for an ordinary feature pull request is possible and will not be blocked.

## Solo-maintainer rationale

`required_approving_review_count` is `0` deliberately. A required approval must come from someone other
than the pull request author, so any non-zero value would prevent a single maintainer from merging their
own work and would force a bypass on every merge, which defeats the point of the rule. Requiring the
pull request itself still delivers the goal: no direct commits to `main`, and every change has a
reviewable record.

Raise this to `1` when a second maintainer joins.

## Emergency bypass

Repository rulesets grant bypass by role, team, or app rather than to individual users. Bypass is granted
to the repository **admin** role (`actor_id: 5`) with `bypass_mode: "always"`, which covers the current
maintainer. The API reports `current_user_can_bypass: "always"` for an admin under this configuration.

Without a bypass actor the same configuration reports `current_user_can_bypass: "never"`, and the only
escape hatch is editing or deleting the ruleset in Settings.

## Reproducing or changing the configuration

Inspect the current rulesets:

```bash
gh api repos/Snowflake-Solutions/cortex-ai-consumption-dashboard/rulesets \
  --jq 'map({name, id, enforcement, target})'
```

Inspect one ruleset in full:

```bash
gh api repos/Snowflake-Solutions/cortex-ai-consumption-dashboard/rulesets/20806131
```

Replace the ruleset from a JSON file, sending the complete rule set because this is a full update.
The `pull_request` rule must include `dismiss_stale_reviews_on_push`, `require_code_owner_review`,
`require_last_push_approval`, `required_approving_review_count`, and
`required_review_thread_resolution`; a subset fails with
`HTTP 422: Invalid property /rules/0: data matches no possible input`:

```bash
gh api -X PUT repos/Snowflake-Solutions/cortex-ai-consumption-dashboard/rulesets/20806131 \
  --input ruleset.json
```

Change a merge setting:

```bash
gh api -X PATCH repos/Snowflake-Solutions/cortex-ai-consumption-dashboard \
  -f squash_merge_commit_title=PR_TITLE
```

Test a change without enforcing it by creating the ruleset with `"enforcement": "evaluate"`, which
records what would have been blocked instead of blocking it.
